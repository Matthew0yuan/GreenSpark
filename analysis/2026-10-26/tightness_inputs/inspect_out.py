"""Debug view of the built table: missing inputs by day and source, and a few rows (no label comparisons).

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/inspect_out.py
"""

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 30)
d = pd.read_parquet(HERE / "out" / "inputs_d1.parquet").set_index("day")
print(d.dtypes.to_string())
print(d.loc["2026-10-14":, ["bat_cap_mw", "bat_last_day", "battery_available_at", "pd_run", "out_snapshot", "st_report",
                            "wx_newest_run", "margin_min_1621_mw", "n_valid_1621"]].to_string())
for c in ["wx_wind_midwest_1621_kmh", "wx_temp_perth_max_day_c", "st_ref_max_1621_mw", "pd_req_max_1621_mw"]:
    miss = d.index[d[c].isna()]
    print(c, len(miss), [f"{x:%Y-%m-%d}" for x in miss[:80]])
print(d.loc[d["label_complete"] == False, ["n_valid_1621", "period"]].query("period != 'history'").to_string())
