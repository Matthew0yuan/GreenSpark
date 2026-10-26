"""R-A review: how much of M1's in-sample ranking comes from the terms it shares with the label by construction
(thermal Capacity Credits + battery capability, coefficient 1 in both)? Score = -(thermal + bat) alone, and
thermal + bat - registered outage (no demand, no weather). Report only.
Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
"""
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path("/Users/matthew.yuan/Desktop/HGS")
TASK = ROOT / "analysis" / "2026-10-26" / "tightness_forecast"
x = pd.read_csv(TASK / "out" / "daily_predictions.csv", parse_dates=["day"], index_col="day")
d = pd.read_parquet(ROOT / "analysis/2026-10-26/tightness_inputs/out/inputs_d1.parquet").set_index("day")
xi = x[x["period"] == "in_sample"].join(d[["out_thermal_max_1621_mw"]]).dropna(subset=["m1_mw", "margin_min_1621_mw"])
n = len(xi)
k = int(np.floor(0.1 * n + 0.5))
act = xi["margin_min_1621_mw"].rank(method="first") <= k
rows = []
for name, s in (("thermal + battery only", xi["thermal"] + xi["bat"]),
                ("thermal + battery - registered outage max", xi["thermal"] + xi["bat"] - xi["out_thermal_max_1621_mw"]),
                ("M1", xi["m1_mw"]), ("B0 (minus demand)", -xi["pd_req_max_1621_mw"])):
    top = s.rank(method="first") <= k
    rows.append({"score": name, "n": n, "k": k, "hits": int((top & act).sum()),
                 "spearman_vs_label": round(float(s.corr(xi["margin_min_1621_mw"], method="spearman")), 3)})
out = pd.DataFrame(rows)
out.to_csv(TASK / "review" / "f3_mechanical.csv", index=False)
print(out.to_string(index=False))
