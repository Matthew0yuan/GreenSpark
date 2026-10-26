"""Step 2 (PLAN.md): at 09:00 the day before, rank tomorrow's 16:00-21:00 lowest reserve margin.

B0  score = the 08:00 pre-dispatch energy requirement, max over D 16:00-21:00 (higher = tighter)
M1  per half-hour t: thermal Capacity Credits - outages registered in the D-1 snapshot + battery capability
    - (pre-dispatch requirement - wind estimate - utility solar estimate - constant); the day's M1 = min over t
    wind / solar estimates: OLS of (pre-dispatch requirement - ex-post residual demand) on wind speed (two regions,
    linear + square) and Perth irradiance, per half-hour
M2  OLS on M1's terms at M1's tightest half-hour t*, with thermal capacity and battery at coefficient 1
Both fits walk forward by calendar month: target days from the table's start to (month start - 5 days), never after
TRAIN_END; months after TRAIN_END reuse the 2026-07 fit (frozen).

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_forecast/forecast.py
Writes out/daily_predictions.csv, out/fits.csv, out/*.csv results, out/summary.json.
"""

import sys

sys.dont_write_bytecode = True  # never write __pycache__ outside the task folder (imports below)

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vpp.evaluate import CUTOFF as VPP_CUTOFF, TRAIN_END as VPP_TRAIN_END  # noqa: E402

INP = HERE.parent / "tightness_inputs" / "out"
OUT = HERE / "out"

TRAIN_END = pd.Timestamp("2026-06-30")      # [PLAN]; must equal vpp.evaluate
CUTOFF = pd.Timestamp("2026-07-01")
assert TRAIN_END == VPP_TRAIN_END and CUTOFF == VPP_CUTOFF
EMBARGO = pd.Timedelta(days=5)              # train on target days <= month start - 5 days [PLAN 4]
MIN_TRAIN = 30                              # fewer training target days -> no prediction that month [PLAN 4]
LOW = 0.10
N_BOOT, SEED = 2000, 20261026
N_SLOTS = 10
FROZEN_MONTH = pd.Period("2026-07", "M")    # the fit every test month reuses
METHODS = ("B0", "M1", "M2")
SCORE = {"B0": "b0_score", "M1": "m1_mw", "M2": "m2_mw", "actual": "margin_min_1621_mw"}
PERIOD_NAMES = {"history": "历史诊断 2024-01-01 至 2025-03-31", "in_sample": "样本内 2025-04-01 至 2026-06-30",
                "test_seen": "测试期（已看过）2026-07-01 至 2026-10-15",
                "test_unseen": "测试期（没看过）2026-10-16 至 2026-10-22"}
CONV_X = ["one", "v1", "v1sq", "v2", "v2sq", "g"]
M2_X = ["one", "out_t", "req_t", "wind_t", "solar_t"]


# ------------------------------------------------------------------------------------------------------------ data
def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    d = pd.read_parquet(INP / "inputs_d1.parquet")
    d = d.loc[:, ~d.columns.duplicated()].set_index("day").sort_index()
    h = pd.read_parquet(INP / "inputs_d1_30min.parquet").sort_values(["day", "target_time"]).reset_index(drop=True)
    assert (h.groupby("day").size() == N_SLOTS).all()
    return d, h


def conv_matrix(h: pd.DataFrame) -> pd.DataFrame:
    v1, v2, g = h["wx_wind_midwest_kmh"], h["wx_wind_merredin_kmh"], h["wx_ghi_perth_wm2"]
    return pd.DataFrame({"one": 1.0, "v1": v1, "v1sq": v1 ** 2, "v2": v2, "v2sq": v2 ** 2, "g": g}, index=h.index)


def ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(X, y, rcond=None)[0]


