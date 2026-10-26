"""Debug: the in-sample day without ST PASA High/Low, the A4 ST PASA mismatch day, and the LRC day table.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/inspect_out2.py
"""

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 90)
d = pd.read_parquet(HERE / "out" / "inputs_d1.parquet").set_index("day")
s = d[d["period"] == "in_sample"]
print(s[s[["st_ref_max_1621_mw", "st_high_max_1621_mw", "st_low_max_1621_mw"]].isna().any(axis=1)][
    ["st_report", "st_version", "st_ref_max_1621_mw", "st_high_max_1621_mw", "st_low_max_1621_mw"]].to_string())
stb = pd.read_parquet(ROOT / "data" / "processed" / "spike_table.parquet",
                      columns=["issue_date", "target_time", "days_ahead", "st_ref", "src_st_report", "src_st_available_at"])
x = stb[(stb["days_ahead"] == 1) & (stb["issue_date"] == "2024-03-17") & (stb["target_time"].dt.hour.between(16, 20))]
print(x.to_string())
print(d.loc["2024-03-18", ["st_report", "st_version", "stpasa_available_at", "st_ref_max_1621_mw"]])
st = pd.read_parquet(ROOT / "data" / "pit" / "stpasa.parquet")
c = st[(st["variable"] == "demand_forecast") & (st["record_id"].between("2024-03-14", "2024-03-17"))]
print(c.groupby(["record_id", "version"]).agg(av=("available_at", "min"), tmin=("target_time", "min"), tmax=("target_time", "max")).to_string())
print(pd.read_csv(HERE / "out" / "lrc_days.csv").to_string())
