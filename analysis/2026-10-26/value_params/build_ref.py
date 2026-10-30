"""Step 1 of PLAN.md, run BEFORE any code change: the reference tables built by the original code.

  A  build("median", 40.0)                          -> out/ref/v2_value_median.npz            (h75, pooled)
  B  build("median", 40.0, hurdle=50, state=True)   -> out/ref/v2_value_median_h50_state.npz  (e5 / G5B's table)

Writes out/ref/build_ref.json (wall times, the git state of vpp/ at the time) and, for information only, compares each
with the main workspace's table of the same name (read only).

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/build_ref.py
"""

import json
import subprocess
import time
from pathlib import Path

import _common
import numpy as np

from vpp.v2 import value as vl

MAIN_PROCESSED = Path("/Users/matthew.yuan/Desktop/HGS/data/processed")
TABLES = {"A": dict(stat="median", energy_mwh=40.0), "B": dict(stat="median", energy_mwh=40.0, hurdle=50.0, state=True)}


def compare(a_path: Path, b_path: Path) -> dict:
    full = _common.compare_npz(a_path, b_path)
    a, b = np.load(a_path), np.load(b_path)
    days = [int(len(a["day"])), int(len(b["day"]))]
    a.close(), b.close()
    return {k: full[k] for k in ("identical", "same_keys", "differ", "n_arrays")} | {"days": days}


def main() -> None:
    ref = _common.OUT / "ref"
    ref.mkdir(parents=True, exist_ok=True)
    status = subprocess.run(["git", "status", "--short", "vpp"], cwd=_common.ROOT, capture_output=True, text=True).stdout
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_common.ROOT, capture_output=True, text=True).stdout.strip()
    rows = {"head": head, "vpp_changes_at_build": status, "tables": {}}
    for name, kw in TABLES.items():
        p = ref / vl.path(**{k: v for k, v in kw.items()}).name
        t0 = time.perf_counter()
        vl.build(**kw, out=p)
        sec = time.perf_counter() - t0
        z = np.load(p)
        row = {"file": p.name, "seconds": round(sec, 2), "days": int(len(z["day"])), "first": str(z["day"][0]),
               "last": str(z["day"][-1]), "n_tables": int(z["n_tables"]), "arrays": sorted(z.files)}
        z.close()
        main_p = MAIN_PROCESSED / p.name
        row["vs_main_workspace"] = compare(p, main_p) if main_p.exists() else "missing"
        first = _common.OUT / "ref_first" / p.name  # an earlier build by the same original code (determinism)
        row["vs_first_build"] = compare(p, first) if first.exists() else "missing"
        rows["tables"][name] = row
        print(f"{name}: {p.name} {sec:.1f} s, {row['days']} days ({row['first']} .. {row['last']}); "
              f"vs main workspace: {row['vs_main_workspace']}; vs the first build: {row['vs_first_build']}")
    (ref / "build_ref.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
