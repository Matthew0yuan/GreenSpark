"""Download ECMWF forecast vintages previous_day1..3 for the spike work (same locations and variables as
scripts/download_weather.py, which only has day1 and day2).

previous_dayN at valid time v comes from the run initialised at floor6h(v - N x 24h) (vpp.spike.sources).
For a forecast issued 09:00 AWST (01:00 UTC) the 18 UTC run of the previous day is not out yet (Open-Meteo
serves IFS 0.25 ~7.4h after initialisation), so targets valid 18-24 UTC need one vintage older than the
day1/day2 pair the price models use: day2 for D+0 and day3 for D+1.

Output: data/raw/weather-vintages/<location>.csv, fetched.csv
Usage:  python scripts/download_weather_vintages.py
"""

import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_weather import API, LOCATIONS, MODEL, START, VARIABLES  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "weather-vintages"
LEADS = ["previous_day1", "previous_day2", "previous_day3"]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = dt.date.today() + dt.timedelta(days=2)
    log = []
    for name, (lat, lon) in LOCATIONS.items():
        params = {"latitude": lat, "longitude": lon, "models": MODEL, "timezone": "Australia/Perth",
                  "start_date": START.isoformat(), "end_date": end.isoformat(),
                  "hourly": ",".join(f"{v}_{lead}" for v in VARIABLES for lead in LEADS)}
        req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}", headers={"User-Agent": "greenspark-hackathon/0.1"})
        with urllib.request.urlopen(req, timeout=600) as resp:
            hourly = json.load(resp)["hourly"]
        cols = list(hourly)
        with (OUT_DIR / f"{name}.csv").open("w") as fh:
            fh.write(",".join(cols) + "\n")
            for row in zip(*(hourly[c] for c in cols)):
                fh.write(",".join("" if v is None else str(v) for v in row) + "\n")
        log.append(f"{name},{dt.datetime.now().isoformat(timespec='seconds')},{len(hourly['time'])}")
        print(f"saved {name}.csv: {len(hourly['time'])} hours")
    (OUT_DIR / "fetched.csv").write_text("location,fetched_at,hours\n" + "\n".join(log) + "\n")


if __name__ == "__main__":
    main()
