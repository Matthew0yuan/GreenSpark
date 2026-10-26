"""Schema only (before PLAN.md): column names, dtypes, non-null counts per period. No target/input relationships.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_forecast/explore_schema.py
"""
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
INP = HERE.parent / "tightness_inputs" / "out"

pd.set_option("display.width", 250)
pd.set_option("display.max_rows", 300)
pd.set_option("display.max_columns", 50)

d = pd.read_parquet(INP / "inputs_d1.parquet")
print(d.shape)
print(d.index.name, d.index[:3].tolist(), d.index[-3:].tolist())
print(d.dtypes.to_string())
print(d.groupby("period").count().T.to_string())

h = pd.read_parquet(INP / "inputs_d1_30min.parquet")
print(h.shape)
print(h.index.names if h.index.nlevels > 1 else h.index.name, h.head(3).T.to_string())
print(h.dtypes.to_string())
