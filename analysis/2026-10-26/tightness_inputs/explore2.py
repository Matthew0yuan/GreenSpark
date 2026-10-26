"""Data-format checks before PLAN.md: ST PASA interval spacing, Capacity Credits revisions between the two
extracts, pre-dispatch runs public after 09:00, and the facility SCADA files' metadata.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/explore2.py
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "raw"
PIT = ROOT / "data" / "pit"
pd.set_option("display.width", 220)

st = pd.read_parquet(PIT / "stpasa.parquet")
dem = st[st["variable"] == "demand_forecast"]
print("ST PASA demand minutes:", dem["target_time"].dt.minute.value_counts().sort_index().to_dict())
one = dem[(dem["record_id"] == "2025-06-10") & (dem["scenario"] == "Reference")].sort_values("target_time")
print("one report rows", len(one), one["target_time"].min(), one["target_time"].max())
print(one["target_time"].diff().value_counts().head())
dups = dem.groupby("record_id")["version"].nunique()
print("report days with >1 version:", (dups > 1).sum(), list(dups[dups > 1].index[:20]))
aud = pd.read_csv(PIT / "stpasa_audit.csv")
print(aud.columns.tolist())
print("report-day - available lag (h) quantiles:",
      ((pd.to_datetime(aud["available_at"]) - pd.to_datetime(aud["run_at"])).dt.total_seconds() / 3600).quantile([0, .5, .9, .99, 1]).round(2).to_dict())

c6 = pd.read_csv(RAW / "facilities" / "capacity-credits-since-market-start-up-to-capacity-year-2026.csv").set_index("Facility")
c7 = pd.read_csv(RAW / "facilities" / "capacity-credits-since-market-start-up-to-capacity-year-2027.csv").set_index("Facility")
print("extracted", c6["Extracted At"].dropna().unique(), c7["Extracted At"].dropna().unique())
for y in ("2023-24", "2024-25", "2025-26", "2026-27"):
    a, b = c6[y].reindex(c7.index), c7[y]
    diff = (a.fillna(-1) - b.fillna(-1)).abs() > 1e-6
    print(y, "facilities differing:", int(diff.sum()), "sum6", round(a.sum(), 1), "sum7", round(b.sum(), 1),
          list(c7.index[diff][:10]))
print(pd.read_csv(RAW / "facilities" / "fetched.csv").to_string())

pdr = pd.read_parquet(PIT / "predispatch.parquet", columns=["record_id", "evidence_at", "available_at", "run_at"])
runs = pdr.drop_duplicates("record_id").copy()
runs["issue_at"] = runs["run_at"].dt.normalize() + pd.Timedelta(hours=9)
late = runs[runs["available_at"] > runs["issue_at"]]
print("runs:", len(runs), "public after 09:00 on their day:", len(late))
print(late.to_string())
print("runs not labelled 0800:", runs[~runs["record_id"].str.endswith("0800")].to_string())

lst = pd.read_csv(RAW / "realtime-outages" / "listing.csv", parse_dates=["listed_at", "label"])
print("outage listing lag (min) quantiles:", ((lst["listed_at"] - lst["label"]).dt.total_seconds() / 60).quantile([0, .5, .99, 1]).to_dict())
man = pd.read_csv(RAW / "realtime-outages" / "manifest.csv", parse_dates=["issue_date", "listed_at", "label"])
print("manifest:", len(man), man["issue_date"].min(), man["issue_date"].max(), "label times", man["label"].dt.strftime("%H:%M").value_counts().to_dict())
print(list((RAW / "facility-scada").iterdir())[:3])
fs = sorted((RAW / "facility-scada").glob("*"))
print([p.name for p in fs if not p.name.startswith("FacilityScada-")])
