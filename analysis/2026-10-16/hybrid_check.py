"""Which FCESS forecast should feed the joint plan? Decide on the validation period (2025-04..09),
then report the same on the test period. Energy is always our blend; FCESS from aemo+ml, 28-day
profile or yesterday."""

import sys

import numpy as np
import pandas as pd

SP = "/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-16"
PROC = "/Users/matthew.yuan/Desktop/HGS/data/processed/"
sys.path.insert(0, SP)
import dispatch_lib as dl  # noqa: E402

H, HALF = pd.Timedelta(hours=8), pd.Timedelta("30min")
df30 = pd.read_parquet(PROC + "wem_30min.parquet")
es = pd.read_parquet(PROC + "esroi.parquet").set_index("trading_date")
rng = np.random.default_rng(0)

# validation forecasts
v = pd.read_parquet(SP + "/validation_forecasts.parquet")
v["ours_energy"] = (v.aemo_ml_wx_energy + v.ml_wx_energy) / 2
src = {}
for da, g in v.groupby("days_ahead"):
    g = g.set_index("ts")
    src["val", da] = {"energy": {"ours": g.ours_energy, "profile28": g.prof28_energy, "baseline": g.prev_energy},
                      **{k: {"aemo_ml": g[f"aemo_ml_{k}"], "profile28": g[f"prof28_{k}"], "baseline": g[f"prev_{k}"]} for k in dl.FCESS}}
# test forecasts
fc = pd.read_parquet(PROC + "forecast_aemo_ml.parquet")
bl = pd.read_parquet(SP + "/energy_blend.parquet")
e = fc[fc["product"] == "energy"].merge(bl, on=["issue_date", "ts", "days_ahead"])
e["ours"] = (e.aemo_ml_wx + e.ml_wx) / 2
for da in (0, 1):
    g = e[e.days_ahead == da].set_index("ts")
    src["test", da] = {"energy": {"ours": g.ours, "profile28": g.profile28, "baseline": g.baseline}}
    for k in dl.FCESS:
        gk = fc[(fc["product"] == k) & (fc.days_ahead == da)].set_index("ts")
        src["test", da][k] = {"aemo_ml": gk.aemo_ml, "profile28": gk.profile28, "baseline": gk.baseline}

COMBOS = {  # name: (energy source, FCESS source)
    "baseline": ("baseline", "baseline"), "profile28": ("profile28", "profile28"),
    "ours(FCESS=aemo_ml)": ("ours", "aemo_ml"), "ours(FCESS=profile28)": ("ours", "profile28"),
    "ours(FCESS=yesterday)": ("ours", "baseline"),
}
rows = []
for split, lo, hi in [("val", "2025-04-01", "2025-09-30"), ("test", "2025-10-01", "2026-10-15")]:
    for day in pd.date_range(lo, hi):
        a = df30.loc[day + H: day + H + pd.Timedelta(hours=23, minutes=30), ["energy"] + dl.FCESS]
        if len(a) != dl.N or a.isna().any().any():
            continue
        af = {k: a[k].to_numpy() for k in dl.FCESS}
        w = es.loc[day]
        win = (int((w.start_ts - day - H) / HALF), int((w.end_ts - day - H) / HALF))
        for oblig in (False, True):
            p = dl.plan(a.energy.to_numpy(), af, win if oblig else None)
            rows.append(dict(split=split, day=day, da=-1, oblig=oblig, method="perfect", total=sum(dl.settle(p, a.energy, af))))
        for da in (0, 1):
            s = src[split, da]
            fe = {m: s["energy"][m].reindex(a.index).to_numpy() for m in ["ours", "profile28", "baseline"]}
            ff = {m: {k: s[k][m].reindex(a.index).to_numpy() for k in dl.FCESS} for m in ["aemo_ml", "profile28", "baseline"]}
            if any(np.isnan(x).any() for x in fe.values()) or any(np.isnan(x).any() for d in ff.values() for x in d.values()):
                continue
            for name, (me, mf) in COMBOS.items():
                pf = {k: np.maximum(ff[mf][k], 0) + 0.01 for k in dl.FCESS}
                for oblig in (False, True):
                    p = dl.plan(fe[me], pf, win if oblig else None)
                    rows.append(dict(split=split, day=day, da=da, oblig=oblig, method=name, total=sum(dl.settle(p, a.energy, af))))
r = pd.DataFrame(rows)
r.to_parquet(SP + "/hybrid_check.parquet")


def ci(x):
    b = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(4000)]
    return f"{x.mean():+.1f} [{np.percentile(b, 2.5):+.1f}, {np.percentile(b, 97.5):+.1f}]"


for split in ("val", "test"):
    for oblig in (False, True):
        for da in (0, 1):
            x = r[(r.split == split) & (r.oblig == oblig) & r.da.isin([-1, da])]
            w = x.pivot_table(index="day", columns="method", values="total").dropna()
            print(f"{split:4s} oblig={oblig!s:5s} D+{da} ({len(w)} days): " + "  ".join(f"{c} {w[c].mean():.1f}" for c in ["perfect"] + list(COMBOS))
                  + f"\n      ours(FCESS=profile28) - ours(FCESS=aemo_ml): {ci((w['ours(FCESS=profile28)'] - w['ours(FCESS=aemo_ml)']).to_numpy())}"
                  + f"   - profile28: {ci((w['ours(FCESS=profile28)'] - w['profile28']).to_numpy())}"
                  + f"   - baseline: {ci((w['ours(FCESS=profile28)'] - w['baseline']).to_numpy())}")
