"""Shared replay for the site-day export (analysis/2026-10-26/ui_redesign/DATA_CONTRACT.md, section 1).

Replays one trading day of the formal configuration e5 (vpp.v2.backtest.E5, "rw50+state t5+roll") on route m1 with the
code as it is now (after the 2026-10-26 power-cap fix), from the day's start state:
  stored energy at the start = the fixed code's soc_end of the day before (power_cap_fix/out/after_ledger.csv, e5);
  each month's highest import so far = from the saved e5 test log (reports' data/processed/v2_test_log.parquet, old
  code). The old and fixed code differ only on 2025-12-05, 12-08 and 2026-02-02, 17:00-17:30, by 0.2-0.5 MW at an
  IRCR cap of 0 MW, far below the 9 MW monthly maxima, so the monthly maxima are the same; the replay is checked
  against the fixed code's daily ledger to make sure.
Records every adopted plan (vpp.v2.record), the 09:00 offers (vpp.v2.offers.Collector) and every 5-min step.
Read-only: bytecode off, nothing written by this module.
"""

import sys

sys.dont_write_bytecode = True

import os  # noqa: E402

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from dataclasses import replace  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from vpp import backtest as bt  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import harness as hs  # noqa: E402
from vpp.v2 import offers as of  # noqa: E402

FIX = ROOT / "analysis/2026-10-26/power_cap_fix"
E5_NAME = vb.E5.name


def start_state(day: pd.Timestamp, days: list[dict]) -> tuple[float, dict, dict]:
    """(stored MWh at the day's start, each month's highest import before it, provenance)."""
    led = pd.read_csv(FIX / "out/after_ledger.csv", parse_dates=["day"])
    led = led[(led["scenario"] == "m1") & (led["strategy"] == E5_NAME)].set_index("day").sort_index()
    before = led[led.index < day]
    soc0 = float(before["soc_end"].iloc[-1])
    old = pd.read_csv(ROOT / "reports/v2_test_daily.csv", parse_dates=["day"])
    old = old[(old["scenario"] == "m1") & (old["strategy"] == E5_NAME)].set_index("day").sort_index()
    soc0_old = float(old[old.index < day]["soc_end"].iloc[-1])
    months = {d["day"]: d["slot_month"] for d in days}
    log = pd.read_parquet(PROCESSED / "v2_test_log.parquet", columns=["day", "slot", "strategy", "meter_mw"])
    log = log[(log["strategy"] == E5_NAME) & (log["day"] < day)]
    mtd: dict = {}
    for d, g in log.groupby("day"):
        for m, x in zip(months[d], g.sort_values("slot")["meter_mw"].to_numpy()):
            mtd[m] = max(mtd.get(m, 0.0), float(x))
    return soc0, mtd, {"soc0_fixed": soc0, "soc0_old_code": soc0_old, "prev_day": str(before.index[-1].date())}


def replay_v1(day: str, days: list[dict], aux: dict) -> dict:
    """The frozen v1 (vpp.v2.config.V1 on m1, as reports/v2_test_daily.csv strategy 'v1') through one day with the code
    as it is now, from v1's saved start state (old code: soc_end of the day before, each month's highest import from
    data/processed/v2_test_log.parquet). Returns the day's summary."""
    from vpp.v2.config import V1
    day = pd.Timestamp(day)
    old = pd.read_csv(ROOT / "reports/v2_test_daily.csv", parse_dates=["day"])
    old = old[(old["scenario"] == "m1") & (old["strategy"] == V1.name)].set_index("day").sort_index()
    soc0 = float(old[old.index < day]["soc_end"].iloc[-1])
    months = {d["day"]: d["slot_month"] for d in days}
    log = pd.read_parquet(PROCESSED / "v2_test_log.parquet", columns=["day", "slot", "strategy", "meter_mw"])
    log = log[(log["strategy"] == V1.name) & (log["day"] < day)]
    mtd: dict = {}
    for d, g in log.groupby("day"):
        for m, x in zip(months[d], g.sort_values("slot")["meter_mw"].to_numpy()):
            mtd[m] = max(mtd.get(m, 0.0), float(x))
    i = next(k for k, d in enumerate(days) if d["day"] == day)
    bt._init_worker(days[i:i + 2], aux)
    _, _, summary, log_day, _ = next(hs.route_days(vb.m1(), V1, soc0, mtd, until=day))
    return {"summary": summary, "saved_total": float(old.loc[day, "total"]), "soc0": soc0,
            "max_mw": float(np.abs(log_day["energy_mwh"].to_numpy()).max() / 0.5)}


