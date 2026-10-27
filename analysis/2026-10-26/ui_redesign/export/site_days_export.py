"""Writes analysis/2026-10-26/ui_redesign/data/site_days.json (DATA_CONTRACT.md, section 1): two trading days of the
site battery (10 MW / 40 MWh behind the factory meter), formal configuration e5 on route m1, with the code as it is
after the 2026-10-26 power-cap fix.

For each day: the plan issued at 09:00 (the "reveal" decision record at interval 2), the 09:00 forecasts, the 09:00
real-time market submission (energy), what the backtest executed (5-min steps to half-hours), the day's money, the
frozen v1 baseline's money that day, and checks of the 09:00 plan. Prints the sanity checks.

Run (repo root):  .venv/Scripts/python.exe analysis/2026-10-26/ui_redesign/export/site_days_export.py
"""

import sys

sys.dont_write_bytecode = True
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import json  # noqa: E402
import math  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import site_days_replay as rp  # noqa: E402
from vpp import backtest as bt  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp import storage_value as sv  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402
from vpp.v2 import offers as of  # noqa: E402

ROOT = rp.ROOT
OUT = ROOT / "analysis/2026-10-26/ui_redesign/data/site_days.json"
DAYS = ("2026-02-02", "2026-10-20")
N, DT, DT5, STEPS = dp.N, dp.DT, dp.DT5, dp.STEPS
FIRST = 2  # the 09:00 plan's first half-hour
TOL = 1e-6
FIX_REL = "analysis/2026-10-26/power_cap_fix"
PERIODS = {
    "in-sample": ("2025-04-01", "2026-06-30"),
    "test-seen": ("2026-07-01", "2026-10-15"),
    "test-unseen": ("2026-10-16", "2026-10-22"),
}


def num(x, nd: int = 6):
    """A JSON number (None for NaN / inf)."""
    if x is None:
        return None
    x = float(x)
    return None if not math.isfinite(x) else round(x, nd) + 0.0


def z(x, nd: int = 3) -> str:
    """A number as text without '-0.000'."""
    return f"{round(float(x), nd) + 0.0:.{nd}f}"


def arr(x, nd: int = 6) -> list:
    return [num(v, nd) for v in x]


def period_of(day: pd.Timestamp, all_days: list) -> dict:
    for label, (a, b) in PERIODS.items():
        if pd.Timestamp(a) <= day <= pd.Timestamp(b):
            n = sum(pd.Timestamp(a) <= d <= pd.Timestamp(b) for d in all_days)
            return {"label": label, "from": a, "to": b, "days": n}
    raise ValueError(day)


def clock(slot: int) -> str:
    return (pd.Timestamp("2000-01-01 08:00") + pd.Timedelta(minutes=30 * slot)).strftime("%H:%M")


def pad(values, first: int = FIRST, n: int = N) -> list:
    """A plan array from `first` on, as a 48-entry list with null before."""
    v = list(values)
    return [None] * first + v + [None] * (n - first - len(v))


# ------------------------------------------------------------------------------------------------ the 09:00 plan
def the_0900_record(records: list) -> dict:
    rec = [x for x in records if int(x["t0"]) == FIRST and x["kind"] == "reveal"]
    if len(rec) != 1:
        raise RuntimeError(f"expected one 09:00 reveal record, found {len(rec)}")
    return rec[0]


