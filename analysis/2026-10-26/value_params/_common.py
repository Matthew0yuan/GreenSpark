"""Shared by this task's scripts: the worktree root first on sys.path, and a check that vpp is imported from it."""

import os
import sys
from pathlib import Path

# the repository tracks vpp's __pycache__: write no bytecode here or in the worker processes (they inherit the env)
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
if sys.path[:1] != [str(ROOT)]:
    sys.path.insert(0, str(ROOT))

import vpp  # noqa: E402

if not Path(vpp.__file__).resolve().is_relative_to(ROOT):
    raise SystemExit(f"vpp imported from {vpp.__file__}, not from the worktree {ROOT}")

import numpy as np  # noqa: E402


def compare_npz(a_path: Path, b_path: Path) -> dict:
    """Array by array: the same names, and for each the same dtype and shape, np.array_equal (NaN equal to NaN: a
    plain np.array_equal calls an array with a NaN unequal to itself) and the same bytes (the strictest)."""
    a, b = np.load(a_path), np.load(b_path)
    keys_a, keys_b = sorted(a.files), sorted(b.files)
    per = {}
    for k in keys_a:
        if k not in keys_b:
            continue
        x, y = a[k], b[k]
        same_type = x.dtype == y.dtype and x.shape == y.shape
        nan_ok = x.dtype.kind in "fc"
        per[k] = {"dtype": str(x.dtype), "shape": list(x.shape), "same_dtype_shape": bool(same_type),
                  "array_equal": bool(same_type and np.array_equal(x, y, equal_nan=nan_ok)),
                  "array_equal_plain": bool(same_type and np.array_equal(x, y)),
                  "bytes_equal": bool(same_type and x.tobytes() == y.tobytes()),
                  "nan_count": int(np.isnan(x).sum()) if nan_ok else 0}
    a.close(), b.close()
    differ = [k for k, v in per.items() if not (v["array_equal"] and v["bytes_equal"])]
    return {"same_keys": keys_a == keys_b, "keys_only_a": sorted(set(keys_a) - set(keys_b)),
            "keys_only_b": sorted(set(keys_b) - set(keys_a)), "n_arrays": len(keys_a), "differ": differ,
            "identical": keys_a == keys_b and not differ, "arrays": per}
