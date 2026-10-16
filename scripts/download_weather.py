"""Download archived weather FORECASTS (not observations) from Open-Meteo's Previous Runs API.

`<var>_previous_day1` is the value the model predicted 24h before the valid time, `_previous_day2`
48h before. For a price forecast issued 08:15 on trading day D:
  - intervals of D   (valid up to 08:00 D+1) -> previous_day1 was issued by 08:00 D  -> usable
  - intervals of D+1 (valid up to 08:00 D+2) -> previous_day2 was issued by 08:00 D  -> usable
so these features never see weather information from after the issue time.

Model: ECMWF IFS 0.25 deg, archived with all variables below from 2024-03-08.
Times are requested in Australia/Perth (AWST, no DST).

Output: data/raw/weather-previous-runs/<location>.csv
Usage:  .venv/bin/python scripts/download_weather.py
"""

import datetime as dt
import json
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
MODEL = "ecmwf_ifs025"
START = dt.date(2023, 10, 1)  # earlier than the archive; missing hours come back empty
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "weather-previous-runs"

LOCATIONS = {
    "perth": (-31.95, 115.86),     # demand (temperature) and most rooftop PV
    "midwest": (-30.40, 115.45),   # Badgingarra / Warradarge / Yandin wind farms
    "merredin": (-31.55, 118.40),  # Collgar wind farm, Merredin solar farm
}
VARIABLES = ["temperature_2m", "shortwave_radiation", "cloud_cover", "wind_speed_100m"]
LEADS = ["previous_day1", "previous_day2"]


def fetch(lat: float, lon: float, end: dt.date) -> dict:
    params = {
        "latitude": lat, "longitude": lon, "models": MODEL, "timezone": "Australia/Perth",
        "start_date": START.isoformat(), "end_date": end.isoformat(),
        "hourly": ",".join(f"{v}_{lead}" for v in VARIABLES for lead in LEADS),
    }
    req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}", headers={"User-Agent": "greenspark-hackathon/0.1"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.load(resp)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = dt.date.today() + dt.timedelta(days=2)  # covers D+1 of the latest issue day
    for name, (lat, lon) in LOCATIONS.items():
        hourly = fetch(lat, lon, end)["hourly"]
        cols = list(hourly)
        dest = OUT_DIR / f"{name}.csv"
        with dest.open("w") as fh:
            fh.write(",".join(cols) + "\n")
            for row in zip(*(hourly[c] for c in cols)):
                fh.write(",".join("" if v is None else str(v) for v in row) + "\n")
        print(f"saved {dest.name}: {len(hourly['time'])} hours")


if __name__ == "__main__":
    main()
