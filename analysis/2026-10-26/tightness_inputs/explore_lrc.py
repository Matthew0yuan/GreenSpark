"""List the LRC advisories in the step-1 file (raw text, start/end/issued) to design the trading-day fix.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/explore_lrc.py
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 400)

a = pd.read_csv(ROOT / "data" / "raw" / "market-advisories" / "market-advisories.csv", encoding="cp1252")
print("issued range", a["Issued At"].min(), a["Issued At"].max(), "rows", len(a))
lrc = a[a["Details"].fillna("").str.lower().str.contains("low reserve")].copy()
print("rows mentioning 'low reserve':", len(lrc))
lrc = lrc.sort_values("Issued At")
for _, r in lrc.iterrows():
    print("-" * 100)
    print(r["ID"], "| issued", r["Issued At"], "| start", r["Start Interval"], "| end", r["End Interval"],
          "| withdrawn", r["Withdrawn"], r["Withdrawn At"], "| state", r["Operating State"])
    print(str(r["Details"])[:600])
