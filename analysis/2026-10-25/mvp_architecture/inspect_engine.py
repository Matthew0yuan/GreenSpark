"""Read-only inspection for the MVP architecture proposal (analysis/2026-10-25/mvp_architecture).

Prints: sizes and schemas of the data and result files the app would read, the date ranges they cover, and the wall
time of the engine calls the app would make (one-day replay of the adopted own-battery configuration from its saved
start state, and one customer-policy evaluation). Writes nothing outside this directory: bytecode is off, no cache
builder is called, and the only output is INSPECTION.json next to this script.

Run from the repository root:  .venv/Scripts/python.exe analysis/2026-10-25/mvp_architecture/inspect_engine.py
"""

import sys

sys.dont_write_bytecode = True  # importing vpp must not write vpp/__pycache__

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
info: dict = {"files": {}, "schemas": {}, "ranges": {}, "timing_s": {}, "checks": {}}


def size_mb(p: Path) -> float:
    return round(p.stat().st_size / 1e6, 2)


def parquet_meta(p: Path) -> dict:
    f = pq.ParquetFile(p)
    return {"rows": f.metadata.num_rows, "columns": f.schema_arrow.names, "mb": size_mb(p)}


# ------------------------------------------------------------------------------------------------ files and schemas
for name in ("wem_30min.parquet", "wem_5min.parquet", "forecast_dispatch_0900.parquet", "forecast_dispatch_0900_d1.parquet",
             "spike_risk_0900.parquet", "aemo_predispatch.parquet", "spike_table.parquet", "esroi.parquet",
             "weather_30min.parquet", "spike_risk_history_0900.parquet", "spike_risk_history_0900_d1.parquet",
             "v2_test_log.parquet", "v2_e5_records.parquet", "policy_sweep.parquet"):
    p = PROCESSED / name
    if p.exists():
        m = parquet_meta(p)
        info["files"][f"data/processed/{name}"] = m["mb"]
        info["schemas"][name] = {"rows": m["rows"], "columns": m["columns"][:80], "n_columns": len(m["columns"])}
for name in ("v2_value_median_h50_state.npz", "v2_value_median.npz", "v2_value_median_h50.npz"):
    p = PROCESSED / name
    if p.exists():
        z = np.load(p)
        info["files"][f"data/processed/{name}"] = size_mb(p)
        days = pd.to_datetime(z["day"].astype(str))
        info["schemas"][name] = {k: list(z[k].shape) for k in z.files}
        info["ranges"][name] = [str(days.min().date()), str(days.max().date()), int(len(days))]
        z.close()
for name in ("v2_test_daily.csv", "v2_test_bills.csv", "v2_test_ircr.csv", "v2_test_result.csv",
             "policy/policy_backtest.csv", "policy/policy_results.csv", "policy/policy_zenith_only.csv",
             "policy/policy_loso.csv", "policy/policy_stress.csv"):
    p = REPORTS / name
    x = pd.read_csv(p, nrows=5)
    info["files"][f"reports/{name}"] = size_mb(p)
    info["schemas"][name] = {"columns": list(x.columns), "n_columns": len(x.columns)}
info["files"]["data/processed (all)"] = round(sum(f.stat().st_size for f in PROCESSED.iterdir() if f.is_file()) / 1e6, 1)

daily = pd.read_csv(REPORTS / "v2_test_daily.csv", parse_dates=["day"])
info["ranges"]["v2_test_daily.csv"] = {
    "days": [str(daily["day"].min().date()), str(daily["day"].max().date())],
    "rows_per_strategy": daily.groupby("strategy").size().to_dict(),
    "scenarios": sorted(daily["scenario"].unique().tolist())}
bills = pd.read_csv(REPORTS / "v2_test_bills.csv")
info["ranges"]["v2_test_bills.csv"] = {"months": [str(bills["month"].min()), str(bills["month"].max())],
                                       "rows": len(bills)}

df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
info["ranges"]["wem_30min"] = [str(df30.index.min()), str(df30.index.max()), str(df30.index.dtype)]
info["schemas"]["wem_30min_index"] = str(df30.index.name)
fc = pd.read_parquet(PROCESSED / "forecast_dispatch_0900.parquet")
time_cols = [c for c in fc.columns if any(k in c for k in ("issue", "ts", "target", "available", "known", "public"))]
info["ranges"]["forecast_dispatch_0900"] = {c: [str(fc[c].min()), str(fc[c].max())] for c in time_cols}
info["schemas"]["forecast_dispatch_0900_dtypes"] = {c: str(fc[c].dtype) for c in fc.columns[:60]}
pdr = pq.ParquetFile(PROCESSED / "aemo_predispatch.parquet").read(columns=["issue_date", "issued_at"]).to_pandas()
info["ranges"]["aemo_predispatch_issued_at"] = [str(pdr["issued_at"].min()), str(pdr["issued_at"].max()),
                                                str(pdr["issued_at"].dt.time.value_counts().head(5).to_dict())]
