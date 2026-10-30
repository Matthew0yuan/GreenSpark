"""The app's dispatcher (e5 at the app's hurdle) every trading day, on the actual prices or on the actual prices with the
spikes taken off (each 5-min energy price capped at K; the 30-min price less the half-hour's mean 5-min excess over K,
and at most K). Forecasts, yesterday's prices, value tables, FCESS prices and the load are unchanged (PLAN.md).

  .venv/Scripts/python.exe analysis/2026-10-30/spike_value/run.py factory none
  .venv/Scripts/python.exe analysis/2026-10-30/spike_value/run.py standalone 200

Writes out/<connection>_<cap>/: daily.csv, log.parquet and, behind the factory meter, bills.csv, ircr.csv, money.csv.
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

E5 = replace(vb.E5, record=False)


def capped(days: list[dict], cap: float) -> list[dict]:
    """Copies of the days with the actual energy prices capped at cap (A$/MWh); nothing else changes."""
    out = []
    for d in days:
        p5 = np.asarray(d["p5"], float)
        p5c = np.minimum(p5, cap)
        excess = (p5 - p5c).reshape(dp.N, dp.STEPS).mean(axis=1)
        rtp = np.minimum(np.asarray(d["rtp"], float) - excess, cap)
        e = dict(d)
        e["p5"], e["rtp"] = p5c, rtp
        e["prices"] = {**d["prices"], "actual": (rtp, d["prices"]["actual"][1])}
        out.append(e)
    return out


def main(connection: str, cap: str) -> None:
    t0 = time.time()
    prefs = replace(bt.PREFS, hurdle_per_mwh=HURDLE)
    if connection == "factory":
        route = ct.Route("m1", cmd_mw=vb.CMD, ircr="dynamic")
        sc = bt._sc("m1", route, (bt.VALUE,), prefs=prefs)
    else:
        sc = bt._sc("sa", ct.Route("sa", metering="standalone"), (bt.VALUE,), prefs=prefs)
    assert sc is not None, bt.REFUSED
    days = bt.load_days("0900")
    if cap != "none":
        K = float(cap)
        days = capped(days, K)
        assert all(d["p5"].max() <= K and d["rtp"].max() <= K for d in days)
    daily, logs, meters, _ = vb.run([(sc, E5, True)], days, bt.q2b_aux())
    print(f"{connection}, cap {cap}: {len(days)} days in {time.time() - t0:.0f} s")
    out = HERE / "out" / f"{connection}_{cap}"
    out.mkdir(parents=True, exist_ok=True)
    if connection == "factory":
        ledger = bt.q2b_ledger(daily, [sc])
        cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
        bills, ircr = bt.q2b_bills(meters, days, [sc], cmd_nb)
        sto.daily_money(ledger, bills).to_csv(out / "money.csv")
        ledger.to_csv(out / "daily.csv", index=False)
        bills.to_csv(out / "bills.csv", index=False)
        ircr.to_csv(out / "ircr.csv", index=False)
    else:
        daily.to_csv(out / "daily.csv", index=False)
    log = pd.concat([x.assign(day=d, strategy=name) for (_, name), lg in logs.items() for d, x in lg.items()],
                    ignore_index=True)
    log.to_parquet(out / "log.parquet")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
