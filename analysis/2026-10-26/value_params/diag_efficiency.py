"""A diagnostic added AFTER seeing report item 6 (not in PLAN.md beforehand): why the 08:00 curve of round trip 0.88 is
mostly below the default's. The idea: a MWh stored at 08:00 is worth what it will sell for (x eta, less wear and
hurdle) when it will be sold, but only what it costs to buy again (/ eta) when the battery would refill it anyway, and
a better round trip makes refilling cheaper. If that is the reason, then with no charging possible the higher round
trip must give the higher curve.

No charging: load x100 (30-80 MW) leaves no room under the CMD in any half-hour (the v2 tables charge no shortfall
penalty, so the table only loses its charging). Pooled tables (h50, no state) at round trip 0.85 and 0.88, in
out/tables/ (vl.path's directory moved there). Writes out/diag_efficiency.json.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/diag_efficiency.py
"""

import json

import _common
import numpy as np

from vpp.v2 import value as vl

TABLES = _common.OUT / "tables"
_path = vl.path
TOL = 1e-3


def main() -> None:
    vl.path = lambda *a, **k: TABLES / _path(*a, **k).name
    out = {}
    for label, load_scale in (("no charging (load x100)", 100.0), ("as built (load x10)", 10.0)):
        p85 = vl.build("median", 40.0, hurdle=50.0, load_scale=load_scale)
        p88 = vl.build("median", 40.0, hurdle=50.0, load_scale=load_scale, round_trip=0.88)
        a, b = np.load(p85), np.load(p88)
        drop = a["curve"].astype(float) - b["curve"].astype(float)  # > 0: 0.88 below 0.85
        out[label] = {"files": [p85.name, p88.name], "cells": int(drop.size), "cells_088_lower": int((drop > TOL).sum()),
                      "share_088_lower": float((drop > TOL).mean()), "largest_drop": float(drop.max()),
                      "mean_curve_085": [round(float(x), 2) for x in a["curve"].astype(float).mean(axis=0)[[0, 5, 10, 20, 30, 39]]],
                      "mean_curve_088": [round(float(x), 2) for x in b["curve"].astype(float).mean(axis=0)[[0, 5, 10, 20, 30, 39]]]}
        a.close(), b.close()
        print(label, json.dumps(out[label]))
    (_common.OUT / "diag_efficiency.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
