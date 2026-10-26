"""Run agents/check.py on this task folder (NUMBERS.csv formulas and checks, STATUS.md last line).

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_forecast/run_check.py
"""

import sys

sys.dont_write_bytecode = True  # no __pycache__ under agents/

from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from agents import check  # noqa: E402

if __name__ == "__main__":
    sys.exit(check.main([str(HERE)]))
