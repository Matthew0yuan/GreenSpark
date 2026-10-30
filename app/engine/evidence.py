"""Evidence for the storage network, per connection and dispatcher: how much the dispatcher earns against simpler ways
of running the same battery and against the other dispatcher, and whether any limit broke, by evaluation period
(never mixed). Reads the saved backtests at the app's hurdle (common.SAVED_DIR: their run.py: v1, e5, the 09:00 plan
alone and prices known ahead) and the 09:00 price forecast; nothing is recomputed here (a rerun is a background job,
app.backend.jobs). Behind the factory meter the money of a day is its ledger plus the month's network bill saving per
day (vpp.stochastic.daily_money), and IRCR is reported per year, not per day."""

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import dispatch as dp
from vpp.data import PROCESSED
from vpp.v2 import backtest as vb

from .common import (DEFAULT_CONNECTION, DEFAULT_DISPATCHER, HURDLE, PERFECT, PLAN_ONLY, SAVED_DIR, SAVED_PERIODS,
                     STRATEGY, check_mode, num, other, period, period_key, saved_rel)


def _ci(diff: pd.Series) -> tuple:
    """Month-block 95% interval of the mean daily difference (vpp.v2.backtest.month_ci); None with one month."""
    if diff.index.to_period("M").nunique() < 2:
        return None, None
    lo, hi = vb.month_ci(diff)
    return num(lo, 1), num(hi, 1)


def saved_runs(connection: str) -> tuple[pd.DataFrame, pd.DataFrame, list]:
    """(the daily rows of every saved run, money per day by strategy, gaps) of one connection's saved folder."""
    folder = SAVED_DIR[connection]
    daily = pd.read_csv(folder / "daily.csv", parse_dates=["day"])
    gaps = []
    refs = folder / "refs_daily.csv"
    if refs.exists():
        daily = pd.concat([daily, pd.read_csv(refs, parse_dates=["day"])], ignore_index=True)
    else:
        gaps.append({"field": "plan_only, prices_known", "reason": "reference runs not found"})
    if connection == "factory":
        money = pd.read_csv(folder / "money.csv", index_col=0, parse_dates=True)
    else:
        money = daily.pivot_table(index="day", columns="strategy", values="total")
    return daily, money, gaps


def verdict(money: pd.DataFrame, connection: str) -> dict:
    """The selection rule on one connection's saved runs at the app's hurdle: e5 over v1 in the training period,
    month-block 95% interval above 0 (analysis/2026-10-27/standalone_v2/PLAN.md, reports/v2_plan.md)."""
    d = (money[STRATEGY[connection, "e5"]] - money[STRATEGY[connection, "v1"]]).dropna()
    d = d[[period_key(x) == "in_sample" for x in d.index]]
    lo, hi = vb.month_ci(d)
    return {"period": period("in_sample", len(d)), "e5_minus_v1_aud_per_day": num(d.mean(), 1), "lo95": num(lo, 1),
            "hi95": num(hi, 1), "passed": bool(lo > 0),
            "rule": "e5 passes if its training-period gain over v1 has a month-block 95% interval above 0",
            "money": "energy + FCESS − wear" + (" + the month's network bill saving per day" if connection == "factory" else ""),
            "source": f"{saved_rel(connection)} (hurdle {HURDLE:g} A$/MWh)"}


