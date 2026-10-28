"""The app's saved backtests behind the factory meter (route m1: CMD 9 MW, IRCR dynamic, the synthetic factory load) at
the app's hurdle (app.engine.common.HURDLE, 50 A$/MWh, set by the user on 2026-10-28): e5 and v1, and as references the
09:00 plan alone ("forecast") and prices known ahead ("perfect"), every trading day. The same runs as
analysis/2026-10-28/factory_hurdle50 (e5 h50, v1 h50), now with the half-hour logs the app replays from (the month's
highest import so far, the meter line). Nothing is chosen here.

  .venv/bin/python analysis/2026-10-28/app_factory_h50/run.py
"""

import sys
import time
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.engine.common import HURDLE  # noqa: E402
from vpp import backtest as bt  # noqa: E402
from vpp import contracts as ct  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp import site_bill as sb  # noqa: E402
from vpp import stochastic as sto  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2.config import V1  # noqa: E402

E5 = replace(vb.E5, record=False)
REFS = ("forecast", "perfect")
TWIN = HERE.parent / "factory_hurdle50"  # the same e5 and v1 runs without logs
TOL = 1e-6


def main() -> None:
    t0 = time.time()
    prefs = replace(bt.PREFS, hurdle_per_mwh=HURDLE)
    route = ct.Route("m1", cmd_mw=vb.CMD, ircr="dynamic")
    sc = bt._sc("m1", route, (bt.VALUE,), prefs=prefs)
    refs = bt._sc("m1", route, REFS, prefs=prefs)
    assert sc is not None and refs is not None, bt.REFUSED
    days = bt.load_days("0900")
    aux = bt.q2b_aux()
    tasks = [(sc, E5, True), (sc, V1, True), *((refs, r, False) for r in REFS)]
    daily, logs, meters, _ = vb.run(tasks, days, aux)
    print(f"m1, hurdle {HURDLE:g} A$/MWh: {len(days)} days x {len(tasks)} runs in {time.time() - t0:.0f} s")

    ledger = bt.q2b_ledger(daily, [sc])
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    bills, ircr = bt.q2b_bills(meters, days, [sc], cmd_nb)
    money = sto.daily_money(ledger, bills)
    is_ref = ledger["strategy"].isin(REFS)
    ledger[~is_ref].to_csv(HERE / "daily.csv", index=False)
    ledger[is_ref].to_csv(HERE / "refs_daily.csv", index=False)
    bills.to_csv(HERE / "bills.csv", index=False)
    ircr.to_csv(HERE / "ircr.csv", index=False)
    money.to_csv(HERE / "money.csv")
    log = pd.concat([x.assign(day=d, strategy=name) for (_, name), lg in logs.items() for d, x in lg.items()],
                    ignore_index=True)
    log.to_parquet(HERE / "log.parquet")

    # checks: the same runs as factory_hurdle50; energy balance; power, stored energy; the meter over the month's cap
    twin = pd.read_csv(TWIN / "daily.csv", parse_dates=["day"])
    twin = twin[twin["config"].isin(["e5 h50", "v1 h50"])]
    mine = ledger[~is_ref].assign(day=pd.to_datetime(ledger.loc[~is_ref, "day"]))
    m = mine.merge(twin, on=["strategy", "day"], suffixes=("", "_twin"))
    gap = max(float((m[c] - m[c + "_twin"]).abs().max()) for c in ("ledger_total", "soc_end"))
    print(f"against factory_hurdle50 (e5 h50, v1 h50): {len(m)} day-rows of {len(mine)}, largest difference {gap:.2e} "
          f"-> {'PASS' if len(m) == len(mine) and gap <= TOL else 'FAIL'}")
    # exact from the day's 5-min sums (run_day: import, export and self exclude regulation): behind the meter the
    # battery discharged self + export and charged import - the factory's load + self. The half-hour log cannot close
    # it: energy_mwh nets charging against regulation raise inside a 5-min step.
    eta = bt.Q2B_ASSET.battery.eta
    by = log.groupby(["strategy", "day"])
    d = daily[~daily["strategy"].isin(REFS)].set_index(["strategy", "day"])
    load = (by["site_load"].sum() * dp.DT).reindex(d.index)
    flows = ((d["import_mwh"] - load + d["self_mwh"]) * eta - (d["self_mwh"] + d["export_mwh"]) / eta
             + d["reg_in_mwh"] * eta - d["reg_out_mwh"] / eta)
    err = float((d["soc_end"] - by["soc_start"].first().reindex(d.index) - flows).abs().max())
    P, E = bt.Q2B_ASSET.battery.power_mw, bt.Q2B_ASSET.battery.energy_mwh
    over_p = int((np.abs(log["energy_mwh"]) / dp.DT > P + TOL).sum())
    soc_bad = int(((log["soc"] < -TOL) | (log["soc"] > E + TOL)).sum())
    over_cap = log[log["meter_mw"] > log["cap_mw"] + TOL].groupby("strategy").size().to_dict()
    print(f"energy balance: largest error {err:.2e} MWh -> {'PASS' if err < TOL else 'FAIL'}; half-hours over {P:g} MW: "
          f"{over_p} of {len(log)}; stored energy outside 0-{E:g} MWh: {soc_bad}; meter over the month's CMD cap "
          f"(half-hours, reported): {over_cap}")
    with pd.option_context("display.width", 200):
        print(ircr[["strategy", "ircr_mw", "ircr_no_battery_mw", "saving_per_year", "intervals_covered"]].round(1)
              .to_string(index=False))


if __name__ == "__main__":
    main()
