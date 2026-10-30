"""R-A review I3: do planted leaks really trip the checks? The delivered P1-P5 only test A1 (re-derived timestamps from
provenance columns). Here each leaky variant (D's own snapshot, D's own pre-dispatch run, fixed previous_day1 weather,
ST PASA report dated D-1) is also pushed through the A5 scrambled-future check, which looks at values, not labels.
Also re-runs the delivered A5 (no variant) on a fresh random sample of days.

Imports build_inputs / leak_audit read only; writes only review/r2_controls.csv.
Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
"""
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

TASK = Path("/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-26/tightness_inputs")
sys.path.insert(0, str(TASK))

import build_inputs as bi  # noqa: E402
import leak_audit as la  # noqa: E402


def main() -> None:
    d = pd.read_parquet(TASK / "out" / "inputs_d1.parquet").set_index("day")
    pdr, st, wx, day_bat = bi.load_sources()
    rng = np.random.default_rng(777)
    full = d.index[d["inputs_complete"]]
    days = pd.DatetimeIndex(rng.choice(full, size=12, replace=False)).sort_values()
    rows = []
    for name, variant in (("A5 delivered (fresh 12 days)", None),
                          ("P1 via A5: D's own snapshot", {"snap_lag": 0}),
                          ("P3 via A5: D's own pre-dispatch run", {"pd_run_lag": 0, "pd_enforce": False}),
                          ("P3b via A5: D-1 run, no 09:00 check", {"pd_enforce": False}),
                          ("P4 via A5: fixed previous_day1 weather", {"wx_rule": "previous_day1"}),
                          ("P5 via A5: ST PASA dated D-1", {"st_rule": "by_date"}),
                          ("P7 via A5: battery shift(1)", {"bat_lag": 1})):
        n, ch = la.scramble(days, pdr, st, wx, day_bat, variant)
        rows.append({"check": name, "days": len(days), "values": n, "changed": ch})
        print(rows[-1], flush=True)
    out = pd.DataFrame(rows)
    out["days_list"] = ",".join(str(x.date()) for x in days)
    out.to_csv(TASK / "review" / "r2_controls.csv", index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