# ----------------------------------------------------------------------------------------------------------- model
def slot_terms(h: pd.DataFrame, conv: np.ndarray) -> pd.DataFrame:
    """Per half-hour: wind / solar estimates, predicted residual demand, M1(t)."""
    X = conv_matrix(h)
    wind = X[["v1", "v1sq", "v2", "v2sq"]].to_numpy() @ conv[1:5]
    solar = X["g"].to_numpy() * conv[5]
    res = h["pd_req_mw"].to_numpy() - conv[0] - wind - solar
    m1 = h["thermal_cap_mw"].to_numpy() - h["out_thermal_mw"].to_numpy() + h["bat_cap_mw"].to_numpy() - res
    return pd.DataFrame({"day": h["day"].to_numpy(), "slot": h["slot"].to_numpy(), "wind_t": wind, "solar_t": solar,
                         "res_t": res, "m1_t": m1, "out_t": h["out_thermal_mw"].to_numpy(),
                         "req_t": h["pd_req_mw"].to_numpy(), "thermal": h["thermal_cap_mw"].to_numpy(),
                         "bat": h["bat_cap_mw"].to_numpy(), "const": conv[0]}, index=h.index)


def day_terms(st: pd.DataFrame) -> pd.DataFrame:
    """Per day: M1 = min over the 10 half-hours (all 10 needed), t*, the terms at t*."""
    ok = st.groupby("day")["m1_t"].count() == N_SLOTS
    s = st[st["day"].isin(ok[ok].index)]
    if s.empty:
        return pd.DataFrame(columns=["m1_mw", "t_star", "out_t", "req_t", "wind_t", "solar_t", "res_t", "thermal",
                                     "bat", "const"])
    at = s.loc[s.groupby("day")["m1_t"].idxmin()].set_index("day")
    return at.rename(columns={"m1_t": "m1_mw", "slot": "t_star"})[
        ["m1_mw", "t_star", "out_t", "req_t", "wind_t", "solar_t", "res_t", "thermal", "bat", "const"]]


def m2_matrix(dt: pd.DataFrame) -> np.ndarray:
    return np.c_[np.ones(len(dt)), dt[["out_t", "req_t", "wind_t", "solar_t"]].to_numpy()]


def fit(d: pd.DataFrame, h: pd.DataFrame, train_end: pd.Timestamp, cap: bool = True) -> dict:
    """The conversion regression and M2 on target days <= train_end (and <= TRAIN_END unless cap is False: only the
    planted leak of self-check T2 lifts it)."""
    last = min(train_end, TRAIN_END) if cap else train_end
    hr = h[(h["day"] <= last)]
    X = conv_matrix(hr)
    y = hr["pd_req_mw"] - hr["lab_residual_mw"]
    ok = X.notna().all(axis=1) & y.notna()
    n_days = hr.loc[ok, "day"].nunique()
    out = {"train_last_day": last, "conv_days": int(n_days), "conv_rows": int(ok.sum())}
    if n_days < MIN_TRAIN:
        return out
    conv = ols(X[ok].to_numpy(), y[ok].to_numpy())
    out["conv"] = conv
    dt = day_terms(slot_terms(hr, conv))
    lab = d["margin_min_1621_mw"].reindex(dt.index)
    keep = lab.notna() & dt[["out_t", "req_t", "wind_t", "solar_t", "thermal", "bat"]].notna().all(axis=1)
    dt, lab = dt[keep], lab[keep]
    out["m2_days"] = int(len(dt))
    if len(dt) < MIN_TRAIN:
        return out
    out["beta"] = ols(m2_matrix(dt), (lab - dt["thermal"] - dt["bat"]).to_numpy())
    return out


def predict(days: pd.DatetimeIndex, d: pd.DataFrame, h: pd.DataFrame, f: dict) -> pd.DataFrame:
    hp = h[h["day"].isin(days)]
    if "conv" not in f:
        return pd.DataFrame(index=pd.DatetimeIndex([], name="day"))
    dt = day_terms(slot_terms(hp, f["conv"]))
    if "beta" in f:
        dt["m2_mw"] = dt["thermal"] + dt["bat"] + m2_matrix(dt) @ f["beta"]
    else:
        dt["m2_mw"] = np.nan
    return dt


def month_train_end(m: pd.Period) -> pd.Timestamp:
    return m.start_time - EMBARGO


