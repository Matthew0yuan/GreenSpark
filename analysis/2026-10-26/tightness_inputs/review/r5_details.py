"""R-A review: details behind r4 (which day's snapshot is not the newest listed; raw archive ends per source).
Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
"""
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

ROOT = Path("/Users/matthew.yuan/Desktop/HGS")
TASK = ROOT / "analysis" / "2026-10-26" / "tightness_inputs"
RAW = ROOT / "data" / "raw"

d = pd.read_parquet(TASK / "out" / "inputs_d1.parquet").set_index("day")
lst = pd.read_csv(RAW / "realtime-outages" / "listing.csv", parse_dates=["listed_at"])
lst = lst[lst["name"].str.startswith("GeneratorOutages_")].sort_values("listed_at")
for D, r in d[d["out_snapshot"].notna()].iterrows():
    L = lst[lst["listed_at"] <= r["issue_at"]]
    if L["name"].iloc[-1] != r["out_snapshot"]:
        print("not newest:", D.date(), "table", r["out_snapshot"], "newest listed <= issue", L["name"].iloc[-1], L["listed_at"].iloc[-1],
              "| last 4:", L.tail(4)[["name", "listed_at"]].values.tolist())
print("listing last:", lst.tail(2)[["name", "listed_at"]].values.tolist())
pit = pd.read_parquet(ROOT / "data" / "pit" / "predispatch.parquet", columns=["record_id"])
print("pit predispatch last run:", pit["record_id"].max())
st = pd.read_parquet(ROOT / "data" / "pit" / "stpasa.parquet", columns=["record_id", "available_at"])
print("pit stpasa last report:", st["record_id"].max(), st["available_at"].max())
man = pd.read_csv(RAW / "st-pasa" / "manifest.csv")
print("st-pasa manifest last:", man["name"].max())
print("raw st-pasa files (last 3):", sorted(p.name for p in (RAW / "st-pasa").iterdir())[-3:])
sc = pd.read_parquet(ROOT / "analysis" / "2026-10-17" / "out" / "scada_5min.parquet", columns=["battery_dis_mw"])
print("scada_5min last:", sc.index.max())
print("raw facility-scada:", sorted(p.name for p in (RAW / "facility-scada").iterdir())[-3:])
wx = pd.read_parquet(ROOT / "data" / "pit" / "weather.parquet", columns=["target_time"])
print("weather last target:", wx["target_time"].max())