def plan_block(r: dict, rec: dict, checks: list, gaps: list) -> tuple[dict, dict]:
    sc, bat = r["scenario"], r["bat"]
    eta, P, E = bat.eta, bat.power_mw, bat.energy_mwh
    tau = sc.asset.limits.fcess_sustain_h
    margin = sc.prefs.load_margin
    util = sc.route.reg_util
    net = np.asarray(rec["net"], float)
    soc = np.asarray(rec["soc"], float)
    f = {k: np.clip(np.asarray(rec[k], float), 0.0, None) for k in dp.FCESS}  # the solver's -0.0000x are zero
    f = {k: np.where(v < 5e-7, 0.0, v) for k, v in f.items()}
    lf = np.asarray(rec["load"], float)  # the plan's own load: forecast x (1 + margin)
    lfe = lf / (1 + margin)              # the expected load
    hard = np.asarray(rec["hard"], float)
    rr, cr, rl, cl = (f[k] for k in dp.FCESS)
    cover = (rr + cr) * tau / eta        # stored MWh behind enabled raise (the plan's own constraint)
    floor = hard + cover
    site_floor = np.zeros(len(net))
    for name, fl in zip(rec["soft_names"], rec["soft_floors"]):
        site_floor = np.maximum(site_floor, np.asarray(fl, float))
    meter = lfe - net
    soc_start = np.r_[rec["stored"], soc[:-1]]
    caps = {n: np.asarray(c, float) for n, c in zip(rec["cap_names"], rec["caps"])}

    # action: derived from the plan's MW and its own economic caps on the meter (the record gives no reason per slot)
    action, action_cap = [], []
    for k in range(len(net)):
        binding = [n for n, c in caps.items() if np.isfinite(c[k]) and c[k] < 0 and abs(-net[k] - c[k]) <= 1e-3]
        if net[k] < -1e-3:
            action.append("charge"); action_cap.append(None)
        elif net[k] > 1e-3 and binding:
            action.append("shave"); action_cap.append("ircr" if "ircr" in binding else binding[0])
        elif net[k] > 1e-3:
            action.append("discharge"); action_cap.append(None)
        else:
            action.append("hold"); action_cap.append(None)

    # money the plan expects from 09:00, at the 09:00 forecast (the record's own fields; two derived splits)
    wear_settled = rec["wear"] - (rec["money"] - rec["value"])
    reg_energy = rec["value"] - (rec["energy_value"] + rec["fcess_value"] - rec["wear"] + rec["stored_value"])
    value = {"energy": num(rec["energy_value"]), "regulation_energy": num(reg_energy), "fcess": num(rec["fcess_value"]),
             "network": None, "ircr": None, "wear": num(wear_settled), "stored_end": num(rec["stored_value"]),
             "net": num(rec["money"])}
    gaps.append({"field": "plan.value_at_forecast_aud.network", "reason": "the plan does not price the network bill: "
                 "the CMD cap steers it through a penalty that is not money (record 'penalty', here "
                 f"{rec['penalty']:.2f}); the bill is settled per calendar month after the fact"})
    gaps.append({"field": "plan.value_at_forecast_aud.ircr", "reason": "the plan does not price IRCR: the IRCR cap "
                 "(flagged days) steers it through a penalty that is not money; IRCR is settled per Hot Season on the "
                 "median of 12 peak intervals"})

    # ---- checks of the 09:00 plan against the limits
    def add(cid, label, ok, worst, detail, economic=False, status=None):
        st = status or ("pass" if ok else ("warn" if economic else "fail"))
        checks.append({"id": cid, "label": label, "status": st, "worst": worst, "detail": detail})

    up = np.maximum.reduce([np.abs(net), net + rr + cr, -net + rl + cl])
    k = int(np.argmax(up))
    add("power", f"Battery power ≤ {P:g} MW (energy plus FCESS headroom)", up.max() <= P + TOL, f"{up.max():.3f} MW",
        f"largest of |net|, net + raise, −net + lower over 09:00–08:00: {up.max():.3f} MW at {clock(FIRST + k)}")
    add("soc_max", f"Stored energy ≤ {E:g} MWh", soc.max() <= E + TOL and soc.min() >= -TOL, f"{soc.max():.3f} MWh",
        f"plan stored energy at the end of each half-hour: {soc.min():.3f}–{soc.max():.3f} MWh")
    gap_hard = soc - hard
    add("soc_reserve", "Stored energy ≥ mandatory floor (compliance obligations, backup)", gap_hard.min() >= -TOL,
        f"{gap_hard.min():.3f} MWh above the floor",
        f"route m1 has no compliance obligation or backup on this day: the record's compliance floor is "
        f"{hard.max():.3f} MWh at every half-hour, so the floor is the FCESS raise cover (checked below)")
    short = site_floor - soc
    add("site_reserve", "Stored energy ≥ site reserve for tomorrow (economic floor)", short.max() <= TOL,
        f"{max(0.0, short.max()):.3f} MWh below", "economic floor 'site reserve: CMD and IRCR' (energy tomorrow's "
        f"factory needs, public at 09:00), up to {site_floor.max():.2f} MWh from {clock(FIRST + int(np.argmax(site_floor > 0)))}"
        if site_floor.max() > 0 else "no site reserve on this day", economic=True)
    imp = lf - net + rl + cl
    imp_e = lfe - net
    k = int(np.argmax(imp))
    add("meter_import", f"Meter import ≤ {sc.site.import_limit_mw:g} MW", imp.max() <= sc.site.import_limit_mw + TOL,
        f"{imp.max():.3f} MW", f"on the plan's own load (forecast × {1 + margin:g}) with FCESS lower headroom: "
        f"{imp.max():.3f} MW at {clock(FIRST + k)}; on the expected load, energy only: {imp_e.max():.3f} MW")
    for name, c in caps.items():
        live = np.isfinite(c)
        if not live.any():
            continue
        over = np.where(live, -net - c, -np.inf)
        over_l = np.where(live, -net + rl + cl - c, -np.inf)
        cap_mw = (c + lfe)[live]  # the cap on meter import, MW
        k = int(np.argmax(over))
        if name == "cmd":
            label = f"Meter import ≤ CMD ({sc.route.cmd_mw:g} MW, or the month's highest so far) on the expected load"
            cid = "cmd"
        else:
            label = f"Meter import ≤ IRCR cap ({cap_mw.min():.0f} MW in {clock(FIRST + int(np.argmax(live)))}–" \
                    f"{clock(FIRST + len(live) - int(np.argmax(live[::-1])))}) on the expected load"
            cid = "ircr_cap"
        add(cid, label + " (economic)", over.max() <= TOL, f"{z(max(0.0, over.max()))} MW over",
            f"cap {z(cap_mw.min())}–{z(cap_mw.max())} MW; the plan's meter on the expected load is at most "
            f"{z((lfe - net)[live].max())} MW; with FCESS lower headroom at most {z(max(0.0, over_l.max()))} MW over "
            "(an economic cap: the plan may exceed it at a penalty)", economic=True)
    exp_ = net + rr + cr - lf
    exp_e = net - lfe
    add("meter_export", f"Meter export ≤ {sc.site.export_limit_mw:g} MW",
        exp_.max() <= sc.site.export_limit_mw + TOL and exp_e.max() <= sc.site.export_limit_mw + TOL,
        f"{max(0.0, exp_.max(), exp_e.max()):.3f} MW",
        f"on the plan's own load with FCESS raise headroom: {max(0.0, exp_.max()):.3f} MW; on the expected load, "
        f"energy only: {max(0.0, exp_e.max()):.3f} MW")
    kr, kl = tau / eta, tau * eta
    c1 = np.minimum(soc, soc_start) - kr * (rr + cr)
    c2 = E - (np.maximum(soc, soc_start) + kl * (rl + cl))
    add("fcess_cover", f"FCESS deliverable for {tau:g} h (stored energy behind raise, room behind lower)",
        c1.min() >= -TOL and c2.min() >= -TOL, f"{z(min(c1.min(), c2.min()))} MWh spare",
        f"stored energy at the start and end of each half-hour minus (raise) × {tau:g} h / η: at least {z(c1.min())} MWh; "
        f"room minus (lower) × {tau:g} h × η: at least {z(c2.min())} MWh (0 means the plan uses the room exactly)")
    planned_out = float(np.clip(net, 0, None).sum() * DT + util[0] * rr.sum() * DT)
    before = sc.asset.limits.cycles_per_day * E - float(rec["budget"])
    add("cycles", f"Daily discharge ≤ {sc.asset.limits.cycles_per_day:g} cycle ({E:g} MWh)",
        planned_out <= float(rec["budget"]) + TOL, f"{before + planned_out:.2f} MWh",
        f"discharged 08:00–09:00 {before:.2f} MWh (executed) + planned from 09:00 {planned_out:.2f} MWh "
        f"(energy and expected regulation-raise energy {util[0]:g} MWh per MW-h) ≤ {E:g} MWh")
    return ({"first_interval": FIRST, "first_interval_clock": clock(FIRST), "kind": "reveal",
             "stored_at_0900_mwh": num(rec["stored"]),
             "battery_mw": pad(arr(net)), "soc_mwh": [None, None] + arr(np.r_[rec["stored"], soc]),
             "fcess_mw": {k: pad(arr(f[k])) for k in dp.FCESS},
             "load_fc_mw": pad(arr(lfe)), "meter_mw": pad(arr(meter)),
             "reserve_floor_mwh": pad(arr(floor)), "site_reserve_floor_mwh": pad(arr(site_floor)),
             "action": ["pre"] * FIRST + action, "action_cap": [None] * FIRST + action_cap,
             "value_at_forecast_aud": value, "value_from_interval": FIRST},
            {"soc_start": soc_start, "net": net})


