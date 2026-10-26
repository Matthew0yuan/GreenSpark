"""PLAN.md, must-pass 3 and report item 7: G5B (vpp.v2.backtest.G5B, "rw50+state t5+roll+lock") through every
backtest day from 2025-04-01 in one process (the start state carried over), on m1 with the battery's parameters
changed as asked; the value table is the one the harness finds for that battery (out/tables/, vl.path's directory
moved there, the name still path()'s).

  default (no flag): compared day by day with reports/v2_g5_daily.csv (strategy rw50+state t5+roll+lock) on the days
                     both have: total, energy, fcess, soc_end; pass = all the reference's days, largest |diff| <= 1e-6
  --power 8 etc.:    the battery's power (MW), energy (MWh), round trip, wear ($/MWh)
Writes out/g5b_<tag>.csv (one row a day) and out/g5b_<tag>.json.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/run_g5b.py [--power 8]
"""

import json
import sys
import time
from dataclasses import replace

import _common
import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import dispatch as dp
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs
from vpp.v2 import value as vl

TABLES = _common.OUT / "tables"
REF_DAILY = _common.ROOT / "reports" / "v2_g5_daily.csv"
LOCK = "rw50+state t5+roll+lock"
FLAGS = {"--power": "power_mw", "--energy": "energy_mwh", "--round-trip": "round_trip", "--wear": "wear_per_mwh"}
_path = vl.path
looked_up = []


def redirected(*a, **k):
    p = TABLES / _path(*a, **k).name
    looked_up.append(p.name)
    return p


def main(argv: list[str]) -> None:
    vl.path = redirected
    change = {name: float(argv[argv.index(flag) + 1]) for flag, name in FLAGS.items() if flag in argv}
    tag = "default" if not change else "_".join(f"{k}{v:g}" for k, v in change.items())
    sc = vb.m1()
    bat_kw = {k: v for k, v in change.items() if k != "wear_per_mwh"}
    asset = replace(sc.asset, battery=replace(sc.asset.battery, **bat_kw))
    if "wear_per_mwh" in change:
        asset = replace(asset, wear_per_mwh=change["wear_per_mwh"])
    sc = replace(sc, asset=asset)
    cfg = vb.G5B
    assert cfg.name == LOCK, cfg.name
    days = bt.load_days("0900")
    bt._init_worker(days, bt.q2b_aux())
    P = hs.scenario(sc, cfg).asset.battery.power_mw
    rows = []
    t0 = time.perf_counter()
    for day, _, s, log, _ in hs.route_days(sc, cfg):
        e = log["energy_mwh"].to_numpy() / dp.DT  # the half-hour's mean MW to (+) or from (-) the grid
        up = e + log["reg_raise_mw"].to_numpy() + log["cont_raise_mw"].to_numpy()
        down = -e + log["reg_lower_mw"].to_numpy() + log["cont_lower_mw"].to_numpy()
        rows.append({"day": day, **{k: s[k] for k in ("total", "energy", "fcess", "wear", "soc_end", "discharged_mwh")},
                     "max_grid_mw": float(np.abs(e).max()), "max_up_mw": float(up.max()), "max_down_mw": float(down.max())})
    sec = time.perf_counter() - t0
    daily = pd.DataFrame(rows)
    daily.to_csv(_common.OUT / f"g5b_{tag}.csv", index=False)
    out = {"tag": tag, "battery": repr(sc.asset.battery), "wear_per_mwh": sc.asset.wear_per_mwh, "power_mw": P,
           "days": len(daily), "first": str(daily["day"].min().date()), "last": str(daily["day"].max().date()),
           "seconds": round(sec, 1), "tables": sorted(set(looked_up)),
           "power_ok": bool((daily[["max_grid_mw", "max_up_mw", "max_down_mw"]] <= P + 1e-6).all().all()),
           "max_mw": {c: float(daily[c].max()) for c in ("max_grid_mw", "max_up_mw", "max_down_mw")}}
    if not change:
        ref = pd.read_csv(REF_DAILY, parse_dates=["day"])
        ref = ref[ref["strategy"] == LOCK].set_index("day")
        ours = daily.set_index("day")
        both = ours.index.intersection(ref.index)
        diff = {k: float((ours.loc[both, k] - ref.loc[both, k]).abs().max()) for k in ("total", "energy", "fcess", "soc_end")}
        out["vs_reference"] = {"reference_days": len(ref), "days_compared": len(both), "from": str(both.min().date()),
                               "to": str(both.max().date()), "max_abs_diff": diff,
                               "pass": len(both) == len(ref) and max(diff.values()) <= 1e-6}
        print("MUST-PASS 3:", "PASS" if out["vs_reference"]["pass"] else "FAIL", out["vs_reference"])
    (_common.OUT / f"g5b_{tag}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
