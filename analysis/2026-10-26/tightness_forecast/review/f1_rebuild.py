"""R-A review F1-F3, F5: rebuild B0 / M1 / M2 and the main criterion from the PLAN text, without importing forecast.py.
Compares with out/daily_predictions.csv, out/fits.csv, out/main_result.csv, out/summary.json (read only).
Writes review/f1_rebuild.csv.  Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
"""
import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path("/Users/matthew.yuan/Desktop/HGS")
TASK = ROOT / "analysis" / "2026-10-26" / "tightness_forecast"
INP = ROOT / "analysis" / "2026-10-26" / "tightness_inputs" / "out"
TRAIN_END = pd.Timestamp("2026-06-30")
FROZEN = pd.Period("2026-07", "M")


def conv_X(h):
    v1, v2, g = h["wx_wind_midwest_kmh"], h["wx_wind_merredin_kmh"], h["wx_ghi_perth_wm2"]
    return np.c_[np.ones(len(h)), v1, v1 ** 2, v2, v2 ** 2, g]


def m1_slots(h, b):
    X = conv_X(h)
    wind = X[:, 1:5] @ b[1:5]
    solar = X[:, 5] * b[5]
    res = h["pd_req_mw"].to_numpy() - b[0] - wind - solar
    m1 = h["thermal_cap_mw"].to_numpy() - h["out_thermal_mw"].to_numpy() + h["bat_cap_mw"].to_numpy() - res
    return pd.DataFrame({"day": h["day"].to_numpy(), "slot": h["slot"].to_numpy(), "m1": m1, "wind": wind, "solar": solar,
                         "out": h["out_thermal_mw"].to_numpy(), "req": h["pd_req_mw"].to_numpy(),
                         "thermal": h["thermal_cap_mw"].to_numpy(), "bat": h["bat_cap_mw"].to_numpy()})


def m1_days(s):
    rows = {}
    for day, g in s.groupby("day"):
        if g["m1"].notna().sum() < 10:
            continue
        i = g["m1"].to_numpy().argmin()          # first minimum in time order
        rows[day] = g.iloc[i]
    return pd.DataFrame(rows).T


