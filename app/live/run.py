"""The live schedule, run by the app's backend (app/backend/main.py) or from the command line.

  every minute   the trading day in progress: new 5-minute prices (today.update); the storage engine re-reads them
  each morning   once AEMO's yearly files hold the trading day that ended at 08:00 (about 08:21) and the new day's
                 08:00 pre-dispatch run is listed (about 08:35): download, rebuild in a subprocess (build.py, ~3 min),
                 then the engines restart on the new data. Retried every 5 minutes until it works.

No vpp here: the backend imports this module, and vpp reads its data folder when it is imported.

Usage (repository root):
    .venv/Scripts/python.exe -m app.live.run --init     copy data/ into var/live/data (once, ~1 GB), then --once
    .venv/Scripts/python.exe -m app.live.run --once     catch up now: download, rebuild, today's prices
"""

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback

from app.live import fetch, today

ROOT = fetch.ROOT
STATE = fetch.LIVE / "state.json"
LOG = fetch.LIVE / "build.log"
SKIP = {"facility-scada"}  # 1.1 GB the live builds never read
READY_AT = dt.time(8, 25)  # the yearly files hold the day that ended at 08:00 from about 08:21
LATEST = dt.time(10, 0)    # after this, build without the day's pre-dispatch run if it never came
RETRY = dt.timedelta(minutes=5)


def initialised() -> bool:
    return STATE.exists()


def init() -> None:
    """A live copy of the research data (raw, processed, pit), which the live builds extend."""
    src = ROOT / "data"
    for sub in ("processed", "pit"):
        if not (fetch.DATA / sub).exists():
            shutil.copytree(src / sub, fetch.DATA / sub)
    for d in (src / "raw").iterdir():
        if d.name not in SKIP and not (fetch.RAW / d.name).exists():
            (shutil.copytree if d.is_dir() else shutil.copy2)(d, fetch.RAW / d.name)


def state() -> dict | None:
    return json.loads(STATE.read_text()) if STATE.exists() else None


def market_through() -> dt.date | None:
    """The last complete trading day in AEMO's yearly 5-minute price file (newest rows first)."""
    files = sorted((fetch.RAW / "market-clearing-prices").glob("MarketClearingPrices-*.csv"))
    if not files:
        return None
    with files[-1].open() as fh:
        fh.readline()
        first = fh.readline().split(",")[0].strip('"')
    t = dt.datetime.strptime(first, "%d/%m/%Y %H:%M:%S")
    return fetch.trading_day(t)


def build() -> tuple[bool, str]:
    env = {**os.environ, "GS_DATA": str(fetch.DATA), "PYTHONIOENCODING": "utf-8"}
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"\n=== build {fetch.now().isoformat(timespec='seconds')}\n")
        fh.flush()
        p = subprocess.run([sys.executable, "-m", "app.live.build"], cwd=ROOT, env=env, stdout=fh,
                           stderr=subprocess.STDOUT, timeout=1800)
    tail = LOG.read_text(encoding="utf-8").splitlines()[-3:]
    return p.returncode == 0, " | ".join(tail)


def morning(day: dt.date) -> dict:
    """Bring the live data to trading day `day`: {"ok": bool, "detail": str}."""
    fetch.market()
    through = market_through()
    if through is None or through < day - dt.timedelta(days=1):
        return {"ok": False, "wait": True, "detail": f"AEMO's yearly files end on trading day {through}; "
                f"waiting for {day - dt.timedelta(days=1)} (published about 08:21)"}
    fetch.predispatch(day - dt.timedelta(days=1))  # the days already over, from AEMO's archive
    note = ""
    try:
        fetch.predispatch(day)
    except Exception as e:  # noqa: BLE001 - the 08:00 run is listed about 08:35, and gone from current/ by ~23:00
        if fetch.now() < dt.datetime.combine(day, LATEST):
            return {"ok": False, "wait": True, "detail": f"AEMO's 08:00 pre-dispatch run of {day} is not listed yet"}
        note = f"; no 08:00 pre-dispatch run for {day} ({e!r}), so no 09:00 plan for it"
    fetch.weather()
    ok, tail = build()
    return {"ok": ok, "detail": tail + note}


class Schedule:
    """The background thread; on_prices() after new 5-minute prices, on_build() after a rebuild."""

    def __init__(self, on_prices=None, on_build=None):
        self.on_prices, self.on_build = on_prices, on_build
        self.status = {"mode": "live", "prices": None, "build": {"state": "idle", "detail": None, "at": None}}
        self.next_build = fetch.now()
        self.stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self.loop, daemon=True, name="live-schedule").start()

    def due(self, now: dt.datetime) -> dt.date | None:
        day = fetch.trading_day(now)
        s = state()
        # built for today: the day before complete, and today's 09:00 forecast (or it is too late to expect it)
        if s is not None and dt.date.fromisoformat(s["live_day"]) >= day and \
                dt.date.fromisoformat(s["complete_through"]) >= day - dt.timedelta(days=1) and \
                (s["live_forecast_halfhours"] > 0 or s["built_at"] >= dt.datetime.combine(day, LATEST).isoformat()):
            return None
        if now < dt.datetime.combine(day, READY_AT) or now < self.next_build:
            return None
        return day

    def tick(self) -> None:
        now = fetch.now()
        try:
            got = today.update(fetch.trading_day(now))
            self.status["prices"] = {**got, "polled_at": now.isoformat(timespec="seconds")}
            if got["added"] and self.on_prices:
                self.on_prices()
        except Exception as e:  # noqa: BLE001 - shown on /meta, retried next minute
            self.status["prices"] = {"error": repr(e), "polled_at": now.isoformat(timespec="seconds")}
        day = self.due(now)
        if day is None:
            return
        self.status["build"] = {"state": "running", "detail": f"trading day {day}", "at": now.isoformat(timespec="seconds")}
        try:
            r = morning(day)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "detail": repr(e) + " " + traceback.format_exc(limit=2)}
        self.status["build"] = {"state": "done" if r["ok"] else "waiting" if r.get("wait") else "failed",
                                "detail": r["detail"], "at": fetch.now().isoformat(timespec="seconds")}
        if r["ok"]:
            if self.on_build:
                self.on_build()
        else:
            self.next_build = fetch.now() + RETRY

    def loop(self) -> None:
        while not self.stop.is_set():
            self.tick()
            self.stop.wait(60 - time.time() % 60 + 5)  # a few seconds past each minute


def main(argv: list[str]) -> None:
    if "--init" in argv:
        init()
    if not (fetch.DATA / "processed").exists():
        raise SystemExit("no live data yet: run with --init first")
    day = fetch.trading_day(fetch.now())
    r = morning(day)
    print(r)
    if r.get("wait") and not initialised():  # the first build: the days already over, without today's plan
        fetch.predispatch(day - dt.timedelta(days=1))
        fetch.weather()
        print(build())
    print(today.update(day))


if __name__ == "__main__":
    main(sys.argv[1:])
