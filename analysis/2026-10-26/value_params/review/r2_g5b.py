"""Reviewer, CHANGED code: G5B (rw50+state t5+roll+lock) on m1 from 2025-04-01 through every day in one process, the
value tables looked up at path()'s names in the scratchpad's tables/ (built by r1_new.py with the changed code).
  default:      compared day by day with reports/v2_g5_daily.csv (that strategy): total, energy, fcess, soc_end
  --power 8:    the battery's power changed; the 7 days 2026-10-16..22 against the default run
Writes review/r2_<tag>.csv and .json.
Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/review/r2_g5b.py [--power 8]
"""

import json
import sys
import time
from dataclasses import replace

import _rc

_rc.use("new")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import harness as hs  # noqa: E402
from vpp.v2 import value as vl  # noqa: E402

_path = vl.path
seen = []


def redirected(*a, **k):
    p = _rc.NEW_TABLES / _path(*a, **k).name
    seen.append(p.name)
    return p


def main(argv) -> None:
    vl.path = redirected
    power = float(argv[argv.index("--power") + 1]) if "--power" in argv else None
    tag = "default" if power is None else f"power{power:g}"
    sc = vb.m1()
    if power is not None:
        sc = replace(sc, asset=replace(sc.asset, battery=replace(sc.asset.battery, power_mw=power)))
    cfg = vb.G5B
    days = bt.load_days("0900")
    bt._init_worker(days, bt.q2b_aux())
    rows, t0 = [], time.perf_counter()
    for day, _, s, log, _ in hs.route_days(sc, cfg):
        e = log["energy_mwh"].to_numpy() / dp.DT
        rows.append({"day": day, "total": s["total"], "energy": s["energy"], "fcess": s["fcess"], "soc_end": s["soc_end"],
                     "max_abs_grid_mw": float(np.abs(e).max())})
    daily = pd.DataFrame(rows)
    daily.to_csv(_rc.HERE / f"r2_{tag}.csv", index=False)
    out = {"config": cfg.name, "battery": repr(sc.asset.battery), "days": len(daily), "first": str(daily["day"].min().date()),
           "last": str(daily["day"].max().date()), "seconds": round(time.perf_counter() - t0, 1), "tables": sorted(set(seen))}
    week = daily[(daily["day"] >= "2026-10-16") & (daily["day"] <= "2026-10-22")]
    out["week_days"] = len(week)
    out["week_sum"] = {k: float(week[k].sum()) for k in ("total", "energy", "fcess")}
    out["week_max_abs_grid_mw"] = float(week["max_abs_grid_mw"].max())
    out["days_over_power"] = int((daily["max_abs_grid_mw"] > sc.asset.battery.power_mw + 1e-6).sum())
    if power is None:
        ref = pd.read_csv(bt.REPORTS / "v2_g5_daily.csv", parse_dates=["day"])
        ref = ref[ref["strategy"] == cfg.name].set_index("day")
        ours = daily.set_index("day")
        both = ours.index.intersection(ref.index)
        out["vs_reference"] = {"reference_days": len(ref), "compared": len(both),
                               "from": str(both.min().date()), "to": str(both.max().date()),
                               "max_abs_diff": {k: float((ours.loc[both, k] - ref.loc[both, k]).abs().max())
                                                for k in ("total", "energy", "fcess", "soc_end")}}
        out["pass"] = len(both) == len(ref) and max(out["vs_reference"]["max_abs_diff"].values()) <= 1e-6
    (_rc.HERE / f"r2_{tag}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
