"""Download AEMO's STEM summary files (one CSV per calendar year of trading dates).

Source: https://data.wa.aemo.com.au/public/public-data/datafiles/stem-summary/stem-summary-YYYY.csv
Columns: Trading Date, Interval Number, Trading Interval, Trading Suspended, Offer Quantity (MWh),
Bid Quantity (MWh), Clearing Quantity (MWh), Clearing Price ($/MWh), Extracted At.

The current year's file is re-extracted every day at about 11:30 AWST and then already holds the next
trading day (the STEM Results Deadline is 11:30 on the Scheduling Day, ESM Rules glossary). Files carry
no publication time per row and are overwritten, so the features set available_at by the rule, not from
these files. Past years are skipped when already on disk; the current year is always re-downloaded.

Output: data/raw/stem-summary/stem-summary-YYYY.csv, manifest.csv (name, url, listed_at, size, fetched_at,
sha256, rows, first_trading_date, last_trading_date, status)
Run:    .venv/bin/python analysis/2026-10-28/scarcity_signals/fetch_stem.py
"""

import datetime as dt
import hashlib
import io
import sys
from pathlib import Path

import pandas as pd

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from aemo_listing import fetch, listing  # noqa: E402

OUT = ROOT / "data" / "raw" / "stem-summary"
YEARS = range(2023, 2027)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {r["name"]: r for r in listing("public/public-data/datafiles/stem-summary")}
    this_year = dt.date.today().year
    rows = []
    for year in YEARS:
        name = f"stem-summary-{year}.csv"
        r = files[name]
        dest = OUT / name
        rec = {**r, "listed_at": r["listed_at"].isoformat(), "fetched_at": "", "status": "cached"}
        if dest.exists() and year < this_year:
            data = dest.read_bytes()
        else:
            data = fetch(r["url"])
            dest.write_bytes(data)
            rec.update(fetched_at=dt.datetime.now().isoformat(timespec="seconds"), status="downloaded")
        df = pd.read_csv(io.BytesIO(data))
        rec.update(sha256=hashlib.sha256(data).hexdigest(), rows=len(df),
                   first_trading_date=df["Trading Date"].min(), last_trading_date=df["Trading Date"].max())
        rows.append(rec)
        print(f"{name}: {rec['status']}, {len(df):,} rows, {rec['first_trading_date']} .. {rec['last_trading_date']}")
    man = OUT / "manifest.csv"
    new = pd.DataFrame(rows)
    if man.exists():  # keep the first fetch of files that did not change
        old = pd.read_csv(man, dtype=str)
        new = pd.concat([old[~old["name"].isin(new["name"])], new], ignore_index=True)
    new.to_csv(man, index=False)


if __name__ == "__main__":
    main()
