"""The trading day in progress: its 5-minute clearing prices so far, from AEMO's Reference Dispatch Solutions.

AEMO publishes one dispatch solution per 5-minute dispatch interval in dispatchData/current/ (about 30 MB each, listed
about 3 minutes after the interval starts; the folder holds the trading day since 08:00). Each solution's first
element is its own (primary) interval, and that element's "prices" object - energy and the five FCESS - sits in the
first ~1.3 MB, so only the head of each file is fetched (HTTP Range), widened if the prices are further in.

These are the dispatch prices as cleared; the yearly MarketClearingPrices file, which replaces them the next morning,
can differ where AEMO re-priced an interval (its "Failure Reason").

Output: var/live/data/today/<YYYYMMDD>.csv, one row per dispatch interval (appended; resumable)."""

import csv
import datetime as dt
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.live import fetch

CURRENT = "public/market-data/wemde/dispatchSolution/dispatchData/current"
OUT = fetch.DATA / "today"
HEAD = 2_000_000  # bytes fetched first
NAME = re.compile(r"ReferenceDispatchSolution_(\d{12})\.json$")
PRIMARY = re.compile(r'"primaryDispatchInterval":"([^"]+)"')
ISSUE = re.compile(r'"dispatchDataIssueID":"([^"]+)"')
INTERVAL = re.compile(r'"dispatchInterval":"([^"]+)"')
PRICES = re.compile(r'"prices":(\{[^{}]*\})')
KEYS = {"energy": "energy", "cont_raise": "contingencyRaise", "cont_lower": "contingencyLower",
        "reg_raise": "regulationRaise", "reg_lower": "regulationLower", "rocof": "rocof"}
FIELDS = ["interval", *KEYS, "issue_id", "listed_at", "fetched_at"]


def path(day: dt.date) -> Path:
    return OUT / f"{day:%Y%m%d}.csv"


def _interval(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).astimezone(fetch.AWST).replace(tzinfo=None)


def parse(head: bytes) -> dict | None:
    """The primary interval's prices from the head of a dispatch solution, or None if they are not in it yet."""
    text = head.decode("utf-8", "replace")
    m, p = PRIMARY.search(text), PRICES.search(text)
    if not m or not p:
        return None
    ivs = [x.group(1) for x in INTERVAL.finditer(text, 0, p.start())]
    if len(set(ivs)) != 1 or ivs[0] != m.group(1):  # the first prices object must be the primary interval's
        raise ValueError(f"dispatch solution layout changed: prices after intervals {sorted(set(ivs))}")
    prices = json.loads(p.group(1))
    issue = ISSUE.search(text)
    return {"interval": _interval(m.group(1)).isoformat(sep=" "), **{k: prices[v] for k, v in KEYS.items()},
            "issue_id": issue.group(1) if issue else ""}


def one(row: dict) -> dict:
    n = HEAD
    while True:
        got = parse(aemo_listing_fetch(row["url"], n))
        if got is not None:
            return {**got, "listed_at": row["listed_at"].isoformat(sep=" "),
                    "fetched_at": fetch.now().isoformat(sep=" ", timespec="seconds")}
        if n >= row["size"]:
            raise ValueError(f"no prices in {row['name']}")
        n *= 4


def aemo_listing_fetch(url: str, first_bytes: int) -> bytes:
    return fetch.aemo_listing.fetch(url, timeout=120, first_bytes=first_bytes)


def update(day: dt.date | None = None, workers: int = 6) -> dict:
    """Fetch every dispatch interval of `day` (the trading day in progress) listed and not on disk yet."""
    day = day or fetch.trading_day(fetch.now())
    OUT.mkdir(parents=True, exist_ok=True)
    have = set()
    if path(day).exists():
        with path(day).open() as fh:
            have = {r["interval"] for r in csv.DictReader(fh)}
    start = dt.datetime.combine(day, dt.time(8))
    todo = []
    for r in fetch.aemo_listing.listing(CURRENT):
        m = NAME.search(r["name"])
        if not m:
            continue
        t = dt.datetime.strptime(m.group(1), "%Y%m%d%H%M")
        if start <= t < start + dt.timedelta(days=1) and t.isoformat(sep=" ") not in have:
            todo.append(r)
    with ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(one, todo))
    new = not path(day).exists()
    with path(day).open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for r in sorted(rows, key=lambda x: x["interval"]):
            w.writerow(r)
    last = max([*have, *(r["interval"] for r in rows)], default=None)
    return {"day": str(day), "added": len(rows), "intervals": len(have) + len(rows), "last_interval": last}


if __name__ == "__main__":
    print(update())
