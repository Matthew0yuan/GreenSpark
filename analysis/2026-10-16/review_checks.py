"""Review checks for the AEMO + ML forecaster (read-only; writes nothing into the project)."""

import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.optimize import milp, LinearConstraint, Bounds

sys.path.insert(0, "/Users/matthew.yuan/Desktop/HGS")
from vpp import aemo_correction as ac  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402
from vpp.evaluate import TEST_START  # noqa: E402

pd.set_option("display.width", 200)
df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
frame = ac.build_frame(df30, runs)
fc = pd.read_parquet(PROCESSED / "forecast_aemo_ml.parquet")
H = pd.Timedelta(hours=8)

# ---------------------------------------------------------------- 1. leakage audit
print("=== 1. Feature audit: recompute features from raw data for random rows")
aemo0 = runs[runs.days_ahead == 0].set_index("ts")["aemo_energy"]
rng = np.random.default_rng(0)
sample = frame[frame.days_ahead.isin([0, 1]) & (frame.target_date >= "2024-01-01")].sample(400, random_state=1)
bad, latest_used = 0, []
for _, r in sample.iterrows():
    def at(k):  # same slot, k days before issue_date
        return r.issue_date - pd.Timedelta(days=k) + H + pd.Timedelta(minutes=30 * r.slot)
    t1 = at(1)
    exp = {
        "prev_energy": df30.energy.get(t1, np.nan),
        "prev7_energy": np.nanmean([df30.energy.get(at(k), np.nan) for k in range(1, 8)]),
        "aemo_err1_energy": df30.energy.get(t1, np.nan) - aemo0.get(t1, np.nan),
        "aemo_err7_energy": np.nanmean([df30.energy.get(at(k), np.nan) - aemo0.get(at(k), np.nan) for k in range(1, 8)]),
    }
    latest_used.append(t1 + pd.Timedelta(minutes=30) - (r.issue_date + H))  # interval end minus issue-day start
    for k, v in exp.items():
        if not np.isclose(r[k], v, equal_nan=True):
            bad += 1
print(f"mismatches: {bad} / {len(sample) * 4};  latest actual used ends at most "
      f"{max(latest_used)} relative to 08:00 of the issue day (must be <= 0)")

print("\n=== 1b. Walk-forward cutoff audit (energy)")
for da in (0, 1):
    data = frame[(frame.days_ahead == da) & frame.actual_energy.notna()]
    worst = pd.Timedelta.min
    for m in pd.date_range(TEST_START.normalize(), data.target_date.max(), freq="MS"):
        train = data.target_date < m - pd.Timedelta(days=da)
        test = (data.target_date >= m) & (data.target_date < m + pd.offsets.MonthBegin(1))
        if not test.any():
            continue
        label_known = data.loc[train, "target_date"].max() + pd.Timedelta(days=1) + H  # end of last train day
        first_issue = data.loc[test, "issued_at"].min()
        worst = max(worst, label_known - first_issue)
    print(f"days_ahead={da}: latest training label becomes known {worst} relative to first test issue (must be <= 0)")

late = runs.groupby("issue_date").agg(issued=("issued_at", "first"))
late = late[late.issued - late.index - pd.Timedelta("8h15min") > pd.Timedelta("10min")]
print(f"\nruns issued >10 min after 08:15: {len(late)}, in test period: {(late.index >= TEST_START.normalize()).sum()}")

# ---------------------------------------------------------------- 2. robustness
print("\n=== 2a. Monthly energy MAE")
e = fc[fc["product"] == "energy"].copy()
e["month"] = (e.ts - H).dt.to_period("M")
for da in (0, 1):
    g = e[e.days_ahead == da].groupby("month")
    m = pd.DataFrame({k: g.apply(lambda x, k=k: (x[k] - x.actual).abs().mean()) for k in ["baseline", "aemo", "aemo_ml"]})
    print(f"days_ahead={da}: aemo+ml beats baseline in {(m.aemo_ml < m.baseline).sum()}/{len(m)} months")
    print(m.round(1).T.to_string())

print("\n=== 2b. Daily paired comparison + block bootstrap (resample days)")
for da in (0, 1):
    x = e[e.days_ahead == da].assign(day=lambda d: (d.ts - H).dt.normalize())
    daily = x.groupby("day").apply(lambda d: pd.Series({k: (d[k] - d.actual).abs().mean() for k in ["baseline", "aemo", "aemo_ml"]}))
    win = (daily.aemo_ml < daily.baseline).mean()
    boots = []
    for _ in range(2000):
        s = daily.sample(len(daily), replace=True, random_state=rng.integers(1e9))
        boots.append(1 - s.aemo_ml.mean() / s.baseline.mean())
    lo, hi = np.percentile(boots, [2.5, 97.5])
    print(f"days_ahead={da}: wins on {win:.0%} of {len(daily)} days; skill vs baseline 95% CI [{lo:.1%}, {hi:.1%}]")

print("\n=== 2c. RMSE (energy)")
t = pd.read_csv("/Users/matthew.yuan/Desktop/HGS/reports/aemo_correction_metrics.csv")
print(t[t["product"] == "energy"].pivot(index="days_ahead", columns="method", values="rmse").round(1).to_string())

