"""Why some half-hours of the G5B runs send more than the battery's power (out/g5b_*.csv, power_days.py): every 5-min
step of a short replay is watched through run_day's on_step (it only observes) and each step whose discharge pw is
above the battery's power is counted with whether a compliance cap on the meter forced it (forced_mw > 0).

A replay, not the full run: 2025-12-01 .. 2025-12-12 and 2026-01-28 .. 2026-02-05 (hot-season days where power_days.py
found such half-hours), each block started empty (soc0 = 0, no month's highest import before it), for the 8 MW battery
on its own table and the 10 MW battery on the default one. Writes out/power_steps.json.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/power_steps.py
"""

import json
from dataclasses import replace

import _common
import pandas as pd

from vpp import backtest as bt
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs
from vpp.v2 import value as vl

TABLES = _common.OUT / "tables"
_path = vl.path
BLOCKS = (("2025-12-01", "2025-12-12"), ("2026-01-28", "2026-02-05"))


def main() -> None:
    vl.path = lambda *a, **k: TABLES / _path(*a, **k).name
    all_days = bt.load_days("0900")
    aux = bt.q2b_aux()
    out = {}
    for P in (10.0, 8.0):
        sc = vb.m1()
        sc = replace(sc, asset=replace(sc.asset, battery=replace(sc.asset.battery, power_mw=P)))
        rows = []
        for a, b in BLOCKS:
            days = [d for d in all_days if pd.Timestamp(a) <= d["day"] <= pd.Timestamp(b) + pd.Timedelta(days=1)]
            bt._init_worker(days, aux)
            hs.VALUE_INPUTS.clear()

            def watch(day, st):
                if st["pw"] > P + 1e-6:
                    rows.append({"day": str(day.date()), "step": st["step"], "pw": st["pw"], "forced_mw": st["forced_mw"],
                                 "load_act": st["load_act"], "exp_room": st["exp_room"]})
            for _ in hs.route_days(sc, vb.G5B, until=pd.Timestamp(b), on_step=watch):
                pass
        x = pd.DataFrame(rows, columns=["day", "step", "pw", "forced_mw", "load_act", "exp_room"])
        out[f"{P:g} MW"] = {"steps_over_power": len(x), "of_them_forced": int((x["forced_mw"] > 0).sum()),
                            "largest_pw": float(x["pw"].max()) if len(x) else None,
                            "pw_equals_forced": int(((x["pw"] - x["forced_mw"]).abs() < 1e-9).sum()),
                            "days": sorted(x["day"].unique().tolist()), "examples": x.head(5).to_dict("records")}
        print(f"{P:g} MW:", json.dumps(out[f"{P:g} MW"], default=str))
    (_common.OUT / "power_steps.json").write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