def info_timing(r: dict, rec: dict, days, aux, checks: list) -> None:
    """The 09:00 plan uses the 09:00 forecast, and no actual price from 09:00 on: replayed with every actual price
    from 09:00 on replaced, the 09:00 record must be the same."""
    d = r["day_input"]
    fc = pd.read_parquet(PROCESSED / "forecast_dispatch_0900.parquet", columns=["ts", "target_date", "energy", "issue_time"])
    fc = fc[fc["target_date"] == r["day"]].sort_values("ts")
    issue = pd.to_datetime(fc["issue_time"]).max()
    same_fc = np.allclose(np.asarray(rec["prices"], float), np.asarray(d["fc0900"], float)[FIRST:], atol=1e-3)
    p = rp.replay(str(r["day"].date()), days, aux, perturb_after_0900=True)
    rec2 = the_0900_record(p["records"])
    keys = ["net", "soc", *dp.FCESS]
    diff = max(float(np.max(np.abs(np.asarray(rec[k], float) - np.asarray(rec2[k], float)))) for k in keys)
    diff_m = abs(rec["money"] - rec2["money"])
    changed_later = float(np.max(np.abs(r["log"]["energy_mwh"].to_numpy() - p["log"]["energy_mwh"].to_numpy())))
    ok = same_fc and diff == 0.0 and diff_m == 0.0 and r["summary"]["timing_errors"] == 0 and issue <= r["day"] + pd.Timedelta(hours=9)
    checks.append({"id": "info_timing", "label": "Only information public at 09:00", "status": "pass" if ok else "fail",
                   "worst": f"{diff:.1e} MW difference",
                   "detail": f"forecast issued {issue}; the plan's prices equal the 09:00 forecast: {same_fc}; replayed "
                             f"with every actual 30-min and 5-min price (energy and FCESS) from 09:00 on replaced by "
                             f"random numbers, the 09:00 plan differs by {diff:.1e} MW and {diff_m:.1e} A$ (the executed "
                             f"day changes by up to {changed_later:.2f} MWh in a half-hour, so the replacement took effect); "
                             f"run_day timing errors {r['summary']['timing_errors']}. Not perturbed: the value-table inputs "
                             "and tomorrow's inputs, whose timing the v2 step checks cover (reports/v2_plan.md, B4, t5); "
                             "the factory load forecast is synthetic (example site × 10)"})


# ------------------------------------------------------------------------------------------------ offers
def offers_block(r: dict, plan_aux: dict, gaps: list) -> tuple[dict, dict]:
    day = r["day"]
    path = f"{FIX_REL}/out_offers/rtms_{day.date()}_0900.json"
    saved = json.loads((ROOT / path).read_text())
    dis = {it["dispatchIntervalFrom"]: it for it in
           saved["submission"]["variation"]["tradingDays"][0]["energy"]["facilities"][0]["dispatchIntervals"]}
    mine = of.request(day, r["forward"], "x", "x")
    mdis = {it["dispatchIntervalFrom"]: it for it in
            mine["submission"]["variation"]["tradingDays"][0]["energy"]["facilities"][0]["dispatchIntervals"]}
    identical = set(dis) == set(mdis) and all(dis[k]["tranches"] == mdis[k]["tranches"] for k in dis)
    planned_price = round(of.FLOOR + 0.01, 2)
    by_hh, detail = [], []
    for t in range(N):
        here = [d for d in range(t * STEPS + 1, (t + 1) * STEPS + 1) if d in dis]
        if t < FIRST:
            by_hh.append(None)
            continue
        acc: dict = {}
        for d in here:
            for x in dis[d]["tranches"]:
                if x["quantity"] > 0 and x["price"] != planned_price:
                    acc.setdefault(x["price"], {})[d] = x["quantity"]
        rows = []
        for price in sorted(acc, key=lambda p: math.inf if p == "MAX" else float(p)):
            q = [acc[price].get(d, 0.0) for d in here]
            rows.append({"mw": num(np.mean(q), 6), "price_aud_per_mwh": price if price == "MAX" else num(price, 2),
                         "dis_with_tranche": len(acc[price]), "mw_min": num(min(q), 3), "mw_max": num(max(q), 3)})
        by_hh.append(rows)
        detail.append(len(here))
    # energy offered only at MAX: stored MWh (the 09:00 plan's, at the start of each half-hour) in segments whose
    # threshold, as the 09:00 offers price them, is at or above the month's ceiling
    a = r["at9"]
    bids, seen, args = a["bids"], a["seen"], a["args"]
    eta, alpha, dw, k_min, ceil = args
    soc_start = plan_aux["soc_start"]
    prot, prot_model, extra_max, round_max = [None] * FIRST, [None] * FIRST, 0.0, 0.0
    for t in range(FIRST, N):
        s = float(soc_start[t - FIRST])
        thr = bids.thresholds(t, seen, eta, alpha, dw, k_min)
        seg = np.clip(s - np.arange(len(thr)) * sv.GRID, 0.0, sv.GRID)  # MWh stored in each segment
        at_max = float(seg[thr >= ceil - 1e-9].sum())
        protected = min(float(bids.protect[t]), s)
        prot.append(num(at_max, 4))
        prot_model.append(num(protected, 4))
        extra_max = max(extra_max, abs(at_max - protected))
        # the protected energy rounded up to whole value segments (a segment is protected if its bottom is below it)
        whole = min(s, math.ceil(float(bids.protect[t]) / sv.GRID - 1e-9) * sv.GRID)
        round_max = max(round_max, abs(at_max - whole))
    gaps.append({"field": "offers_0900.by_halfhour[2]", "reason": "the 09:00 submission covers only the DIs still open "
                 "at 09:00 after the 15-minute Gate Closure: DI 16 (09:15) on, so 09:00–09:30 has 3 of its 6 DIs"})
    return ({"source": path, "status": "backtest", "first_di": min(dis), "last_di": max(dis),
             "di_count": len(dis), "dis_per_halfhour": [None] * FIRST + detail,
             "identical_to_e5_replay": identical,
             "ceiling_aud_per_mwh": num(ceil, 2),
             "by_halfhour": by_hh, "protected_mwh": prot, "protected_mwh_model": prot_model},
            {"max_abs_at_max_vs_protect": extra_max, "max_abs_at_max_vs_protect_whole_segments": round_max,
             "identical": identical})


