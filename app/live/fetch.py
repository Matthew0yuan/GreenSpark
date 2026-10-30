"""Downloads for the live data, into var/live/data/raw with the research layout, reusing the scripts/ downloaders.

  market     AEMO's yearly CSVs (5-minute prices, Reference Trading Price, demand, rooftop PV, requirements, ESR
             obligation windows). AEMO appends the trading day that ended at 08:00 at about 08:20 (Extracted At).
  predispatch AEMO's Reference Pre-Dispatch run labelled 08:00 of each trading day, as scripts/download_predispatch.py
             keeps it. Days already over: the day's archive (previous/, from about 08:03 the next morning). The day in
             progress: the run itself from current/ (listed about 08:35; current/ keeps only the last ~15 hours, so the
             08:00 run is gone by about 23:00 - the whole file is kept in var/live/inbox).
  weather    Open-Meteo's archived ECMWF forecasts (scripts/download_weather.py, download_weather_vintages.py).

Times are AWST (UTC+8, no daylight saving). A trading day runs 08:00 to 08:00."""

import csv
import datetime as dt
import re
import sys
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "var" / "live"
DATA = LIVE / "data"
RAW = DATA / "raw"
INBOX = LIVE / "inbox"
AWST = dt.timezone(dt.timedelta(hours=8))
BASE = "https://data.wa.aemo.com.au"
PD_CURRENT = BASE + "/public/market-data/wemde/dispatchSolution/preDispatchData/current"
MARKET = ["price", "demand", "dpv", "requirements", "esr"]  # scripts/download_wem.py dataset filters

sys.path.insert(0, str(ROOT / "scripts"))
import aemo_listing  # noqa: E402
import download_predispatch as dpd  # noqa: E402
import download_weather as dw  # noqa: E402
import download_weather_vintages as dwv  # noqa: E402
import download_wem as dwem  # noqa: E402

dwem.RAW_DIR = RAW
dpd.OUT_DIR = RAW / "predispatch-0800"
dw.OUT_DIR = RAW / "weather-previous-runs"
dwv.OUT_DIR = RAW / "weather-vintages"


def now() -> dt.datetime:
    """AWST wall-clock time, naive (as every timestamp in the data)."""
    return dt.datetime.now(AWST).replace(tzinfo=None)


def trading_day(t: dt.datetime) -> dt.date:
    return (t - dt.timedelta(hours=8)).date()


def market() -> None:
    dwem.main(MARKET)


def _weather_into(out_dir: Path, leads: list[str], days: int) -> list[str]:
    """The last `days` days (to the day after tomorrow) of each location, merged into its CSV: values already there
    stay (an archived forecast does not change), missing ones are filled, new hours added."""
    import json
    import urllib.parse
    import pandas as pd

    end = now().date() + dt.timedelta(days=2)
    log = []
    for name, (lat, lon) in dw.LOCATIONS.items():
        dest = out_dir / f"{name}.csv"
        last = pd.read_csv(dest, usecols=["time"])["time"].max()[:10] if dest.exists() else None
        # at least `days`, and back to a few days before the file's end if it is older (the app was off)
        start = min(end - dt.timedelta(days=days + 2),
                    dt.date.fromisoformat(last) - dt.timedelta(days=3) if last else dw.START)
        params = {"latitude": lat, "longitude": lon, "models": dw.MODEL, "timezone": "Australia/Perth",
                  "start_date": start.isoformat(), "end_date": end.isoformat(),
                  "hourly": ",".join(f"{v}_{lead}" for v in dw.VARIABLES for lead in leads)}
        hourly = json.loads(aemo_listing.fetch(f"{dw.API}?{urllib.parse.urlencode(params)}", timeout=300))["hourly"]
        new = pd.DataFrame(hourly).set_index("time")
        old = pd.read_csv(dest, dtype={"time": str}).set_index("time") if dest.exists() else new.iloc[:0]
        merged = old.combine_first(new)[list(old.columns) or list(new.columns)]
        merged.sort_index().to_csv(dest, na_rep="")
        log.append((name, now().isoformat(timespec="seconds"), len(merged)))
    return [f"{n}: {k} hours" for n, _, k in log] if out_dir != dwv.OUT_DIR else \
        _vintages_log(log)


def _vintages_log(log: list) -> list[str]:
    (dwv.OUT_DIR / "fetched.csv").write_text("location,fetched_at,hours\n" + "\n".join(f"{n},{t},{k}" for n, t, k in log) + "\n")
    return [f"{n}: {k} hours" for n, _, k in log]


def weather(days: int = 14) -> list[str]:
    """ECMWF forecasts from Open-Meteo: the two leads vpp.data uses and the three vintages vpp.spike.sources uses."""
    return [*_weather_into(dw.OUT_DIR, dw.LEADS, days), *_weather_into(dwv.OUT_DIR, dwv.LEADS, days)]


def _write_run(dest: Path, run: str, issued: str, rows: list[dict]) -> None:
    tmp = dest.with_suffix(".tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["run", "issued", "interval", *[c for c, _, _ in dpd.FIELDS]])
        w.writeheader()
        for r in rows:
            w.writerow({"run": run, "issued": issued, **r})
    tmp.replace(dest)


def current_run(day: dt.date, label: str = "0800") -> str:
    """The pre-dispatch run labelled `label` of `day` from current/, saved whole in the inbox and as the day's CSV."""
    name = f"ReferencePre-DispatchSolution_{day:%Y%m%d}{label}.json"
    src = INBOX / name
    if not src.exists():
        INBOX.mkdir(parents=True, exist_ok=True)
        data = aemo_listing.fetch(f"{PD_CURRENT}/{name}", timeout=600)
        tmp = src.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(src)
    text = src.read_text(encoding="utf-8")
    issue = re.search(r'"dispatchDataIssueID":"(\d{14})', text[:2000])
    rows = dpd.extract(text)
    _write_run(dpd.OUT_DIR / f"{day:%Y%m%d}.csv", f"{day:%Y%m%d}{label}", issue.group(1) if issue else "", rows)
    for old in INBOX.glob("ReferencePre-DispatchSolution_*.json"):  # ~125 MB each; the builds read the CSVs
        if old != src:
            old.unlink()
    return f"saved {day:%Y%m%d}.csv from current/{name}: {len(rows)} intervals"


def predispatch(through: dt.date) -> list[str]:
    """Each trading day's 08:00 run up to `through` that is not on disk yet."""
    dpd.OUT_DIR.mkdir(parents=True, exist_ok=True)
    have = sorted(dpd.OUT_DIR.glob("*.csv"))
    day = dt.datetime.strptime(have[-1].stem, "%Y%m%d").date() + dt.timedelta(days=1) if have else through
    today = trading_day(now())
    msgs = []
    while day <= through:
        try:
            msgs.append(dpd.fetch_day(day) if day < today else current_run(day))
        except urllib.error.HTTPError as e:  # the archive is not out yet (08:00-08:05): the run may still be current
            if e.code != 404 or day < today - dt.timedelta(days=1):
                raise
            msgs.append(current_run(day))
        day += dt.timedelta(days=1)
    return msgs
