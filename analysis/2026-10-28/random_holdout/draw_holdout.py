"""PLAN.md: draw the held-out days, 5 business days a month 2025-04 .. 2026-06 with complete backtest data.
  .venv/bin/python analysis/2026-10-28/random_holdout/draw_holdout.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import wa_calendar as wc  # noqa: E402

SEED, PER_MONTH = 20261028, 5
MONTHS = pd.period_range("2025-04", "2026-06", freq="M")


def main() -> None:
    days = pd.DatetimeIndex([d["day"] for d in bt.load_days("0900")])
    ok = days[wc.business_day(days)]
    rng = np.random.default_rng(SEED)
    rows = []
    for m in MONTHS:
        pool = ok[ok.to_period("M") == m]
        pick = np.sort(rng.choice(pool.to_numpy(), PER_MONTH, replace=False))
        rows += [{"day": pd.Timestamp(d).date(), "month": str(m), "pool": len(pool),
                  "hot_season": pd.Timestamp(d).month in (12, 1, 2, 3)} for d in pick]
    out = pd.DataFrame(rows)
    out.to_csv(HERE / "holdout_days.csv", index=False)
    print(f"{len(out)} days over {out['month'].nunique()} months, {int(out['hot_season'].sum())} in Dec-Mar; "
          f"pool per month {out.groupby('month')['pool'].first().min()}-{out.groupby('month')['pool'].first().max()} "
          f"business days -> {HERE / 'holdout_days.csv'}")


if __name__ == "__main__":
    main()