# ------------------------------------------------------------------------------------------------ outcome
def outcome_block(r: dict, gaps: list) -> tuple[dict, dict]:
    sc, bat = r["scenario"], r["bat"]
    eta, P, E = bat.eta, bat.power_mw, bat.energy_mwh
    util = sc.route.reg_util
    log, st, summ = r["log"], r["steps"].sort_values("step").reset_index(drop=True), r["summary"]
    day = r["day"]
    battery = log["energy_mwh"].to_numpy() / DT
    soc = np.r_[log["soc_start"].iloc[0], log["soc"].to_numpy()]
    # MW sold beyond the plan in force at each step (re-planned every half-hour) by the layered offers
    base = np.maximum(st["pw_plan"].to_numpy(), 0.0)
    pw, pw_off = st["pw"].to_numpy(), st["pw_offer"].to_numpy()
    sold = st["sold"].notna().to_numpy() & (pw_off > base + 1e-9)
    extra = np.where(sold, np.maximum(0.0, np.minimum(pw, pw_off) - base), 0.0)
    cleared = extra.reshape(N, STEPS).mean(axis=1)
    # exact replay of the stored energy, step by step (run_day's arithmetic: efficiency each way, regulation's use)
    s = float(st["s"].iloc[0])
    worst_step = 0.0
    s_end = np.zeros(len(st))
    for i in range(len(st)):
        if i:
            worst_step = max(worst_step, abs(s - float(st["s"].iloc[i])))
        x = pw[i]
        s = s - x * DT5 / eta if x > 0 else s - x * DT5 * eta
        up = min(util[0] * st["en_reg_raise"].iloc[i] * DT5, s * eta)
        down = min(util[1] * st["en_reg_lower"].iloc[i] * DT5, (E - s) / eta)
        s += down * eta - up / eta
        s_end[i] = s
    worst_hh = float(np.max(np.abs(s_end.reshape(N, STEPS)[:, -1] - log["soc"].to_numpy())))
    # half-hour reconciliation from the half-hour MW alone: discharge / eta, charge x eta
    e = log["energy_mwh"].to_numpy()
    approx = np.where(e > 0, -e / eta, -e * eta)
    resid = np.diff(soc) - approx
    # money: the fixed code's daily ledger, and the month's bill saving per day (vpp.stochastic.daily_money)
    led = pd.read_csv(ROOT / FIX_REL / "out/after_ledger.csv", parse_dates=["day"])
    led = led[(led["scenario"] == "m1") & (led["strategy"] == rp.E5_NAME)].set_index("day").loc[day]
    bills = pd.read_csv(ROOT / FIX_REL / "out/after_bills.csv")
    b = bills[(bills["strategy"] == rp.E5_NAME) & (bills["month"] == day.strftime("%Y-%m"))].iloc[0]
    money_csv = pd.read_csv(ROOT / FIX_REL / "out/after_money.csv", parse_dates=["day"]).set_index("day")
    net_csv = float(money_csv.loc[day, rp.E5_NAME])
    network = float(b["saving"] / b["days"])
    net = summ["energy"] + summ["fcess"] - summ["wear"] + network
    old = pd.read_csv(ROOT / "reports/v2_test_daily.csv", parse_dates=["day"])
    old = old[(old["scenario"] == "m1") & (old["strategy"] == rp.E5_NAME)].set_index("day").loc[day]
    old_b = pd.read_csv(ROOT / "reports/v2_test_bills.csv")
    old_b = old_b[(old_b["strategy"] == rp.E5_NAME) & (old_b["month"] == day.strftime("%Y-%m"))].iloc[0]
    old_net = float(old["ledger_total"] + old_b["saving"] / old_b["days"])
    over_cap = log["meter_mw"].to_numpy() - log["cap_mw"].to_numpy()
    over_hh = [clock(t) for t in np.flatnonzero(over_cap > 1e-6)]
    gaps.append({"field": "outcome.money_aud.ircr", "reason": "IRCR is settled once per Hot Season on the median import "
                 "in the 12 peak intervals (2025-26 for e5 with the fixed code: IRCR 0 MW against 6.24 MW without the "
                 "battery, A$2,116,716 saving per year, power_cap_fix/out/after_ircr.csv); it is not split into days "
                 "(AGENTS.md: no linear per-day conversion)"})
    out = {"source": f"{FIX_REL} code replayed: vpp.v2.harness.route_days (e5, m1) from the saved start state; money "
                     f"{FIX_REL}/out/after_ledger.csv, after_bills.csv, after_money.csv",
           "status": "backtest",
           "battery_mw": arr(battery), "soc_mwh": arr(soc), "meter_mw": arr(log["meter_mw"]),
           "load_mw": arr(log["site_load"]), "offer_cleared_mw": arr(cleared),
           "fcess_mw": {k: arr(log[f"{k}_mw"]) for k in dp.FCESS},
           "money_aud": {"energy": num(summ["energy"]), "fcess": num(summ["fcess"]), "network": num(network),
                         "ircr": None, "wear": num(summ["wear"]), "net": num(net),
                         "market_net": num(summ["total"])},
           "network_basis": f"the month's ({day:%Y-%m}) RT7 bill saving against the factory without a battery, "
                            f"A${b['saving']:.2f} over {b['days']:.2f} backtest days, per day (not this day's own); "
                            f"the month's highest import with the battery {b['peak_mw']:.3f} MW against a 9 MW CMD",
           "discharged_mwh": num(summ["discharged_mwh"]), "triggers": int(summ["triggers"]),
           "replans": int(summ["replans"]), "max_import_mw": num(summ["max_import_mw"]),
           "cap_excess": {k[len("excess_"):]: num(v) for k, v in summ.items() if k.startswith("excess_")},
           "cap_excess_halfhours": over_hh}
    checks = {"soc_min": float(soc.min()), "soc_max": float(soc.max()), "battery_abs_max_hh": float(np.abs(battery).max()),
              "battery_abs_max_step": float(np.abs(pw).max()), "soc_step_replay_max_err": worst_step,
              "soc_halfhour_end_err": worst_hh, "soc_hh_approx_resid_max": float(np.abs(resid).max()),
              "cleared_vs_offer_mwh": float(abs(extra.sum() * DT5 - log["offer_mwh"].sum())),
              "net_vs_after_money": net - net_csv, "market_vs_after_ledger": summ["total"] - float(led["total"]),
              "energy_vs_ledger": summ["energy"] - float(led["energy"]), "net_vs_old_reports": net - old_net,
              "old_net": old_net, "net_csv": net_csv}
    return out, checks


