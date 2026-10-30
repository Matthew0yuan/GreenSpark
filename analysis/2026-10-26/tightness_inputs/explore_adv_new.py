"""Shape of the fetched WEMDE market advisory file: columns, issued range, overlap with the step-1 file, LRC rows.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/explore_adv_new.py
"""

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 500)

new = pd.read_csv(HERE / "raw" / "data.wa.aemo.com.au__public__market-data__wemde__marketAdvisory__marketAdvisoryReport__market-advisories.csv",
                  encoding="cp1252").rename(columns={"Id": "ID"})
old = pd.read_csv(ROOT / "data" / "raw" / "market-advisories" / "market-advisories.csv", encoding="cp1252")
print("new", new.shape, list(new.columns))
print("old", old.shape, list(old.columns))
print("new issued", new["Issued At"].min(), new["Issued At"].max())
print("new extracted", new["Extracted At"].dropna().unique()[:5])
print("ID overlap", len(set(new["ID"]) & set(old["ID"])), "new-only", len(set(new["ID"]) - set(old["ID"])))
both = new.merge(old, on="ID", suffixes=("_new", "_old"))
for c in ("Start Interval", "End Interval", "Issued At", "Details", "Withdrawn"):
    if f"{c}_new" in both:
        print(c, "differs on", int((both[f"{c}_new"].astype(str) != both[f"{c}_old"].astype(str)).sum()), "of", len(both))
lrc = new[new["Details"].fillna("").str.lower().str.contains("low reserve")].sort_values("Issued At")
print("new rows mentioning low reserve:", len(lrc))
for _, r in lrc.iterrows():
    print("-" * 80)
    print(r["ID"], "| issued", r["Issued At"], "| start", r["Start Interval"], "| end", r["End Interval"], "| withdrawn", r.get("Withdrawn"))
    print(str(r["Details"])[:500])
