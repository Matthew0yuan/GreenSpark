"""Follow-up review checks: AEMO D+1 regime change, and revenue differences with bootstrap CIs."""

import sys

import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds

sys.path.insert(0, "/Users/matthew.yuan/Desktop/HGS")
from vpp import aemo_correction as ac  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402

pd.set_option("display.width", 200)
H = pd.Timedelta(hours=8)
df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
frame = ac.build_frame(df30, runs)
fc = pd.read_parquet(PROCESSED / "forecast_aemo_ml.parquet")

print("=== A. AEMO energy forecast by month and horizon (test period)")
f = frame[(frame.target_date >= "2025-10-01") & frame.actual_energy.notna()].copy()
f["month"] = f.target_date.dt.to_period("M")
g = f.groupby(["days_ahead", "month"])
summary = pd.DataFrame({
    "bias": g.apply(lambda x: (x.aemo_energy - x.actual_energy).mean()),
    "share>=500": g.apply(lambda x: (x.aemo_energy >= 500).mean()),
    "share_deficit": g.apply(lambda x: (x.aemo_energy_deficit_mw > 0).mean()),
    "actual>=500": g.apply(lambda x: (x.actual_energy >= 500).mean()),
})
print(summary.round(3).unstack("days_ahead").to_string())
d1 = f[f.days_ahead == 1]
print("\nD+1 AEMO >= $500 by hour (share of intervals):")
print(d1.groupby(d1.ts.dt.hour).apply(lambda x: (x.aemo_energy >= 500).mean()).round(3).to_dict())

print("\n=== B. Battery value with paired bootstrap (resample days)")
orig = ac.features_for
ac.features_for = lambda p: [c for c in orig(p) if not c.startswith("aemo")]
ml_only = {}
for da in (0, 1):
    f2 = frame.copy()
    f2["aemo_energy"] = 0.0
    pred = ac.walk_forward(f2, "energy", da)
    ml_only[da] = frame.loc[pred.index].assign(ml_only=pred.values).set_index(["issue_date", "ts"])["ml_only"]
ac.features_for = orig

# 28-day average price profile by slot from the last complete days (a "fixed schedule" heuristic)
act = ac._by_day_slot(df30["energy"])
prof28 = act.rolling(28, min_periods=14).mean()

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
    cobj = np.zeros(nv); cobj[:N] = prices * DT; cobj[N:2 * N] = -prices * DT
    res = milp(cobj, constraints=CONS, bounds=BOUNDS, integrality=INTEG)
    return res.x[N:2 * N] - res.x[:N]


rng = np.random.default_rng(0)
e = fc[fc["product"] == "energy"]
for da in (0, 1):
    x = e[e.days_ahead == da].set_index(["issue_date", "ts"]).join(ml_only[da]).reset_index()
    x["day"] = (x.ts - H).dt.normalize()
    out = []
    for day, d in x.groupby("day"):
        d = d.sort_values("ts")
        if len(d) != N or d[["baseline", "aemo", "aemo_ml", "ml_only"]].isna().any().any():
            continue
        last_complete = d.issue_date.iloc[0] - pd.Timedelta(days=1)
        if last_complete not in prof28.index or prof28.loc[last_complete].isna().any():
            continue
        actual = d.actual.to_numpy()
        fcs = {"perfect": actual, "profile28": prof28.loc[last_complete].to_numpy(),
               **{k: d[k].to_numpy() for k in ["baseline", "aemo", "aemo_ml", "ml_only"]}}
        out.append({"day": day, **{k: float((actual * plan(v) * DT).sum()) for k, v in fcs.items()}})
    r = pd.DataFrame(out).set_index("day")
    print(f"\ndays_ahead={da}, {len(r)} days; $/day:", r.mean().round(1).to_dict())

    def ci(a, b):
        diff = (r[a] - r[b]).to_numpy()
        boots = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(4000)]
        return f"{a} - {b}: {diff.mean():+.1f} $/day, 95% CI [{np.percentile(boots, 2.5):+.1f}, {np.percentile(boots, 97.5):+.1f}]"

    for a, b in [("aemo_ml", "baseline"), ("ml_only", "aemo_ml"), ("aemo_ml", "profile28"), ("ml_only", "profile28"), ("aemo", "baseline")]:
        print("  " + ci(a, b))
    q = r.copy()
    q["month"] = q.index.to_period("M")
    print("  monthly $/day:")
    print(q.groupby("month").mean().round(0).T.to_string())
