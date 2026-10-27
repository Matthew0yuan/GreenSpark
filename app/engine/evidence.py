"""Evidence for the storage network: how much the adopted dispatcher earns against simpler ways of running the same
battery, and whether any limit broke, by evaluation period (never mixed). Reads the saved backtests of
analysis/2026-10-27/standalone_v2 (run.py: v1 and e5; run_refs.py: the 09:00 plan alone and prices known ahead) and
the 09:00 price forecast; nothing is recomputed here (a rerun is a background job, app.backend.jobs)."""

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import dispatch as dp
from vpp.data import PROCESSED
from vpp.v2 import backtest as vb

from .common import PERIODS, STANDALONE, num, period, period_key

PLAN_ONLY, PERFECT = "forecast", "perfect"


def _ci(diff: pd.Series) -> tuple:
    """Month-block 95% interval of the mean daily difference (vpp.v2.backtest.month_ci); None with one month."""
    if diff.index.to_period("M").nunique() < 2:
        return None, None
    lo, hi = vb.month_ci(diff)
    return num(lo, 1), num(hi, 1)


def evidence() -> dict:
    adopted = (STANDALONE / "adopted.txt").read_text(encoding="utf-8").strip()
    other = vb.E5.name if adopted == bt.VALUE else bt.VALUE
    short = {bt.VALUE: "v1", vb.E5.name: "e5", PLAN_ONLY: "plan only", PERFECT: "prices known"}
    daily = pd.read_csv(STANDALONE / "daily.csv", parse_dates=["day"])
    gaps = []
    refs_path = STANDALONE / "refs_daily.csv"
    if refs_path.exists():
        daily = pd.concat([daily, pd.read_csv(refs_path, parse_dates=["day"])], ignore_index=True)
    else:
        gaps.append({"field": "plan_only, prices_known", "reason": "reference runs not found (run_refs.py)"})
    v = daily.pivot_table(index="day", columns="strategy", values="total")
    keys = pd.Series([period_key(d) for d in v.index], index=v.index)

    rows = []
    for k in PERIODS:
        g = v[keys == k]
        row = {"period": period(k, len(g)), "per_day_aud": {short[s]: num(g[s].mean(), 1) for s in g.columns}}
        if PLAN_ONLY in g:
            d = g[adopted] - g[PLAN_ONLY]
            lo, hi = _ci(d)
            row["uplift_vs_plan_only"] = {"mean": num(d.mean(), 1), "lo95": lo, "hi95": hi,
                                          "pct": num(100 * d.mean() / abs(g[PLAN_ONLY].mean()), 1) if abs(g[PLAN_ONLY].mean()) > 1 else None,
                                          "days_better": int((d > 0).sum())}
        d = g[other] - g[adopted]
        lo, hi = _ci(d)
        row["candidate_minus_adopted"] = {"mean": num(d.mean(), 1), "lo95": lo, "hi95": hi, "days_better": int((d > 0).sum())}
        if PERFECT in g:
            row["share_of_prices_known"] = num(g[adopted].mean() / g[PERFECT].mean(), 3)
        parts = daily[(daily["strategy"] == adopted) & daily["day"].isin(g.index)]
        row["adopted_parts_aud"] = {"energy": num(parts["energy"].mean(), 1), "fcess": num(parts["fcess"].mean(), 1),
                                    "wear": num(parts["wear"].mean(), 1)}
        rows.append(row)

    # price forecast (Q2A): mean absolute error of the 09:00 forecast against yesterday's prices, per period
    fc = pd.read_parquet(PROCESSED / "forecast_dispatch_0900.parquet")
    fc = fc[fc["energy"].notna() & fc["yday_energy"].notna() & fc["actual_energy"].notna()]
    err, yerr = (fc["energy"] - fc["actual_energy"]).abs(), (fc["yday_energy"] - fc["actual_energy"]).abs()
    td = pd.to_datetime(fc["target_date"])
    evening = pd.to_datetime(fc["ts"]).dt.hour.between(17, 20)
    fk = pd.Series([period_key(d) for d in td], index=fc.index)
    forecast = []
    for k in PERIODS:
        m = fk == k
        e = (m & evening)
        forecast.append({"period": period(k, td[m].nunique()),
                         "mae_all_day": num(err[m].mean(), 2), "mae_all_day_yesterday": num(yerr[m].mean(), 2),
                         "mae_17_21": num(err[e].mean(), 2), "mae_17_21_yesterday": num(yerr[e].mean(), 2),
                         "change_pct": num(100 * (err[m].mean() - yerr[m].mean()) / yerr[m].mean(), 1)})

    # limits: every half-hour of the adopted dispatcher's backtest log
    log = pd.read_parquet(STANDALONE / "log.parquet", columns=["day", "strategy", "energy_mwh", "soc"])
    log = log[log["strategy"] == adopted]
    P, E = bt.Q2B_ASSET.battery.power_mw, bt.Q2B_ASSET.battery.energy_mwh
    lk = pd.Series([period_key(d) for d in pd.to_datetime(log["day"])], index=log.index)
    limits = []
    for k in PERIODS:
        g = log[lk == k]
        limits.append({"period": period(k, g["day"].nunique()), "halfhours": int(len(g)),
                       "over_power": int((np.abs(g["energy_mwh"]) / dp.DT > P + 1e-6).sum()),
                       "soc_outside": int(((g["soc"] < -1e-6) | (g["soc"] > E + 1e-6)).sum())})
    return {"adopted": short[adopted], "adopted_name": adopted, "candidate": short[other], "candidate_name": other,
            "selection_rule": "e5 adopted only if its training-period uplift over v1 has a month-block 95% interval "
                              "above 0 (analysis/2026-10-27/standalone_v2/PLAN.md, written before the run)",
            "periods": rows, "forecast": forecast, "limits": limits, "gaps": gaps, "status": "backtest",
            "source": "analysis/2026-10-27/standalone_v2 daily.csv, refs_daily.csv, log.parquet; "
                      "data/processed/forecast_dispatch_0900.parquet"}
