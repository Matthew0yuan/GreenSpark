"""PLAN.md, report items 4-6: tables of other batteries and sites, with the formal options (--hurdle 50 --state).

  4  wall time of build(rebuild=True) for each group (one parameter changed each), with the default's alongside
  5  every array of values (curve, marg, curve_state, marg_state) non-increasing in stored energy (diff <= 1e-6) and
     >= 0: the cells that are not
  6  direction of the 08:00 curve: round trip 0.88 against the default 0.85, and the default wear $30 against $40
     (the higher efficiency / lower wear should not be lower): cells more than 1e-3 $/MWh lower, their share, the
     largest drop, and whether the curve averaged over the days drops in any segment
Tables go to out/tables/ (vl.path's directory moved there, the name still path()'s). Writes out/build_params.json.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/build_params.py
"""

import json
import time

import _common
import numpy as np

from vpp.v2 import value as vl

TABLES = _common.OUT / "tables"
REF = _common.OUT / "ref"
_path = vl.path
OPTS = dict(stat="median", hurdle=50.0, state=True)
GROUPS = {"default": {}, "8 MW": dict(power_mw=8.0), "36 MWh": dict(energy_mwh=36.0), "round trip 0.88": dict(round_trip=0.88),
          "wear $40": dict(wear_per_mwh=40.0), "CMD 10 MW": dict(cmd_mw=10.0)}
TOL_MONO, TOL_DIR = 1e-6, 1e-3
SEGMENTS = (0, 5, 10, 20, 30, 35)


def redirected(*a, **k):
    return TABLES / _path(*a, **k).name


def shape_checks(z) -> dict:
    out = {}
    for k, axis in (("curve", 1), ("marg", 3), ("curve_state", 2), ("marg_state", 4)):
        x = z[k].astype(float)
        out[k] = {"shape": list(x.shape), "rising_cells": int((np.diff(x, axis=axis) > TOL_MONO).sum()),
                  "negative_cells": int((x < 0).sum()), "nan_cells": int(np.isnan(x).sum())}
    return out


def direction(hi: np.ndarray, lo: np.ndarray) -> dict:
    """hi should not be below lo (same shape; last axis the segments)."""
    hi, lo = hi.astype(float), lo.astype(float)
    drop = lo - hi
    mean_hi, mean_lo = hi.reshape(-1, hi.shape[-1]).mean(axis=0), lo.reshape(-1, lo.shape[-1]).mean(axis=0)
    return {"cells": int(drop.size), "cells_lower": int((drop > TOL_DIR).sum()), "share_lower": float((drop > TOL_DIR).mean()),
            "largest_drop": float(drop.max()), "largest_rise": float(-drop.min()),
            "mean_curve_lower_segments": [int(s) for s in np.flatnonzero(mean_lo - mean_hi > TOL_DIR)],
            "mean_gain_by_segment": {int(s): float(mean_hi[s] - mean_lo[s]) for s in SEGMENTS if s < len(mean_hi)}}


def main() -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    vl.path = redirected
    out = {"options": OPTS, "groups": {}}
    paths = {}
    for name, kw in GROUPS.items():
        t0 = time.perf_counter()
        p = vl.build(**OPTS, rebuild=True, **kw)
        sec = time.perf_counter() - t0
        paths[name] = p
        rec = vl.recorded(p)
        z = np.load(p)
        row = {"file": p.name, "seconds": round(sec, 2), "days": int(len(z["day"])), "n_tables": int(z["n_tables"]),
               "weeks_max": int(z["weeks"].max()), "json": rec, "checks": shape_checks(z),
               "mean_curve": {int(s): float(z["curve"][:, s].astype(float).mean()) for s in SEGMENTS if s < z["curve"].shape[1]}}
        z.close()
        if name == "default":
            row["identical_to_ref"] = _common.compare_npz(p, REF / p.name)["identical"]
        out["groups"][name] = row
        bad = sum(v["rising_cells"] + v["negative_cells"] + v["nan_cells"] for v in row["checks"].values())
        print(f"{name}: {p.name} {sec:.1f} s, {row['days']} days, json {'yes' if rec else 'no'}; "
              f"non-increasing and >= 0: {'PASS' if bad == 0 else f'FAIL ({bad} cells)'}; mean 08:00 curve "
              + " / ".join(f"{v:.1f}" for v in row["mean_curve"].values()))
    d = np.load(paths["default"])
    for name in GROUPS:
        if name == "default":
            continue
        z = np.load(paths[name])
        same_shape = z["curve"].shape == d["curve"].shape
        out["groups"][name]["vs_default_curve_max_abs_diff"] = (
            float(np.abs(z["curve"].astype(float) - d["curve"].astype(float)).max()) if same_shape else None)
        z.close()
    pairs = {"round trip 0.88 vs 0.85": (paths["round trip 0.88"], paths["default"]),
             "wear $30 vs $40": (paths["default"], paths["wear $40"])}
    out["direction"] = {}
    for label, (ph, pl) in pairs.items():
        h, low = np.load(ph), np.load(pl)
        res = {k: direction(h[k], low[k]) for k in ("curve", "curve_state")}
        out["direction"][label] = res
        h.close(), low.close()
        c = res["curve"]
        print(f"direction {label}: 08:00 curve lower in {c['cells_lower']} of {c['cells']} cells "
              f"({c['share_lower']:.2%}), largest drop {c['largest_drop']:.3f} $/MWh; mean curve lower in segments "
              f"{c['mean_curve_lower_segments']}; curve_state lower in {res['curve_state']['cells_lower']} cells")
    d.close()
    (_common.OUT / "build_params.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
