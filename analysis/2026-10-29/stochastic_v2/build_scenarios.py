"""The 09:00 price scenarios (vpp.scenarios) extended to every backtest day, written here: the method stays the one
chosen before the cutoff (anen365_vol_level, findings section 17); nothing is re-chosen. The forecast history is
rebuilt from HISTORY_START, and vpp.scenarios.history checks that it equals the dispatcher's forecast on the overlap.
The days the old file (data/processed/scenarios_0900.parquet) has are compared with it.

  .venv/bin/python analysis/2026-10-29/stochastic_v2/build_scenarios.py
"""

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import scenarios as sn  # noqa: E402

METHOD = "anen365_vol_level"  # chosen on validation in round 3 (reports/scenarios_calibration.csv) [fixed]
OUT = HERE / "scenarios_0900.parquet"


def main() -> None:
    t0 = time.time()
    old = pd.read_parquet(sn.OUT)
    sn.HISTORY, sn.SIGNALS_FILE, sn.OUT = HERE / "forecast_history_0900.parquet", HERE / "scenario_signals.parquet", OUT
    fc, sig = sn.history(rebuild=True)
    bank = sn.Bank(fc, sig)
    print(f"bank: {len(bank.dates)} complete days, {bank.dates[0]:%Y-%m-%d} .. {bank.dates[-1]:%Y-%m-%d}")
    sn.save(bank, METHOD)
    new = pd.read_parquet(OUT)
    key = ["day", "member", "slot"]
    both = old.merge(new, on=key, suffixes=("_old", "_new"))
    cols = ["energy", *sn.FCESS]
    gap = {c: float(np.abs(both[f"{c}_old"] - both[f"{c}_new"]).max()) for c in cols}
    same_analog = float((both["analog_day_old"] == both["analog_day_new"]).mean())
    print(f"old file: {old['day'].nunique()} days to {old['day'].max():%Y-%m-%d}; new: {new['day'].nunique()} days to "
          f"{new['day'].max():%Y-%m-%d}")
    print(f"on the {both['day'].nunique()} common days: largest |old - new| {gap}; same analog day {same_analog:.4f}")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
