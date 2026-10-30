"""PLAN.md, report item 7: the test days 2026-10-16 .. 10-22 of the 8 MW and the 10 MW G5B runs side by side (both run
from 2025-04-01, the start state carried over). Writes out/week.csv and out/week.json.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/week_table.py
"""

import json

import _common
import pandas as pd

COLS = ("total", "energy", "fcess", "wear", "soc_end")
a = pd.read_csv(_common.OUT / "g5b_default.csv", parse_dates=["day"]).set_index("day")
b = pd.read_csv(_common.OUT / "g5b_power_mw8.csv", parse_dates=["day"]).set_index("day")
sel = lambda x: x[(x.index >= "2026-10-16") & (x.index <= "2026-10-22")]  # noqa: E731
a7, b7 = sel(a), sel(b)
t = pd.concat({"10 MW": a7[list(COLS)], "8 MW": b7[list(COLS)]}, axis=1)
for c in COLS:
    t[("8 - 10", c)] = t[("8 MW", c)] - t[("10 MW", c)]
t.to_csv(_common.OUT / "week.csv")
summary = {"days": len(b7), "days_total_differs": int(((b7["total"] - a7["total"]).abs() > 1e-6).sum()),
           **{f"sum_{c}_{k}": float(x[c].sum()) for c in ("total", "energy", "fcess", "wear") for k, x in (("10", a7), ("8", b7))}}
(_common.OUT / "week.json").write_text(json.dumps(summary, indent=1))
pd.set_option("display.width", 250)
print(t.round(1).to_string())
print(json.dumps(summary, indent=1))
