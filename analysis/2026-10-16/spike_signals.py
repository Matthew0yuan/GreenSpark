"""How predictable are price spikes at 08:15 for the same trading day (D+0)?
Univariate AUCs of candidate signals, then a walk-forward LightGBM spike classifier."""

import sys

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

sys.path.insert(0, "/Users/matthew.yuan/Desktop/HGS")
from vpp import aemo_correction as ac  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402

df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
wx = pd.read_parquet(PROCESSED / "weather_30min.parquet")
f = ac.build_frame(df30, runs, wx)
f = f[f.days_ahead == 0].copy()
print(f[ac.AEMO_SYSTEM].describe().loc[["mean", "min", "max"]].round(0).to_string())

f["margin_avail"] = f.aemo_available_injection_mw - f.aemo_req_energy_mw
f["margin_inservice"] = f.aemo_in_service_injection_mw - f.aemo_req_energy_mw
f["net_demand_fc"] = f.aemo_demand_level_mw - f.aemo_dpv_level_mw
daily_max = df30.energy.groupby((df30.index - pd.Timedelta(hours=8)).normalize()).max()
spike_day = (daily_max >= 300).astype(float)
last = f.issue_date - pd.Timedelta(days=1)
f["yday_max"] = daily_max.reindex(last).to_numpy()
f["spike_days_7"] = spike_day.rolling(7).sum().reindex(last).to_numpy()
f["day_margin_min"] = f.groupby("issue_date").margin_avail.transform("min")
f["day_aemo_max"] = f.groupby("issue_date").aemo_energy.transform("max")
SIGNALS = ["aemo_energy", "day_aemo_max", "margin_avail", "margin_inservice", "day_margin_min", "aemo_energy_deficit_mw",
           "net_demand_fc", "wx_ghi", "wx_ghi_day", "wx_temp", "wx_tmax_day", "wx_wind_evening", "prev_energy", "yday_max", "spike_days_7"]

periods = {"validation": ("2025-04-01", "2025-10-01"), "test": ("2025-10-01", "2026-10-16")}
for thr in (200, 300):
    print(f"\n=== spike = interval price >= ${thr}")
    for name, (lo, hi) in periods.items():
        x = f[(f.target_date >= lo) & (f.target_date < hi)].dropna(subset=["actual_energy"])
        y = (x.actual_energy >= thr).to_numpy()
        res = {}
        nan = x[SIGNALS].isna().mean()
        if (nan > 0.5).any():
            print("  mostly-missing signals:", nan[nan > 0.5].round(2).to_dict())
        for s in SIGNALS:
            ok = x[s].notna().to_numpy()
            if not ok.any():
                continue
            auc = roc_auc_score(y[ok], x[s].to_numpy()[ok])
            res[s] = f"{max(auc, 1 - auc):.2f}{'+' if auc >= 0.5 else '-'}"
        print(f"  {name}: {y.sum()} spike intervals ({y.mean():.2%}) | AUC (+ = higher means more spikes): {res}")

# walk-forward spike classifier, monthly refits, same rule as the price models
FEATS = SIGNALS + [c for c in ac.features_for("energy", True) if c not in SIGNALS]
for thr in (200, 300):
    preds = []
    for m in pd.date_range("2025-04-01", "2026-10-01", freq="MS"):
        tr = f[(f.target_date < m) & f.actual_energy.notna()]
        te = f[(f.target_date >= m) & (f.target_date < m + pd.offsets.MonthBegin(1)) & f.actual_energy.notna()]
        if te.empty:
            continue
        clf = lgb.LGBMClassifier(objective="binary", n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=50,
                                 subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=0, n_jobs=4)
        clf.fit(tr[FEATS], (tr.actual_energy >= thr).astype(int))
        preds.append(pd.DataFrame({"p": clf.predict_proba(te[FEATS])[:, 1], "y": (te.actual_energy >= thr).to_numpy(),
                                   "day": te.target_date.to_numpy(), "ts": te.ts.to_numpy()}))
    pr = pd.concat(preds, ignore_index=True)
    print(f"\n=== classifier, spike >= ${thr}")
    for name, (lo, hi) in periods.items():
        x = pr[(pr.day >= lo) & (pr.day < hi)]
        top = x.p >= x.p.quantile(0.98)
        d = x.groupby("day").agg(p=("p", "max"), y=("y", "max"))
        dtop = d.p >= d.p.quantile(0.8)
        print(f"  {name}: interval AUC {roc_auc_score(x.y, x.p):.3f}, avg precision {average_precision_score(x.y, x.p):.3f} (base {x.y.mean():.3f}); "
              f"top-2% alerts catch {x.y[top].sum() / x.y.sum():.0%} of spikes at precision {x.y[top].mean():.0%} | "
              f"day AUC {roc_auc_score(d.y, d.p):.3f}: top-20% days hold {d.y[dtop].sum() / max(d.y.sum(), 1):.0%} of spike days ({int(d.y.sum())} spike days)")
