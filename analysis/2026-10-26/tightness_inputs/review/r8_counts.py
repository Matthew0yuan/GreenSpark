"""R-A review: recount a few RESULTS numbers from raw files (listing lag share, snapshot 08:30 share, ST PASA D-2 share).
Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
"""
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

ROOT = Path("/Users/matthew.yuan/Desktop/HGS")
TASK = ROOT / "analysis" / "2026-10-26" / "tightness_inputs"
lst = pd.read_csv(ROOT / "data/raw/realtime-outages/listing.csv", parse_dates=["listed_at", "label"])
lag = (lst["listed_at"] - lst["label"]).dt.total_seconds() / 60
print("listing rows", len(lst), "share lag <= 1 min", round((lag <= 1).mean(), 4), "max lag min", lag.max())
d = pd.read_parquet(TASK / "out" / "inputs_d1.parquet").set_index("day")
x = d[d.index <= "2026-10-15"]
s = x["out_snapshot"].dropna()
print("snapshots to 10-15:", len(s), "0830:", int(s.str.contains("0830.csv").sum()), "0800:", int(s.str.contains("0800.csv").sum()))
a = x["st_report_age_days"].dropna()
print("ST PASA to 10-15:", len(a), "age 2:", int((a == 2).sum()))
p = x["pd_run"].dropna()
print("pre-dispatch to 10-15:", len(p), "08:00 runs:", int(p.str.endswith("0800").sum()))
w = x["wx_newest_run"].dropna()
print("weather to 10-15:", len(w), "newest = D-2 12Z:",
      int(sum(r.endswith(f"{(D - pd.Timedelta(days=2)):%Y%m%d}12Z") for D, r in w.items())))
print("table LRC days", int(d["lrc"].sum()), "by period", d.groupby("period")["lrc"].sum().to_dict())
