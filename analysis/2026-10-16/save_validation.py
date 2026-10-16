"""Validation-period (2025-04 .. 2025-09) forecasts for the joint-dispatch check: energy blend and
FCESS aemo+ml, walk-forward exactly as on the test period (frame cut at 2025-10-01 so it stops there)."""

import sys

import pandas as pd

sys.path.insert(0, "/Users/matthew.yuan/Desktop/HGS")
from vpp import aemo_correction as ac  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402

SP = "/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-16"
FCESS = ["reg_raise", "cont_raise", "reg_lower", "cont_lower"]
df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
wx = pd.read_parquet(PROCESSED / "weather_30min.parquet")
frame = ac.build_frame(df30, runs, wx)
frame = frame[frame["target_date"] < "2025-10-01"]
ac.TEST_START = pd.Timestamp("2025-04-01 08:00")
orig = ac.features_for
out = []
for da in (0, 1):
    cols = {}
    cols["aemo_ml_wx_energy"] = ac.walk_forward(frame, "energy", da, weather=True) + frame["aemo_energy"]
    for p in FCESS:
        cols[f"aemo_ml_{p}"] = ac.walk_forward(frame, p, da) + frame[f"aemo_{p}"]
    ac.features_for = lambda p, weather=False: [c for c in orig(p, True) if not c.startswith("aemo")]
    f2 = frame.copy()
    f2["aemo_energy"] = 0.0
    cols["ml_wx_energy"] = ac.walk_forward(f2, "energy", da)
    ac.features_for = orig
    idx = cols["ml_wx_energy"].index
    keep = ["issue_date", "ts", "days_ahead", "target_date"] + [f"prev_{p}" for p in ["energy"] + FCESS] + [f"prof28_{p}" for p in ["energy"] + FCESS]
    out.append(frame.loc[idx, keep].assign(**{k: v.reindex(idx) for k, v in cols.items()}))
pd.concat(out, ignore_index=True).to_parquet(SP + "/validation_forecasts.parquet")
print("saved")
