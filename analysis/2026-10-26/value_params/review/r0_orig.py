"""Reviewer, ORIGINAL code (HEAD 681bbad, extracted to the scratchpad):
  build   tables A (h75) and B (h50 state) with build(out=...) into the scratchpad's orig_tables/
  cells   one (month, level) task per parameter group solved directly with the original storage_value (CMD patched as
          the module constant, battery and site built by hand) and the original value._marginals / _by_state:
          scratchpad cells_orig.npz, to compare with the changed code's value._solve (r1_new.py cells)
Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/review/r0_orig.py build|cells
"""

import json
import sys
import time
from dataclasses import replace

import _rc

_rc.use("orig")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import site as st  # noqa: E402
from vpp import storage_value as sv  # noqa: E402
from vpp.v2 import value as vl  # noqa: E402

GROUPS, MONTH, LEVEL, HURDLE = _rc.GROUPS, pd.Period(_rc.MONTH, "M"), _rc.LEVEL, _rc.HURDLE


def build() -> None:
    _rc.ORIG_TABLES.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, kw in {"A": dict(), "B": dict(hurdle=50.0, state=True)}.items():
        p = vl.path("median", 40.0, kw.get("hurdle", bt.HURDLE), kw.get("state", False), False)
        t0 = time.perf_counter()
        vl.build("median", 40.0, out=_rc.ORIG_TABLES / p.name, **kw)
        out[name] = {"file": p.name, "seconds": round(time.perf_counter() - t0, 2)}
        print(name, out[name])
    (_rc.SCR / "orig_build.json").write_text(json.dumps(out, indent=1))


def cells() -> None:
    table = vl.prices()
    lv = vl.levels(table, "median")
    flags = vl.regimes()
    ps = sv.price_states(table, MONTH, level=lv)
    rtrans = vl.regime_trans(table, MONTH, lv, flags, ps)
    prefs = replace(bt.PREFS, hurdle_per_mwh=HURDLE)
    at = {**ps, "means": LEVEL + ps["means"]}
    res = {"short_penalty_9": np.float64(sv.SHORT_PENALTY)}
    from vpp import site_bill as sb
    res["short_penalty_10"] = np.float64(sb.RT7(sb.cmd_kva(10.0)).enuc_per_mw())
    for name, (P, E, rt, wear, cmd, imp, scale) in GROUPS.items():
        sv.CMD = cmd  # the original code reads the module constant at call time
        asset = replace(bt.Q2B_ASSET, battery=replace(bt.Q2B_ASSET.battery, power_mw=P, energy_mwh=E, round_trip=rt),
                        wear_per_mwh=wear)
        site = replace(st.FACTORY, import_limit_mw=imp, load_scale=scale)
        load = sv.load_profiles(MONTH, site)
        V, weeks = sv.solve(at, load, asset=asset, prefs=prefs, site=site, short_penalty=0.0)
        curve, marg = vl._marginals(V, ps)
        acts = sv.actions(at, load, asset, prefs, site, 0.0)
        cs, ms = vl._by_state(V, ps, acts, rtrans, E)
        for k, v in (("curve", curve), ("marg", marg), ("curve_state", cs), ("marg_state", ms)):
            res[f"{name}|{k}"] = v.astype(np.float32)
        res[f"{name}|weeks"] = np.int64(weeks)
        print(name, "weeks", weeks, "curve[0,:6]", np.round(curve[0, :6], 2))
    sv.CMD = 9.0
    np.savez(_rc.SCR / "cells_orig.npz", **res)


if __name__ == "__main__":
    {"build": build, "cells": cells}[sys.argv[1]]()
