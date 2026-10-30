"""PLAN.md: e5, G5B and G5B on an 8 MW battery through every day (vpp.v2.backtest.run, the backtest's own pipeline:
ledger, bills, money), with the code as it is; run once before the fix and once after.

Writes out/<tag>_ledger.csv, out/<tag>_bills.csv, out/<tag>_ircr.csv, out/<tag>_money.csv and out/<tag>_power.csv
(per config and day, the largest half-hour battery MW, |energy_mwh| / 0.5 h, and the half-hours over the power).

Run (repo root):  .venv/bin/python analysis/2026-10-26/power_cap_fix/run_money.py <tag>
"""

import os
import sys
from dataclasses import replace
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # the repository tracks vpp's __pycache__; the workers inherit this
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp import site_bill as sb  # noqa: E402
from vpp import stochastic as sto  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402

OUT = Path(__file__).parent / "out"


def scenarios():
    m1 = vb.m1()
    m8 = replace(m1, name="m1 8 MW", asset=replace(m1.asset, battery=replace(m1.asset.battery, power_mw=8.0)))
    e5 = replace(vb.E5, record=False)
    return [(m1, e5), (m1, vb.G5B), (m8, vb.G5B)]


def main(tag: str) -> None:
    OUT.mkdir(exist_ok=True)
    days = bt.load_days("0900")
    aux = bt.q2b_aux()
    runs = scenarios()
    daily, logs, meters, _ = vb.run([(sc, cfg, True) for sc, cfg in runs], days, aux)
    scs = list({sc.name: sc for sc, _ in runs}.values())
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    ledger = bt.q2b_ledger(daily, scs)
    bills, ircr = bt.q2b_bills(meters, days, scs, cmd_nb)
    money = sto.daily_money(ledger, bills)
    power = []
    for sc, cfg in runs:
        P = sc.asset.battery.power_mw
        for d, lg in logs[sc.name, cfg.name].items():
            mw = np.abs(lg["energy_mwh"].to_numpy()) / dp.DT
            power.append({"scenario": sc.name, "strategy": cfg.name, "day": d, "power_mw": P, "max_mw": float(mw.max()),
                          "half_hours_over": int((mw > P + 1e-6).sum())})
    ledger.to_csv(OUT / f"{tag}_ledger.csv", index=False)
    bills.to_csv(OUT / f"{tag}_bills.csv", index=False)
    ircr.to_csv(OUT / f"{tag}_ircr.csv", index=False)
    money.to_csv(OUT / f"{tag}_money.csv")
    pw = pd.DataFrame(power)
    pw.to_csv(OUT / f"{tag}_power.csv", index=False)
    print(pw.groupby(["scenario", "strategy"]).agg(days=("day", "size"), days_over=("half_hours_over", lambda x: int((x > 0).sum())),
                                                    max_mw=("max_mw", "max")).to_string())


if __name__ == "__main__":
    main(sys.argv[1])
