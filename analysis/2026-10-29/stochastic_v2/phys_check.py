"""The log-only energy check of run.py (balance) on a saved log, with two planted errors it must catch: one half-hour of
charging without the efficiency loss, and a day starting 0.5 MWh above the day before's end.

  .venv/bin/python analysis/2026-10-29/stochastic_v2/phys_check.py [log file]
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
for p in (ROOT, str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import run  # noqa: E402  (this folder)
from vpp import backtest as bt  # noqa: E402


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "log_smoke.parquet"
    log = pd.read_parquet(path)
    eta = bt.Q2B_ASSET.battery.eta
    for name, g in log.groupby("strategy"):
        by_day = {d: x.reset_index(drop=True) for d, x in g.groupby("day")}
        free, chain, lost = run.balance(by_day)
        days = sorted(by_day)
        # planted 1: the first half-hour charging over 0.5 MWh stores e x 1 instead of e x eta, later ones shifted
        a = {d: x.copy() for d, x in by_day.items()}
        day = next(d for d in days if (a[d]["energy_mwh"] < -0.5).any())
        x = a[day]
        t = int(np.flatnonzero(x["energy_mwh"].to_numpy() < -0.5)[0])
        x.loc[t:, "soc"] += -x.loc[t, "energy_mwh"] * (1 - eta)
        free1, _, _ = run.balance(a)
        # planted 2: the 5th day starts 0.5 MWh higher
        b = {d: x.copy() for d, x in by_day.items()}
        b[days[4]]["soc_start"] += 0.5
        _, chain2, _ = run.balance(b)
        print(f"{name}: {len(days)} days; energy from nothing {free:.2e} MWh, day chain gap {chain:.2e} MWh, lost in "
              f"round trips {lost:.3f} MWh/day | planted no-loss half-hour caught {free1 > 1e-6} ({free1:.3f}); planted "
              f"start gap caught {chain2 > 1e-6} ({chain2:.3f})")


if __name__ == "__main__":
    main()
