"""Download post-reform WEM public datasets from data.wa.aemo.com.au.

File lists are read from each dataset's manifest, so new years are picked up
automatically. Past years are skipped if already on disk; the current year is
always re-downloaded because AEMO keeps appending to it.

Usage:
    python3 scripts/download_wem.py            # all datasets
    python3 scripts/download_wem.py prices     # only datasets whose key contains "prices"
"""

import datetime as dt
import re
import sys
import urllib.request
from pathlib import Path

BASE = "https://data.wa.aemo.com.au"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# dataset key -> manifest name
DATASETS = {
    # Q2A targets: 5-min energy + 5 FCESS clearing prices
    "market-clearing-prices": "market-clearing-prices-csv",
    # 30-min energy price (time-weighted average of the 5-min prices)
    "reference-trading-price": "reference-trading-price-csv",
    # forecast features
    "operational-demand": "operational-demand-withdrawal-csv",
    "estimated-dpv": "estimated-dpv-csv",
    "market-requirements": "market-requirements-csv",
    # Q2B: capacity credit obligation windows for storage
    "esr-obligation-intervals": "electric-storage-resource-obligation-intervals",
}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "greenspark-hackathon/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def manifest_entries(manifest: str) -> list[tuple[int, str]]:
    text = fetch(f"{BASE}/manifests/{manifest}.yaml").decode()
    urls = re.findall(r'url:\s*"([^"]+)"', text)
    years = [int(y) for y in re.findall(r"year:\s*(\d{4})", text)]
    return sorted(set(zip(years, urls)))


def main(filters: list[str]) -> None:
    this_year = dt.date.today().year
    for key, manifest in DATASETS.items():
        if filters and not any(f in key for f in filters):
            continue
        out_dir = RAW_DIR / key
        out_dir.mkdir(parents=True, exist_ok=True)
        for year, url in manifest_entries(manifest):
            dest = out_dir / url.rsplit("/", 1)[-1]
            if dest.exists() and year < this_year:
                print(f"skip  {key}/{dest.name}")
                continue
            data = fetch(url)
            dest.write_bytes(data)
            print(f"saved {key}/{dest.name} ({len(data) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1:])
