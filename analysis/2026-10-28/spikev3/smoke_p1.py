"""Smoke test of the step-1 features on 3 issue days (not a result).
Run: .venv/bin/python analysis/2026-10-28/spikev3/smoke_p1.py"""
import sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
import pandas as pd  # noqa: E402
from vpp.spikev3 import features as fx  # noqa: E402

t = pd.read_parquet(ROOT / "analysis/2026-10-28/scarcity_signals/out/spike_table_ext.parquet")
days = pd.to_datetime(["2025-06-10", "2026-01-14", "2026-10-20"])
r = t[t["issue_date"].isin(days)]
t0 = time.time(); f1 = fx.g1_stem(r, fx.load_stem()); print("g1", round(time.time()-t0, 1), "s", f1["_stem_public_ok"].all())
t0 = time.time(); f2 = fx.g2_norm(t); print("g2 (all rows)", round(time.time()-t0, 1), "s")
t0 = time.time(); s = fx.Supply(); f4 = fx.g4_rsi(r, s); print("g4", round(time.time()-t0, 1), "s; unmapped credit share 2025:", round(float(s.unmapped_share.get(2025, float('nan'))), 3))
t0 = time.time(); f5 = fx.g5_wind(r, pd.read_parquet(ROOT / "analysis/2026-10-28/scarcity_signals/out/pit/weather.parquet")); print("g5", round(time.time()-t0, 1), "s")
x = pd.concat([f1, f2.loc[r.index], f4, f5], axis=1)
print(x.groupby(r["issue_date"]).agg(["mean", "min", "max"]).T.round(2).to_string())