def baseline_block(day: pd.Timestamp, gaps: list, days: list, aux: dict) -> tuple[dict, dict]:
    """The frozen v1 baseline on the same route and day (reports/v2_test_daily.csv, strategy v1; old code), with a
    one-day replay of v1 on the code as it is now from v1's saved start state."""
    d = pd.read_csv(ROOT / "reports/v2_test_daily.csv", parse_dates=["day"])
    row = d[(d["scenario"] == "m1") & (d["strategy"] == "v1") & (d["day"] == day)]
    if not len(row):
        gaps.append({"field": "baseline_same_day", "reason": "no v1 row for this day"})
        return None, {}
    row = row.iloc[0]
    b = pd.read_csv(ROOT / "reports/v2_test_bills.csv")
    b = b[(b["strategy"] == "v1") & (b["month"] == day.strftime("%Y-%m"))].iloc[0]
    network = float(b["saving"] / b["days"])
    log = pd.read_parquet(PROCESSED / "v2_test_log.parquet", columns=["day", "strategy", "energy_mwh"])
    log = log[(log["strategy"] == "v1") & (log["day"] <= day)]
    if not len(log):
        raise RuntimeError("v1 is not in data/processed/v2_test_log.parquet: its power cannot be checked")
    mw = log.assign(mw=log["energy_mwh"].abs() / DT).groupby("day")["mw"].max()
    over_days = mw[mw > 10.0 + 1e-6]
    q = pd.read_csv(ROOT / "reports/q2b_10mw_daily.csv", parse_dates=["day"])
    q = q[(q["scenario"] == "m1") & (q["strategy"] == "forecast+offer(value)") & (q["day"] == day)]
    same_q2b = float(q["ledger_total"].iloc[0] - row["ledger_total"]) if len(q) else None
    v1 = rp.replay_v1(str(day.date()), days, aux)
    fixed_total = float(v1["summary"]["total"])
    note = (f"old code (before the 2026-10-26 power-cap fix). v1's half-hours above 10 MW in the saved log up to this "
            f"day: {len(over_days)} day(s){' (' + ', '.join(str(x.date()) for x in over_days.index) + ')' if len(over_days) else ''}"
            f"; on this day: {'yes' if day in over_days.index else 'no'}. Replayed for this day alone with the fixed code "
            f"from v1's saved start state: market net A${fixed_total:,.2f} (saved A${float(row['ledger_total']):,.2f}, "
            f"difference A${fixed_total - float(row['ledger_total']):+,.2f}; largest half-hour {v1['max_mw']:.3f} MW). "
            "The month's bill saving was not recomputed")
    blk = {"name": "v1 frozen baseline", "net_aud": num(row["ledger_total"] + network),
           "market_net_aud": num(row["ledger_total"]), "network_aud": num(network),
           "energy_aud": num(row["energy"]), "fcess_aud": num(row["fcess"]), "wear_aud": num(row["wear"]),
           "market_net_aud_fixed_code_replay": num(fixed_total),
           "source": "reports/v2_test_daily.csv (scenario m1, strategy v1: ledger_total) + reports/v2_test_bills.csv "
                     "(v1, the month's saving / days); market_net_aud_fixed_code_replay: site_days_replay.replay_v1",
           "status": "backtest", "note": note}
    if day in over_days.index or len(over_days):
        gaps.append({"field": "baseline_same_day", "reason": "v1's saved result is from the old code; " + note})
    return blk, {"over_days": [str(x.date()) for x in over_days.index], "q2b_10mw_minus_v2_test": same_q2b,
                 "v1_fixed_minus_saved_market": fixed_total - float(row["ledger_total"]),
                 "v1_replay_saved_total_check": float(v1["saved_total"] - row["total"])}


