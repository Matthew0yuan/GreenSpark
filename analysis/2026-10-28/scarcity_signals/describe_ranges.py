"""Value ranges of the scarcity features (sanity only; no labels are read)."""
import sys
from pathlib import Path
import pandas as pd
sys.dont_write_bytecode = True
OUT = Path(__file__).resolve().parent / "out"
f = pd.read_parquet(OUT / "scarcity_features.parquet")
cols = [c for c in f.columns if c.startswith(("fx_", "pdx_", "stem_")) and not c.endswith(("_ok", "_ok_1330", "auction"))]
print(f[cols].describe(percentiles=[0.5, 0.9, 0.99]).T.round(2).to_string())
