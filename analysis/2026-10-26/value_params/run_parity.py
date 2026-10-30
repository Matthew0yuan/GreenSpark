"""PLAN.md, must-pass 2: runs `python -m vpp.v2.checks --parity` from the worktree root with this interpreter (no
bytecode written) and keeps its output in out/parity.txt with the exit code.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/run_parity.py
"""

import os
import subprocess
import sys

import _common

cmd = [sys.executable, "-m", "vpp.v2.checks", "--parity"]
res = subprocess.run(cmd, cwd=_common.ROOT, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
text = f"$ (cwd {_common.ROOT}) {' '.join(cmd)}\n{res.stdout}{res.stderr}exit code: {res.returncode}\n"
(_common.OUT / "parity.txt").write_text(text)
print(text)
print("MUST-PASS 2:", "PASS" if res.returncode == 0 and "ALL CHECKS PASS" in res.stdout else "FAIL")
