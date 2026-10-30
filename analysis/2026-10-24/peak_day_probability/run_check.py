"""Run the repository's number and claim checks (agents/check.py, imported read-only) on this task's tables.

Run:  .venv/Scripts/python.exe analysis/2026-10-24/peak_day_probability/run_check.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "agents"))
import check  # noqa: E402


def main() -> None:
    check.utf8_stdout()
    findings = check.check_numbers(HERE / "NUMBERS.csv")
    if (HERE / "CLAIMS.csv").exists():
        findings += check.check_claims(HERE / "CLAIMS.csv")
    check.print_findings(findings)
    bad = [f for f in findings if f[0] == check.FAIL]
    print(f"{len(findings)} findings, {len(bad)} FAIL")


if __name__ == "__main__":
    main()
