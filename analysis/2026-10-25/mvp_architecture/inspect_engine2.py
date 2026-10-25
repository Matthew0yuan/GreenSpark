"""Second read-only probe for the MVP architecture proposal: a chained 7-day replay of the adopted own-battery
configuration (rw50+state t5+roll) from its saved start state, checked day by day against the saved test run; the same
days with one changed parameter (export limit 3 MW) to show that it recomputes; the guard that refuses a battery the
value table was not built for; the memory of the day bundle; and which tools are on PATH. Writes only
INSPECTION2.json next to this script (bytecode off, no cache builder called).

Run from the repository root:  .venv/Scripts/python.exe analysis/2026-10-25/mvp_architecture/inspect_engine2.py
"""

import sys

sys.dont_write_bytecode = True

import json
import shutil
import time
import tracemalloc
from dataclasses import replace
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from vpp import backtest as bt
from vpp import config as cf
from vpp.data import PROCESSED
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs

info: dict = {}
tracemalloc.start()
t = time.perf_counter()
days = bt.load_days("0900")
info["load_days_s"] = round(time.perf_counter() - t, 2)
cur, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
info["load_days_mb"] = {"current": round(cur / 1e6, 1), "peak": round(peak / 1e6, 1), "days": len(days)}
aux = bt.q2b_aux()

name = vb.E5.name
daily = pd.read_csv(bt.REPORTS / "v2_test_daily.csv", parse_dates=["day"])
saved = daily[daily["strategy"] == name].set_index("day").sort_index()
log = pd.read_parquet(PROCESSED / "v2_test_log.parquet", columns=["day", "slot", "strategy", "meter_mw"])
log = log[log["strategy"] == name]
months = {d["day"]: d["slot_month"] for d in days}


def start_state(day):
    before = saved[saved.index < day]
    mtd: dict = {}
    for d, g in log[log["day"] < day].groupby("day"):
        for m, x in zip(months[d], g.sort_values("slot")["meter_mw"].to_numpy()):
            mtd[m] = max(mtd.get(m, 0.0), x)
    return (float(before["soc_end"].iloc[-1]) if len(before) else 0.0), mtd


def chain(first: str, last: str, sc=None, cfg=vb.E5):
    first, last = pd.Timestamp(first), pd.Timestamp(last)
    i = next(k for k, d in enumerate(days) if d["day"] == first)
    j = next(k for k, d in enumerate(days) if d["day"] == last)
    soc0, mtd0 = start_state(first)
    bt._init_worker(days[i:j + 2], aux)
    rows, t0 = [], time.perf_counter()
    for day, _, summary, _, recs in hs.route_days(sc or vb.m1(), cfg, soc0, mtd0, until=last):
        rows.append({"day": str(day.date()), "total": summary["total"], "soc_end": summary["soc_end"],
                     "max_import_mw": summary["max_import_mw"], "export_mwh": summary["export_mwh"],
                     "records": len(recs)})
    return rows, round(time.perf_counter() - t0, 2)


def against_saved(rows):
    worst = 0.0
    for r in rows:
        s = saved.loc[pd.Timestamp(r["day"])]
        worst = max(worst, abs(r["total"] - s["total"]), abs(r["soc_end"] - s["soc_end"]))
    return worst


cfg = replace(vb.E5, record=True)
for label, a, b in (("unseen test days", "2026-10-16", "2026-10-22"), ("hot season, in-sample", "2026-01-15", "2026-01-21")):
    rows, sec = chain(a, b, cfg=cfg)
    info[f"chain {label} {a}..{b}"] = {"seconds": sec, "per_day_s": round(sec / len(rows), 2),
                                        "largest_abs_diff_vs_saved": against_saved(rows), "days": len(rows)}

# one changed parameter the value table does not depend on: the export limit
sc = vb.m1()
sc3 = replace(sc, site=replace(sc.site, export_limit_mw=3.0))
rows3, sec3 = chain("2026-10-16", "2026-10-22", sc=sc3, cfg=cfg)
base, _ = chain("2026-10-16", "2026-10-22", cfg=cfg)
info["export 3 MW, 2026-10-16..22"] = {"seconds": sec3, "recomputed": True,
                                       "days_with_different_total": sum(abs(x["total"] - y["total"]) > 1e-6
                                                                        for x, y in zip(rows3, base)),
                                       "largest_export_mwh_base": max(r["export_mwh"] for r in base),
                                       "largest_export_mwh_3mw": max(r["export_mwh"] for r in rows3)}

# a battery the adopted value table was not built for
sc36 = replace(sc, asset=replace(sc.asset, battery=replace(sc.asset.battery, energy_mwh=36.0)))
try:
    chain("2026-10-16", "2026-10-16", sc=sc36, cfg=cfg)
    info["energy 36 MWh with the 40 MWh table"] = "ran (no guard)"
except Exception as e:  # noqa: BLE001
    info["energy 36 MWh with the 40 MWh table"] = f"refused: {type(e).__name__}: {e}"
sc8 = replace(sc, asset=replace(sc.asset, battery=replace(sc.asset.battery, power_mw=8.0)))
try:
    r8, _ = chain("2026-10-16", "2026-10-16", sc=sc8, cfg=cfg)
    info["power 8 MW with the 10 MW table"] = "ran (no guard: the table was solved at 10 MW)"
except Exception as e:  # noqa: BLE001
    info["power 8 MW with the 10 MW table"] = f"refused: {type(e).__name__}: {e}"

info["global_state_after_runs"] = {"bt._DAYS": len(bt._DAYS), "bt._AUX_keys": sorted(bt._AUX),
                                   "hs.VALUE_INPUTS_keys": [str(k) for k in hs.VALUE_INPUTS],
                                   "bt.REFUSED": dict(bt.REFUSED), "bt.WARNED": {k: v for k, v in bt.WARNED.items()}}
info["tools_on_path"] = {t: shutil.which(t) is not None for t in ("node", "npm", "pnpm", "uv", "git", "docker")}
info["packages"] = {d.metadata["Name"]: d.version for d in metadata.distributions()
                    if d.metadata["Name"] and d.metadata["Name"].lower() in
                    {"pandas", "numpy", "scipy", "pyarrow", "highspy", "lightgbm", "scikit-learn", "matplotlib"}}
info["python"] = sys.version.split()[0]
(OUT / "INSPECTION2.json").write_text(json.dumps(info, indent=1, default=str), encoding="utf-8")
print(json.dumps(info, indent=1, default=str))
