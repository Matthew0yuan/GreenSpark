"""Re-run the no-AEMO weather model (ml_wx) on the test months and save the energy blend forecast."""

import sys

import pandas as pd

sys.path.insert(0, "/Users/matthew.yuan/Desktop/HGS")
from vpp import aemo_correction as ac  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402

OUT = "/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-16/energy_blend.parquet"
df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
wx = pd.read_parquet(PROCESSED / "weather_30min.parquet")
frame = ac.build_frame(df30, runs, wx)
orig = ac.features_for
ac.features_for = lambda p, weather=False: [c for c in orig(p, True) if not c.startswith("aemo")]
f2 = frame.copy()
f2["aemo_energy"] = 0.0
out = []
for da in (0, 1):
    p = ac.walk_forward(f2, "energy", da)
    out.append(frame.loc[p.index, ["issue_date", "ts", "days_ahead"]].assign(ml_wx=p.to_numpy()))
pd.concat(out, ignore_index=True).to_parquet(OUT)
print("saved", OUT)