def evidence(connection: str = DEFAULT_CONNECTION, dispatcher: str = DEFAULT_DISPATCHER) -> dict:
    check_mode(connection, dispatcher)
    factory = connection == "factory"
    me, them = STRATEGY[connection, dispatcher], STRATEGY[connection, other(dispatcher)]
    short = {me: dispatcher, them: other(dispatcher), PLAN_ONLY: "plan only", PERFECT: "prices known"}
    daily, v, gaps = saved_runs(connection)
    v = v[[c for c in (me, them, PLAN_ONLY, PERFECT) if c in v.columns]]
    keys = pd.Series([period_key(d) for d in v.index], index=v.index)
    led = daily.pivot_table(index="day", columns="strategy", values="ledger_total") if factory else None

    rows = []
    for k in SAVED_PERIODS:
        g = v[keys == k]
        row = {"period": period(k, len(g)), "per_day_aud": {short[s]: num(g[s].mean(), 1) for s in g.columns}}
        if PLAN_ONLY in g:
            d = g[me] - g[PLAN_ONLY]
            lo, hi = _ci(d)
            row["uplift_vs_plan_only"] = {"mean": num(d.mean(), 1), "lo95": lo, "hi95": hi,
                                          "pct": num(100 * d.mean() / abs(g[PLAN_ONLY].mean()), 1) if abs(g[PLAN_ONLY].mean()) > 1 else None,
                                          "days_better": int((d > 0).sum())}
        d = g[them] - g[me]
        lo, hi = _ci(d)
        row["candidate_minus_adopted"] = {"mean": num(d.mean(), 1), "lo95": lo, "hi95": hi, "days_better": int((d > 0).sum())}
        if PERFECT in g:
            row["share_of_prices_known"] = num(g[me].mean() / g[PERFECT].mean(), 3)
        parts = daily[(daily["strategy"] == me) & daily["day"].isin(g.index)]
        row["adopted_parts_aud"] = {"energy": num(parts["energy"].mean(), 1), "fcess": num(parts["fcess"].mean(), 1),
                                    "wear": num(parts["wear"].mean(), 1)}
        if factory:
            row["adopted_parts_aud"]["network"] = num((g[me] - led[me].reindex(g.index)).mean(), 1)
        rows.append(row)

    # price forecast (Q2A): mean absolute error of the 09:00 forecast against yesterday's prices, per period
    fc = pd.read_parquet(PROCESSED / "forecast_dispatch_0900.parquet")
    fc = fc[fc["energy"].notna() & fc["yday_energy"].notna() & fc["actual_energy"].notna()]
    err, yerr = (fc["energy"] - fc["actual_energy"]).abs(), (fc["yday_energy"] - fc["actual_energy"]).abs()
    td = pd.to_datetime(fc["target_date"])
    evening = pd.to_datetime(fc["ts"]).dt.hour.between(17, 20)
    fk = pd.Series([period_key(d) for d in td], index=fc.index)
    forecast = []
    for k in SAVED_PERIODS:
        m = fk == k
        e = (m & evening)
        forecast.append({"period": period(k, td[m].nunique()),
                         "mae_all_day": num(err[m].mean(), 2), "mae_all_day_yesterday": num(yerr[m].mean(), 2),
                         "mae_17_21": num(err[e].mean(), 2), "mae_17_21_yesterday": num(yerr[e].mean(), 2),
                         "change_pct": num(100 * (err[m].mean() - yerr[m].mean()) / yerr[m].mean(), 1)})

    # limits: every half-hour of this dispatcher's backtest log (behind the meter also the meter over its CMD cap,
    # an economic limit: the dispatcher may exceed it at the month's excess charge)
    cols = ["day", "strategy", "energy_mwh", "soc"] + (["meter_mw", "cap_mw"] if factory else [])
    log = pd.read_parquet(SAVED_DIR[connection] / "log.parquet", columns=cols)
    log = log[log["strategy"] == me]
    P, E = bt.Q2B_ASSET.battery.power_mw, bt.Q2B_ASSET.battery.energy_mwh
    lk = pd.Series([period_key(d) for d in pd.to_datetime(log["day"])], index=log.index)
    limits = []
    for k in SAVED_PERIODS:
        g = log[lk == k]
        row = {"period": period(k, g["day"].nunique()), "halfhours": int(len(g)),
               "over_power": int((np.abs(g["energy_mwh"]) / dp.DT > P + 1e-6).sum()),
               "soc_outside": int(((g["soc"] < -1e-6) | (g["soc"] > E + 1e-6)).sum())}
        if factory:
            row["over_cmd"] = int((g["meter_mw"] > g["cap_mw"] + 1e-6).sum())
        limits.append(row)

    site = None
    if factory:
        ircr = pd.read_csv(SAVED_DIR[connection] / "ircr.csv").set_index("strategy")
        site = {"ircr_saving_aud_per_year": {short[s]: num(ircr.loc[s, "saving_per_year"], 0) for s in (me, them)
                                              if s in ircr.index},
                "ircr_intervals_covered": {short[s]: int(ircr.loc[s, "intervals_covered"]) for s in (me, them)
                                           if s in ircr.index},
                "ircr_mw_no_battery": num(ircr["ircr_no_battery_mw"].iloc[0], 2),
                "ircr_season": "2025-26 Hot Season (1 Dec 2025 – 31 Mar 2026), in the training period",
                "load": "synthetic factory load [assumed]: order of magnitude only",
                "source": f"{saved_rel(connection)}/ircr.csv (vpp.backtest.q2b_bills)"}
    v_all = verdict(v if them in v and me in v else saved_runs(connection)[1], connection)
    return {"connection": connection, "adopted": dispatcher, "adopted_name": me, "candidate": other(dispatcher),
            "candidate_name": them, "verdict": v_all,
            "selection_rule": f"{v_all['rule']} (at hurdle {HURDLE:g} A$/MWh): "
                              f"{'passed' if v_all['passed'] else 'not passed'} on this connection",
            "periods": rows, "forecast": forecast, "limits": limits, "site": site, "gaps": gaps, "status": "backtest",
            "source": f"{saved_rel(connection)} daily.csv, refs_daily.csv, log.parquet"
                      + (", money.csv, ircr.csv" if factory else "") + "; data/processed/forecast_dispatch_0900.parquet"}