def walk_forward(d: pd.DataFrame, h: pd.DataFrame, months=None, include_month: bool = False) -> tuple[pd.DataFrame, list]:
    """Predictions for every target day, each month from its own fit (test months: the frozen 2026-07 fit).
    include_month: the planted leak of self-check T2 (the month's own days in its training)."""
    all_months = pd.period_range(d.index.min(), d.index.max(), freq="M")
    months = all_months if months is None else months
    preds, fits, cache = [], [], {}
    for m in months:
        fm = m if m <= FROZEN_MONTH else FROZEN_MONTH
        te = fm.end_time.normalize() if include_month else month_train_end(fm)
        if fm not in cache:
            cache[fm] = fit(d, h, te)
            f = cache[fm]
            fits.append({"month": str(fm), "train_last_day": f["train_last_day"].date(), "conv_days": f["conv_days"],
                         "conv_rows": f["conv_rows"], "m2_days": f.get("m2_days", 0),
                         **{f"conv_{k}": v for k, v in zip(CONV_X, f.get("conv", [np.nan] * 6))},
                         **{f"beta_{k}": v for k, v in zip(M2_X, f.get("beta", [np.nan] * 5))}})
        days = d.index[(d.index >= m.start_time) & (d.index <= m.end_time)]
        p = predict(days, d, h, cache[fm])
        p["fit_month"] = str(fm)
        preds.append(p)
    return pd.concat(preds).sort_index(), fits


# ------------------------------------------------------------------------------------------------------ evaluation
def top_flags(score: pd.Series, k: int) -> pd.Series:
    """True on the k lowest scores (ties: earlier day first)."""
    return score.rank(method="first") <= k


def month_flags(score: pd.Series, month: pd.Series) -> pd.Series:
    return score.groupby(month).rank(pct=True, method="first") <= LOW


def kk(n: int) -> int:
    return int(np.floor(LOW * n + 0.5))


def boot_recall_diff(flag_a: pd.Series, flag_b: pd.Series, actual: pd.Series, month: pd.Series) -> dict:
    """Recall of actual's days by flag_a and by flag_b, and a - b with a month-block 95% interval (flags fixed)."""
    f = pd.DataFrame({"a": flag_a.astype(bool), "b": flag_b.astype(bool), "y": actual.astype(bool), "m": month})
    arr = np.array([[(x.a & x.y).sum(), (x.b & x.y).sum(), x.y.sum()] for _, x in f.groupby("m")], float)
    tot = arr.sum(0)
    out = {"n_actual": int(tot[2]), "hits_a": int(tot[0]), "hits_b": int(tot[1]),
           "recall_a": tot[0] / tot[2] if tot[2] else np.nan, "recall_b": tot[1] / tot[2] if tot[2] else np.nan,
           "months": len(arr)}
    out["diff"] = out["recall_a"] - out["recall_b"]
    rng = np.random.default_rng(SEED)
    bs, skipped = [], 0
    for _ in range(N_BOOT):
        s = arr[rng.integers(0, len(arr), len(arr))].sum(0)
        if s[2] == 0:
            skipped += 1
            continue
        bs.append((s[0] - s[1]) / s[2])
    out["lo"], out["hi"] = (np.percentile(bs, [2.5, 97.5]) if bs else (np.nan, np.nan))
    out["skipped"] = skipped
    return out


def boot_diff(spike: pd.Series, low: pd.Series, month: pd.Series) -> dict:
    """Spike-day share on low days minus on the other days, month-block 95% interval (step 1's boot_diff)."""
    f = pd.DataFrame({"s": spike.astype(bool), "l": low.astype(bool), "m": month})
    a = np.array([[(x.l & x.s).sum(), x.l.sum(), (~x.l & x.s).sum(), (~x.l).sum()] for _, x in f.groupby("m")], float)

    def stat(s):
        return s[0] / s[1] - s[2] / s[3]

    tot = a.sum(0)
    out = {"n_low": int(tot[1]), "n_rest": int(tot[3]), "rate_low": tot[0] / tot[1] if tot[1] else np.nan,
           "rate_rest": tot[2] / tot[3] if tot[3] else np.nan, "diff": stat(tot) if tot[1] and tot[3] else np.nan}
    rng = np.random.default_rng(SEED)
    bs = [stat(s) for s in (a[rng.integers(0, len(a), len(a))].sum(0) for _ in range(N_BOOT)) if s[1] > 0 and s[3] > 0]
    out["lo"], out["hi"] = (np.percentile(bs, [2.5, 97.5]) if bs else (np.nan, np.nan))
    return out


