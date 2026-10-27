"""Read-only replay of the frozen v1 baseline and the adopted e5 (rw50+state t5+roll) on route m1, with the code as it
is now (after the 2026-10-26 power-cap fix), through every backtest day 2025-04-01 .. bt.LAST_DAY. It uses the
backtest's own pipeline exactly as `python -m vpp.v2.backtest --step test` and
analysis/2026-10-26/power_cap_fix/run_money.py do (vpp.v2.backtest.run, bt.q2b_ledger, bt.q2b_bills,
vpp.stochastic.daily_money), and writes only into this directory:

  site_summary_replay_ledger.csv   daily ledger (as reports/v2_test_daily.csv)
  site_summary_replay_bills.csv    monthly RT7 bills and saving (as reports/v2_test_bills.csv)
  site_summary_replay_ircr.csv     2025-26 Hot Season IRCR (as reports/v2_test_ircr.csv)
  site_summary_replay_money.csv    day x strategy money = ledger + the month's bill saving per day
  site_summary_replay_log.parquet  half-hour logs (as data/processed/v2_test_log.parquet)

Why: the published v1 and e5 outputs predate the fix (power_cap_fix/RESULTS.md section 4); the fix's own rerun
(power_cap_fix/out/after_*) has e5 but no v1 and no half-hour logs. Nothing in vpp/ or data/ is written: the value
tables are only loaded (vpp.v2.harness), never built.

Run (repo root):  .venv/Scripts/python.exe analysis/2026-10-26/ui_redesign/export/site_summary_replay.py
"""

import os
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # the repository tracks vpp's __pycache__; the workers inherit this
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import site_bill as sb  # noqa: E402
from vpp import stochastic as sto  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2.config import V1  # noqa: E402


def main() -> None:
    t0 = time.time()
    days = bt.load_days("0900")
    aux = bt.q2b_aux()
    sc = vb.m1()
    configs = (V1, replace(vb.E5, record=False))
    print(f"{len(days)} days {days[0]['day'].date()} .. {days[-1]['day'].date()}; configs {[c.name for c in configs]}", flush=True)
    daily, logs, meters, _ = vb.run([(sc, cfg, True) for cfg in configs], days, aux)
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    ledger = bt.q2b_ledger(daily, [sc])
    bills, ircr = bt.q2b_bills(meters, days, [sc], cmd_nb)
    money = sto.daily_money(ledger, bills)
    ledger.to_csv(HERE / "site_summary_replay_ledger.csv", index=False)
    bills.to_csv(HERE / "site_summary_replay_bills.csv", index=False)
    ircr.to_csv(HERE / "site_summary_replay_ircr.csv", index=False)
    money.to_csv(HERE / "site_summary_replay_money.csv")
    pd.concat([x.assign(day=d, strategy=name) for (_, name), lg in logs.items() for d, x in lg.items()],
              ignore_index=True).to_parquet(HERE / "site_summary_replay_log.parquet")
    print(f"done in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
