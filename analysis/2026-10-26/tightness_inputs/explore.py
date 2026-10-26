"""Look at the shape and coverage of the inputs before writing PLAN.md (no labels, no results).

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/explore.py
"""

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 40)

PIT = ROOT / "data" / "pit"
PROC = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"

for name in ("predispatch", "stpasa", "weather"):
    d = pd.read_parquet(PIT / f"{name}.parquet")
    print(f"\n=== pit/{name}: {len(d):,} rows")
    print(d.dtypes.to_string())
    print(d.head(3).T.to_string())
    for c in ("variable", "facility_or_service", "scenario", "version"):
        if d[c].notna().any():
            print(c, d[c].value_counts().head(20).to_dict())
    print("target_time", d["target_time"].min(), d["target_time"].max())
    print("available_at", d["available_at"].min(), d["available_at"].max())

st = pd.read_parquet(PROC / "spike_table.parquet")
print(f"\n=== spike_table: {len(st):,} rows, issue {st['issue_date'].min()} .. {st['issue_date'].max()}")
print(list(st.columns))
print(st["days_ahead"].value_counts().to_dict())

w30 = pd.read_parquet(PROC / "wem_30min.parquet")
print("\n=== wem_30min", w30.index.min(), w30.index.max(), list(w30.columns))
s5 = pd.read_parquet(ROOT / "analysis" / "2026-10-17" / "out" / "scada_5min.parquet")
print("=== scada_5min", s5.index.min(), s5.index.max(), list(s5.columns))
o = pd.read_parquet(ROOT / "analysis" / "2026-10-17" / "out" / "outages_30min.parquet")
print("=== outages_30min", o.index.min(), o.index.max(), list(o.columns))

ex = pd.read_parquet(RAW / "realtime-outages" / "extracts" / "20250815.parquet")
print("\n=== outage extract 20250815", ex.shape)
print(ex.dtypes.to_string())
print(ex[["snapshot_file", "snapshot_label", "listed_at"]].drop_duplicates().to_string())
lst = pd.read_csv(RAW / "realtime-outages" / "listing.csv")
print("listing", lst.shape, list(lst.columns))
print(lst.head().to_string())
man = pd.read_csv(RAW / "realtime-outages" / "manifest.csv")
print("manifest", man.shape, list(man.columns))
print(man.head().to_string())

adv = pd.read_csv(RAW / "market-advisories" / "market-advisories.csv", encoding="cp1252")
print("\n=== advisories", adv.shape, list(adv.columns))
print(adv.head(3).T.to_string())
print(pd.read_csv(RAW / "market-advisories" / "fetched.csv").to_string())

daily = pd.read_csv(ROOT / "analysis" / "2026-10-26" / "supply_tightness" / "out" / "daily.csv")
print("\n=== step1 daily", daily.shape, list(daily.columns))
print(daily["day"].min(), daily["day"].max())
h1 = pd.read_parquet(PROC / "spike_risk_history_0900_d1.parquet")
print("=== risk d1 history", h1.shape, h1["target_date"].min(), h1["target_date"].max(), list(h1.columns))
