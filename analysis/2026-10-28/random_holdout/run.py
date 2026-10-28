"""PLAN.md: both connections (sa: standalone, m1: behind the factory) x v1 and e5, every trading day, on this worktree's
retrained inputs. Writes daily.csv, money_m1.csv, bills_m1.csv and log.parquet here; prints check 4 only.
  .venv/bin/python analysis/2026-10-28/random_holdout/run.py   (from the worktree root, after pipeline.py)
"""
import sys
import time
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp import site_bill as sb  # noqa: E402
from vpp import stochastic as sto  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2.config import V1  # noqa: E402

TOL = 1e-6


def balance(daily: pd.DataFrame) -> float:
    """Check 4: largest |stored energy change - (in x eta - out / eta)| over every day, MWh (standalone_v2/run.py).
    Behind the meter the battery's own charge and discharge are import/export less the factory's part, so the
    check uses the logs' battery energy instead (see log_balance)."""
    eta = bt.Q2B_ASSET.battery.eta
    worst = 0.0
    for _, g in daily.sort_values("day").groupby("strategy"):
        start = np.concatenate([[0.0], g["soc_end"].to_numpy()[:-1]])
        flows = (g["import_mwh"] + g["reg_in_mwh"]) * eta - (g["export_mwh"] + g["reg_out_mwh"]) / eta
        worst = max(worst, float(np.abs(g["soc_end"].to_numpy() - start - flows.to_numpy()).max()))
    return worst


def log_balance(log: pd.DataFrame) -> float:
    """Check 4 on the half-hour logs: stored energy at the end of each day against the start plus the day's battery
    energy (discharge / eta, charge x eta, regulation included in energy_mwh only as its net, so this is a bound)."""
    worst = 0.0
    for _, g in log.sort_values(["day", "slot"]).groupby(["scenario", "strategy"]):
        d = g.groupby("day")
        start, end = d["soc_start"].first().to_numpy(), d["soc"].last().to_numpy()
        worst = max(worst, float(np.abs(start[1:] - end[:-1]).max()))  # each day starts where the last ended
    return worst


def main() -> None:
    t0 = time.time()
    days = bt.load_days("0900")
    aux = bt.q2b_aux()
    sa, m1 = vb.sa(), vb.m1()
    assert sa is not None and m1 is not None, bt.REFUSED
    e5 = replace(vb.E5, record=False)
    tasks = [(sa, e5, True), (sa, bt.VALUE, True), (m1, e5, True), (m1, V1, True)]
    daily, logs, meters, _ = vb.run(tasks, days, aux)
    print(f"{len(days)} days x {len(tasks)} runs in {time.time() - t0:.0f} s", flush=True)
    daily.to_csv(HERE / "daily.csv", index=False)
    log = pd.concat([x.assign(day=d, scenario=sc, strategy=name) for (sc, name), lg in logs.items()
                     for d, x in lg.items()], ignore_index=True)
    log.to_parquet(HERE / "log.parquet")

    dm = daily[daily["scenario"] == "m1"]
    ledger = bt.q2b_ledger(dm, [m1])
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    bills, ircr = bt.q2b_bills({k: v for k, v in meters.items() if k[0] == "m1"}, days, [m1], cmd_nb)
    money = sto.daily_money(ledger, bills)
    ledger.to_csv(HERE / "ledger_m1.csv", index=False)
    bills.to_csv(HERE / "bills_m1.csv", index=False)
    ircr.to_csv(HERE / "ircr_m1.csv", index=False)
    money.to_csv(HERE / "money_m1.csv")

    err = balance(daily[daily["scenario"] == "sa"])
    chain = log_balance(log)
    P, E = bt.Q2B_ASSET.battery.power_mw, bt.Q2B_ASSET.battery.energy_mwh
    over_p = int((np.abs(log["energy_mwh"]) / dp.DT > P + TOL).sum())
    soc_bad = int(((log["soc"] < -TOL) | (log["soc"] > E + TOL)).sum())
    print(f"check 4 - energy balance (standalone): largest error {err:.2e} MWh -> {'PASS' if err < TOL else 'FAIL'}")
    print(f"check 4 - each day starts where the last ended (both): largest gap {chain:.2e} MWh -> "
          f"{'PASS' if chain < TOL else 'FAIL'}")
    print(f"check 4 - half-hour mean output within {P:g} MW: {over_p} of {len(log)} -> {'PASS' if over_p == 0 else 'FAIL'}; "
          f"stored energy within 0-{E:g} MWh: {soc_bad} outside -> {'PASS' if soc_bad == 0 else 'FAIL'}")


if __name__ == "__main__":
    main()