fa = fc.dropna(subset=["energy"])
info["checks"]["forecast_rows_with_energy_by_days_ahead"] = fa.groupby("days_ahead").size().to_dict() \
    if "days_ahead" in fa.columns else "no days_ahead column"

# ------------------------------------------------------------------------------------------------ engine constants
from vpp import backtest as bt
from vpp import evaluate as ev
from vpp.policy import policy_simulator as ps
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs

info["checks"]["constants"] = {"bt.FIRST_DAY": bt.FIRST_DAY, "bt.LAST_DAY": bt.LAST_DAY, "CUTOFF": str(ev.CUTOFF.date()),
                               "TRAIN_END": str(ev.TRAIN_END.date()), "ps.FIRST_DAY": str(ps.FIRST_DAY.date()),
                               "ps.LAST_DAY": str(ps.LAST_DAY.date()), "ps.SEASONS": list(ps.SEASONS),
                               "E5": vb.E5.name, "E5_fields": {k: str(v) for k, v in vb.E5.__dict__.items()},
                               "Q2B_ASSET": str(bt.Q2B_ASSET), "FACTORY": str(bt.st.FACTORY), "CMD": vb.CMD,
                               "HURDLE": bt.HURDLE, "PREFS": str(bt.PREFS)}

# ------------------------------------------------------------------------------------------------ timing: own battery
t = time.perf_counter()
days = bt.load_days("0900")
info["timing_s"]["load_days_0900"] = round(time.perf_counter() - t, 2)
info["ranges"]["load_days_0900"] = [str(days[0]["day"].date()), str(days[-1]["day"].date()), len(days)]
present = {d["day"] for d in days}
calendar = pd.date_range(days[0]["day"], days[-1]["day"])
info["ranges"]["load_days_missing"] = [str(d.date()) for d in calendar if d not in present]
t = time.perf_counter()
aux = bt.q2b_aux()
info["timing_s"]["q2b_aux"] = round(time.perf_counter() - t, 2)
info["checks"]["ircr_flags_by_season"] = {
    "flags0": pd.Series(1, index=aux["flags0"]).groupby(bt.sb.hot_season(aux["flags0"])).size().to_dict(),
    "flags1": pd.Series(1, index=aux["flags1"]).groupby(bt.sb.hot_season(aux["flags1"])).size().to_dict(),
    "tolerance": list(aux["ircr_tolerance"]), "value_per_mw_flagged_day": aux["ircr_value_mw"]}

name = vb.E5.name
log = pd.read_parquet(PROCESSED / "v2_test_log.parquet", columns=["day", "slot", "strategy", "meter_mw"])
log = log[log["strategy"] == name]
saved = daily[(daily["strategy"] == name)].set_index("day").sort_index()
months = {d["day"]: d["slot_month"] for d in days}


def start_state(day: pd.Timestamp) -> tuple[float, dict]:
    """Stored energy at the day's start and each month's highest import before it, from the saved test run."""
    before = saved[saved.index < day]
    mtd: dict = {}
    for d, g in log[log["day"] < day].groupby("day"):
        for m, x in zip(months[d], g.sort_values("slot")["meter_mw"].to_numpy()):
            mtd[m] = max(mtd.get(m, 0.0), x)
    return (float(before["soc_end"].iloc[-1]) if len(before) else 0.0), mtd


replays = {}
for label, day in (("hot season day, in-sample", "2026-02-02"), ("test day, unseen part", "2026-10-20")):
    day = pd.Timestamp(day)
    i = next(k for k, d in enumerate(days) if d["day"] == day)
    soc0, mtd0 = start_state(day)
    bt._init_worker(days[i:i + 2], aux)
    for rec in (False, True):
        from dataclasses import replace
        cfg = replace(vb.E5, record=rec)
        t = time.perf_counter()
        _, _, summary, lg, records = next(hs.route_days(vb.m1(), cfg, soc0, mtd0, until=day))
        dt = time.perf_counter() - t
        row = saved.loc[day]
        replays[f"{label} {day.date()} record={rec}"] = {
            "seconds": round(dt, 2), "total": summary["total"], "saved_total": float(row["total"]),
            "soc_end": summary["soc_end"], "saved_soc_end": float(row["soc_end"]),
            "identical": bool(summary["total"] == row["total"] and summary["soc_end"] == row["soc_end"]),
            "replans": summary["replans"], "records": len(records), "log_columns": len(lg.columns)}
