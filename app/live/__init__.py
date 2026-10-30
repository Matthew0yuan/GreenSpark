"""Live AEMO data for the app (2026-10-29). The research data in data/ stay frozen at their last refresh; the app's live
copy lives in var/live/data (same layout, git-ignored) and the engines read it through GS_DATA.

  fetch.py   downloads from AEMO WA (data.wa.aemo.com.au) and Open-Meteo into var/live/data/raw
  build.py   rebuilds the model inputs there with the models frozen (trained to 2026-06-30), keeping the rows of days
             already built, and records what each day is built from (var/live/state.json)
  today.py   the trading day in progress: its 5-minute prices so far, from AEMO's dispatch solutions
  run.py     the schedule: the 09:00 build once AEMO's 08:00 pre-dispatch run is public, the 5-minute prices every
             5 minutes; also the command line

Run:  .venv/Scripts/python.exe -m app.live.run [--once]
"""
