"""R-A review: how close each source's assumed public time is to the 09:00 issue (sensitivity of the assumptions).
Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
"""
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

TASK = Path("/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-26/tightness_inputs")
d = pd.read_parquet(TASK / "out" / "inputs_d1.parquet").set_index("day")
rows = []
for src, col in (("predispatch", "predispatch_available_at"), ("outages", "outages_available_at"),
                 ("stpasa", "stpasa_available_at"), ("weather", "weather_available_at")):
    v = pd.to_datetime(d[col])
    slack = (d["issue_at"] - v).dt.total_seconds() / 60
    s = slack.dropna()
    rows.append({"source": src, "days": len(s), "min_slack_min": round(s.min(), 1), "median_slack_min": round(s.median(), 1),
                 "days_slack_lt_15min": int((s < 15).sum()), "days_slack_lt_30min": int((s < 30).sum()),
                 "days_slack_lt_60min": int((s < 60).sum())})
out = pd.DataFrame(rows)
# pre-dispatch issue-ID times of the runs used
iss = pd.to_datetime(d["predispatch_available_at"]) - pd.Timedelta(minutes=20)
print(iss.dt.strftime("%H:%M").value_counts().head(10))
out.to_csv(TASK / "review" / "r7_sensitivity.csv", index=False)
print(out.to_string(index=False))
by_period = d.assign(slack=(d["issue_at"] - pd.to_datetime(d["predispatch_available_at"])).dt.total_seconds() / 60)
print(by_period.groupby("period")["slack"].describe())