info["timing_s"]["replay_one_day_e5"] = replays
info["schemas"]["run_day_summary_keys"] = sorted(summary.keys())
info["schemas"]["run_day_log_columns"] = list(lg.columns)
info["schemas"]["record_keys"] = sorted(records[0].keys()) if records else []

# ------------------------------------------------------------------------------------------------ timing: customer policy
bk = pd.read_csv(REPORTS / "policy" / "policy_backtest.csv")
r = bk[(bk["scenario"] == "S2b") & (bk["case"] == "C optimised") & (bk["period"] == "in-sample")].iloc[0]
pol = ps.Policy(name="C", comp_type=r["policy_comp_type"], pay_per_kwh=float(r["policy_pay_per_kwh"]),
                pay_per_event=float(r["policy_pay_per_event"]), annual_fee=float(r["policy_annual_fee"]),
                max_events=int(r["policy_max_events"]), event_h=float(r["policy_event_h"]),
                standby_h=float(r["policy_standby_h"]), reserve_soc=float(r["policy_reserve_soc"]))
obl = ps.Obligations("S2b", capacity="net")
t = time.perf_counter()
m = ps.load_market()
info["timing_s"]["policy_load_market"] = round(time.perf_counter() - t, 2)
t = time.perf_counter()
fcs = ps.load_forecasts()
info["timing_s"]["policy_load_forecasts"] = round(time.perf_counter() - t, 2)
t = time.perf_counter()
ps.baseline()
info["timing_s"]["policy_baseline"] = round(time.perf_counter() - t, 2)
t = time.perf_counter()
fit = ps.fit_rule(pol, obl, ps.Participation())
info["timing_s"]["policy_fit_rule_first (family built)"] = round(time.perf_counter() - t, 2)
t = time.perf_counter()
e = ps.evaluate_policy(pol, market_obligations=obl, fit=fit)
info["timing_s"]["policy_evaluate_in_sample"] = round(time.perf_counter() - t, 2)
info["checks"]["policy_C_S2b_reproduced"] = {"zenith_profit": e.zenith_profit, "saved": float(r["zenith_profit"]),
                                              "same_to_1c": bool(abs(e.zenith_profit - float(r["zenith_profit"])) < 0.01),
                                              "rule": {"theta": str(fit.rule.theta), "ircr_q": fit.rule.ircr_q}}
custom = replace(pol, name="custom", pay_per_kwh=0.60, pay_per_event=0.0, comp_type="energy", max_events=30, event_h=3.0)
t = time.perf_counter()
ec = ps.evaluate_policy(custom, market_obligations=obl)
info["timing_s"]["policy_custom_new_family_fit_and_evaluate"] = round(time.perf_counter() - t, 2)
t = time.perf_counter()
et = ps.evaluate_policy(custom, market_obligations=obl, period="test")
info["timing_s"]["policy_custom_test_refit"] = round(time.perf_counter() - t, 2)
info["checks"]["custom_example_runs"] = {"in_sample_feasible": ec.feasible, "test_days": et.days,
                                         "test_activations": et.activations}
info["schemas"]["PolicyEvaluation_fields"] = list(e.__dataclass_fields__)
fam = ps.family(pol.event_h, pol.standby_h, pol.reserve_soc)
info["checks"]["family_array_mb"] = round(sum(getattr(fam, k).nbytes for k in fam.__dataclass_fields__
                                              if isinstance(getattr(fam, k), np.ndarray)) / 1e6, 1)
season = ps.season_of(m)
fl = {q: {int(y): int(fcs.flags[q][season == y].sum()) for y in (2023, 2024, 2025, 2026)} for q in fcs.flags}
info["checks"]["policy_flags_by_season"] = fl
t = time.perf_counter()
zo = ps.zenith_only()
info["timing_s"]["zenith_only"] = round(time.perf_counter() - t, 2)
info["checks"]["zenith_only_total"] = zo["total"].to_dict()

try:
    import psutil  # noqa: F401
    info["checks"]["rss_mb"] = round(psutil.Process(os.getpid()).memory_info().rss / 1e6, 0)
except ImportError:
    info["checks"]["rss_mb"] = "psutil not installed"
info["checks"]["cpu_count"] = os.cpu_count()
for mod in ("fastapi", "uvicorn", "pydantic", "streamlit", "flask", "httpx", "pytest", "duckdb", "orjson"):
    try:
        __import__(mod)
        info["checks"][f"installed_{mod}"] = True
    except ImportError:
        info["checks"][f"installed_{mod}"] = False

(OUT / "INSPECTION.json").write_text(json.dumps(info, indent=1, default=str), encoding="utf-8")
print(json.dumps(info["timing_s"], indent=1, default=str))
print(json.dumps(info["checks"], indent=1, default=str)[:4000])
