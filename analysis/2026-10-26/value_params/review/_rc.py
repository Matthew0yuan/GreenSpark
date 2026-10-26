"""Reviewer's shared setup. code="new": the worktree's vpp (the changed code); code="orig": HEAD's vpp extracted by
`git archive HEAD vpp` into the scratchpad (its data/ and reports/ are symlinks to the worktree's, read only).
No bytecode is written (the repository tracks vpp's __pycache__)."""

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

WT = Path("/Users/matthew.yuan/Desktop/HGS/.claude/worktrees/wf_99c47f23-799-2")
MAIN = Path("/Users/matthew.yuan/Desktop/HGS")
SCR = Path("/private/tmp/claude-1888676191/-Users-matthew-yuan-Desktop-HGS/793652c4-3422-417d-b973-a1ee688bb63d/scratchpad")
TASK = WT / "analysis/2026-10-26/value_params"
HERE = TASK / "review"
NEW_TABLES = SCR / "tables"        # tables the reviewer built with the changed code
ORIG_TABLES = SCR / "orig_tables"  # tables the reviewer built with HEAD's code

GROUPS = {  # name: (power, energy, round trip, wear, CMD, import limit, load scale)
    "default": (10.0, 40.0, 0.85, 30.0, 9.0, 12.0, 10.0),
    "8 MW": (8.0, 40.0, 0.85, 30.0, 9.0, 12.0, 10.0),
    "36 MWh": (10.0, 36.0, 0.85, 30.0, 9.0, 12.0, 10.0),
    "rt 0.88": (10.0, 40.0, 0.88, 30.0, 9.0, 12.0, 10.0),
    "wear 40": (10.0, 40.0, 0.85, 40.0, 9.0, 12.0, 10.0),
    "CMD 10": (10.0, 40.0, 0.85, 30.0, 10.0, 12.0, 10.0),
    "import 8": (10.0, 40.0, 0.85, 30.0, 9.0, 8.0, 10.0),
    "load x12": (10.0, 40.0, 0.85, 30.0, 9.0, 12.0, 12.0),
    "all": (7.5, 30.0, 0.9, 35.0, 10.5, 9.5, 11.0),
}
MONTH, LEVEL, HURDLE = "2026-01", 100.0, 50.0  # the (month, level) solved for the pass-through check


def use(code: str):
    root = WT if code == "new" else SCR / "orig"
    sys.path.insert(0, str(root))
    import vpp
    got = Path(vpp.__file__).resolve()
    if not got.is_relative_to(root.resolve()):
        raise SystemExit(f"vpp imported from {got}, not {root}")
    print(f"[{code}] vpp from {got}")
    return vpp


def compare_npz(a_path: Path, b_path: Path) -> dict:
    """Same names; per array same dtype and shape, array_equal (NaN equal to NaN for floats) and same bytes."""
    import numpy as np
    a, b = np.load(a_path), np.load(b_path)
    ka, kb = sorted(a.files), sorted(b.files)
    differ = []
    for k in ka:
        if k not in kb:
            differ.append(k)
            continue
        x, y = a[k], b[k]
        ok = x.dtype == y.dtype and x.shape == y.shape
        ok = ok and np.array_equal(x, y, equal_nan=x.dtype.kind in "fc") and x.tobytes() == y.tobytes()
        if not ok:
            differ.append(k)
    a.close(), b.close()
    return {"same_keys": ka == kb, "n_arrays": len(ka), "differ": differ, "identical": ka == kb and not differ}
