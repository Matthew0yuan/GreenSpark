"""Reviewer, CHANGED code (the worktree's vpp):
  build   default A, B and the five non-default h50+state tables at path()'s names in the scratchpad's tables/
          (vl.path's directory moved there); compares A, B with out/ref/, the reviewer's original-code build and the
          main workspace's data/processed/; the non-default ones with out/tables/; recomputes items 5 and 6
  cells   value._solve for one (month, level) per parameter group against the original code's direct solve
          (r0_orig.py cells); short_penalty_for against the original SHORT_PENALTY
Writes review/r1_<mode>.json.
Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/review/r1_new.py build|cells
"""

import json
import sys
import time

import _rc

_rc.use("new")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import storage_value as sv  # noqa: E402
from vpp.v2 import value as vl  # noqa: E402

_path = vl.path
OPTS = dict(stat="median", hurdle=50.0, state=True)
GROUPS = {"8 MW": dict(power_mw=8.0), "36 MWh": dict(energy_mwh=36.0), "round trip 0.88": dict(round_trip=0.88),
          "wear $40": dict(wear_per_mwh=40.0), "CMD 10 MW": dict(cmd_mw=10.0)}


def redirected(*a, **k):
    return _rc.NEW_TABLES / _path(*a, **k).name


def mono(z) -> dict:
    out = {}
    for k, axis in (("curve", 1), ("marg", 3), ("curve_state", 2), ("marg_state", 4)):
        x = z[k].astype(float)
        out[k] = int((np.diff(x, axis=axis) > 1e-6).sum() + (x < 0).sum() + np.isnan(x).sum())
    return out


def direction(hi, lo) -> dict:
    hi, lo = hi.astype(float), lo.astype(float)
    drop = lo - hi
    mh, ml = hi.reshape(-1, hi.shape[-1]).mean(0), lo.reshape(-1, lo.shape[-1]).mean(0)
    return {"cells": int(drop.size), "lower": int((drop > 1e-3).sum()), "share": float((drop > 1e-3).mean()),
            "largest_drop": float(drop.max()), "mean_lower_segments": int((ml - mh > 1e-3).sum()),
            "mean_seg5": [float(mh[5]), float(ml[5])]}


def build() -> None:
    _rc.NEW_TABLES.mkdir(parents=True, exist_ok=True)
    vl.path = redirected
    out = {"defaults": {}, "groups": {}}
    for name, kw in {"A": dict(stat="median"), "B": dict(OPTS)}.items():
        t0 = time.perf_counter()
        p = vl.build(rebuild=True, **kw)
        sec = round(time.perf_counter() - t0, 2)
        row = {"file": p.name, "seconds": sec, "json_written": p.with_suffix(".json").exists(),
               "vs_out_ref": _rc.compare_npz(p, _rc.TASK / "out/ref" / p.name),
               "vs_reviewer_orig": _rc.compare_npz(p, _rc.ORIG_TABLES / p.name)}
        main = _rc.MAIN / "data/processed" / p.name
        if main.exists():
            row["vs_main_workspace"] = _rc.compare_npz(p, main)
        out["defaults"][name] = row
        print(name, json.dumps(row))
    for name, kw in GROUPS.items():
        t0 = time.perf_counter()
        p = vl.build(rebuild=True, **OPTS, **kw)
        sec = round(time.perf_counter() - t0, 2)
        z = np.load(p)
        row = {"file": p.name, "seconds": sec, "record": vl.recorded(p), "bad_cells": mono(z),
               "vs_out_tables": _rc.compare_npz(p, _rc.TASK / "out/tables" / p.name),
               "mean_curve_seg5": float(z["curve"][:, 5].astype(float).mean()),
               "mean_curve_seg20": float(z["curve"][:, 20].astype(float).mean())}
        z.close()
        out["groups"][name] = row
        print(name, json.dumps({k: v for k, v in row.items() if k != "record"}))
    d = np.load(redirected(**OPTS))
    r = np.load(redirected(**OPTS, round_trip=0.88))
    w = np.load(redirected(**OPTS, wear_per_mwh=40.0))
    out["default_bad_cells"] = mono(d)
    out["default_mean_curve_seg5"] = float(d["curve"][:, 5].astype(float).mean())
    out["direction"] = {"rt 0.88 vs 0.85": {k: direction(r[k], d[k]) for k in ("curve", "curve_state")},
                        "wear 30 vs 40": {k: direction(d[k], w[k]) for k in ("curve", "curve_state")}}
    print(json.dumps(out["direction"], indent=1))
    (_rc.HERE / "r1_build.json").write_text(json.dumps(out, indent=1, default=str))


def cells() -> None:
    orig = np.load(_rc.SCR / "cells_orig.npz")
    table = vl.prices()
    lv = vl.levels(table, "median")
    flags = vl.regimes()
    ps = sv.price_states(table, pd.Period(_rc.MONTH, "M"), level=lv)
    rtrans = vl.regime_trans(table, pd.Period(_rc.MONTH, "M"), lv, flags, ps)
    out = {"short_penalty_for(9) == original SHORT_PENALTY":
           bool(np.float64(sv.short_penalty_for(9.0)) == orig["short_penalty_9"]),
           "SHORT_PENALTY unchanged": bool(np.float64(sv.SHORT_PENALTY) == orig["short_penalty_9"]),
           "short_penalty_for(10) == RT7 at 10 MW": bool(np.float64(sv.short_penalty_for(10.0)) == orig["short_penalty_10"]),
           "groups": {}}
    for name, (P, E, rt, wear, cmd, imp, scale) in _rc.GROUPS.items():
        params = vl.TableParams(P, E, rt, wear, cmd, imp, scale)
        res = vl._solve((pd.Period(_rc.MONTH, "M"), _rc.LEVEL, ps, params.key(), _rc.HURDLE, rtrans, None))
        got = {"curve": res[2], "marg": res[3], "curve_state": res[5], "marg_state": res[6]}
        row = {"weeks_same": int(res[4]) == int(orig[f"{name}|weeks"])}
        for k, v in got.items():
            o = orig[f"{name}|{k}"]
            row[k] = {"shape_same": v.shape == o.shape, "bytes_same": v.shape == o.shape and v.tobytes() == o.tobytes(),
                      "max_abs": float(np.abs(v.astype(float) - o.astype(float)).max()) if v.shape == o.shape else None}
        dflt = vl._solve((pd.Period(_rc.MONTH, "M"), _rc.LEVEL, ps, vl.DEFAULT_PARAMS.key(), _rc.HURDLE, rtrans, None))
        row["differs_from_default_curve"] = (None if dflt[2].shape != res[2].shape else
                                             float(np.abs(dflt[2].astype(float) - res[2].astype(float)).max()))
        out["groups"][name] = row
        print(name, json.dumps(row))
    (_rc.HERE / "r1_cells.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    {"build": build, "cells": cells}[sys.argv[1]]()
