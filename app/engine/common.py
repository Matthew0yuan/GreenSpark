"""Shared helpers of the engine adapter: paths, evaluation periods, JSON-safe numbers, clock labels.

Conventions (analysis/2026-10-25/mvp_architecture/API_CONTRACT.md sections 1-2): a value the model does not give is
None with a reason in `gaps`, never 0; units are in field names; every aggregate carries its period; half-hour arrays
have 48 entries (index 0 = 08:00-08:30 of the trading day), stored-energy arrays 49 (start of each half-hour, then the
end of the day); battery + is discharge."""

import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STANDALONE = ROOT / "analysis" / "2026-10-28" / "app_hurdle50"  # the saved backtests the app replays (run.py there)
FACTORY = ROOT / "analysis" / "2026-10-28" / "app_factory_h50"  # the same behind the factory meter (run.py there)
# A$/MWh margin for forecast error that the plans and offer prices charge on each MWh discharged (RiskPrefs.hurdle_per_mwh).
# The user set 50 for the app on 2026-10-28 (after 15, analysis/2026-10-28/app_hurdle15), e5's own offer margin; the
# research code keeps vpp.backtest.HURDLE (75, chosen in-sample).
HURDLE = 50.0
SAVED = STANDALONE.relative_to(ROOT).as_posix()  # as API sources cite it
# The connection (user, 2026-10-28): behind the factory meter (route m1: CMD 9 MW, IRCR, the synthetic factory load) or
# on its own connection (route sa); either runs e5 (the default) or v1. The saved backtests name each run by strategy.
CONNECTIONS = ("factory", "standalone")
DISPATCHERS = ("e5", "v1")
DEFAULT_CONNECTION, DEFAULT_DISPATCHER = "factory", "e5"
SAVED_DIR = {"factory": FACTORY, "standalone": STANDALONE}
STRATEGY = {("factory", "e5"): "rw50+state t5+roll", ("factory", "v1"): "v1",
            ("standalone", "e5"): "rw50+state t5+roll", ("standalone", "v1"): "forecast+offer(value)"}
PLAN_ONLY, PERFECT = "forecast", "perfect"  # the reference runs in each saved folder's refs_daily.csv


def saved_rel(connection: str) -> str:
    return SAVED_DIR[connection].relative_to(ROOT).as_posix()


def other(dispatcher: str) -> str:
    return "v1" if dispatcher == "e5" else "e5"


def check_mode(connection: str, dispatcher: str) -> None:
    if connection not in CONNECTIONS:
        raise ValueError(f"connection must be one of {CONNECTIONS}")
    if dispatcher not in DISPATCHERS:
        raise ValueError(f"dispatcher must be one of {DISPATCHERS}")


N = 48
FIRST = 2  # the 09:00 plan's first half-hour

FIRST_DAY, CUTOFF, UNSEEN, LAST_DAY = (pd.Timestamp(x) for x in ("2025-04-01", "2026-07-01", "2026-10-16", "2026-10-22"))
PERIODS = {
    "in_sample": {"kind": "in_sample", "label": "Training period", "start": "2025-04-01", "end": "2026-06-30",
                  "used_for_selection": True},
    "test_seen": {"kind": "test_seen", "label": "Test · reviewed", "start": "2026-07-01", "end": "2026-10-15",
                  "used_for_selection": False},
    "test_unseen": {"kind": "test_unseen", "label": "Test · blind", "start": "2026-10-16", "end": "2026-10-22",
                    "used_for_selection": False},
    # live data (app/live): the days after the saved backtests, as AEMO publishes them; the end is set on loading
    "live": {"kind": "live", "label": "Live", "start": "2026-10-23", "end": None, "used_for_selection": False},
}


SAVED_PERIODS = ("in_sample", "test_seen", "test_unseen")  # what the saved backtests (and a full rerun) cover


def period_key(day) -> str:
    d = pd.Timestamp(day)
    return "in_sample" if d < CUTOFF else "test_seen" if d < UNSEEN else "test_unseen" if d <= LAST_DAY else "live"


def period(key: str, days: int) -> dict:
    return {**PERIODS[key], "days": int(days)}


def num(x, nd: int = 6):
    """A JSON number, or None for None / NaN / inf."""
    if x is None:
        return None
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    x = float(x)
    return None if not math.isfinite(x) else round(x, nd) + 0.0


def arr(x, nd: int = 6) -> list:
    return [num(v, nd) for v in x]


def pad(values, first: int = FIRST, n: int = N) -> list:
    """A plan array from `first` on, as an n-entry list with None before."""
    v = list(values)
    return [None] * first + v + [None] * (n - first - len(v))


def clock(slot: int) -> str:
    return (pd.Timestamp("2000-01-01 08:00") + pd.Timedelta(minutes=30 * slot)).strftime("%H:%M")


def weekday_label(day) -> str:
    d = pd.Timestamp(day)
    return f"{d:%a} {d.day} {d:%b %Y}"


def hot_season(day) -> str | None:
    d = pd.Timestamp(day)
    if d.month == 12:
        return f"{d.year}-{(d.year + 1) % 100:02d}"
    if d.month in (1, 2, 3):
        return f"{d.year - 1}-{d.year % 100:02d}"
    return None
