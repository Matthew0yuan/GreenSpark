"""Fourth read-only probe: the price-scenario file a forecast band could come from, the day counts of each reporting
period in the saved own-battery run, and the in-memory size of the adopted value table. Writes only INSPECTION4.json
next to this script (bytecode off; nothing is built).

Run from the repository root:  .venv/Scripts/python.exe analysis/2026-10-25/mvp_architecture/inspect_engine4.py
"""

import sys

sys.dont_write_bytecode = True

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
info: dict = {}

p = PROCESSED / "scenarios_0900.parquet"
f = pq.ParquetFile(p)
info["scenarios_0900"] = {"rows": f.metadata.num_rows, "columns": f.schema_arrow.names, "mb": round(p.stat().st_size / 1e6, 2)}
x = f.read(columns=[c for c in ("day", "member", "slot") if c in f.schema_arrow.names]).to_pandas()
if "day" in x:
    info["scenarios_0900"].update(days=[str(pd.Timestamp(x["day"].min()).date()), str(pd.Timestamp(x["day"].max()).date()),
                                        int(x["day"].nunique())])
if "member" in x:
    info["scenarios_0900"]["members"] = int(x["member"].nunique())

daily = pd.read_csv(REPORTS / "v2_test_daily.csv", parse_dates=["day"])
d = daily[daily["strategy"] == "rw50+state t5+roll"]["day"]
info["days"] = {"in_sample_2025-04-01_2026-06-30": int((d <= "2026-06-30").sum()),
                "test_seen_2026-07-01_2026-10-15": int(((d >= "2026-07-01") & (d <= "2026-10-15")).sum()),
                "test_unseen_2026-10-16_on": int((d >= "2026-10-16").sum()),
                "contract_year_2025-26": int(((d >= "2025-07-01") & (d <= "2026-06-30")).sum()),
                "hot_season_2025-26": int(((d >= "2025-12-01") & (d <= "2026-03-31")).sum()),
                "all": int(len(d))}

z = np.load(PROCESSED / "v2_value_median_h50_state.npz")
info["value_table_in_memory_mb"] = round(sum(z[k].nbytes for k in z.files) / 1e6, 1)
info["value_table_arrays_mb"] = {k: round(z[k].nbytes / 1e6, 2) for k in z.files if z[k].nbytes > 1e6}
z.close()

risk = pd.read_parquet(PROCESSED / "spike_risk_0900.parquet")
risk = risk[risk["days_ahead"] == 0]
info["spike_risk_0900"] = {"rows": int(len(risk)), "null_p_spike": int(risk["p_spike"].isna().sum()),
                           "null_p_spike_by_slot_first4": risk.assign(slot=((risk["ts"] - risk["target_date"]) /
                                                                            pd.Timedelta(minutes=30) - 16).astype(int))
                           .groupby("slot")["p_spike"].apply(lambda s: int(s.isna().sum())).head(4).to_dict()}
fc = pd.read_parquet(PROCESSED / "forecast_dispatch_0900.parquet", columns=["ts", "target_date", "energy", "reg_raise"])
slot = ((fc["ts"] - fc["target_date"]) / pd.Timedelta(minutes=30) - 16).astype(int)
info["forecast_0900_null_energy_by_slot_first4"] = fc["energy"].isna().groupby(slot).sum().head(4).astype(int).to_dict()
info["forecast_0900_null_fcess"] = int(fc["reg_raise"].isna().sum())
(OUT / "INSPECTION4.json").write_text(json.dumps(info, indent=1, default=str), encoding="utf-8")
print(json.dumps(info, indent=1, default=str))
