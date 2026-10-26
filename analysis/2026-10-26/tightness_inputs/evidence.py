"""Facts quoted in CLAIMS.csv, read straight from the files (raw/ fetches and local data): hashes, issued ranges,
extract times, listing lines.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/evidence.py
"""

import hashlib
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = ROOT / "data" / "raw"
R = HERE / "raw"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


old = RAW / "market-advisories" / "market-advisories.csv"
ref = R / "data.wa.aemo.com.au__public__public-data__datafiles__post-market-advisory__market-advisories.csv"
new = R / "data.wa.aemo.com.au__public__market-data__wemde__marketAdvisory__marketAdvisoryReport__market-advisories.csv"
print("step-1 file sha256", sha(old))
print("refetched post-market-advisory sha256", sha(ref), "identical:", sha(old) == sha(ref))
o = pd.read_csv(ref, encoding="cp1252")
n = pd.read_csv(new, encoding="cp1252")
print("post-market-advisory issued", o["Issued At"].min(), "..", o["Issued At"].max(), len(o))
s1 = pd.read_csv(old, encoding="cp1252")
cols = [c for c in s1.columns if c != "Extracted At"]
a, b = s1[cols].sort_values("ID").reset_index(drop=True), o[cols].sort_values("ID").reset_index(drop=True)
print("same rows except Extracted At:", a.equals(b), "| Extracted At step1", s1["Extracted At"].dropna().unique(),
      "refetch", o["Extracted At"].dropna().unique())
print("wemde report issued", n["Issued At"].min(), "..", n["Issued At"].max(), len(n), "extracted", n["Extracted At"].dropna().unique())
for i in (300031, 300063, 300139):
    r = n[n["Id"] == i].iloc[0]
    m = re.search(r"peak period on [^.]{0,40}", r["Details"])
    print(i, "start", r["Start Interval"], "issued", r["Issued At"], "|", m.group(0) if m else "")
for i in (210752, 210734, 210995, 210590):
    r = o[o["ID"] == i].iloc[0]
    m = re.search(r"(peak period|\(LRC\) for)[^.]{0,60}", str(r["Details"]))
    print(i, "start", r["Start Interval"], "issued", r["Issued At"], "|", m.group(0) if m else str(r["Details"])[:100])

for f in sorted((RAW / "facilities").glob("capacity-credits-*.csv")):
    c = pd.read_csv(f)
    print(f.name, "extracted", c["Extracted At"].dropna().unique(), "last year column", [x for x in c.columns if x[:2] == "20"][-1])
with open(RAW / "facility-scada" / "FacilityScada-2026-10.csv", encoding="utf-8") as fh:
    print("SCADA 2026-10 first data row:", fh.readlines()[1].strip())
for name in ("data.wa.aemo.com.au__public__market-data__wemde__dispatchSolution__preDispatchData__current.html",
             "data.wa.aemo.com.au__public__public-data__datafiles__facility-scada-csv.html",
             "data.wa.aemo.com.au__public__market-data__wemde__marketAdvisory__marketAdvisoryReport.html"):
    text = re.sub(r"<[^>]*>", "", (R / name).read_text().replace("<br>", "\n"))
    hits = [ln.strip() for ln in text.splitlines() if re.search(r"202610260800|FacilityScada-2026-10|market-advisories", ln)]
    print(name, hits)
lst = pd.read_csv(RAW / "realtime-outages" / "listing.csv", parse_dates=["listed_at", "label"])
lag = (lst["listed_at"] - lst["label"]).dt.total_seconds() / 60
print("outage listing lag: files", len(lst), "share <= 1 min", round((lag <= 1).mean(), 4), "max", lag.max())
man = pd.read_csv(RAW / "st-pasa" / "manifest.csv", parse_dates=["listed_at", "member_newest"])
print("st-pasa manifest rows", len(man), "zip rows", int(man["name"].str.lower().str.endswith(".zip").sum()))
