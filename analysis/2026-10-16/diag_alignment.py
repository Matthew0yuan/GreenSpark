"""Timestamp alignment: compare each forecast with the actual series shifted by k half-hours.
If labels line up, the error is smallest (correlation largest) at k = 0."""

import numpy as np
import pandas as pd

PROC = "/Users/matthew.yuan/Desktop/HGS/data/processed/"
SP = "/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-16"
HALF = pd.Timedelta("30min")
df30 = pd.read_parquet(PROC + "wem_30min.parquet")
runs = pd.read_parquet(PROC + "aemo_predispatch.parquet")
wx = pd.read_parquet(PROC + "weather_30min.parquet")
d0 = runs[runs.days_ahead == 0].set_index("ts")
fc = pd.read_parquet(PROC + "forecast_aemo_ml.parquet")
e = fc[(fc["product"] == "energy")].merge(pd.read_parquet(SP + "/energy_blend.parquet"), on=["issue_date", "ts", "days_ahead"])
e["ours"] = (e.aemo_ml_wx + e.ml_wx) / 2
ours0 = e[e.days_ahead == 0].set_index("ts")["ours"]

checks = {
    "AEMO DPV forecast vs actual DPV (MW)": (d0["aemo_dpv_level_mw"], df30["dpv_mw"]),
    "AEMO demand forecast vs operational demand (MW)": (d0["aemo_demand_level_mw"], df30["demand_mw"]),
    "AEMO price forecast vs actual RTP ($/MWh)": (d0["aemo_energy"], df30["energy"]),
    "ECMWF Perth radiation (24h-old forecast) vs actual DPV": (wx["wx_perth_shortwave_radiation_previous_day1"], df30["dpv_mw"]),
    "Our D+0 energy forecast vs actual RTP ($/MWh)": (ours0, df30["energy"]),
}
ks = [-2, -1, 0, 1, 2]
for name, (f, a) in checks.items():
    f = f.dropna()
    row = {}
    for k in ks:
        y = a.reindex(f.index + k * HALF).to_numpy()
        ok = ~np.isnan(y)
        row[k] = (np.abs(f.to_numpy()[ok] - y[ok]).mean(), np.corrcoef(f.to_numpy()[ok], y[ok])[0, 1])
    best_mae, best_r = min(ks, key=lambda k: row[k][0]), max(ks, key=lambda k: row[k][1])
    print(f"{name}\n  " + "  ".join(f"k={k:+d}: MAE {row[k][0]:.1f} r {row[k][1]:.3f}" for k in ks)
          + f"\n  -> best MAE at k={best_mae:+d}, best corr at k={best_r:+d}")
# where does the actual DPV peak and the price trough sit in the day?
prof = df30.loc["2025-10-01":].groupby(df30.loc["2025-10-01":].index.strftime("%H:%M"))[["dpv_mw", "energy"]].mean()
print("\nTest-period mean profile: DPV peak at", prof.dpv_mw.idxmax(), "| price trough at", prof.energy.idxmin(),
      "| price peak at", prof.energy.idxmax())
