"""Fetch AEMO WA market advisory files (and directory listings that may point to them) into raw/.

Every attempt is logged to raw/fetched.csv (url, http status or error, fetched_at AWST, bytes, sha256, saved file),
successful or not, so the evidence gap (if any) lists the addresses tried.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/fetch_advisories.py [extra URLs ...]
"""

import csv
import datetime as dt
import hashlib
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
RAW.mkdir(exist_ok=True)
LOG = RAW / "fetched.csv"
AGENT = {"User-Agent": "greenspark-hackathon/0.1"}
BASE = "https://data.wa.aemo.com.au"

CANDIDATES = [
    f"{BASE}/public/public-data/datafiles/post-market-advisory/market-advisories.csv",   # the step-1 file
    f"{BASE}/public/public-data/datafiles/post-market-advisory/",
    f"{BASE}/public/public-data/datafiles/",
    f"{BASE}/public/market-data/wemde/",
    f"{BASE}/datafiles/post-market-advisory/market-advisories.csv",
    f"{BASE}/public/public-data/datafiles/market-advisories/",
]


def awst_now() -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def save_name(url: str) -> str:
    tail = re.sub(r"^https?://", "", url).rstrip("/").replace("/", "__")
    return tail + ("" if re.search(r"\.(csv|json|zip)$", tail) else ".html")


def get(url: str) -> dict:
    row = {"url": url, "fetched_at_awst": awst_now(), "status": "", "bytes": "", "sha256": "", "file": "", "error": ""}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=120) as r:
            body = r.read()
            row["status"] = r.status
    except urllib.error.HTTPError as e:
        row["status"], row["error"] = e.code, str(e)[:200]
        return row
    except Exception as e:  # network blocked, DNS, timeout: logged as the evidence gap
        row["error"] = repr(e)[:200]
        return row
    name = save_name(url)
    (RAW / name).write_bytes(body)
    row.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest(), file=name)
    return row


def main() -> None:
    urls = sys.argv[1:] or CANDIDATES  # explicit URLs replace the default candidates
    new = not LOG.exists()
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["url", "fetched_at_awst", "status", "bytes", "sha256", "file", "error"])
        if new:
            w.writeheader()
        for u in urls:
            row = get(u)
            w.writerow(row)
            print(row)


if __name__ == "__main__":
    main()