def main() -> None:
    d = pd.read_parquet(INP / "inputs_d1.parquet").set_index("day").sort_index()
    h = pd.read_parquet(INP / "inputs_d1_30min.parquet").sort_values(["day", "target_time"]).reset_index(drop=True)
    lab = d["margin_min_1621_mw"]
    months = pd.period_range(d.index.min(), d.index.max(), freq="M")
    preds, fits = [], []
    for m in months:
        fm = min(m, FROZEN)
        last = min(fm.start_time - pd.Timedelta(days=5), TRAIN_END)
        tr = h[h["day"] <= last]
        X = conv_X(tr)
        y = (tr["pd_req_mw"] - tr["lab_residual_mw"]).to_numpy()
        ok = np.isfinite(X).all(1) & np.isfinite(y)
        ndays = tr.loc[ok, "day"].nunique()
        fits.append({"month": str(m), "fit_month": str(fm), "last": last.date(), "conv_days": ndays})
        if ndays < 30:
            continue
        b = np.linalg.lstsq(X[ok], y[ok], rcond=None)[0]
        td = m1_days(m1_slots(tr, b))
        td = td[td.index.isin(lab.dropna().index)]
        td = td.dropna(subset=["out", "req", "wind", "solar", "thermal", "bat"])
        fits[-1]["m2_days"] = len(td)
        beta = None
        if len(td) >= 30:
            A = np.c_[np.ones(len(td)), td[["out", "req", "wind", "solar"]].to_numpy(float)]
            yy = (lab.reindex(td.index) - td["thermal"].astype(float) - td["bat"].astype(float)).to_numpy()
            beta = np.linalg.lstsq(A, yy, rcond=None)[0]
            fits[-1].update({f"beta{i}": v for i, v in enumerate(beta)})
        hm = h[(h["day"] >= m.start_time) & (h["day"] <= m.end_time)]
        pdays = m1_days(m1_slots(hm, b))
        if len(pdays):
            pdays["m2"] = (pdays["thermal"].astype(float) + pdays["bat"].astype(float)
                           + np.c_[np.ones(len(pdays)), pdays[["out", "req", "wind", "solar"]].to_numpy(float)] @ beta) if beta is not None else np.nan
            preds.append(pdays[["m1", "m2", "slot"]])
    p = pd.concat(preds)
    p.index = pd.DatetimeIndex(p.index)
    rows = []
    theirs = pd.read_csv(TASK / "out" / "daily_predictions.csv", parse_dates=["day"], index_col="day")
    j = theirs.join(p, how="outer")
    for a, b_ in (("m1_mw", "m1"), ("m2_mw", "m2")):
        both = j[a].notna() & j[b_].notna()
        rows.append({"item": f"F1 {a} vs mine", "value": f"{int(both.sum())} days, max |diff| {float((j.loc[both, a] - j.loc[both, b_].astype(float)).abs().max()):.2e} MW, "
                                                      f"present only one side {int((j[a].notna() != j[b_].notna()).sum())}"})
    rows.append({"item": "F1 t_star vs mine", "value": f"mismatches {int((j['t_star'].notna() & (j['t_star'] != j['slot'])).sum())}"})

    # F3: fits
    f = pd.DataFrame(fits)
    tf = pd.read_csv(TASK / "out" / "fits.csv")
    viol = 0
    for _, r in tf.iterrows():
        mm = pd.Period(r["month"], "M")
        viol += pd.Timestamp(r["train_last_day"]) > min(mm.start_time - pd.Timedelta(days=5), TRAIN_END)
    rows.append({"item": "F3 fits.csv months with train_last_day > month start - 5 or > TRAIN_END", "value": int(viol)})
    rows.append({"item": "F3 fits.csv rows (months fitted)", "value": f"{len(tf)}; last fit month {tf['month'].iloc[-1]}; test months reuse it: "
                 f"{sorted(set(theirs.loc[theirs['period'].str.startswith('test'), 'fit_month'].dropna()))}"})
    mine_days = f.set_index("month")["conv_days"]
    rows.append({"item": "F3 conv_days per fit month equal", "value": bool((tf.set_index("month")["conv_days"] == mine_days.reindex(tf["month"]).to_numpy()).all())})

    # F2: main criterion, in-sample
    x = d[["period", "margin_min_1621_mw", "pd_req_max_1621_mw"]].join(p[["m1", "m2"]].astype(float))
    xi = x[(x["period"] == "in_sample")].dropna()
    n = len(xi)
    k = int(np.floor(0.1 * n + 0.5))

    def top(s):
        order = sorted(range(len(s)), key=lambda i: (s.iloc[i], i))
        f_ = np.zeros(len(s), bool)
        f_[order[:k]] = True
        return pd.Series(f_, index=s.index)
    act = top(xi["margin_min_1621_mw"])
    b0 = top(-xi["pd_req_max_1621_mw"])
    m1 = top(xi["m1"])
    m2 = top(xi["m2"])
    month = xi.index.to_period("M")
    res = {"n": n, "k": k, "hits_B0": int((b0 & act).sum()), "hits_M1": int((m1 & act).sum()), "hits_M2": int((m2 & act).sum())}
    for name, fl in (("M1", m1), ("M2", m2)):
        arr = np.array([[(fl[month == mm] & act[month == mm]).sum(), (b0[month == mm] & act[month == mm]).sum(), act[month == mm].sum()]
                        for mm in sorted(set(month))], float)
        for seed in (20261026, 1, 2):
            rng = np.random.default_rng(seed)
            bs = []
            for _ in range(2000):
                s = arr[rng.integers(0, len(arr), len(arr))].sum(0)
                if s[2]:
                    bs.append((s[0] - s[1]) / s[2])
            lo, hi = np.percentile(bs, [2.5, 97.5])
            res[f"{name}_seed{seed}"] = f"diff {(arr[:, 0].sum() - arr[:, 1].sum()) / arr[:, 2].sum():+.4f}, 95% [{lo:+.4f}, {hi:+.4f}], share<=0 {np.mean(np.array(bs) <= 0):.3f}"
    for kk_, v in res.items():
        rows.append({"item": f"F2 {kk_}", "value": v})
    mr = pd.read_csv(TASK / "out" / "main_result.csv")
    rows.append({"item": "F2 delivered main_result", "value": mr[["method", "hits_method", "hits_B0", "diff", "lo", "hi", "verdict"]].to_dict("records")})

    # F5: test periods
    for per in ("test_seen", "test_unseen"):
        xt = x[x["period"] == per].dropna()
        nt = len(xt)
        if nt < 10:
            rows.append({"item": f"F5 {per}", "value": f"{nt} days with label and all three predictions -> not evaluable"})
            continue
        kt = int(np.floor(0.1 * nt + 0.5))
        k = kt
        a = top(xt["margin_min_1621_mw"])
        rows.append({"item": f"F5 {per}", "value": f"n {nt}, k {kt}, hits B0 {int((top(-xt['pd_req_max_1621_mw']) & a).sum())}, "
                                                    f"M1 {int((top(xt['m1']) & a).sum())}, M2 {int((top(xt['m2']) & a).sum())}, "
                                                    f"MAE M1 {float((xt['m1'] - xt['margin_min_1621_mw']).abs().mean()):.1f}, M2 {float((xt['m2'] - xt['margin_min_1621_mw']).abs().mean()):.1f}"})
    out = pd.DataFrame(rows)
    out.to_csv(TASK / "review" / "f1_rebuild.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 400)
    print(out.to_string(index=False))
    print(json.dumps({k_: v for k_, v in json.load(open(TASK / "out" / "summary.json"))["test_seen"].items() if k_ in ("n", "k", "recall_B0", "recall_M1", "recall_M2")}))


if __name__ == "__main__":
    main()