def replay(day: str, days: list[dict] | None = None, aux: dict | None = None, perturb_after_0900: bool = False) -> dict:
    """One day of e5 on m1 from its start state. perturb_after_0900: every actual price from 09:00 on (30-min RTP,
    5-min energy and FCESS prices, settled FCESS prices) replaced by other numbers, to show the 09:00 plan does not
    depend on them."""
    day = pd.Timestamp(day)
    days = bt.load_days("0900") if days is None else days
    aux = bt.q2b_aux() if aux is None else aux
    i = next(k for k, d in enumerate(days) if d["day"] == day)
    soc0, mtd0, prov = start_state(day, days)
    sub = [dict(d) for d in days[i:i + 2]]  # the day and tomorrow (tomorrow's site needs are public at 09:00)
    if perturb_after_0900:
        d = sub[0]
        rng = np.random.default_rng(7)
        d["rtp"] = d["rtp"].copy()
        d["rtp"][2:] = rng.uniform(-100, 1000, len(d["rtp"]) - 2)
        d["p5"] = d["p5"].copy()
        d["p5"][12:] = rng.uniform(-100, 1000, len(d["p5"]) - 12)
        d["p5_f"] = {k: v.copy() for k, v in d["p5_f"].items()}
        d["act_f"] = {k: v.copy() for k, v in d["act_f"].items()}
        for k in d["p5_f"]:
            d["p5_f"][k][12:] = rng.uniform(0, 300, len(d["p5_f"][k]) - 12)
            d["act_f"][k][2:] = rng.uniform(0, 300, len(d["act_f"][k]) - 2)
        d["prices"] = dict(d["prices"])
        d["prices"]["actual"] = (d["rtp"], d["act_f"])
    bt._init_worker(sub, aux)
    sc = vb.m1()
    cfg = replace(vb.E5, record=True)
    bat = hs.scenario(sc, cfg).asset.battery
    col = of.Collector(bat)
    steps: list[dict] = []
    seen: dict = {}

    def on_step(d, x):
        col(d, x)
        en = x["enabled"] or {}
        steps.append({"step": x["step"], "t": x["t"], "s": x["s"], "budget": x["budget"], "pw_plan": x["pw_plan"],
                      "sellable": x["sellable"], "sold": x["sold"], "pw_offer": x["pw_offer"], "pw": x["pw"],
                      "price": x["price"], "safety": bool(x["safety"]), "forced_mw": x["forced_mw"],
                      "load_act": x["load_act"], "load_fc": x["load_fc"], "plan_t0": x["plan_t0"],
                      **{f"en_{k}": float(v) for k, v in en.items()}})

    def on_day(d, kw, summary, log):
        seen["kw"] = kw
        col.on_day(d, kw, summary, log)

    out = next(hs.route_days(sc, cfg, soc0, mtd0, until=day, on_day=on_day, on_step=on_step))
    d, terms, summary, log, recs = out
    return {"day": day, "terms": terms, "summary": summary, "log": log, "records": recs, "steps": pd.DataFrame(steps),
            "kw": seen["kw"], "forward": col.forward.get(day, {}), "at9": col._at9, "scenario": sc, "cfg": cfg,
            "bat": bat, "start": prov, "mtd0": mtd0, "aux": aux, "day_input": sub[0]}