print("\n=== 2d. Day-0 MAE without the in-progress 08:00 interval (and without 08:00-08:30)")
x = e[e.days_ahead == 0]
slot = ac.trading_slot(pd.DatetimeIndex(x.ts))
for skip in (0, 1, 2):
    y = x[slot >= skip]
    print(f"  skip first {skip} slots: " + "  ".join(f"{k} {(y[k] - y.actual).abs().mean():.2f}" for k in ["baseline", "aemo", "aemo_ml"]))

# ---------------------------------------------------------------- 3. value check
print("\n=== 3. Battery value check (1 MW / 2 MWh, 85% round trip, <=1 cycle per trading day)")
print("    plan each trading day on the forecast, settle on actual RTP; MILP with no simultaneous charge/discharge")
orig = ac.features_for
ac.features_for = lambda p: [c for c in orig(p) if not c.startswith("aemo")]
ml_only = {}
for da in (0, 1):
    f2 = frame.copy()
    f2["aemo_energy"] = 0.0
    pred = ac.walk_forward(f2, "energy", da)
    ml_only[da] = frame.loc[pred.index].assign(ml_only=pred.values).set_index(["issue_date", "ts"])["ml_only"]
ac.features_for = orig

P, E, DT = 1.0, 2.0, 0.5
ETA = np.sqrt(0.85)
N = 48
# variables: c[0:N], d[N:2N], s[2N:3N], u[3N:4N] (u=1 charging allowed)
nv = 4 * N
A, lb, ub = [], [], []
for t in range(N):  # s_t - s_{t-1} - eta*dt*c_t + dt/eta*d_t = 0  (s_{-1} = 0)
    row = np.zeros(nv); row[2 * N + t] = 1; row[t] = -ETA * DT; row[N + t] = DT / ETA
    if t > 0:
        row[2 * N + t - 1] = -1
    A.append(row); lb.append(0); ub.append(0)
for t in range(N):  # c_t <= P u_t ; d_t <= P (1 - u_t)
    row = np.zeros(nv); row[t] = 1; row[3 * N + t] = -P; A.append(row); lb.append(-np.inf); ub.append(0)
    row = np.zeros(nv); row[N + t] = 1; row[3 * N + t] = P; A.append(row); lb.append(-np.inf); ub.append(P)
row = np.zeros(nv); row[N:2 * N] = DT; A.append(row); lb.append(0); ub.append(E)  # <= 1 cycle
row = np.zeros(nv); row[3 * N - 1] = 1; A.append(row); lb.append(0); ub.append(0)  # end empty
CONS = LinearConstraint(np.array(A), lb, ub)
BOUNDS = Bounds(np.r_[np.zeros(3 * N), np.zeros(N)], np.r_[np.full(2 * N, P), np.full(N, E), np.ones(N)])
INTEG = np.r_[np.zeros(3 * N), np.ones(N)]


def plan(prices: np.ndarray) -> np.ndarray:
    cobj = np.zeros(nv); cobj[:N] = prices * DT; cobj[N:2 * N] = -prices * DT  # minimise cost
    res = milp(cobj, constraints=CONS, bounds=BOUNDS, integrality=INTEG)
    return res.x[N:2 * N] - res.x[:N]  # net discharge MW


for da in (0, 1):
    x = e[e.days_ahead == da].set_index(["issue_date", "ts"]).join(ml_only[da]).reset_index()
    x["day"] = (x.ts - H).dt.normalize()
    rev = {k: [] for k in ["perfect", "baseline", "aemo", "aemo_ml", "ml_only"]}
    for _, d in x.groupby("day"):
        if len(d) != N or d[["baseline", "aemo", "aemo_ml", "ml_only"]].isna().any().any():
            continue
        d = d.sort_values("ts")
        actual = d.actual.to_numpy()
        for k in rev:
            net = plan(actual if k == "perfect" else d[k].to_numpy())
            rev[k].append(float((actual * net * DT).sum()))
    r = pd.DataFrame(rev)
    summary = pd.DataFrame({"$/day": r.mean(), "capture_vs_perfect": r.mean() / r.perfect.mean()})
    print(f"days_ahead={da} ({len(r)} days):")
    print(summary.round(3).T.to_string())

# ---------------------------------------------------------------- 4. explanation material
print("\n=== 4a. What the energy correction model relies on (gain share, trained on pre-test data)")
for da in (0, 1):
    data = frame[(frame.days_ahead == da) & frame.actual_energy.notna() & (frame.target_date < TEST_START.normalize())]
    cols = ac.features_for("energy")
    mdl = lgb.LGBMRegressor(**ac.LGB_PARAMS, importance_type="gain").fit(data[cols], data.actual_energy - data.aemo_energy)
    imp = pd.Series(mdl.feature_importances_, index=cols)
    print(f"days_ahead={da}, {len(data):,} training rows:", (imp / imp.sum()).sort_values(ascending=False).head(8).round(3).to_dict())

print("\n=== 4b. Worked example: 18:00 interval, one test day")
row = frame[(frame.issue_date == "2026-03-10") & (frame.slot == 20)]
fx = fc[(fc["product"] == "energy") & (fc.issue_date == "2026-03-10")].set_index(["ts"])
cols = ["issue_date", "ts", "days_ahead", "aemo_energy", "prev_energy", "prev7_energy", "aemo_err1_energy",
        "aemo_err7_energy", "aemo_req_energy_mw", "aemo_available_injection_mw", "actual_energy"]
ex = row[cols].set_index("ts").join(fx[["aemo_ml"]])
ex["correction"] = ex.aemo_ml - ex.aemo_energy
print(ex.T.to_string())
