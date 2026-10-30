"""Compares the default tables now in out/tables/ with out/ref/ (no rebuild; _common.compare_npz), e.g. after
time_builds.py rebuilt them again. Writes out/compare_tables.json.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/compare_tables.py
"""

import json

import _common

out = {}
for name in ("v2_value_median.npz", "v2_value_median_h50_state.npz"):
    r = _common.compare_npz(_common.OUT / "tables" / name, _common.OUT / "ref" / name)
    out[name] = {k: r[k] for k in ("identical", "same_keys", "differ", "n_arrays")}
    print(name, out[name])
(_common.OUT / "compare_tables.json").write_text(json.dumps(out, indent=1))
