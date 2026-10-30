"""Prints data/site_days.json per half-hour (a reading aid for the export; writes nothing).

Run (repo root):  .venv/Scripts/python.exe analysis/2026-10-26/ui_redesign/export/site_days_view.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
x = json.loads((ROOT / "analysis/2026-10-26/ui_redesign/data/site_days.json").read_text(encoding="utf-8"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def f(v, nd=2):
    return "    -" if v is None else f"{v:6.{nd}f}"


for d in x:
    print(f"\n===== {d['day']} {d['weekday_label']} {d['period']} flag {d['ircr_flag_0900']} {d['ircr_flag_basis']}")
    print("value_at_forecast", d["plan"]["value_at_forecast_aud"])
    print("outcome money", d["outcome"]["money_aud"], d["outcome"]["cap_excess"], d["outcome"]["cap_excess_halfhours"])
    print("baseline", {k: v for k, v in d["baseline_same_day"].items() if k != "source"})
    p, o, pr, of = d["plan"], d["outcome"], d["price"], d["offers_0900"]
    print("hh  time  plan_mw act     cap   soc   floor  site  prot  protM  p10   p50    p90   act   max5   out_mw clr   "
          "meter_o offers")
    for t in range(48):
        tm = f"{8 + (t // 2) % 24:02d}:{30 * (t % 2):02d}"
        offers = of["by_halfhour"][t]
        os_ = "null" if offers is None else " ".join(f"{r['mw']:.2f}@{r['price_aud_per_mwh']}" for r in offers)
        print(f"{t:2d} {tm} {f(p['battery_mw'][t])} {p['action'][t]:9s} {str(p['action_cap'][t]):5s} "
              f"{f(p['soc_mwh'][t])} {f(p['reserve_floor_mwh'][t])} {f(p['site_reserve_floor_mwh'][t])} "
              f"{f(of['protected_mwh'][t])} {f(of['protected_mwh_model'][t])} {f(pr['forecast_p10'][t] if pr['forecast_p10'] else None, 0)} "
              f"{f(pr['forecast_p50'][t], 0)} {f(pr['forecast_p90'][t] if pr['forecast_p90'] else None, 0)} "
              f"{f(pr['actual'][t], 0)} {f(pr['actual_max_5min'][t], 0)} {f(o['battery_mw'][t])} {f(o['offer_cleared_mw'][t])} "
              f"{f(o['meter_mw'][t])} | {os_}")
    print("checks:")
    for c in d["checks_plan"]:
        print(f"  {c['id']:18s} {c['status']:11s} {c['worst']} :: {c['detail']}")
    print("gaps:")
    for g in d["gaps"]:
        print("  ", g["field"], "::", g["reason"])
