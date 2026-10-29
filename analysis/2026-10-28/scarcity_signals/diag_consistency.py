"""Inspect the consistency-check mismatches of build_table.py (labels on 2026-10-16, p_std_7, ST PASA)."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "out"
KEY = ["issue_date", "days_ahead", "target_time"]
old = pd.read_parquet(ROOT / "data/processed/spike_table.parquet").set_index(KEY).sort_index()
new = pd.read_parquet(OUT / "spike_table_ext.parquet").set_index(KEY).sort_index().reindex(old.index)
lab = old["spike"].isna() != new["spike"].isna()
print("label mismatches: old NaN & new value:", int((old["spike"].isna() & new["spike"].notna()).sum()), "of", int(lab.sum()),
      "| target dates:", sorted(set(pd.DatetimeIndex(old.index[lab].get_level_values("target_time")).normalize().date)))
d = (old["p_std_7"] - new["p_std_7"]).abs()
print("p_std_7 abs diff: max", d.max(), "| >1e-9:", int((d > 1e-9).sum()), "| NaN pattern differs:", int((old["p_std_7"].isna() != new["p_std_7"].isna()).sum()))
st = (old["st_ref"] - new["st_ref"]).abs()
bad = old.index[(st > 0) | (old["st_ref"].isna() != new["st_ref"].isna())]
print("st_ref differing issue dates:", sorted(set(bad.get_level_values("issue_date").date))[:20])
x = pd.DataFrame({"old_report": old.loc[bad, "src_st_report"], "new_report": new.loc[bad, "src_st_report"],
                  "old_av": old.loc[bad, "src_st_available_at"], "new_av": new.loc[bad, "src_st_available_at"]}).drop_duplicates()
print(x.head(10).to_string())
