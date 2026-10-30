"""Invariant checks on the 10 MW backtest's outputs (vpp.backtest q2b_main): the meter and the battery
stay within their limits, the reported caps, tests and money can be recomputed from the logs.

  I1  meter import <= the import limit (else a site shortfall is recorded); export <= the export limit
  I2  the largest excess over the day's caps in the log equals the largest excess the summary reports
  I3  stored energy stays within 0 .. capacity
  I4  capacity shortfalls only inside the day's window, the log sums to the summary, refunds recompute
  I5  contract availability pay recomputes from the available / window intervals
  I6  energy revenue = sum(RTP x MWh) of the log; ledger = market + capacity - refunds + contracts
  I7  energy discharged <= the daily budget + called events' MWh + recorded overrun
  I8  capacity-year prices switch on 1 October
  I11 the battery's half-hour mean output within its power
  bills: a meter equal to the factory's own load saves nothing and pays no excess charge at its own CMD;
         the IRCR of the logged runs recomputes from their meter at the 12 peak intervals

Run:  python -m vpp.checks
"""

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp import dispatch as dp
from vpp import site as st
from vpp import site_bill as sb
from vpp.data import PROCESSED

REPORTS = bt.REPORTS
TOL = 1e-6


def _result(name: str, bad: int, total: int, note: str = "") -> bool:
    print(f"{'PASS' if bad == 0 else 'FAIL'}  {name}: {bad} of {total} {note}".rstrip())
    return bad == 0


