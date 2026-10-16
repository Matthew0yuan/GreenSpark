"""Let the real-time market catch spikes: follow the morning plan, but keep a standing offer to
discharge at threshold K. In any 5-min dispatch interval whose price reaches K the battery runs at
full discharge (as a price-taker it is dispatched exactly then); energy is settled per 30-min
trading interval at the Reference Trading Price, as in the WEM. Later planned discharges are cut
when the battery runs short. Energy only, no capacity obligation, plans made at 08:15 (D+0).

K variants: fixed $150/$200/$300, and dynamic K_t = max($150, 1.2 x highest forecast price
for the rest of the trading day) - only sell early when the price beats what is expected later.
"""

import sys

import numpy as np
import pandas as pd

SP = "/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-16"
PROC = "/Users/matthew.yuan/Desktop/HGS/data/processed/"
sys.path.insert(0, SP)
import dispatch_lib as dl  # noqa: E402

H, DT5 = pd.Timedelta(hours=8), 5 / 60
df30 = pd.read_parquet(PROC + "wem_30min.parquet")
p5 = pd.read_parquet(PROC + "wem_5min.parquet", columns=["energy"])["energy"]
KEY = ["issue_date", "ts", "days_ahead"]
val = pd.read_parquet(SP + "/validation_forecasts.parquet")
val["fc"] = (val.aemo_ml_wx_energy + val.ml_wx_energy) / 2
fc = pd.read_parquet(PROC + "forecast_aemo_ml.parquet")
te = fc[fc["product"] == "energy"].merge(pd.read_parquet(SP + "/energy_blend.parquet"), on=KEY)
te["fc"] = (te.aemo_ml_wx + te.ml_wx) / 2
f0 = pd.concat([val[KEY + ["fc"]], te[KEY + ["fc"]]], ignore_index=True)
f0 = f0[f0.days_ahead == 0].set_index("ts")["fc"]


def run(net, price5, rtp, thresholds):
    """Simulate one day at 5-min resolution; thresholds[t] is K for trading interval t (inf = off)."""
    soc, rev, dis = 0.0, 0.0, 0.0
    for t in range(dl.N):
        energy_t = 0.0
        for j in range(6):
            pw = dl.P if price5[6 * t + j] >= thresholds[t] else net[t]
            if pw > 0:
                pw = min(pw, soc * dl.ETA / DT5)
                soc -= pw * DT5 / dl.ETA
                dis += pw * DT5
            else:
                pw = max(pw, -(dl.E - soc) / dl.ETA / DT5)
                soc -= pw * DT5 * dl.ETA
            energy_t += pw * DT5
        rev += rtp[t] * energy_t
    return rev, dis


rows = []
for day in pd.date_range("2025-04-01", "2026-10-15"):
    ts = pd.date_range(day + H, periods=dl.N, freq="30min")
    fcd, rtp = f0.reindex(ts).to_numpy(), df30.energy.reindex(ts).to_numpy()
    pr5 = p5.reindex(pd.date_range(day + H, periods=dl.N * 6, freq="5min")).to_numpy()
    if np.isnan(fcd).any() or np.isnan(rtp).any() or np.isnan(pr5).any():
        continue
    net = dl.plan(fcd)["net"]
    later_max = np.r_[np.maximum.accumulate(fcd[::-1])[::-1][1:], -np.inf]
    variants = {"plan only": np.full(dl.N, np.inf), "K=$150": np.full(dl.N, 150.0), "K=$200": np.full(dl.N, 200.0),
                "K=$300": np.full(dl.N, 300.0), "K dynamic": np.maximum(150.0, 1.2 * later_max)}
    perfect = dl.settle(dl.plan(rtp), rtp, {k: np.zeros(dl.N) for k in dl.FCESS})[0]
    rec = {"day": day, "perfect": perfect}
    for name, k in variants.items():
        rec[name], rec[name + " MWh"] = run(net, pr5, rtp, k)
    rows.append(rec)
r = pd.DataFrame(rows)
rng = np.random.default_rng(0)


def ci(x):
    b = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(4000)]
    return f"{x.mean():+.1f} [{np.percentile(b, 2.5):+.1f}, {np.percentile(b, 97.5):+.1f}]"


for name, lo, hi in [("validation 2025-04..09", "2025-04-01", "2025-10-01"), ("test 2025-10..2026-10", "2025-10-01", "2026-10-16"),
                     ("  since 2026-04", "2026-04-01", "2026-10-16")]:
    x = r[(r.day >= lo) & (r.day < hi)]
    print(f"\n{name} ({len(x)} days): perfect plan {x.perfect.mean():.1f} $/day, plan only {x['plan only'].mean():.1f} "
          f"(discharge {x['plan only MWh'].mean():.2f} MWh/day)")
    for v in ["K=$150", "K=$200", "K=$300", "K dynamic"]:
        d = (x[v] - x["plan only"]).to_numpy()
        print(f"  {v:10s}: {x[v].mean():6.1f} $/day, gain {ci(d)}, days helped {(d > 0.5).mean():.0%} / hurt {(d < -0.5).mean():.0%}, "
              f"discharge {x[v + ' MWh'].mean():.2f} MWh/day")