# ------------------------------------------------------------------------------------------------ prices
def price_block(r: dict, gaps: list) -> dict:
    day = r["day"]
    fc = np.asarray(r["day_input"]["fc0900"], float)
    p50 = [None] * FIRST + arr(fc[FIRST:], 3)
    sc = pd.read_parquet(PROCESSED / "scenarios_0900.parquet", columns=["day", "member", "slot", "energy"])
    sc = sc[pd.to_datetime(sc["day"]) == day]
    p10 = p90 = None
    if len(sc):
        piv = sc.pivot_table(index="slot", columns="member", values="energy")
        slots = piv.index.to_numpy().astype(int)
        # the file numbers the 46 half-hours from 09:00 either as trading-day slots 2..47 or as 0..45
        offset = 0 if slots.min() == FIRST and slots.max() == N - 1 else FIRST if slots.min() == 0 and slots.max() == N - 1 - FIRST else None
        if offset is None:
            raise RuntimeError(f"unexpected scenario slots {slots.min()}..{slots.max()}")
        print(f"{day.date()} scenarios: slots {slots.min()}..{slots.max()} ({len(slots)}), offset {offset}, "
              f"members {piv.shape[1]}")
        q10, q90 = np.quantile(piv.to_numpy(), [0.1, 0.9], axis=1)
        p10, p90 = [None] * N, [None] * N
        for s_, a, b in zip(slots + offset, q10, q90):
            p10[int(s_)], p90[int(s_)] = num(a, 3), num(b, 3)
        members = piv.shape[1]
    else:
        members = 0
        gaps.append({"field": "price.forecast_p10, price.forecast_p90", "reason": "the 09:00 price scenarios "
                     "(data/processed/scenarios_0900.parquet, vpp.scenarios) end on 2026-10-15; none for this day"})
    risk = pd.read_parquet(PROCESSED / "spike_risk_0900.parquet", columns=["ts", "target_date", "days_ahead", "p_spike"])
    risk = risk[(risk["target_date"] == day) & (risk["days_ahead"] == 0)].sort_values("ts")
    log = r["log"]
    return {"forecast_p50": p50, "forecast_p10": p10, "forecast_p90": p90, "actual": arr(log["rtp"], 3),
            "actual_max_5min": arr(log["max_5min_price"], 3), "spike_prob_ge_200": arr(risk["p_spike"], 4),
            "scenario_members": members,
            "source": "p50: data/processed/forecast_dispatch_0900.parquet 'energy' (vpp.forecast, as load_days fc0900); "
                      "p10/p90: data/processed/scenarios_0900.parquet (vpp.scenarios), 10% and 90% quantiles of the day's "
                      "members per half-hour; actual: data/processed/wem_30min.parquet 'energy' (the run_day log 'rtp'); "
                      "spike_prob_ge_200: data/processed/spike_risk_0900.parquet 'p_spike'",
            "status": {"forecast_p50": "backtest", "forecast_p10": "derived" if len(sc) else None,
                       "forecast_p90": "derived" if len(sc) else None, "actual": "backtest",
                       "spike_prob_ge_200": "backtest"}}


