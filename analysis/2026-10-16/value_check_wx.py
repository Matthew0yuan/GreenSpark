"""Battery value check on the saved energy forecasts (1 MW / 2 MWh, 85% RT, <=1 cycle/day, plan once a day)."""

import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds

fc = pd.read_parquet("/Users/matthew.yuan/Desktop/HGS/data/processed/forecast_aemo_ml.parquet")
H = pd.Timedelta(hours=8)
P, E, DT, N = 1.0, 2.0, 0.5, 48
ETA = np.sqrt(0.85)
nv = 4 * N
A, lb, ub = [], [], []
for t in range(N):
    row = np.zeros(nv); row[2 * N + t] = 1; row[t] = -ETA * DT; row[N + t] = DT / ETA
    if t > 0:
        row[2 * N + t - 1] = -1
    A.append(row); lb.append(0); ub.append(0)
for t in range(N):
    row = np.zeros(nv); row[t] = 1; row[3 * N + t] = -P; A.append(row); lb.append(-np.inf); ub.append(0)
    row = np.zeros(nv); row[N + t] = 1; row[3 * N + t] = P; A.append(row); lb.append(-np.inf); ub.append(P)
row = np.zeros(nv); row[N:2 * N] = DT; A.append(row); lb.append(0); ub.append(E)
row = np.zeros(nv); row[3 * N - 1] = 1; A.append(row); lb.append(0); ub.append(0)
CONS = LinearConstraint(np.array(A), lb, ub)
BOUNDS = Bounds(np.zeros(nv), np.r_[np.full(2 * N, P), np.full(N, E), np.ones(N)])
INTEG = np.r_[np.zeros(3 * N), np.ones(N)]


def plan(prices):
    c = np.zeros(nv); c[:N] = prices * DT; c[N:2 * N] = -prices * DT
    return (lambda x: x[N:2 * N] - x[:N])(milp(c, constraints=CONS, bounds=BOUNDS, integrality=INTEG).x)


methods = ["baseline", "profile28", "aemo", "aemo_ml", "aemo_ml_wx"]
rng = np.random.default_rng(0)
e = fc[fc["product"] == "energy"]
for da in (0, 1):
    x = e[e.days_ahead == da].assign(day=lambda d: (d.ts - H).dt.normalize())
    out = []
    for day, d in x.groupby("day"):
        d = d.sort_values("ts")
        if len(d) != N or d[methods].isna().any().any():
            continue
        a = d.actual.to_numpy()
        out.append({"perfect": float((a * plan(a) * DT).sum()),
                    **{k: float((a * plan(d[k].to_numpy()) * DT).sum()) for k in methods}})
    r = pd.DataFrame(out)
    print(f"days_ahead={da} ({len(r)} days) $/day:", r.mean().round(1).to_dict())
    for a, b in [("aemo_ml_wx", "aemo_ml"), ("aemo_ml_wx", "baseline"), ("aemo_ml_wx", "profile28")]:
        diff = (r[a] - r[b]).to_numpy()
        boots = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(4000)]
        print(f"  {a} - {b}: {diff.mean():+.1f} $/day, 95% CI [{np.percentile(boots, 2.5):+.1f}, {np.percentile(boots, 97.5):+.1f}]")
