"""Energy: aemo+ml+wx vs ml+wx (no AEMO) vs their 50/50 blend, on validation and test periods, plus value."""

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/matthew.yuan/Desktop/HGS")
sys.path.insert(0, "/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-16")
from vpp import aemo_correction as ac  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402

H = pd.Timedelta(hours=8)
df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
wx = pd.read_parquet(PROCESSED / "weather_30min.parquet")
frame = ac.build_frame(df30, runs, wx)
orig = ac.features_for
ac.TEST_START = pd.Timestamp("2025-04-01 08:00")  # walk forward through validation AND test months

preds = {}
for da in (0, 1):
    p_aemo = ac.walk_forward(frame, "energy", da, weather=True) + frame["aemo_energy"]
    ac.features_for = lambda p, weather=False: [c for c in orig(p, True) if not c.startswith("aemo")]
    f2 = frame.copy(); f2["aemo_energy"] = 0.0
    p_ml = ac.walk_forward(f2, "energy", da)
    ac.features_for = orig
    idx = p_aemo.index.intersection(p_ml.index)
    preds[da] = pd.DataFrame({
        "issue_date": frame.loc[idx, "issue_date"], "ts": frame.loc[idx, "ts"], "target_date": frame.loc[idx, "target_date"],
        "actual": frame.loc[idx, "actual_energy"], "baseline": frame.loc[idx, "prev_energy"],
        "profile28": frame.loc[idx, "prof28_energy"], "aemo_ml_wx": p_aemo[idx], "ml_wx": p_ml[idx],
    }).dropna()
    preds[da]["blend"] = (preds[da]["aemo_ml_wx"] + preds[da]["ml_wx"]) / 2

cols = ["baseline", "profile28", "aemo_ml_wx", "ml_wx", "blend"]
for name, lo, hi in [("validation 2025-04..09", "2025-04-01", "2025-10-01"), ("test 2025-10..2026-10", "2025-10-01", "2027-01-01")]:
    print(f"=== {name}")
    for da in (0, 1):
        x = preds[da][(preds[da].target_date >= lo) & (preds[da].target_date < hi)]
        print(f"  days_ahead={da}: " + "  ".join(
            f"{k} {(x[k]-x.actual).abs().mean():.2f}/{np.sqrt(((x[k]-x.actual)**2).mean()):.1f}" for k in cols) + "   (MAE/RMSE)")

# monthly MAE on test for D+1
x = preds[1][preds[1].target_date >= "2025-10-01"].copy()
x["m"] = x.target_date.dt.to_period("M")
print("\nD+1 monthly MAE (test):")
print(x.groupby("m").apply(lambda g: pd.Series({k: (g[k]-g.actual).abs().mean() for k in ["profile28", "aemo_ml_wx", "ml_wx", "blend"]})).round(1).T.to_string())

# value on the test period
import value_check_wx as vc  # noqa: E402  (prints its own table for saved forecasts first)
rng = np.random.default_rng(0)
print("\n=== Value on test period ($/day, 1 MW / 2 MWh)")
for da in (0, 1):
    x = preds[da][preds[da].target_date >= "2025-10-01"].assign(day=lambda d: (d.ts - H).dt.normalize())
    out = []
    for _, d in x.groupby("day"):
        d = d.sort_values("ts")
        if len(d) != vc.N:
            continue
        a = d.actual.to_numpy()
        out.append({k: float((a * vc.plan(d[k].to_numpy()) * vc.DT).sum()) for k in ["profile28", "aemo_ml_wx", "ml_wx", "blend"]})
    r = pd.DataFrame(out)
    print(f"days_ahead={da} ({len(r)} days):", r.mean().round(1).to_dict())
    for a, b in [("blend", "aemo_ml_wx"), ("blend", "profile28")]:
        diff = (r[a] - r[b]).to_numpy()
        boots = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(4000)]
        print(f"  {a} - {b}: {diff.mean():+.1f}, 95% CI [{np.percentile(boots, 2.5):+.1f}, {np.percentile(boots, 97.5):+.1f}]")
