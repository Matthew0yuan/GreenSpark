"""Step 1 (PLAN.md): an ex-post reserve margin per trading day, and whether its low days are the spike, LRC and
IRCR peak days, beyond what high residual demand already says.

margin(t) = thermal Capacity Credits - coal/gas outage loss + battery capacity seen in the last 90 trading days
            - (unscheduled operational demand - wind - utility solar), MW, per 30-min trading interval
M_D       = the day's lowest margin; R_D = the day's highest residual demand.

Run from the repo root:  .venv/Scripts/python.exe analysis/2026-10-26/supply_tightness/tightness_label.py
Writes out/daily.csv, out/q_results.csv, out/event_days.csv, out/low_days.csv, out/summary.json.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (ROOT, ROOT / "analysis" / "2026-10-17"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from common import fuel_of  # noqa: E402  (analysis/2026-10-17)
from vpp import site_bill as sb  # noqa: E402
from vpp.data import PROCESSED, RAW, trading_date  # noqa: E402
from vpp.spike.outages import capacity_credits, capacity_year  # noqa: E402
from vpp.v2 import risk as vr  # noqa: E402

OUT17 = ROOT / "analysis" / "2026-10-17" / "out"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

PERIODS = {"in_sample": (pd.Timestamp("2025-04-01"), pd.Timestamp("2026-06-30")),   # judged
           "history": (pd.Timestamp("2024-01-01"), pd.Timestamp("2025-03-31"))}     # diagnostic only
LOW = 0.10            # bottom share of days by margin / top share by residual demand [PLAN]
BAT_DAYS = 90         # battery capacity look-back, trading days before the day [PLAN]
MIN_VALID = 40        # a day needs this many of its 48 margins
N_BOOT, SEED = 2000, 20261026
MIN_GROUP = 10        # Q2: fewer low-margin days than this in the subset -> cannot judge
SPIKE = 200.0         # $/MWh, 30-min RTP [project definition]
THERMAL = ("coal", "gas_base", "peaker", "other")


def half_hours() -> pd.DataFrame:
    w = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["energy", "unscheduled_demand_mw", "demand_mw"])
    s5 = pd.read_parquet(OUT17 / "scada_5min.parquet", columns=["wind_mw", "solar_mw", "battery_dis_mw"])
    s30 = s5.resample("30min").mean()
    o = pd.read_parquet(OUT17 / "outages_30min.parquet", columns=["out_coal_mw", "out_gas_mw"])
    df = w.join(s30, how="inner").join(o, how="inner")

    cc = capacity_credits()
    fuel = pd.Series([fuel_of(f) for f in cc.index], index=cc.index)
    thermal = cc[fuel.isin(THERMAL)].sum()
    df["thermal_cap_mw"] = thermal.reindex(capacity_year(df.index)).to_numpy()
    df["outage_mw"] = df["out_coal_mw"] + df["out_gas_mw"]
    df["residual_mw"] = df["unscheduled_demand_mw"] - df["wind_mw"] - df["solar_mw"]
    df["day"] = trading_date(df.index)
    day_bat = df.groupby("day")["battery_dis_mw"].max()
    bat = day_bat.shift(1).rolling(BAT_DAYS, min_periods=BAT_DAYS).max()
    df["battery_cap_mw"] = df["day"].map(bat)
    df["margin_mw"] = df["thermal_cap_mw"] - df["outage_mw"] + df["battery_cap_mw"] - df["residual_mw"]
    return df


def lrc_advisories() -> pd.DataFrame:
    a = pd.read_csv(RAW / "market-advisories" / "market-advisories.csv", encoding="cp1252")
    a = a[a["Details"].fillna("").str.lower().str.contains("low reserve condition")].copy()
    issued = pd.to_datetime(a["Issued At"])
    start = pd.to_datetime(a["Start Interval"], errors="coerce")
    typo = start.isna() | ((start - issued).abs() > pd.Timedelta(days=3))
    a["day"] = trading_date(pd.DatetimeIndex(start.where(~typo, issued)))
    a["issued"] = issued
    a["start_typo"] = typo
    return a[["ID", "issued", "Start Interval", "day", "start_typo", "Withdrawn"]]


def daily(df: pd.DataFrame) -> pd.DataFrame:
    ok = df.dropna(subset=["margin_mw"])
    g = ok.groupby("day")
    at = ok.loc[g["margin_mw"].idxmin()].set_index("day")
    d = pd.DataFrame({"margin_min_mw": at["margin_mw"], "min_time": [t.strftime("%H:%M") for t in g["margin_mw"].idxmin()],
                      "residual_at_min_mw": at["residual_mw"], "outage_at_min_mw": at["outage_mw"],
                      "battery_cap_mw": at["battery_cap_mw"], "thermal_cap_mw": at["thermal_cap_mw"],
                      "n_valid": g["margin_mw"].count()})
    full = df.groupby("day")
    d["residual_max_mw"] = full["residual_mw"].max()
    d["rtp_max"] = full["energy"].max()
    d["spike"] = d["rtp_max"] >= SPIKE
    cr = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=["cont_raise"])["cont_raise"]
    d["cont_raise_max"] = cr.groupby(trading_date(cr.index)).max()
    d = d[d["n_valid"] >= MIN_VALID]

    lrc = lrc_advisories()
    first = lrc.sort_values("issued").groupby("day")["issued"].first()
    d["lrc"] = d.index.isin(first.index)
    d["lrc_first_issued"] = first.reindex(d.index)
    peaks = set()
    for y in (2023, 2024, 2025):
        peaks |= set(trading_date(sb.ircr_intervals(df["demand_mw"], y)))
    d["ircr_peak"] = d.index.isin(peaks)
    risk = vr.day_risk(vr.history(days_ahead=1))
    d["high_risk_d1"] = vr.recent_flags(risk).reindex(d.index)
    d["month"] = d.index.to_period("M")
    return d


def flags(d: pd.DataFrame, how: str) -> tuple[pd.Series, pd.Series]:
    """Low-margin days and high-residual-demand days of the frame, over the whole frame or within each month."""
    if how == "full":
        return (d["margin_min_mw"] <= d["margin_min_mw"].quantile(LOW),
                d["residual_max_mw"] >= d["residual_max_mw"].quantile(1 - LOW))
    m = d.groupby("month")
    return (m["margin_min_mw"].rank(pct=True, method="first") <= LOW,
            m["residual_max_mw"].rank(ascending=False, pct=True, method="first") <= LOW)


def boot_diff(spike: pd.Series, low: pd.Series, month: pd.Series) -> dict:
    """Spike-day share on low days minus on the other days, with a month-block bootstrap 95% interval."""
    f = pd.DataFrame({"s": spike.astype(bool), "l": low.astype(bool), "m": month})
    a = np.array([[(x.l & x.s).sum(), x.l.sum(), (~x.l & x.s).sum(), (~x.l).sum()] for _, x in f.groupby("m")], float)

    def stat(s):
        return s[0] / s[1] - s[2] / s[3]

    tot = a.sum(0)
    out = {"n_low": int(tot[1]), "n_rest": int(tot[3]), "rate_low": tot[0] / tot[1] if tot[1] else np.nan,
           "rate_rest": tot[2] / tot[3] if tot[3] else np.nan, "diff": stat(tot) if tot[1] and tot[3] else np.nan,
           "months": len(a)}
    rng = np.random.default_rng(SEED)
    bs = [stat(s) for s in (a[rng.integers(0, len(a), len(a))].sum(0) for _ in range(N_BOOT)) if s[1] > 0 and s[3] > 0]
    out["lo"], out["hi"] = (np.percentile(bs, [2.5, 97.5]) if bs else (np.nan, np.nan))
    return out


def judge(r: dict, min_n: int = 0) -> str:
    if r["n_low"] < min_n:
        return "cannot judge"
    return "pass" if r["lo"] > 0 else "fail"


def main() -> None:
    df = half_hours()
    d = daily(df)
    d.to_csv(OUT / "daily.csv")
    rows, summary = [], {}
    for period, (a, b) in PERIODS.items():
        x = d[(d.index >= a) & (d.index <= b)].copy()
        summary[period] = {"days": len(x), "spike_days": int(x["spike"].sum()), "lrc_days": int(x["lrc"].sum()),
                           "ircr_peak_days": int(x["ircr_peak"].sum()),
                           "margin_q": x["margin_min_mw"].quantile([0, 0.1, 0.5, 0.9, 1]).round(0).tolist(),
                           "rho_margin_residual": x["margin_min_mw"].corr(x["residual_max_mw"], method="spearman"),
                           "rho_margin_cont_raise": x["margin_min_mw"].corr(x["cont_raise_max"], method="spearman"),
                           "min_time_share_16_21": float(x["min_time"].between("16:00", "20:30").mean())}
        x["pct"] = x["margin_min_mw"].rank(pct=True)
        x["res_pct"] = x["residual_max_mw"].rank(pct=True, ascending=False)
        d.loc[x.index, f"pct_{period}"] = x["pct"]
        for how in ("full", "month"):
            low, high_r = flags(x, how)
            q1 = boot_diff(x["spike"], low, x["month"])
            sub = ~high_r
            q2 = boot_diff(x.loc[sub, "spike"], low[sub], x.loc[sub, "month"])
            rows.append({"period": period, "threshold": how, "question": "Q1", **q1, "verdict": judge(q1),
                         "overlap_low_highR": int((low & high_r).sum()), "n_highR": int(high_r.sum())})
            rows.append({"period": period, "threshold": how, "question": "Q2", **q2, "verdict": judge(q2, MIN_GROUP),
                         "overlap_low_highR": int((low & high_r).sum()), "n_highR": int(high_r.sum())})
            x[f"low_{how}"], x[f"highR_{how}"] = low, high_r
        ev = x[x["lrc"] | x["ircr_peak"]]
        ev = ev.assign(period=period)[["period", "lrc", "ircr_peak", "lrc_first_issued", "margin_min_mw", "min_time", "pct",
                                       "residual_max_mw", "res_pct", "outage_at_min_mw", "battery_cap_mw", "spike",
                                       "rtp_max", "high_risk_d1", "low_full", "low_month"]]
        ev.to_csv(OUT / "event_days.csv", mode="w" if period == "in_sample" else "a", header=period == "in_sample")
        if period == "in_sample":
            med = x[["residual_at_min_mw", "outage_at_min_mw", "battery_cap_mw", "thermal_cap_mw"]].median()
            lo = x[x["low_full"]].copy()
            lo["d_residual"] = -(lo["residual_at_min_mw"] - med["residual_at_min_mw"])
            lo["d_outage"] = -(lo["outage_at_min_mw"] - med["outage_at_min_mw"])
            lo["d_battery"] = lo["battery_cap_mw"] - med["battery_cap_mw"]
            lo["d_thermal"] = lo["thermal_cap_mw"] - med["thermal_cap_mw"]
            lo["main_cause"] = lo[["d_residual", "d_outage", "d_battery", "d_thermal"]].idxmin(axis=1)
            lo.sort_values("margin_min_mw")[["margin_min_mw", "min_time", "residual_at_min_mw", "outage_at_min_mw",
                                             "battery_cap_mw", "d_residual", "d_outage", "d_battery", "d_thermal",
                                             "main_cause", "spike", "rtp_max", "lrc", "ircr_peak", "high_risk_d1",
                                             "highR_full"]].to_csv(OUT / "low_days.csv")
            summary[period]["low_day_main_cause"] = lo["main_cause"].value_counts().to_dict()
            summary[period]["median_components_at_min"] = med.round(0).to_dict()
            hr = x["high_risk_d1"]
            summary[period]["high_risk_d1_flagged_on_low_full"] = f'{int((hr[x["low_full"]] == 1).sum())}/{int(x["low_full"].sum())}'
        if period == "history":
            lr = x[x["lrc"]]
            summary[period]["lrc_pct_median"] = float(lr["pct"].median()) if len(lr) else None
            summary[period]["lrc_in_bottom10_full"] = f'{int(lr["low_full"].sum())}/{len(lr)}'
            summary[period]["lrc_in_bottom10_month"] = f'{int(lr["low_month"].sum())}/{len(lr)}'
            summary[period]["lrc_in_bottom25"] = f'{int((lr["pct"] <= 0.25).sum())}/{len(lr)}'
            summary[period]["lrc_spike_share"] = float(lr["spike"].mean()) if len(lr) else None
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "q_results.csv", index=False)
    with open(OUT / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=str)

    pd.set_option("display.width", 220)
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    print(res.round(3).to_string(index=False))
    print(pd.read_csv(OUT / "event_days.csv").round(2).to_string(index=False))


if __name__ == "__main__":
    main()
