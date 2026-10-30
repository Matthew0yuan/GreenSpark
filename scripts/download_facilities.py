"""Download the facility reference data the outage features need.

capacity-credits-since-market-start  Capacity Credits per facility and Capacity Year (1 Oct - 30 Sep).
    Credits for a Capacity Year are assigned in the Reserve Capacity Cycle about two years earlier, so
    the value for the year containing an issue time was public before it (vpp.spike.outages uses them as
    the same-basis capacity a full outage removes).
post-facilities  current facility list with Facility Class (Scheduled, Semi-Scheduled, loads, DSP). Only
    the class is used: a static attribute, not a quantity that changes with the outage.

Output: data/raw/facilities/<file>, data/raw/facilities/fetched.csv
Usage:  python scripts/download_facilities.py
"""

import csv
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aemo_listing import BASE, fetch  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "facilities"
MANIFESTS = ["capacity-credits-since-market-start", "post-facilities"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for m in MANIFESTS:
        text = fetch(f"{BASE}/manifests/{m}.yaml").decode()
        for url, generated in re.findall(r'url:\s*"([^"]+)"\s*(?:year:\s*\d+\s*)?generatedAt:\s*"([^"]+)"', text):
            data = fetch(url.replace(" ", "%20"))
            dest = OUT / url.rsplit("/", 1)[-1]
            dest.write_bytes(data)
            rows.append({"manifest": m, "file": dest.name, "url": url, "generated_at": generated,
                         "fetched_at": dt.datetime.now().isoformat(timespec="seconds"), "bytes": len(data)})
            print(f"saved {dest.name} ({len(data) / 1e3:.0f} kB, generated {generated})")
    with (OUT / "fetched.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
