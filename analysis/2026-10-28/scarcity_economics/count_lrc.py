"""Count AEMO WEM market advisories that mention a Low Reserve Condition, and how far ahead they were issued.

Only reads a local copy of the public file; changes nothing.
Source file: https://data.wa.aemo.com.au/public/public-data/datafiles/post-market-advisory/market-advisories.csv
Run from the repo root:
    .venv/bin/python analysis/2026-10-28/scarcity_economics/count_lrc.py <path to market-advisories.csv>
"""

import csv
import re
import statistics
import sys
from collections import Counter
from datetime import datetime


def main(path: str) -> None:
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))
    issued = [r["Issued At"] for r in rows if r.get("Issued At")]
    print(f"rows {len(rows)}; Issued At from {min(issued)} to {max(issued)}")
    hits = [r for r in rows if re.search(r"low reserve", r.get("Details") or "", re.I)]
    print(f"advisories mentioning a Low Reserve Condition: {len(hits)}")
    print("by month:", sorted(Counter(r["Issued At"][:7] for r in hits).items()))
    print("withdrawn:", Counter(r.get("Withdrawn") for r in hits))
    lead = []
    for r in hits:
        a = datetime.fromisoformat(r["Issued At"][:19])
        s = datetime.fromisoformat(r["Start Interval"][:19])
        lead.append((s - a).total_seconds() / 3600)
    lead.sort()
    print(f"hours from issue to start interval: median {statistics.median(lead):.1f}, max {lead[-1]:.1f}, "
          f"min {lead[0]:.1f}; issued before start: {sum(x > 0 for x in lead)} of {len(lead)}")
    print("LOR levels named:", Counter(m for r in hits for m in re.findall(r"LOR ?[123]", r["Details"])))


if __name__ == "__main__":
    main(sys.argv[1])
