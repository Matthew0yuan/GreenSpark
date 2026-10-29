"""How long one week takes for e5 and its stochastic versions (single process), to size the full run. Uses the old
scenario file (to 2026-10-15); an in-sample week, nothing is chosen on its money.

  .venv/bin/python analysis/2026-10-29/stochastic_v2/timing.py [planner ...]
"""

import sys
import time
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import stochastic as sto  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import harness as hs  # noqa: E402
from vpp.v2 import value as vl  # noqa: E402

WEEK = ("2026-01-12", "2026-01-18")


def main() -> None:
    planners = sys.argv[1:] or ["", "sp"]
    days = bt.load_days("0900")
    sto.attach(days)
    week = [d for d in days if pd.Timestamp(WEEK[0]) <= d["day"] <= pd.Timestamp(WEEK[1])]
    for t in vb.STEPS["e5"]["tables"]:
        vl.build(t[0], t[1], hurdle=t[2], state=len(t) > 3 and t[3])
    bt._init_worker(week, bt.q2b_aux())
    sc = vb.m1()
    for p in planners:
        cfg = replace(vb.E5, name=f"e5 {p or 'roll'}", planner=p, record=False)
        t0 = time.time()
        rows = [(day, s["total"], s.get("planner_fallback"), s.get("plans_short")) for day, _, s, _, _ in hs.route_days(sc, cfg)]
        dt = time.time() - t0
        print(f"{cfg.name}: {len(rows)} days in {dt:.1f} s ({dt / len(rows):.1f} s/day); "
              f"fallback days {sum(r[2] or 0 for r in rows)}, plans short {sum(r[3] or 0 for r in rows)}", flush=True)


if __name__ == "__main__":
    main()
