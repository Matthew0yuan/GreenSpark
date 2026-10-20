"""The site behind the battery's grid connection (Q2B: "the site's own load").

The battery and the site's load share one connection point. The battery can only charge as fast as
the import limit leaves room for after the site's load, and FCESS headroom has to fit inside the same
limits.

The hackathon data has no site meter data, so the backtest uses an example commercial site
(example_load); use load_csv for a real site's 30-min load when one is available. FACTORY is the
project's site since 2026-10-19: the example site scaled to a factory of 3-9 MW, behind whose meter
the 10 MW / 40 MWh battery sits (import 12 MW, export 10 MW; both assumed).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Site:
    import_limit_mw: float = 1.2  # most the connection may draw from the grid
    export_limit_mw: float = 1.0  # most it may send to the grid
    load_margin: float = 0.16     # plans assume the site may draw this much more than forecast (2x example noise)
    load_scale: float = 1.0       # the site's load is the example (or CSV) load times this


FACTORY = Site(import_limit_mw=12.0, export_limit_mw=10.0, load_margin=0.16, load_scale=10.0)


def example_load(ts: pd.DatetimeIndex, noise: float = 0.08, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """(forecast, actual) load in MW of an example mid-size commercial site, per interval in ts.

    Weekdays: 0.30 MW overnight, ramping to 0.80 MW for 08:00-17:00 and back to 0.35 MW by 20:00.
    Weekends: 0.45 MW for 08:00-17:00, 0.30 MW otherwise. +15% for 10:00-18:00 in Dec-Mar (cooling).
    The forecast is this profile; the actual adds AR(1) noise of relative size `noise`.
    """
    h = np.asarray(ts.hour + ts.minute / 60, float)
    weekday = np.asarray(ts.dayofweek < 5)
    workday = np.select([h < 6, h < 8, h < 17, h < 20], [0.30, 0.30 + 0.25 * (h - 6), 0.80, 0.80 - 0.15 * (h - 17)], 0.35)
    weekend = np.where((h >= 8) & (h < 17), 0.45, 0.30)
    cooling = np.asarray(ts.month.isin([12, 1, 2, 3])) & (h >= 10) & (h < 18)
    profile = np.where(weekday, workday, weekend) * np.where(cooling, 1.15, 1.0)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(len(ts))
    err = np.zeros(len(ts))
    for k in range(1, len(ts)):
        err[k] = 0.8 * err[k - 1] + np.sqrt(1 - 0.8 ** 2) * noise * z[k]
    return profile, np.clip(profile * (1 + err), 0.0, None)


def load_csv(path, ts: pd.DatetimeIndex) -> np.ndarray:
    """A real site's load in MW: CSV with columns ts (interval start, AWST) and load_mw, 30-min grid."""
    load = pd.read_csv(path, parse_dates=["ts"]).set_index("ts")["load_mw"]
    return load.reindex(ts).to_numpy()
