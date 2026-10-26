"""The new command-line flags of python -m vpp.v2.value, run through vl.main with the tables in out/tables/ (vl.path's
directory moved there, the name still path()'s) so nothing is written elsewhere: each call must find the table already
built for its parameters (no rebuild: the file is not rewritten) and print its summary. Writes out/cli_check.json.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/cli_check.py
"""

import json
import time

import _common

from vpp.v2 import value as vl

TABLES = _common.OUT / "tables"
_path = vl.path
CALLS = (["--power", "8", "--hurdle", "50", "--state"], ["--energy", "36", "--hurdle", "50", "--state"],
         ["--round-trip", "0.88", "--hurdle", "50", "--state"], ["--wear", "40", "--hurdle", "50", "--state"],
         ["--cmd", "10", "--hurdle", "50", "--state"], ["--import-limit", "15", "--hurdle", "50", "--state"])


def main() -> None:
    vl.path = lambda *a, **k: TABLES / _path(*a, **k).name
    rows = []
    for argv in CALLS:
        params = vl.TableParams(**{vl.CLI[f]: float(v) for f, v in zip(argv[0::2], argv[1::2]) if f in vl.CLI})
        p = vl.path(hurdle=50.0, state=True, **params.kwargs())
        before = p.stat().st_mtime_ns
        t0 = time.perf_counter()
        vl.main(argv)
        sec = time.perf_counter() - t0
        rows.append({"argv": argv, "file": p.name, "seconds": round(sec, 2), "rewritten": p.stat().st_mtime_ns != before,
                     "command": vl.command(params, "median", 50.0, True, False)})
    ok = all(not r["rewritten"] for r in rows)
    (_common.OUT / "cli_check.json").write_text(json.dumps({"pass": ok, "calls": rows}, indent=1))
    print("CLI:", "PASS" if ok else "FAIL", json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
