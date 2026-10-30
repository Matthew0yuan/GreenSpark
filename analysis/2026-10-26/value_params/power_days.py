"""Reads out/g5b_default.csv and out/g5b_power_mw8.csv: the days whose half-hour grid-side MW (energy_mwh / 0.5 h,
regulation energy included) goes over the battery's power, over all days and over the test days 2026-10-16 .. 10-22.
(max_up_mw = that MW + the raise FCESS enabled counts the regulation energy twice: not a valid check, left out.)
Writes out/power_days.json.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/power_days.py
"""

import json

import _common
import pandas as pd

out = {}
for tag, P in (("default", 10.0), ("power_mw8", 8.0)):
    d = pd.read_csv(_common.OUT / f"g5b_{tag}.csv", parse_dates=["day"])
    over = d[d["max_grid_mw"] > P + 1e-6]
    week = d[(d["day"] >= "2026-10-16") & (d["day"] <= "2026-10-22")]
    months = over["day"].dt.strftime("%Y-%m").value_counts().sort_index().to_dict()
    out[tag] = {"power_mw": P, "days": len(d), "days_over": len(over), "largest_mw": float(d["max_grid_mw"].max()),
                "over_by_month": months, "test_week_days": len(week), "test_week_max_mw": float(week["max_grid_mw"].max()),
                "test_week_over": int((week["max_grid_mw"] > P + 1e-6).sum())}
    print(tag, json.dumps(out[tag]))
(_common.OUT / "power_days.json").write_text(json.dumps(out, indent=1))
