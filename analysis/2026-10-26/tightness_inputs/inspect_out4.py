"""Which history days lack weather beyond the archive start, and the ST PASA report-age distribution.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/inspect_out4.py
"""

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
d = pd.read_parquet(HERE / "out" / "inputs_d1.parquet").set_index("day")
wx = ["wx_wind_midwest_1621_kmh", "wx_wind_merredin_1621_kmh", "wx_temp_perth_max_day_c", "wx_temp_perth_max_1621_c",
      "wx_ghi_perth_mean_day_wm2", "wx_ghi_perth_mean_1621_wm2"]
m = d[wx].isna().any(axis=1)
print("days missing some weather:", int(m.sum()), "first", d.index[m].min(), "last", d.index[m].max())
print(d.loc[m & (d.index > "2024-03-07"), wx].to_string())
print(d.loc[d["period"] != "test_unseen", "st_report_age_days"].value_counts().sort_index().to_dict())
print(d.loc[d["period"] == "test_unseen", "st_report_age_days"].to_dict())
