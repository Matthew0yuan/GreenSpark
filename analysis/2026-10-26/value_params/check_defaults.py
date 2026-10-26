"""PLAN.md, must-pass 1 (after the code change): the default parameters rebuild tables A and B bit for bit.

  - path() of the defaults (and of the option combinations that existed before: energy, hurdle, state, regime, mean)
    gives exactly the old name (the old formula is copied below);
  - A and B rebuilt with build(rebuild=True) at path()'s names, in out/tables/ (vl.path's directory moved there, the
    name still path()'s), each compared with out/ref/ array by array (_common.compare_npz: names, dtype, shape,
    np.array_equal with NaN equal to NaN, bytes);
  - no .json written beside a default table.
Writes out/check_defaults.json.

Run (worktree root):  PYTHONDONTWRITEBYTECODE=1 /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/check_defaults.py
"""

import json
import time

import _common

from vpp import backtest as bt
from vpp.v2 import value as vl

TABLES = _common.OUT / "tables"
REF = _common.OUT / "ref"
_path = vl.path


def redirected(*a, **k):
    return TABLES / _path(*a, **k).name


def old_name(stat="median", energy_mwh=40.0, hurdle=bt.HURDLE, state=False, regime=False) -> str:
    """vpp.v2.value.path's name before the change (HEAD 681bbad), copied."""
    return (f"v2_value_{stat}{'' if energy_mwh == 40.0 else f'_{energy_mwh:g}mwh'}"
            f"{'' if hurdle == bt.HURDLE else f'_h{hurdle:g}'}{'_state' if state else ''}"
            f"{'_regime' if regime else ''}.npz")


COMBOS = [dict(), dict(energy_mwh=36.0), dict(stat="mean"), dict(hurdle=50.0), dict(hurdle=0.0), dict(hurdle=25.0),
          dict(hurdle=50.0, state=True), dict(hurdle=50.0, state=True, regime=True), dict(energy_mwh=36.0, hurdle=50.0)]
BUILDS = {"A": dict(stat="median", energy_mwh=40.0), "B": dict(stat="median", energy_mwh=40.0, hurdle=50.0, state=True)}


def main() -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    out = {"names": [], "tables": {}}
    for c in COMBOS:
        new, old = _path(**c).name, old_name(**c)
        out["names"].append({"options": c, "new": new, "old": old, "same": new == old})
    # the import limit enters the table only as min(import, CMD): 15 MW (above the CMD) is the default's table
    out["names"].append({"options": {"hurdle": 50.0, "state": True, "import_limit_mw": 15.0},
                         "new": _path(hurdle=50.0, state=True, import_limit_mw=15.0).name,
                         "old": old_name(hurdle=50.0, state=True),
                         "same": _path(hurdle=50.0, state=True, import_limit_mw=15.0).name == old_name(hurdle=50.0, state=True)})
    names_ok = all(r["same"] for r in out["names"])
    print(f"{'PASS' if names_ok else 'FAIL'}  path() names of the defaults as before: "
          f"{sum(r['same'] for r in out['names'])} of {len(out['names'])}")
    vl.path = redirected
    ok = names_ok
    for name, kw in BUILDS.items():
        t0 = time.perf_counter()
        p = vl.build(**kw, rebuild=True)
        sec = time.perf_counter() - t0
        cmp = _common.compare_npz(p, REF / p.name)
        json_written = p.with_suffix(".json").exists()
        passed = p.name == old_name(**kw) and cmp["identical"] and not json_written
        ok &= passed
        out["tables"][name] = {"file": p.name, "seconds": round(sec, 2), "no_json": not json_written,
                               "pass": passed, **cmp}
        print(f"{'PASS' if passed else 'FAIL'}  {name} {p.name} rebuilt in {sec:.1f} s: {cmp['n_arrays']} arrays, "
              f"identical to out/ref/ {cmp['identical']} (differ {cmp['differ']}), json written {json_written}")
    out["pass"] = bool(ok)
    (_common.OUT / "check_defaults.json").write_text(json.dumps(out, indent=1))
    print("MUST-PASS 1:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
