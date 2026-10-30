"""At hurdle 15: e5 minus v1 by period, with the 10-27 PLAN.md rule (in-sample month-block 95% lower bound > 0).
Reported only: the app keeps v1 (user's choice of scope, 2026-10-28)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
import pandas as pd  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402

d = pd.read_csv(HERE / "daily.csv", parse_dates=["day"])
v = d.pivot_table(index="day", columns="strategy", values="total")
p = d.drop_duplicates("day").set_index("day")["period"]
diff = v[vb.E5.name] - v["forecast+offer(value)"]
for name, g in diff.groupby(p):
    lo, hi = vb.month_ci(g) if g.index.to_period("M").nunique() > 1 else (float("nan"), float("nan"))
    print(f"{name}: {len(g)} days, e5 - v1 {g.mean():.1f} A$/day, month-block 95% [{lo:.1f}, {hi:.1f}], e5 better {int((g > 0).sum())} days")
