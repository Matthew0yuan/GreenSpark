"""Median (L1) vs expected-value (L2) energy forecasts: MAE and dispatch money, validation and test."""

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
KEY = ["issue_date", "ts", "days_ahead"]

l2 = pd.read_parquet(SP + "/energy_l2.parquet")
l2["l2"] = (l2.aemo_ml_wx_l2 + l2.ml_wx_l2) / 2
val = pd.read_parquet(SP + "/validation_forecasts.parquet")
val["l1"] = (val.aemo_ml_wx_energy + val.ml_wx_energy) / 2
fc = pd.read_parquet(PROC + "forecast_aemo_ml.parquet")
te = fc[fc["product"] == "energy"].merge(pd.read_parquet(SP + "/energy_blend.parquet"), on=KEY)
te["l1"] = (te.aemo_ml_wx + te.ml_wx) / 2
l1 = pd.concat([val[KEY + ["l1"]], te[KEY + ["l1"]]], ignore_index=True)
en = l1.merge(l2[KEY + ["l2"]], on=KEY)
en["actual"] = df30.energy.reindex(en.ts).to_numpy()
# FCESS planning prices: 28-day profile (the choice made on validation)
prof = pd.concat([val[KEY + [f"prof28_{k}" for k in dl.FCESS]].rename(columns={f"prof28_{k}": k for k in dl.FCESS}),
                  fc[fc["product"].isin(dl.FCESS)].pivot_table(index=KEY, columns="product", values="profile28").reset_index()],
                 ignore_index=True)
en = en.merge(prof, on=KEY)

rows = []
for da in (0, 1):
    for day, g in en[en.days_ahead == da].groupby(pd.DatetimeIndex(en.loc[en.days_ahead == da, "ts"]).map(lambda t: (t - H).normalize())):
        g = g.sort_values("ts")
        a = df30.loc[g.ts, ["energy"] + dl.FCESS]
        if len(g) != dl.N or a.isna().any().any() or g[["l1", "l2"] + dl.FCESS].isna().any().any():
            continue
        af = {k: a[k].to_numpy() for k in dl.FCESS}
        w = es.loc[day]
        win = (int((w.start_ts - day - H) / HALF), int((w.end_ts - day - H) / HALF))
        pf = {k: np.maximum(g[k].to_numpy(), 0) + 0.01 for k in dl.FCESS}
        rec = dict(day=day, da=da)
        for m in ("l1", "l2"):
            rec[f"{m}_mae"] = np.abs(g[m].to_numpy() - a.energy.to_numpy()).mean()
            rec[f"{m}_energy_only"] = dl.settle(dl.plan(g[m].to_numpy()), a.energy, af)[0]
            rec[f"{m}_joint_oblig"] = sum(dl.settle(dl.plan(g[m].to_numpy(), pf, win), a.energy, af))
        rows.append(rec)
r = pd.DataFrame(rows)
r.to_parquet(SP + "/target_dispatch.parquet")


def ci(x):
    b = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(4000)]
    return f"{x.mean():+.1f} [{np.percentile(b, 2.5):+.1f}, {np.percentile(b, 97.5):+.1f}]"


for name, lo, hi in [("validation 2025-04..09", "2025-04-01", "2025-10-01"), ("test 2025-10..2026-10", "2025-10-01", "2026-10-16"),
                     ("  of which since 2026-04", "2026-04-01", "2026-10-16")]:
    for da in (0, 1):
        x = r[(r.da == da) & (r.day >= lo) & (r.day < hi)]
        print(f"{name} D+{da} ({len(x)} days): MAE L1 {x.l1_mae.mean():.2f} vs L2 {x.l2_mae.mean():.2f} | "
              f"energy-only $/day L1 {x.l1_energy_only.mean():.1f} L2 {x.l2_energy_only.mean():.1f} diff {ci((x.l2_energy_only - x.l1_energy_only).to_numpy())} | "
              f"joint+obligation L1 {x.l1_joint_oblig.mean():.1f} L2 {x.l2_joint_oblig.mean():.1f} diff {ci((x.l2_joint_oblig - x.l1_joint_oblig).to_numpy())}")
