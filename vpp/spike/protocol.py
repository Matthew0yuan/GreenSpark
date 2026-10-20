"""The spike forecast protocol, fixed before any modelling (2026-10-17). See reports/spike_protocol.md.

Target. Energy only. The 30-min Reference Trading Price (RTP, $/MWh, time-weighted mean of the six 5-min
dispatch prices; what energy settles at). Label "spike" = RTP >= SPIKE ($200, the business threshold the
earlier spike work used; not a quantile, not re-fitted). Separate labels, never mixed with the main one:
"spike300" (RTP >= $300) and "any5" (some 5-min price in the interval >= SPIKE). Negative prices are a
different problem and are not modelled here.

Issue. One forecast per trading day D, issued at ISSUE = 09:00 AWST on D. Targets are the 30-min trading
intervals starting at or after the issue: D+0 = 09:00 D .. 07:30 D+1 (lead 0-22.5h) and D+1 = 08:00 D+1
.. 07:30 D+2 (lead 23-46.5h; the day-ahead task). lead_hours = (interval start - issue) / 1h. The 08:00
and 08:30 intervals of D start before the issue and are not forecast.

Why 09:00. The AEMO pre-dispatch run labelled 08:00 is issued (dispatchDataIssueID) at 08:15 on 88% of
days and 08:30 on 11%, and the public JSON appeared 20 minutes after its issue ID on all 34 runs listed on
2026-10-16 (08:00 run listed 08:35). 09:00 leaves room for that on all but ~8 of 1,111 days; on those days
the run is treated as not yet public.

Availability. A record may be used at issue time T only if available_at <= T (vpp.spike.sources sets
available_at per source from the evidence noted there). AVAILABILITY holds the central rule and DELAYED
the sensitivity run with every source assumed later.

Periods. The split this study was run on (2026-10-17), kept as it was: test = TEST_MONTHS, every trading
day from 2025-04-01 outside them is validation; models and calibrators train on labels up to TRAIN_END at
most. (vpp.evaluate moved to a strict time split on 2026-10-20; this study has not been re-run on it.) Walk-forward with monthly refits; the
model for month M trains on target days before M - EMBARGO_DAYS (labels known, re-pricing settled).
Out-of-sample predictions start at OOS_START so calibrators for 2025-04 have six months of history.
"""

import numpy as np
import pandas as pd

TEST_MONTHS = ("2025-10", "2025-12", "2026-02", "2026-04", "2026-06", "2026-08")
TRAIN_END = pd.Timestamp("2026-07-16")

ISSUE = pd.Timedelta(hours=9)          # issue time after the start of the trading day's calendar date
DAY_START = pd.Timedelta(hours=8)      # WEM trading day starts 08:00 AWST
SPIKE = 200.0                          # $/MWh, main label
SPIKE_HIGH = 300.0                     # $/MWh, secondary label
EVENT_GAP = 1                          # spike runs separated by <= 1 non-spike interval are one event
EVAL_START = pd.Timestamp("2025-04-01")  # first validation trading day
OOS_START = pd.Timestamp("2024-10-01")   # first month with out-of-sample predictions (calibration warm-up)
EMBARGO_DAYS = 2
CAL_MONTHS = 6                         # calibrators fit on the last 6 months of out-of-sample predictions
BUDGETS = (0.02, 0.05, 0.10)           # alert budgets: share of target intervals flagged, fixed in advance

# minutes after the evidence timestamp a record is assumed public; DELAYED is the sensitivity run
AVAILABILITY = {
    "predispatch": pd.Timedelta(minutes=20),   # after dispatchDataIssueID
    "stpasa": pd.Timedelta(0),                 # after max(listing time, newest ZIP member time)
    "outages": pd.Timedelta(0),                # after the snapshot's listing time
    "weather": pd.Timedelta(hours=8),          # after model initialisation (Open-Meteo: 7.35h on 2026-10-16)
}
DELAYED = {
    "predispatch": pd.Timedelta(minutes=60),
    "stpasa": pd.Timedelta(hours=24),
    "outages": pd.Timedelta(hours=24),
    "weather": pd.Timedelta(hours=14),
}


def period_of(trading_day) -> np.ndarray:
    """'test' for trading days in TEST_MONTHS, 'validation' otherwise."""
    months = pd.DatetimeIndex(trading_day).strftime("%Y-%m")
    return np.where(np.isin(months, TEST_MONTHS), "test", "validation")


def issue_time(trading_day) -> pd.Timestamp:
    return pd.Timestamp(trading_day) + ISSUE


def trading_day(ts) -> pd.DatetimeIndex:
    return (pd.DatetimeIndex(ts) - DAY_START).normalize()