def add_ranks(x: pd.DataFrame, k: int) -> pd.DataFrame:
    x = x.copy()
    n = len(x)
    for key, col in SCORE.items():
        r = x[col].rank(method="first")
        x[f"rank_{key}"] = r
        x[f"pct_{key}"] = r / n
        x[f"top_{key}"] = r <= k
        x[f"mtop_{key}"] = month_flags(x[col], x["month"])
    return x


def evaluate_period(x: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """x: the period's days with a label and all three predictions."""
    n, k = len(x), kk(len(x))
    x = add_ranks(x, k)
    res = {"n": n, "k": k}
    for m in ("M1", "M2"):
        res[f"main_{m}"] = boot_recall_diff(x[f"top_{m}"], x["top_B0"], x["top_actual"], x["month"])
        res[f"month_{m}"] = boot_recall_diff(x[f"mtop_{m}"], x["mtop_B0"], x["mtop_actual"], x["month"])
    for m in METHODS:
        res[f"recall_{m}"] = float((x[f"top_{m}"] & x["top_actual"]).sum() / k) if k else np.nan
        res[f"month_recall_{m}"] = float((x[f"mtop_{m}"] & x["mtop_actual"]).sum() / x["mtop_actual"].sum())
        res[f"spearman_{m}"] = float(x[SCORE[m]].corr(x["margin_min_1621_mw"], method="spearman"))
        res[f"spike_{m}"] = boot_diff(x["spike"], x[f"top_{m}"], x["month"])
    res["spike_actual"] = boot_diff(x["spike"], x["top_actual"], x["month"])
    for m in ("M1", "M2"):
        e = x[SCORE[m]] - x["margin_min_1621_mw"]
        res[f"mae_{m}"] = float(e.abs().mean())
        res[f"bias_{m}"] = float(e.mean())
    return x, res


def main() -> None:
    OUT.mkdir(exist_ok=True)
    d, h = load()
    p, fits = walk_forward(d, h)
    x = d[["period", "margin_min_1621_mw", "margin_min_time", "spike", "rtp_max", "lrc", "ircr_peak",
           "high_risk_d1", "pd_req_max_1621_mw"]].join(p)
    x["b0_score"] = -x["pd_req_max_1621_mw"]
    x["month"] = x.index.to_period("M").astype(str)
    pd.DataFrame(fits).to_csv(OUT / "fits.csv", index=False)

    summary, ranked = {}, []
    for period in PERIOD_NAMES:
        xp = x[x["period"] == period]
        have = xp.dropna(subset=["margin_min_1621_mw", "b0_score", "m1_mw", "m2_mw"])
        summary[period] = {"days": len(xp), "label": int(xp["margin_min_1621_mw"].notna().sum()),
                           "b0": int(xp["b0_score"].notna().sum()), "m1": int(xp["m1_mw"].notna().sum()),
                           "m2": int(xp["m2_mw"].notna().sum()), "evaluated": len(have)}
        if len(have) < 10:
            summary[period]["note"] = "无法评估：有标签且三个方法都有预测的天少于 10 天"
            continue
        xr, res = evaluate_period(have)
        summary[period].update(res)
        ranked.append(xr.assign(eval_period=period))
    allr = pd.concat(ranked)
    cols = ["period", "month", "fit_month", "margin_min_1621_mw", "margin_min_time", "b0_score", "pd_req_max_1621_mw",
            "m1_mw", "t_star", "m2_mw", "out_t", "req_t", "wind_t", "solar_t", "res_t", "thermal", "bat", "const",
            "spike", "rtp_max", "lrc", "ircr_peak", "high_risk_d1"] + \
           [c for c in allr.columns if c.startswith(("rank_", "pct_", "top_", "mtop_"))]
    out = x.join(allr[[c for c in allr.columns if c.startswith(("rank_", "pct_", "top_", "mtop_"))]])
    out[[c for c in cols if c in out.columns]].to_csv(OUT / "daily_predictions.csv")

    # ---- main criterion (in-sample)
    s = summary["in_sample"]
    rows = []
    for m in ("M1", "M2"):
        r = s[f"main_{m}"]
        rows.append({"period": PERIOD_NAMES["in_sample"], "n": s["n"], "k": s["k"], "method": m,
                     "recall_method": r["recall_a"], "recall_B0": r["recall_b"], "hits_method": r["hits_a"],
                     "hits_B0": r["hits_b"], "diff": r["diff"], "lo": r["lo"], "hi": r["hi"],
                     "skipped_resamples": r["skipped"], "months": r["months"],
                     "verdict": "通过" if r["lo"] > 0 else "不通过"})
    pd.DataFrame(rows).to_csv(OUT / "main_result.csv", index=False)

    # ---- secondary (in-sample) and the same numbers for the other periods
    sec = []
    for period, sp in summary.items():
        if "n" not in sp:
            continue
        for m in METHODS:
            row = {"period": period, "n": sp["n"], "k": sp["k"], "method": m, "recall_top10": sp[f"recall_{m}"],
                   "recall_month10": sp[f"month_recall_{m}"], "spearman": sp[f"spearman_{m}"],
                   "spike_top": sp[f"spike_{m}"]["rate_low"], "spike_rest": sp[f"spike_{m}"]["rate_rest"],
                   "spike_diff": sp[f"spike_{m}"]["diff"], "spike_lo": sp[f"spike_{m}"]["lo"],
                   "spike_hi": sp[f"spike_{m}"]["hi"]}
            if m != "B0":
                row.update({"mae": sp[f"mae_{m}"], "bias": sp[f"bias_{m}"],
                            "month_diff_vs_B0": sp[f"month_{m}"]["diff"], "month_lo": sp[f"month_{m}"]["lo"],
                            "month_hi": sp[f"month_{m}"]["hi"], "main_diff_vs_B0": sp[f"main_{m}"]["diff"],
                            "main_lo": sp[f"main_{m}"]["lo"], "main_hi": sp[f"main_{m}"]["hi"]})
            sec.append(row)
        sa = sp["spike_actual"]
        sec.append({"period": period, "n": sp["n"], "k": sp["k"], "method": "actual", "spike_top": sa["rate_low"],
                    "spike_rest": sa["rate_rest"], "spike_diff": sa["diff"], "spike_lo": sa["lo"], "spike_hi": sa["hi"]})
    pd.DataFrame(sec).to_csv(OUT / "secondary.csv", index=False)

    # ---- event days, high-risk comparison, not-hot subset (in-sample)
    xi = allr[allr["eval_period"] == "in_sample"]
    ev = xi[xi["lrc"] | xi["ircr_peak"]]
    evcols = ["lrc", "ircr_peak", "margin_min_1621_mw", "m1_mw", "m2_mw", "pd_req_max_1621_mw", "spike", "rtp_max",
              "high_risk_d1"] + [f"{a}_{m}" for m in ("actual",) + METHODS for a in ("pct", "top", "mtop")]
    ev[evcols].to_csv(OUT / "events_in_sample.csv")

    hr = xi["high_risk_d1"] == 1
    nh = int(hr.sum())
    hrows = [{"method": "high_risk_d1", "days_flagged": nh, "hits": int((hr & xi["top_actual"]).sum()),
              "recall": float((hr & xi["top_actual"]).sum() / xi["top_actual"].sum())}]
    for m in METHODS:
        fl = top_flags(xi[SCORE[m]], nh)
        r = boot_recall_diff(fl, hr, xi["top_actual"], xi["month"])
        hrows.append({"method": m, "days_flagged": nh, "hits": r["hits_a"], "recall": r["recall_a"],
                      "diff_vs_high_risk": r["diff"], "lo": r["lo"], "hi": r["hi"]})
    pd.DataFrame(hrows).to_csv(OUT / "vs_high_risk.csv", index=False)
    summary["in_sample"]["high_risk_days"] = nh
    summary["in_sample"]["high_risk_known_days"] = int(xi["high_risk_d1"].notna().sum())

    nothot = xi["top_actual"] & ~xi["top_B0"]
    summary["in_sample"]["not_hot_tight_days"] = int(nothot.sum())
    summary["in_sample"]["not_hot_hits"] = {m: int((nothot & xi[f"top_{m}"]).sum()) for m in METHODS}
    summary["in_sample"]["not_hot_days_list"] = [str(t.date()) for t in xi.index[nothot]]

    # ---- history diagnostic: the 2024 winter outage + low wind LRC days
    lrc = pd.read_csv(INP / "lrc_days.csv", parse_dates=["day"])
    reason = lrc["reason"].fillna("").str.lower()
    sel = lrc[(lrc["day"] >= "2024-06-01") & (lrc["day"] <= "2024-08-31") & lrc["lrc"]
              & reason.str.contains("outage") & (reason.str.contains("wind") | reason.str.contains("semi-scheduled"))]
    xh = allr[allr["eval_period"] == "history"]
    hd = pd.DataFrame(index=pd.DatetimeIndex(sel["day"], name="day"))
    hd["status"] = sel.set_index("day")["status"]
    hd = hd.join(x[["margin_min_1621_mw", "m1_mw", "m2_mw", "pd_req_max_1621_mw", "t_star", "out_t", "res_t",
                    "wind_t", "solar_t", "spike", "rtp_max", "high_risk_d1", "fit_month"]])
    hd = hd.join(xh[[f"{a}_{m}" for m in ("actual",) + METHODS for a in ("pct", "top", "mtop")]])
    hd["in_history_eval"] = hd.index.isin(xh.index)
    hd.to_csv(OUT / "history_lrc_outage_lowwind.csv")
    summary["history"]["outage_lowwind_days"] = len(hd)
    if "n" in summary["history"]:
        summary["history"]["outage_lowwind_in_top10"] = {m: int(hd[f"top_{m}"].fillna(False).astype(bool).sum())
                                                         for m in ("actual",) + METHODS}
        summary["history"]["outage_lowwind_in_month_top10"] = {
            m: int(hd[f"mtop_{m}"].fillna(False).astype(bool).sum()) for m in ("actual",) + METHODS}
        summary["history"]["first_day_evaluated"] = str(xh.index.min().date())

    # ---- test-period LRC days
    xt = allr[allr["eval_period"].isin(["test_seen", "test_unseen"])]
    tl = x[(x["period"].isin(["test_seen", "test_unseen"])) & x["lrc"]][["period", "margin_min_1621_mw", "m1_mw",
                                                                         "m2_mw", "pd_req_max_1621_mw", "spike",
                                                                         "rtp_max", "high_risk_d1"]]
    tl = tl.join(xt[[f"{a}_{m}" for m in ("actual",) + METHODS for a in ("pct", "top")]])
    tl.to_csv(OUT / "events_test.csv")

    with open(OUT / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(pd.DataFrame(fits).round(3).to_string(index=False))
    print(pd.read_csv(OUT / "main_result.csv").round(3).to_string(index=False))
    print(pd.read_csv(OUT / "secondary.csv").round(3).to_string(index=False))
    print(pd.read_csv(OUT / "events_in_sample.csv").round(3).to_string(index=False))
    print(pd.read_csv(OUT / "vs_high_risk.csv").round(3).to_string(index=False))
    print(pd.read_csv(OUT / "history_lrc_outage_lowwind.csv").round(3).to_string(index=False))
    print(pd.read_csv(OUT / "events_test.csv").round(3).to_string(index=False))
    print(json.dumps({k: {kk_: v for kk_, v in s_.items() if not isinstance(v, dict)} for k, s_ in summary.items()},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