# ------------------------------------------------------------------------------------------------ one day
def export_day(day: str, days: list, aux: dict) -> tuple[dict, dict]:
    r = rp.replay(day, days, aux)
    D = r["day"]
    sc, bat = r["scenario"], r["bat"]
    rec = the_0900_record(r["records"])
    gaps: list = []
    checks: list = []
    plan, plan_aux = plan_block(r, rec, checks, gaps)
    info_timing(r, rec, days, aux, checks)
    offers, offer_chk = offers_block(r, plan_aux, gaps)
    val = pd.read_csv(ROOT / FIX_REL / "out_offers/validation.csv", parse_dates=["day"])
    v = val[(val["day"] == D) & (val["request"] == "0900")].iloc[0]
    checks.append({"id": "offer_format", "label": "09:00 submission passes the AEMO RTMS schema and rule checks",
                   "status": "pass" if int(v["errors"]) == 0 else "fail", "worst": f"{int(v['errors'])} errors",
                   "detail": f"{int(v['dis'])} DIs checked by vpp.v2.offers.schema_errors and rule_errors "
                             f"({FIX_REL}/out_offers/validation.csv); facility code, ramp rate and other registration "
                             "fields are placeholders (vpp.v2.offers.ASSUMED)"})
    checks.append({"id": "contractual", "label": "Contractual obligations (PPA, SCM, NCESS, supply contract)",
                   "status": "not_checked", "worst": None,
                   "detail": "route m1 carries no contract; no check of Zenith's real contracts exists"})
    checks.append({"id": "market_eligibility", "label": "Market registration and eligibility (ESR, FCESS accreditation)",
                   "status": "not_checked", "worst": None,
                   "detail": "the model assumes the battery is registered as a scheduled Electric Storage Resource "
                             "accredited for all four FCESS; no check exists"})
    outcome, out_chk = outcome_block(r, gaps)
    base, base_chk = baseline_block(D, gaps, days, aux)
    price = price_block(r, gaps)
    flags0, flags1 = set(aux["flags0"]), set(aux["flags1"])
    hot = D.month in (12, 1, 2, 3)
    flag = (D in flags0 or D in flags1) if hot else None
    if not hot:
        gaps.append({"field": "ircr_flag_0900", "reason": "not applicable: outside the Hot Season (1 Dec–31 Mar), when "
                     "IRCR peak intervals cannot fall; the dynamic rule flags Hot Season days only "
                     "(vpp.site_bill.dynamic_flags)"})
    gaps.append({"field": "plan.action", "reason": "the decision record gives no reason per half-hour; derived from "
                 "the plan's MW and its own caps (rule in sources)"})
    period = period_of(D, [d["day"] for d in days])
    out = {
        "day": str(D.date()), "weekday_label": f"{D:%a} {D.day} {D:%b %Y}",
        "period": period, "hot_season": "2025-26" if hot else None,
        "config": f"e5 ({rp.E5_NAME}), route m1",
        "battery": {"power_mw": bat.power_mw, "energy_mwh": bat.energy_mwh, "round_trip": bat.round_trip},
        "limits": {"import_mw": sc.site.import_limit_mw, "export_mw": sc.site.export_limit_mw, "cmd_mw": sc.route.cmd_mw},
        "ircr_flag_0900": flag,
        "ircr_flag_basis": None if not hot else {"same_day_0900": D in flags0, "day_before_0900": D in flags1},
        "start_state": {"stored_mwh": num(r["start"]["soc0_fixed"]), "month_max_import_mw":
                        num(r["mtd0"].get(D.strftime("%Y-%m"), sc.route.cmd_mw))},
        "plan": {**plan, "source": "vpp.dispatch.run_day on_plan record at interval 2, kind 'reveal' (vpp.v2.record."
                                   "Recorder), replayed with the fixed code", "status": "backtest"},
        "price": price,
        "offers_0900": offers,
        "outcome": outcome,
        "baseline_same_day": base,
        "checks_plan": checks,
        "sanity_checks": {
            "soc_within_0_40_mwh": bool(-TOL <= out_chk["soc_min"] and out_chk["soc_max"] <= bat.energy_mwh + TOL),
            "soc_range_mwh": [num(out_chk["soc_min"], 4), num(out_chk["soc_max"], 4)],
            "battery_abs_max_mw": {"half_hour": num(out_chk["battery_abs_max_hh"], 4),
                                   "five_min": num(out_chk["battery_abs_max_step"], 4)},
            "soc_replay_max_error_mwh": num(max(out_chk["soc_step_replay_max_err"], out_chk["soc_halfhour_end_err"]), 12),
            "soc_halfhour_formula_max_residual_mwh": num(out_chk["soc_hh_approx_resid_max"], 12),
            "net_minus_after_money_aud": num(out_chk["net_vs_after_money"], 9),
            "market_net_minus_after_ledger_aud": num(out_chk["market_vs_after_ledger"], 9),
            "net_minus_published_reports_aud": num(out_chk["net_vs_old_reports"], 6),
            "rule": "SoC replayed step by step with run_day's arithmetic (discharge / η, charge × η, η = √0.85, plus "
                    "regulation use: raise out / η, lower in × η) must equal the logged SoC (tolerance 1e-9 MWh); the "
                    "half-hour formula ΔSoC = −E/η (E > 0) or −E×η (E < 0) on the half-hour's net MWh E is reported "
                    "alongside; money must equal power_cap_fix/out/after_money.csv and after_ledger.csv (1e-6 A$)"},
        "gaps": gaps,
        "sources": sources(D, r),
    }
    led = pd.read_csv(ROOT / FIX_REL / "out/after_ledger.csv", parse_dates=["day"])
    led = led[(led["scenario"] == "m1") & (led["strategy"] == rp.E5_NAME) & (led["day"] == D)].iloc[0]
    return out, {"offer": offer_chk, "outcome": out_chk, "baseline": base_chk,
                 "plan_money_vs_ledger_plan_money": float(rec["money"] - led["plan_money"]),
                 "plan_net_hh_first": plan["battery_mw"][:6], "first_interval": plan["first_interval"]}


