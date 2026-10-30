"""PLAN.md check 1, positive controls: the held-out test sees every unit of datetime, a held-out day put into a
training set raises, and monthly_refits never hands a held-out day to a fit.
  .venv/bin/python analysis/2026-10-28/random_holdout/check1_control.py   (from the worktree root)
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import aemo_correction as ac  # noqa: E402
from vpp import evaluate as ev  # noqa: E402

day = ev.HOLDOUT[0]
assert len(ev.HOLDOUT) == 75, len(ev.HOLDOUT)
for x in (pd.Series([day]).astype("datetime64[us]"), pd.DatetimeIndex([day + pd.Timedelta(hours=13)]), [str(day.date())]):
    assert ev.held_out(x).all(), x
assert not ev.held_out([day - pd.Timedelta(days=1)]).any() or (day - pd.Timedelta(days=1)) in ev.HOLDOUT
try:
    ev.no_holdout([day], "planted")
    raise SystemExit("FAIL: a held-out day in a training set did not raise")
except ValueError as e:
    print(f"PASS  planted held-out day raises: {e}")

dates = pd.date_range("2025-03-01", "2026-08-31")
data = pd.DataFrame({"target_date": np.repeat(dates, 2).astype("datetime64[us]")})
seen = []


def fit_predict(train, test):
    seen.append(pd.DatetimeIndex(data.loc[train, "target_date"]).unique())
    return pd.Series(0.0, index=data.index[test])


ac.monthly_refits(data, 0, pd.Timestamp("2025-04-01"), fit_predict)
used = seen[0].append(seen[1:]) if seen else pd.DatetimeIndex([])
bad = int(ev.held_out(used).sum())
removed = sum(r["held_out_days_removed"] for r in ac.TRAIN_LOG)
print(f"{'PASS' if bad == 0 and removed > 0 else 'FAIL'}  monthly_refits on toy data: {len(seen)} fits, "
      f"{bad} held-out days handed to a fit, {removed} held-out day-fits removed")
