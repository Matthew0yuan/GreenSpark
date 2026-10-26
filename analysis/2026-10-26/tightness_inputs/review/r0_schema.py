"""Review helper: schemas of the raw sources (no results)."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
import pandas as pd
ROOT = Path("/Users/matthew.yuan/Desktop/HGS")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40)
for f in ("predispatch", "stpasa", "weather"):
    t = pd.read_parquet(ROOT / "data" / "pit" / f"{f}.parquet")
    print("=====", f, t.shape); print(t.dtypes); print(t.head(3).T)
    if f == "weather":
        print(t["version"].value_counts().head(20)); print(t["facility_or_service"].unique(), t["variable"].unique())
        print(t["record_id"].head(3).tolist())
        x = t.head(5)[["target_time", "run_at", "available_at", "record_id"]]; print(x)
lst = pd.read_csv(ROOT / "data/raw/realtime-outages/listing.csv"); print(lst.head()); print(lst.columns.tolist(), len(lst))
s = pd.read_parquet(ROOT / "data/raw/realtime-outages/extracts/20250601.parquet"); print(s.columns.tolist()); print(s[["snapshot_file", "listed_at", "snapshot_label"]].drop_duplicates())
p = pd.read_csv(sorted((ROOT / "data/raw/predispatch-0800").glob("*.csv"))[400], nrows=5); print(p.T)
m = pd.read_csv(ROOT / "data/raw/st-pasa/manifest.csv"); print(m.head()); print(m.columns.tolist())
sc = pd.read_parquet(ROOT / "analysis/2026-10-17/out/scada_5min.parquet"); print(sc.columns.tolist()); print(sc.index[:3], sc.index[-3:])
stb = pd.read_parquet(ROOT / "data/processed/spike_table.parquet"); print([c for c in stb.columns if c.startswith(("src_", "issue", "target", "days", "pd_", "st_", "wx_", "out_", "bat"))])