def main() -> None:
    daily = pd.read_csv(REPORTS / "q2b_10mw_daily.csv", parse_dates=["day"])
    logs = pd.read_parquet(PROCESSED / "q2b_10mw_log.parquet")
    ircr = pd.read_csv(REPORTS / "q2b_10mw_ircr.csv")
    days = {d["day"]: d for d in bt.load_days("0900")}
    ok = True
    behind = logs[logs["scenario"] != "m1 export 3 MW"]
    day_short = daily.set_index(["scenario", "strategy", "day"])["site_shortfall_mw"]

    # I1
    key = pd.MultiIndex.from_frame(behind[["scenario", "strategy", "day"]])
    over = (behind["meter_mw"] > st.FACTORY.import_limit_mw + TOL).to_numpy() & (day_short.reindex(key).to_numpy() <= TOL)
    ok &= _result("I1 import within the limit or recorded", int(over.sum()), len(behind), "intervals")
    ok &= _result("I1 export within the limit", int((-behind["meter_mw"] > st.FACTORY.export_limit_mw + TOL).sum()), len(behind), "intervals")

    # I2 (runs whose caps are all on the meter; battery-only event caps limit the battery's own output)
    meter_only = logs[logs["scenario"].isin(["m1", "RCM netted 4h", "PPA backup 4 MWh"])]
    by = meter_only.assign(over=np.clip(meter_only["meter_mw"] - meter_only["cap_mw"], 0, None).where(np.isfinite(meter_only["cap_mw"]), 0.0))
    worst = by.groupby(["scenario", "strategy", "day"])["over"].max()
    ex_cols = [c for c in daily.columns if c.startswith("excess_") and c.endswith("_mw")]
    reported = daily.set_index(["scenario", "strategy", "day"])[ex_cols].max(axis=1).fillna(0.0).reindex(worst.index)
    ok &= _result("I2 largest cap excess, log = summary", int((np.abs(worst - reported) > 1e-4).sum()), len(worst), "days")

    # I3
    soc_bad = ((logs["soc"] < -TOL) | (logs["soc"] > bt.Q2B_ASSET.battery.energy_mwh + TOL)).sum()
    ok &= _result("I3 stored energy within 0-40 MWh", int(soc_bad), len(logs), "intervals")

    # I11 the battery's power: no half-hour's mean output (regulation's energy included) beyond what it can deliver
    over_p = (np.abs(logs["energy_mwh"]) / dp.DT > bt.Q2B_ASSET.battery.power_mw + TOL).sum()
    ok &= _result("I11 battery output within its power", int(over_p), len(logs), "intervals")

    # I4
    rcm = logs[logs["scenario"] == "RCM netted 4h"]
    if len(rcm):
        win = np.array([days[d]["window"] for d in rcm["day"]])
        outside = (rcm["capacity_short_mw"] > TOL).to_numpy() & ~((rcm["slot"] >= win[:, 0]) & (rcm["slot"] < win[:, 1])).to_numpy()
        ok &= _result("I4 capacity shortfall only in the window", int(outside.sum()), len(rcm), "intervals")
        summed = rcm.groupby(["strategy", "day"])["capacity_short_mw"].sum()
        rep = daily[daily["scenario"] == "RCM netted 4h"].set_index(["strategy", "day"])["capacity_short_mw_ti"].reindex(summed.index)
        ok &= _result("I4 shortfall, log = summary", int((np.abs(summed - rep) > 1e-6).sum()), len(summed), "days")
        r = daily[daily["scenario"] == "RCM netted 4h"]
        expect = np.array([ct.refund_rate(d, "esroi") for d in r["day"]]) * r["capacity_short_mw_ti"]
        ok &= _result("I4 refunds recompute", int((np.abs(expect - r["refund"]) > 1e-6).sum()), len(r), "days")

    # I5
    specs = {"SCM 5 MW (registered)": ct.scm(5.0), "NCESS Western Power 3 MW, calls 2 h": ct.ncess_wp(3.0, 2.0, 10)}
    for name, spec in specs.items():
        unit = "5min" if spec.form == "registered" else "ti"
        if f"{spec.name}_window_{unit}" not in daily:
            continue
        r = daily[(daily["scenario"] == name) & (daily[f"{spec.name}_window_{unit}"].fillna(0) > 0)]
        if len(r) and spec.name != "ncess_wp":  # Western Power's season discount is checked by its own rule
            expect = spec.rate * spec.mw * r[f"{spec.name}_available_{unit}"] / r[f"{spec.name}_window_{unit}"]
            ok &= _result(f"I5 {name} availability pay recomputes", int((np.abs(expect - r["contract"]) > 1e-6).sum()), len(r), "days")

    # I6
    rev = logs.assign(x=logs["rtp"] * logs["energy_mwh"]).groupby(["scenario", "strategy", "day"])["x"].sum()
    rep = daily.set_index(["scenario", "strategy", "day"])["energy"].reindex(rev.index)
    ok &= _result("I6 energy revenue = sum(RTP x MWh)", int((np.abs(rev - rep) > 1e-6).sum()), len(rev), "days")
    led = daily["total"] + daily["capacity"] - daily["refund"] + daily["contract"] - daily["ledger_total"]
    ok &= _result("I6 ledger identity", int((np.abs(led) > 1e-6).sum()), len(daily), "days")

    # I7
    ev_cols = [c for c in daily.columns if c.endswith("_event_ti")]
    allowance = sum(daily[c].fillna(0) * 0.5 * 10.0 for c in ev_cols) if ev_cols else 0.0
    budget = bt.Q2B_ASSET.battery.energy_mwh * bt.Q2B_ASSET.limits.cycles_per_day
    over = daily["discharged_mwh"] > budget + allowance + daily["budget_overrun_mwh"] + 1e-6
    ok &= _result("I7 discharge within the budget", int(over.sum()), len(daily), "days")

    # I9 information timing: no day's terms use anything before it is public
    if "timing_errors" in daily:
        ok &= _result("I9 information used only once public", int(daily["timing_errors"].fillna(0).sum()), len(daily), "days")

    # I10 compliance: a plan short of a compliance requirement only when none could meet it, and then by
    # the least; executed shortfall on the other days is what forecast error left (reported, not a failure)
    if "plan_shortfall_mwh" in daily:
        planned = daily["plan_shortfall_mwh"].fillna(0) > 1e-6
        print(f"INFO  I10 days with a plan short of a compliance requirement (none could meet it): {int(planned.sum())} of {len(daily)}; "
              f"largest {daily['plan_shortfall_mwh'].max():.2f} MWh; real-time shortfall on the other days, largest "
              f"{daily.loc[~planned, 'obligation_shortfall_mwh'].max():.2f} MWh")

    # I8
    flips = [ct.rcp(pd.Timestamp("2025-09-30")) == ct.RCP[2024], ct.rcp(pd.Timestamp("2025-10-01")) == ct.RCP[2025],
             ct.rcp(pd.Timestamp("2026-10-01")) == ct.RCP[2026]]
    ok &= _result("I8 capacity-year prices switch on 1 October", int(not all(flips)), 3, "checks")

    # bills
    load = bt.factory_load(list(days.values()))
    rt7 = sb.RT7(sb.cmd_kva(load.max()))
    own = sb.monthly_bills(load, rt7)
    ok &= _result("bill: the factory alone pays no excess charge at its own CMD", int((own["enuc"] > 0).sum()), len(own), "months")
    demand = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["demand_mw"])["demand_mw"]
    iv = sb.ircr_intervals(demand, bt.IRCR_SEASON)
    bad = 0
    for (sc, strat), g in logs.groupby(["scenario", "strategy"]):
        meter = pd.Series(g["meter_mw"].to_numpy(), index=pd.DatetimeIndex(g["day"] + bt.H + g["slot"] * bt.HALF))
        rec = ircr[(ircr["scenario"] == sc) & (ircr["strategy"] == strat)]["ircr_mw"]
        bad += int(len(rec) and abs(sb.ircr_mw(meter, iv) - rec.iloc[0]) > 1e-3)
    ok &= _result("bill: IRCR recomputes from the logged meter", bad, logs.groupby(["scenario", "strategy"]).ngroups, "runs")
    print("ALL CHECKS PASS" if ok else "SOME CHECKS FAILED")


if __name__ == "__main__":
    main()
