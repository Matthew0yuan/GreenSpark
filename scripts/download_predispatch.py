"""Download AEMO's Reference Pre-Dispatch price forecast, one run per trading day.

Each daily archive (~200 MB) holds the 48 half-hourly pre-dispatch runs of that trading day.
We only want the 08:00 run: issued at the start of trading day D, it forecasts every
30-min interval of D (0-24h ahead) and D+1 (24-48h ahead). HTTP range requests let
zipfile read just the central directory and that one member (~5 MB), so the full
history is a few GB instead of hundreds.

Per-interval prices plus a few system-level quantities are extracted with regexes over
the flat sub-objects (the full JSON is ~120 MB per run, mostly constraint detail).

Output: data/raw/predispatch-0800/<YYYYMMDD>.csv (one file per trading day; resumable).

Usage:
    .venv/bin/python scripts/download_predispatch.py [START YYYY-MM-DD] [END YYYY-MM-DD]
"""

import csv
import datetime as dt
import io
import re
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ARCHIVE = "https://data.wa.aemo.com.au/public/market-data/wemde/dispatchSolution/preDispatchData/previous"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "predispatch-0800"

# (column, JSON object, key inside it)
FIELDS = [
    ("energy", "prices", "energy"),
    ("cont_raise", "prices", "contingencyRaise"),
    ("cont_lower", "prices", "contingencyLower"),
    ("reg_raise", "prices", "regulationRaise"),
    ("reg_lower", "prices", "regulationLower"),
    ("rocof", "prices", "rocof"),
    ("req_energy_mw", "marketServiceRequirements", "energy"),
    ("demand_level_mw", "contingencySolution", "demandLevel"),
    ("dpv_level_mw", "contingencySolution", "dpvLevel"),
    ("in_service_injection_mw", "inServiceQuantities", "energyInjectionCapacity"),
    ("available_injection_mw", "availableQuantities", "energyInjectionCapacity"),
    ("dispatch_injection_mw", "dispatchTotal", "energyInjectionCapacity"),
    ("dispatch_withdrawal_mw", "dispatchTotal", "energyWithdrawalCapacity"),
    ("energy_deficit_mw", "marketShortfalls", "energyDeficit"),
]
OBJECTS = sorted({obj for _, obj, _ in FIELDS})
# Start of each solutionData element; older runs omit the "scenario" field before it.
HEADER_RE = re.compile(r'[{,]"dispatchInterval":"([^"]+)"')
OBJECT_RES = {obj: re.compile(rf'"{obj}":(\{{[^{{}}]*\}})') for obj in OBJECTS}
NUMBER_RE = re.compile(r'"(\w+)":(-?[\d.eE+-]+)')


class HttpRangeFile(io.RawIOBase):
    """Seekable read-only view of a remote file, fetched lazily with HTTP Range requests."""

    READAHEAD = 256 * 1024

    def __init__(self, url: str):
        self.url = url
        self.pos = 0
        self.block_start, self.block = 0, b""
        self.size = self._fetch_size()

    def _get(self, start: int, end: int) -> bytes:
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "greenspark-hackathon/0.1"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            if start == 0 and end == 0:
                self._content_range = resp.headers["Content-Range"]
            return resp.read()

    def _fetch_size(self) -> int:
        self._get(0, 0)
        return int(self._content_range.rsplit("/", 1)[1])

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = base + offset
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        lo = self.pos - self.block_start
        if not (0 <= lo and lo + n <= len(self.block)):
            end = min(self.size, self.pos + max(n, self.READAHEAD)) - 1
            self.block_start, self.block = self.pos, self._get(self.pos, end)
            lo = 0
        out = self.block[lo:lo + n]
        self.pos += len(out)
        return out


def pick_member(names: list[str], day: dt.date) -> str:
    """The 08:00 run of the trading day, or the earliest run in the archive if it is missing."""
    runs = sorted(n for n in names if re.search(r"_\d{12}\.json$", n))
    wanted = [n for n in runs if n.endswith(f"_{day:%Y%m%d}0800.json")]
    return wanted[0] if wanted else runs[0]


def extract(text: str) -> list[dict]:
    starts = [(m.start(), m.group(1)) for m in HEADER_RE.finditer(text)]
    if len({iv for _, iv in starts}) != len(starts):
        raise ValueError("duplicate dispatchInterval headers; JSON layout changed?")
    rows = []
    for i, (pos, interval) in enumerate(starts):
        segment = text[pos: starts[i + 1][0] if i + 1 < len(starts) else len(text)]
        objects = {}
        for obj, rx in OBJECT_RES.items():
            m = rx.search(segment)
            objects[obj] = dict(NUMBER_RE.findall(m.group(1))) if m else {}
        row = {"interval": interval}
        for col, obj, key in FIELDS:
            row[col] = objects[obj].get(key, "")
        rows.append(row)
    return rows


def fetch_day(day: dt.date) -> str:
    dest = OUT_DIR / f"{day:%Y%m%d}.csv"
    if dest.exists():
        return f"skip  {dest.name}"
    url = f"{ARCHIVE}/DispatchSolutionPreDispatch_{day:%Y%m%d}.zip"
    with zipfile.ZipFile(HttpRangeFile(url)) as zf:
        member = pick_member(zf.namelist(), day)
        text = zf.read(member).decode()
    run = re.search(r"_(\d{12})\.json$", member).group(1)
    issue = re.search(r'"dispatchDataIssueID":"(\d{14})', text[:2000])
    rows = extract(text)
    tmp = dest.with_suffix(".tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["run", "issued", "interval", *[c for c, _, _ in FIELDS]])
        w.writeheader()
        for r in rows:
            w.writerow({"run": run, "issued": issue.group(1) if issue else "", **r})
    tmp.rename(dest)
    return f"saved {dest.name}: run {run}, {len(rows)} intervals"


def main(start: dt.date, end: dt.date, workers: int = 6) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    days = [start + dt.timedelta(d) for d in range((end - start).days + 1)]
    failed = []
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(fetch_day, d): d for d in days}
        for i, fut in enumerate(as_completed(futures), 1):
            day = futures[fut]
            try:
                msg = fut.result()
            except Exception as exc:  # keep going; failures are reported and can be retried
                failed.append(day)
                msg = f"FAIL  {day}: {exc!r}"
            print(f"[{i}/{len(days)}] {msg}", flush=True)
    print(f"done; {len(failed)} failed: {[str(d) for d in failed]}")


if __name__ == "__main__":
    args = [dt.date.fromisoformat(a) for a in sys.argv[1:3]]
    start = args[0] if args else dt.date(2023, 10, 1)
    end = args[1] if len(args) > 1 else dt.date.today() - dt.timedelta(days=1)
    main(start, end)
