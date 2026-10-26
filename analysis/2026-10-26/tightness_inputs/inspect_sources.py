"""Why some days lack ST PASA or weather inputs: report copies around the gaps, weather coverage per location/variable.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/inspect_sources.py
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
PIT = ROOT / "data" / "pit"
pd.set_option("display.width", 250)

st = pd.read_parquet(PIT / "stpasa.parquet")
dem = st[st["variable"] == "demand_forecast"]
cop = dem.groupby(["record_id", "version"]).agg(available_at=("available_at", "min"), tmin=("target_time", "min"),
                                                 tmax=("target_time", "max"), n=("value", "size"),
                                                 scen=("scenario", lambda s: "|".join(sorted(set(s))))).reset_index()
for a, b in (("2024-01-22", "2024-01-27"), ("2024-10-10", "2024-10-17"), ("2026-07-20", "2026-07-29")):
    print(cop[(cop["record_id"] >= a) & (cop["record_id"] <= b)].to_string(index=False))
aud = pd.read_csv(PIT / "stpasa_audit.csv")
print(aud[(aud["report_day"] >= "2026-07-20") & (aud["report_day"] <= "2026-07-29")][["source_file", "report_day", "layout", "n_demand", "error"]].to_string())

wx = pd.read_parquet(PIT / "weather.parquet")
print(wx.groupby(["facility_or_service", "variable", "version"])["target_time"].agg(["min", "max", "count"]).to_string())
