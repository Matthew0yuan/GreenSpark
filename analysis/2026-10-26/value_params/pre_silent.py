"""Step 1 of PLAN.md, item 2, run BEFORE any code change: with the original harness, an 8 MW battery (everything else
the default) through G5B for two days, to see whether it stops or silently uses the 10 MW table. The value tables are
read from out/ref/ (vl.path redirected there; the file name is still path()'s). Writes out/pre_silent.json.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/pre_silent.py
"""

import json
import subprocess
from dataclasses import replace

import _common

from vpp import backtest as bt
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs
from vpp.v2 import value as vl

REF = _common.OUT / "ref"
_path = vl.path
used = []


def redirected(*a, **k):
    p = REF / _path(*a, **k).name
    used.append(p.name)
    return p


def main() -> None:
    vl.path = redirected
    status = subprocess.run(["git", "status", "--short", "vpp"], cwd=_common.ROOT, capture_output=True, text=True).stdout
    days = bt.load_days("0900")[:3]
    bt._init_worker(days, bt.q2b_aux())
    sc = vb.m1()
    sc8 = replace(sc, asset=replace(sc.asset, battery=replace(sc.asset.battery, power_mw=8.0)))
    out = {"vpp_changes_at_run": status, "battery": repr(sc8.asset.battery), "days": []}
    try:
        for day, _, summary, log, _ in hs.route_days(sc8, vb.G5B, until=days[1]["day"]):
            out["days"].append({"day": str(day.date()), "total": summary["total"], "soc_end": summary["soc_end"],
                                "max_grid_mw": float(log["energy_mwh"].abs().max() / 0.5)})
        out["error"] = None
    except Exception as e:  # noqa: BLE001  (what the original code does is the point)
        out["error"] = f"{type(e).__name__}: {e}"
    out["tables_looked_up"] = sorted(set(used))
    out["value_inputs_keys"] = [str(k) for k in hs.VALUE_INPUTS]
    (_common.OUT / "pre_silent.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
