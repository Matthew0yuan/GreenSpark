"""Third read-only probe: whether the engine's existing English account (vpp.dispatch.explain) and the v2 outputs
(vpp.v2.outputs) run on one replayed day of the adopted configuration, and whether the ledger and bill functions run on
a computed few-day chain. Writes only INSPECTION3.json next to this script (bytecode off, no cache builder called).

Run from the repository root:  .venv/Scripts/python.exe analysis/2026-10-25/mvp_architecture/inspect_engine3.py
"""

import sys

sys.dont_write_bytecode = True

import json
import time
import traceback
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import dispatch as dp
from vpp import site_bill as sb
from vpp.data import PROCESSED
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs
from vpp.v2 import outputs as op

info: dict = {}
days = bt.load_days("0900")
aux = bt.q2b_aux()
name = vb.E5.name
daily = pd.read_csv(bt.REPORTS / "v2_test_daily.csv", parse_dates=["day"])
saved = daily[daily["strategy"] == name].set_index("day").sort_index()
log = pd.read_parquet(PROCESSED / "v2_test_log.parquet", columns=["day", "slot", "strategy", "meter_mw"])
log = log[log["strategy"] == name]
months = {d["day"]: d["slot_month"] for d in days}
first, last = pd.Timestamp("2026-10-18"), pd.Timestamp("2026-10-20")
before = saved[saved.index < first]
soc0 = float(before["soc_end"].iloc[-1])
mtd0: dict = {}
for d, g in log[log["day"] < first].groupby("day"):
    for m, x in zip(months[d], g.sort_values("slot")["meter_mw"].to_numpy()):
        mtd0[m] = max(mtd0.get(m, 0.0), x)
i = next(k for k, d in enumerate(days) if d["day"] == first)
j = next(k for k, d in enumerate(days) if d["day"] == last)
sub = days[i:j + 2]
bt._init_worker(sub, aux)
sc = vb.m1()
cfg = replace(vb.E5, record=True)
seen = {}
rows, meters, logs, recs_all = [], [], {}, []
for day, terms, summary, lg, recs in hs.route_days(sc, cfg, soc0, mtd0, until=last,
                                                    on_day=lambda d, kw, s, l: seen.update({d: kw})):
    rows.append({"scenario": sc.name, "strategy": cfg.name, "day": day, **summary})
    meters.append(lg["meter_mw"].to_numpy().astype(np.float32))
    logs[day] = (lg, summary, terms)
    recs_all.extend(recs)

# the engine's English account on the last day
day = last
lg, summary, terms = logs[day]
_, strat, prefs = bt.strategy(bt.VALUE, sc.prefs)
kw = seen[day]
try:
    lines = dp.explain(lg, summary, sc.asset, kw["strat"], kw["prefs"], None, sc.site, terms)
    info["dp.explain"] = {"ok": True, "lines": len(lines), "first_lines": lines[:12]}
except Exception as e:  # noqa: BLE001
    info["dp.explain"] = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]}

# the v2 outputs from the 09:00 record of that day
day_recs = [r for r in recs_all if r["day"] == day]
info["records_on_day"] = {"count": len(day_recs), "kinds": pd.Series([r["kind"] for r in day_recs]).value_counts().to_dict(),
                          "t0_first": [int(r["t0"]) for r in day_recs[:4]]}
rec = next(r for r in day_recs if int(r["t0"]) == 2)
snap = op.Snapshot(rec, bat=sc.asset.battery, load_margin=prefs.load_margin, sustain_h=sc.asset.limits.fcess_sustain_h,
                   wear=sc.asset.wear_per_mwh, alpha=strat.alpha, dw=dp.decision_wear(sc.asset, prefs), k_min=strat.k_min,
                   bids=kw.get("bids"), ladder=cfg.ladder, offer_lag=cfg.offer_lag)
outs = {}
for fn in ("charging_plan", "reserve_plan", "bidding_plan", "allocation"):
    try:
        x = getattr(op, fn)(snap)
        outs[fn] = {"rows": len(x), "columns": list(x.columns),
                    "text_values": sorted({str(v) for c in x.columns if x[c].dtype == object for v in x[c].unique()})[:20]}
    except Exception as e:  # noqa: BLE001
        outs[fn] = {"error": f"{type(e).__name__}: {e}"}
outs["money"] = {k: float(v) for k, v in op.money(snap).items()}
info["v2_outputs_0900"] = outs
info["record_soft_names"] = rec["soft_names"]
info["record_cap_names"] = rec["cap_names"]

# ledger and bills on the computed chain
t = time.perf_counter()
frame = pd.DataFrame(rows)
frame["period"] = bt.period_of(frame["day"])
ledger = bt.q2b_ledger(frame, [sc])
cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
bills, ircr = bt.q2b_bills({(sc.name, cfg.name): np.vstack(meters)}, sub[:-1], [sc], cmd_nb)
info["ledger_and_bills_s"] = round(time.perf_counter() - t, 2)
info["ledger_columns"] = list(ledger.columns)
info["bills_rows"] = bills[["month", "days", "peak_mw", "saving"]].astype(str).to_dict("records")
info["ircr_on_partial_period"] = ircr.astype(str).to_dict("records")
info["cmd_nb_mw"] = cmd_nb
diff = max(abs(r["total"] - saved.loc[r["day"], "total"]) for r in rows)
info["chain_largest_abs_diff_vs_saved_total"] = diff
(OUT / "INSPECTION3.json").write_text(json.dumps(info, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print(json.dumps(info, indent=1, default=str, ensure_ascii=False)[:6000])
