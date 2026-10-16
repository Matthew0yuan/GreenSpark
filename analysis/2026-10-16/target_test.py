"""Does forecasting the expected price (L2) instead of the median (L1) earn more in dispatch?
Same features and walk-forward as the energy blend; only the LightGBM objective changes."""

import sys

import pandas as pd

sys.path.insert(0, "/Users/matthew.yuan/Desktop/HGS")
from vpp import aemo_correction as ac  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402

SP = "/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-16"
df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
wx = pd.read_parquet(PROCESSED / "weather_30min.parquet")
frame = ac.build_frame(df30, runs, wx)
ac.TEST_START = pd.Timestamp("2025-04-01 08:00")
ac.LGB_PARAMS = {**ac.LGB_PARAMS, "objective": "l2"}
orig = ac.features_for
out = []
for da in (0, 1):
    a = ac.walk_forward(frame, "energy", da, weather=True) + frame["aemo_energy"]
    ac.features_for = lambda p, weather=False: [c for c in orig(p, True) if not c.startswith("aemo")]
    f2 = frame.copy()
    f2["aemo_energy"] = 0.0
    m = ac.walk_forward(f2, "energy", da)
    ac.features_for = orig
    idx = m.index
    out.append(frame.loc[idx, ["issue_date", "ts", "days_ahead"]].assign(aemo_ml_wx_l2=a.reindex(idx).to_numpy(), ml_wx_l2=m.to_numpy()))
pd.concat(out, ignore_index=True).to_parquet(SP + "/energy_l2.parquet")
print("saved")
