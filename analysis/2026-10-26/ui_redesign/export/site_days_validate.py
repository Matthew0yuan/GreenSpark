"""Checks data/site_days.json against DATA_CONTRACT.md section 1: keys, array lengths, allowed values, sign
conventions, and that the plan and outcome arrays agree with each other where they must. Prints problems; writes nothing.

Run (repo root):  .venv/Scripts/python.exe analysis/2026-10-26/ui_redesign/export/site_days_validate.py
"""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
x = json.loads((ROOT / "analysis/2026-10-26/ui_redesign/data/site_days.json").read_text(encoding="utf-8"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
problems = []


def need(cond, msg):
    if not cond:
        problems.append(msg)


def length(a, n, name):
    need(a is None or (isinstance(a, list) and len(a) == n), f"{name}: length {None if a is None else len(a)} != {n}")


def finite(v, name):
    if isinstance(v, dict):
        for k, w in v.items():
            finite(w, f"{name}.{k}")
    elif isinstance(v, list):
        for i, w in enumerate(v):
            finite(w, f"{name}[{i}]")
    elif isinstance(v, float):
        need(math.isfinite(v), f"{name}: not finite")


TOP = ["day", "weekday_label", "period", "config", "battery", "limits", "ircr_flag_0900", "plan", "price", "offers_0900",
       "outcome", "baseline_same_day", "checks_plan", "gaps", "sources"]
STATUS = {"pass", "fail", "warn", "not_checked"}
CHECK_IDS = {"power", "soc_max", "soc_reserve", "meter_import", "cmd", "meter_export", "fcess_cover", "cycles",
             "info_timing", "contractual", "market_eligibility"}
ACTIONS = {"pre", "charge", "discharge", "shave", "hold"}
for d in x:
    day = d["day"]
    finite(d, day)
    for k in TOP:
        need(k in d, f"{day}: missing {k}")
    need(set(d["period"]) >= {"label", "from", "to", "days"}, f"{day}: period keys")
    p = d["plan"]
    f0 = p["first_interval"]
    for k in ("battery_mw", "load_fc_mw", "meter_mw", "reserve_floor_mwh", "action"):
        length(p[k], 48, f"{day}.plan.{k}")
    length(p["soc_mwh"], 49, f"{day}.plan.soc_mwh")
    for k, a in p["fcess_mw"].items():
        length(a, 48, f"{day}.plan.fcess_mw.{k}")
    need(set(p["fcess_mw"]) == {"reg_raise", "reg_lower", "cont_raise", "cont_lower"}, f"{day}: fcess keys")
    need(all(a in ACTIONS for a in p["action"]), f"{day}: action values")
    need(all(v is None for v in p["battery_mw"][:f0]) and all(v is not None for v in p["battery_mw"][f0:]),
         f"{day}: plan nulls before first_interval only")
    need(set(p["value_at_forecast_aud"]) >= {"energy", "fcess", "network", "ircr", "wear", "net"}, f"{day}: value keys")
    v = p["value_at_forecast_aud"]
    tot = v["energy"] + v["regulation_energy"] + v["fcess"] - v["wear"] + v["stored_end"]
    need(abs(tot - v["net"]) < 1e-3, f"{day}: plan value parts {tot} != net {v['net']}")
    for t in range(f0, 48):  # meter = load - battery; SoC moves with the plan (charging x eta at most)
        need(abs(p["meter_mw"][t] - (p["load_fc_mw"][t] - p["battery_mw"][t])) < 1e-5, f"{day}: plan meter {t}")
        if p["action"][t] == "charge":
            need(p["battery_mw"][t] < 0 and p["soc_mwh"][t + 1] > p["soc_mwh"][t] - 1e-6, f"{day}: charge {t}")
        if p["action"][t] in ("discharge", "shave"):
            need(p["battery_mw"][t] > 0 and p["soc_mwh"][t + 1] < p["soc_mwh"][t], f"{day}: discharge {t}")
    pr = d["price"]
    for k in ("forecast_p50", "forecast_p10", "forecast_p90", "actual"):
        length(pr[k], 48, f"{day}.price.{k}")
    if pr["forecast_p10"] is not None:
        for t in range(f0, 48):
            need(pr["forecast_p10"][t] <= pr["forecast_p90"][t], f"{day}: p10 > p90 at {t}")
    o = d["offers_0900"]
    length(o["by_halfhour"], 48, f"{day}.offers.by_halfhour")
    length(o["protected_mwh"], 48, f"{day}.offers.protected_mwh")
    need(all(o["by_halfhour"][t] is None for t in range(f0)), f"{day}: offers before first_interval not null")
    for t in range(f0, 48):
        rows = o["by_halfhour"][t]
        need(isinstance(rows, list), f"{day}: offers {t} not a list")
        prices = [r["price_aud_per_mwh"] for r in rows]
        nums = [q for q in prices if q != "MAX"]
        need(nums == sorted(nums) and all(q == "MAX" for q in prices[len(nums):]), f"{day}: offer prices not rising {t}")
        need(all(r["mw"] > 0 for r in rows), f"{day}: non-positive tranche {t}")
        room = 10.0 - max(p["battery_mw"][t], 0.0)
        need(sum(r["mw"] for r in rows) <= room + 0.01, f"{day}: offered {sum(r['mw'] for r in rows)} > room {room} at {t}")
        sold = o["protected_mwh"][t]
        need(sold is not None and 0 <= sold <= p["soc_mwh"][t] + 1e-6, f"{day}: protected_mwh {t}")
    out = d["outcome"]
    for k in ("battery_mw", "meter_mw", "load_mw", "offer_cleared_mw"):
        length(out[k], 48, f"{day}.outcome.{k}")
    length(out["soc_mwh"], 49, f"{day}.outcome.soc_mwh")
    need(out["soc_mwh"][f0] is not None and abs(out["soc_mwh"][f0] - p["soc_mwh"][f0]) < 1e-6,
         f"{day}: outcome SoC at 09:00 != plan's start")
    for t in range(48):  # meter = load - battery (battery includes regulation energy, which also moves the meter)
        need(abs(out["meter_mw"][t] - (out["load_mw"][t] - out["battery_mw"][t])) < 1e-5, f"{day}: outcome meter {t}")
        need(0 <= out["offer_cleared_mw"][t] <= max(out["battery_mw"][t], 0) + 1e-6, f"{day}: cleared {t}")
    m = out["money_aud"]
    need(set(m) >= {"energy", "fcess", "network", "ircr", "wear", "net"}, f"{day}: money keys")
    need(abs(m["energy"] + m["fcess"] - m["wear"] + m["network"] - m["net"]) < 1e-4, f"{day}: money parts")
    ids = {c["id"] for c in d["checks_plan"]}
    need(ids >= CHECK_IDS, f"{day}: missing checks {CHECK_IDS - ids}")
    need(all(c["status"] in STATUS for c in d["checks_plan"]), f"{day}: check status")
    need(all(c["status"] == "not_checked" for c in d["checks_plan"] if c["id"] in ("contractual", "market_eligibility")),
         f"{day}: contractual / market_eligibility must be not_checked")
    for g in d["gaps"]:
        need(set(g) == {"field", "reason"}, f"{day}: gap keys {g}")
    b = d["baseline_same_day"]
    need(b is None or {"name", "net_aud", "source"} <= set(b), f"{day}: baseline keys")
    print(day, "plan", f0, "checks", sorted((c["id"], c["status"]) for c in d["checks_plan"]))
print("problems:", len(problems))
for q in problems[:50]:
    print(" ", q)
