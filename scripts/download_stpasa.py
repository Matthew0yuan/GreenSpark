"""Download AEMO's daily ST PASA reports (data.wa.aemo.com.au/public/market-data/st-pasa/).

One ZIP per report day. From 2024-06-23 it holds "<day> STPASA DemandForecast.csv/json" (7 days of
30-min operational demand forecasts: Reference / High / Low / AvailableCapacity scenarios, from the
WEMDE week-ahead schedule) and "<day>_ST-PASA_Energy_ESS_Shortfall_Report.json" (shortfalls "as forecast
at 11AM on the trading day the ST PASA report is published"). Earlier reports are a single .xlsx.
File names change over time, so the listing is read rather than guessed.

When was each report public? The listing time is the upload time for 2024-2026 files (11:07-11:37 AWST,
a few minutes after the ZIP members were written), but some 2023 files were re-uploaded weeks later.
So the manifest keeps both the listing time and the newest ZIP member time; vpp.sources.stpasa decides
what to trust.

Output: data/raw/st-pasa/zips/<name>, data/raw/st-pasa/manifest.csv (one row per listed file).
Usage:  python scripts/download_stpasa.py
"""

import csv
import datetime as dt
import hashlib
import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aemo_listing import fetch, listing  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "st-pasa"
FIELDS = ["name", "url", "listed_at", "size", "fetched_at", "sha256", "members", "member_newest", "status"]


def get(row: dict) -> dict:
    dest = OUT / "zips" / row["name"]
    out = {**row, "fetched_at": "", "sha256": "", "members": "", "member_newest": "", "status": ""}
    try:
        if dest.exists() and dest.stat().st_size == row["size"]:
            data = dest.read_bytes()
            out["status"] = "cached"
        else:
            data = fetch(row["url"])
            out["fetched_at"] = dt.datetime.now().isoformat(timespec="seconds")
            dest.write_bytes(data)
            out["status"] = "downloaded"
        out["sha256"] = hashlib.sha256(data).hexdigest()
        if row["name"].lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                infos = zf.infolist()
            out["members"] = "|".join(i.filename for i in infos)
            out["member_newest"] = max(dt.datetime(*i.date_time) for i in infos).isoformat()
    except Exception as exc:  # recorded in the manifest, never skipped silently
        out["status"] = f"error: {exc!r}"
    return out


def main() -> None:
    (OUT / "zips").mkdir(parents=True, exist_ok=True)
    rows = listing("public/market-data/st-pasa")
    listed = dt.datetime.now().isoformat(timespec="seconds")
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(get, rows))
    old = {}
    if (OUT / "manifest.csv").exists():  # keep the first fetch time of files fetched before
        with (OUT / "manifest.csv").open() as fh:
            old = {r["name"]: r for r in csv.DictReader(fh)}
    with (OUT / "manifest.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS + ["listing_read_at"])
        w.writeheader()
        for r in results:
            if not r["fetched_at"] and r["name"] in old:
                r["fetched_at"] = old[r["name"]]["fetched_at"]
            w.writerow({**r, "listed_at": r["listed_at"].isoformat(), "listing_read_at": listed})
    bad = [r for r in results if r["status"].startswith("error")]
    print(f"{len(results)} files listed, {sum(r['status'] == 'downloaded' for r in results)} downloaded, "
          f"{len(bad)} errors: {[r['name'] for r in bad]}")


if __name__ == "__main__":
    main()
