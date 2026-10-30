"""Regression for the registration switch (PLAN.md, check 1): Scheduled routes on sample days, saved before and
after the change and compared row for row.

  .venv/Scripts/python.exe analysis/2026-10-26/registration_switch/parity.py before
  .venv/Scripts/python.exe analysis/2026-10-26/registration_switch/parity.py after
  .venv/Scripts/python.exe analysis/2026-10-26/registration_switch/parity.py compare
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import contracts as ct  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402

WINDOWS = (("2025-08-04", "2025-08-15"), ("2026-01-12", "2026-01-23"), ("2026-08-03", "2026-08-14"))


def sample_days() -> list[dict]:
    days = bt.load_days("0900")
    keep = [d for d in days if any(pd.Timestamp(a) <= d["day"] <= pd.Timestamp(b) for a, b in WINDOWS)]
    return keep


def tasks() -> list[tuple]:
    sa = bt._sc("standalone market", ct.Route("x", metering="standalone"), ("perfect", "forecast", bt.VALUE))
    m1 = vb.m1()
    return [(sa, "perfect", False), (sa, "forecast", False), (sa, bt.VALUE, False), (m1, bt.VALUE, False),
            (m1, vb.B4, False)]


def run(tag: str) -> None:
    days = sample_days()
    daily, _, _, _ = vb.run(tasks(), days, bt.q2b_aux())
    daily = daily.sort_values(["scenario", "strategy", "day"]).reset_index(drop=True)
    daily.to_csv(HERE / f"parity_{tag}.csv", index=False)
    print(f"{tag}: {len(daily)} rows, {daily['day'].nunique()} days")


def compare() -> None:
    a, b = (pd.read_csv(HERE / f"parity_{t}.csv") for t in ("before", "after"))
    assert list(a.columns) == list(b.columns), (set(a.columns) ^ set(b.columns))
    assert len(a) == len(b), (len(a), len(b))
    num = a.select_dtypes("number").columns
    diff = (a[num] - b[num]).abs().fillna(0.0)
    nan_mismatch = (a[num].isna() != b[num].isna()).to_numpy().sum()
    other = [c for c in a.columns if c not in num]
    text_mismatch = int((a[other].astype(str) != b[other].astype(str)).to_numpy().sum())
    print(f"rows {len(a)}; largest numeric difference {float(diff.to_numpy().max()):.3g}; "
          f"NaN mismatches {int(nan_mismatch)}; text mismatches {text_mismatch}")
    worst = diff.max().sort_values(ascending=False).head(5)
    print(worst.to_string())
    ok = float(diff.to_numpy().max()) == 0.0 and nan_mismatch == 0 and text_mismatch == 0
    print("PASS: identical" if ok else "FAIL")


if __name__ == "__main__":
    arg = sys.argv[1]
    compare() if arg == "compare" else run(arg)
