"""Columns of the two delivered tables and one example day (format check for RESULTS.md).

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/inspect_out3.py
"""

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
pd.set_option("display.width", 250)
pd.set_option("display.max_rows", 200)
d = pd.read_parquet(HERE / "out" / "inputs_d1.parquet")
x = pd.read_parquet(HERE / "out" / "inputs_d1_30min.parquet")
print(d.shape, x.shape)
print(list(x.columns))
print(d[d["day"] == "2025-07-15"].T.to_string())
print(x[x["day"] == "2025-07-15"].iloc[:2].T.to_string())