def sources(D: pd.Timestamp, r: dict) -> dict:
    fixed_note = ("2026-02-02 is one of the 3 days on which the old code ran the battery above 10 MW (17:00–17:30); "
                  "this file uses the fixed code, whose day differs from the published reports/v2_test_daily.csv and "
                  "reports/v2_e5_daily.csv (market total A$16,745.30 against A$16,752.08)") if D == pd.Timestamp("2026-02-02") \
        else "the fixed code and the published reports agree on this day"
    return {
        "engine": {"file": "vpp/v2/harness.py route_days; vpp/dispatch.py run_day", "config": "vpp/v2/backtest.py E5, m1()",
                   "code_state": "after the 2026-10-26 power-cap fix (analysis/2026-10-26/power_cap_fix/RESULTS.md)",
                   "note": fixed_note, "status": "backtest"},
        "start_state": {"file": f"{FIX_REL}/out/after_ledger.csv soc_end of the day before (e5); month's highest import "
                                "from data/processed/v2_test_log.parquet meter_mw (e5)",
                        "check": "the replayed day equals the fixed code's ledger row (energy, fcess, wear, total, "
                                 "soc_end)", "status": "backtest"},
        "plan": {"function": "record kind 'reveal', t0 = 2 (09:00): the plan adopted when the 09:00 forecast, "
                             "tomorrow's site reserve and tomorrow's market state are revealed; later records are the "
                             "30-minute re-plans and are not used",
                 "battery_mw": "record 'net' (+ discharge)", "soc_mwh": "record 'stored' then 'soc' (end of each "
                 "half-hour)", "fcess_mw": "record reg_raise, cont_raise, reg_lower, cont_lower (enabled MW)",
                 "load_fc_mw": "record 'load' / (1 + 0.16): the expected factory load (synthetic: example site × 10)",
                 "meter_mw": "derived: load_fc_mw − battery_mw (energy only; regulation's expected energy, 0.14 MWh "
                             "per MW-h raise and 0.05 per MW-h lower, is not included)",
                 "reserve_floor_mwh": "derived: record 'hard' (compliance floor, 0 on m1) + (reg_raise + cont_raise) × "
                                      "0.25 h / η, η = √0.85; the plan's own LP keeps s ≥ each of them",
                 "site_reserve_floor_mwh": "record soft floor 'site reserve: CMD and IRCR' (economic, end of half-hour)",
                 "action": "derived: 'pre' before 09:00; 'charge' if battery_mw < −0.001; 'shave' if battery_mw > "
                           "0.001 and an economic meter cap of the plan binds (−net = cap − expected load within "
                           "0.001 MW; action_cap names it: cmd or ircr); 'discharge' if > 0.001 otherwise; 'hold' else "
                           "(no planned energy; FCESS stays enabled, see fcess_mw, and expected regulation-raise use "
                           "still lowers the stored energy)",
                 "value_at_forecast_aud": "record energy_value, fcess_value, stored_value (stored_end: energy left at "
                                          "08:00 next day at the plan's end value), money (net); derived: wear = "
                                          "record wear − (money − value) = A$30 × planned discharge MWh; "
                                          "regulation_energy = value − (energy + fcess − record wear + stored_end). "
                                          "net = energy + regulation_energy + fcess − wear + stored_end, from 09:00. "
                                          "Not comparable with outcome.money_aud.net (that has no stored_end and "
                                          "includes the network saving)",
                 "status": "backtest (derived where stated)"},
        "offers_0900": {"file": f"{FIX_REL}/out_offers/rtms_{D.date()}_0900.json (made by {FIX_REL}/run_offers.py "
                                "for G5B = e5 with Gate Closure lock)",
                        "check": "regenerated here from the e5 replay with vpp.v2.offers.Collector: every DI's energy "
                                 "tranches identical (identical_to_e5_replay)",
                        "by_halfhour": "per half-hour, the injection tranches above the plan (every positive tranche "
                                       "except the planned discharge at −999.99) of the DIs in the submission; per "
                                       "price: mw = mean over those DIs (0 where a DI has no tranche at that price), "
                                       "mw_min/mw_max, dis_with_tranche. Prices are the real thresholds (whole cents); "
                                       "'MAX' = the month's Energy Offer Price Ceiling. When the plan charges, the "
                                       "planned charging is bid at one cent below the first sell tranche (not listed)",
                        "protected_mwh": "derived: stored MWh at the start of each half-hour (the 09:00 plan's) in "
                                         "value segments whose threshold (LadderBids.thresholds with the 09:00 offers' "
                                         "inputs) is at or above the month's ceiling, i.e. offered only at MAX. On both "
                                         "days this equals protected_mwh_model rounded up to whole 1 MWh segments "
                                         "(LadderBids marks a segment protected if its bottom is below the protected "
                                         "level). protected_mwh_model = min(LadderBids.protect, stored): the factory's "
                                         "energy for today's caps and tomorrow's needs (vpp.v2.bids.protection), from "
                                         "09:00. Within one 5-min DI the battery can sell only ~0.9 MWh, so MAX tranches "
                                         "appear in a DI only when the protected level is that close to the stored energy",
                        "status": "backtest"},
        "outcome": {"battery_mw": "run_day log energy_mwh / 0.5 h (includes regulation energy)", "soc_mwh": "log "
                    "soc_start then soc (end of each half-hour)", "meter_mw": "log meter_mw (half-hour mean)",
                    "load_mw": "log site_load (actual, synthetic)",
                    "offer_cleared_mw": "derived from the 5-min steps (run_day on_step): max(0, min(executed MW, MW "
                                        "after the offer) − max(plan MW, 0)) where the layered offer sold, averaged "
                                        "over the half-hour; the plan is the one in force at the step (re-planned "
                                        "every half-hour), not the 09:00 plan; the backtest's offers are priced from "
                                        "the latest 5-min price, not locked at 09:00",
                    "money_aud": "energy, fcess, wear, market_net: run_day summary = after_ledger.csv; network: "
                                 "after_bills.csv saving / days of the month; net = energy + fcess − wear + network "
                                 "= after_money.csv (vpp.stochastic.daily_money)", "status": "backtest"},
        "baseline_same_day": {"file": "reports/v2_test_daily.csv, reports/v2_test_bills.csv (strategy v1 on m1, the "
                                      "frozen v1 of reports/v2_plan.md, run in the same --step test)",
                              "status": "backtest"},
        "checks_plan": {"function": "this export (site_days_export.py plan_block, info_timing) against the record and "
                                    "the limits in vpp/site.py FACTORY, vpp/config.py, route m1", "status": "derived"},
        "price": {"p50_model": "50/50 blend of two LightGBM models trained with L1 loss (median regression), walk-"
                               "forward monthly refits (vpp/forecast.py, vpp/aemo_correction.py); issued 09:00, covers "
                               "09:00–07:30, so 08:00–09:00 is null",
                  "p10_p90": "the 20-member analog-ensemble scenarios (anen365_vol_level) are calibrated on validation "
                             "(vpp/scenarios.py gate G1: inside the 10–90% range of 20 members expected 72.6%, not 80%); "
                             "the e5 dispatcher does not use them",
                  "spike_prob_ge_200": "P(half-hour price ≥ A$200), LightGBM classifier (vpp/spike_risk.py); not used "
                                       "by e5"},
    }


def main():
    days = bt.load_days("0900")
    aux = bt.q2b_aux()
    out, report = [], {}
    for day in DAYS:
        x, chk = export_day(day, days, aux)
        out.append(x)
        report[day] = chk
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(report, indent=1, default=str))
    for x in out:
        print(x["day"], [(c["id"], c["status"], c["worst"]) for c in x["checks_plan"]])
        print(" gaps:", [g["field"] for g in x["gaps"]])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
