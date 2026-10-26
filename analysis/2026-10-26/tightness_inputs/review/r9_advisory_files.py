"""R-A review: the fetched raw/ market-advisory.csv (singular, a third AEMO advisory file) - does it carry LRC notices
that the two files used by build_inputs.py miss?  Also checks the CLAIMS quote of the pre-dispatch listing.
Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
"""
import re
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

TASK = Path("/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-26/tightness_inputs")
RAW = TASK / "raw"
f = RAW / "data.wa.aemo.com.au__public__public-data__datafiles__market-advisory__market-advisory.csv"
a = pd.read_csv(f, encoding="cp1252")
print("third file rows", len(a), "issued", a["Issued At"].min(), "..", a["Issued At"].max())
txt = (a["Summary"].fillna("") + " " + a["Details"].fillna("")).str.lower()
l = a[txt.str.contains("low reserve")]
print("rows mentioning 'low reserve':", len(l))
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 160)
print(l[["Issued At", "Summary", "Details"]].to_string(index=False))
ld = pd.read_csv(TASK / "out" / "lrc_days.csv", parse_dates=["day"])
print("lrc_days days:", sorted(ld.loc[ld["lrc"], "day"].dt.strftime("%Y-%m-%d")))
html = (RAW / "data.wa.aemo.com.au__public__market-data__wemde__dispatchSolution__preDispatchData__current.html").read_text(errors="ignore")
m = re.search(r"([^>]{0,60})<A HREF=\"[^\"]*202610260800\.json\"", html, flags=re.I)
print("listing text before the 08:00 run link:", m.group(1) if m else None)
