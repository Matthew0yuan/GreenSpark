"""Report item 4 again, added after seeing the single timings of build_params.py vary (the same default table took
4.5-16.8 s across runs; the machine is shared with other agents): each group of build_params.py rebuilt REPEATS
times in turn (round-robin, so a slow spell hits every group alike), wall time of build(rebuild=True); min, median and
max per group. The single timings of PLAN.md stay as planned (out/build_params.json). Writes out/time_builds.json.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/time_builds.py
"""

import json
import os
import statistics
import time

import _common

from vpp.v2 import value as vl
from build_params import GROUPS, OPTS

TABLES = _common.OUT / "tables"
_path = vl.path
REPEATS = 3


def main() -> None:
    vl.path = lambda *a, **k: TABLES / _path(*a, **k).name
    times = {name: [] for name in GROUPS}
    for _ in range(REPEATS):
        for name, kw in GROUPS.items():
            t0 = time.perf_counter()
            vl.build(**OPTS, rebuild=True, **kw)
            times[name].append(round(time.perf_counter() - t0, 2))
    out = {"repeats": REPEATS, "cpus": os.cpu_count(), "load_avg_after": os.getloadavg(),
           "groups": {n: {"seconds": t, "min": min(t), "median": statistics.median(t), "max": max(t)} for n, t in times.items()}}
    (_common.OUT / "time_builds.json").write_text(json.dumps(out, indent=1))
    for n, r in out["groups"].items():
        print(f"{n}: median {r['median']:.1f} s (min {r['min']:.1f}, max {r['max']:.1f}) {r['seconds']}")
    print("load average after:", out["load_avg_after"])


if __name__ == "__main__":
    main()
