"""Check one sentence of RESULTS.md: when the 2024-06-08/09/10 ST PASA reports were public.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/inspect_out5.py
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
st = pd.read_parquet(ROOT / "data" / "pit" / "stpasa.parquet", columns=["record_id", "version", "available_at", "variable"])
st = st[st["variable"] == "demand_forecast"]
print(st[st["record_id"].between("2024-06-07", "2024-06-10")].groupby(["record_id", "version"])["available_at"].min().to_string())
d = pd.read_parquet(Path(__file__).resolve().parent / "out" / "inputs_d1.parquet").set_index("day")
print(d.loc["2024-06-11", ["issue_at", "st_report", "stpasa_available_at", "st_report_age_days"]])
