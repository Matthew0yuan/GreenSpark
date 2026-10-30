"""Perfect-foresight versions of e5's spike-related inputs (PLAN.md section 2.2). Each takes what was known only
afterwards; they bound what better information could be worth and are never achievable.

  labels      day label: a 30-min RTP >= SPIKE from 09:00 (slot FIRST_SLOT) of the trading day
  spike_c     the in-sample median RTP of the half-hours >= SPIKE (O-B's injected price)
  partners    each day's partner in the same calendar month (shuffled controls), never itself
  inject      a day's inputs with the 09:00 energy forecast (and for "C_ef" the plans' FCESS prices) replaced
  with_states the value table's per-day market states replaced (O-A and its control)
"""

import numpy as np
import pandas as pd

from vpp import dispatch as dp
from vpp.spikev3 import protocol as pr

PRICE_ORACLES = ("B", "B_ctrl", "B2", "C_e", "C_ef")
CAP_EPS = 0.01  # $/MWh below SPIKE: a known non-spike half-hour's forecast is capped just under it


def labels(rtp: pd.DataFrame) -> pd.Series:
    """(trading day x slot) RTP -> 1.0 if some half-hour from 09:00 is >= SPIKE, 0.0 if none, NaN without prices."""
    x = rtp.iloc[:, pr.FIRST_SLOT:]
    out = (x >= pr.SPIKE).any(axis=1).astype(float)
    return out.where(x.notna().all(axis=1))


def spike_c(rtp: pd.DataFrame) -> float:
    """Median RTP of the in-sample half-hours (every slot) at or above SPIKE."""
    x = rtp[(rtp.index >= pr.IN_START) & (rtp.index <= pr.IN_END)].to_numpy().ravel()
    x = x[np.isfinite(x) & (x >= pr.SPIKE)]
    return float(np.median(x))


def partners(days, seed: int = pr.SEED) -> dict:
    """Within each calendar month the days in a seeded random order, day i paired with day i + 1 (the last with the
    first): a partner from the same month that is never the day itself (a month of one day keeps itself)."""
    rng = np.random.default_rng(seed)
    days = pd.DatetimeIndex(sorted(pd.DatetimeIndex(days).normalize().unique()))
    out = {}
    for _, g in pd.Series(days, index=days).groupby(days.to_period("M")):
        order = list(rng.permutation(g.to_numpy()))
        for i, d in enumerate(order):
            out[pd.Timestamp(d)] = pd.Timestamp(order[(i + 1) % len(order)])
    return out


def spike_slots(rtp: np.ndarray) -> np.ndarray:
    """Slots from 09:00 whose 30-min RTP is >= SPIKE."""
    s = np.zeros(len(rtp), bool)
    s[pr.FIRST_SLOT:] = rtp[pr.FIRST_SLOT:] >= pr.SPIKE
    return s


def _timing(fc: np.ndarray, s: np.ndarray, spike_price) -> None:
    """Two-sided spike timing (PLAN change log 1): the spike slots get spike_price (max with the forecast for a
    constant), the other slots from 09:00 are capped just under SPIKE."""
    fc[s] = np.maximum(fc[s], spike_price) if np.isscalar(spike_price) else spike_price[s]
    calm = np.zeros(len(fc), bool)
    calm[pr.FIRST_SLOT:] = ~s[pr.FIRST_SLOT:]
    fc[calm] = np.minimum(fc[calm], pr.SPIKE - CAP_EPS)


def inject(d: dict, mode: str, c: float, partner: dict | None = None) -> dict:
    """A copy of one day's inputs (vpp.backtest.load_days, issue 0900) with the plans' price inputs replaced:
    B      spike slots from 09:00: forecast = max(forecast, c); other slots from 09:00 capped under SPIKE
    B_ctrl as B with the partner day's spike slots
    B2     as B with the actual RTP in the spike slots
    C_e    every slot from 09:00: forecast = actual RTP
    C_ef   C_e, and the plans' FCESS prices = actual (made positive + OFFER_EPS; from 08:00, the bridge has them)
    Slots 0-1 (08:00, 08:30, yesterday's prices before the 09:00 forecast) are left as they are. The bridge tuple is
    replaced, never changed in place (it shares its FCESS dict with prices["forecast"])."""
    out = dict(d)
    fc = np.array(d["fc0900"], dtype=float)
    rtp = np.asarray(d["rtp"], dtype=float)
    if mode == "B":
        _timing(fc, spike_slots(rtp), c)
    elif mode == "B_ctrl":
        _timing(fc, spike_slots(np.asarray(partner["rtp"], dtype=float)), c)
    elif mode == "B2":
        _timing(fc, spike_slots(rtp), rtp)
    elif mode in ("C_e", "C_ef"):
        fc[pr.FIRST_SLOT:] = rtp[pr.FIRST_SLOT:]
        if mode == "C_ef":
            yday, _ = d["prices"]["bridge"]
            out["prices"] = {**d["prices"], "bridge": (yday, {k: np.maximum(np.asarray(v, float), 0) + dp.OFFER_EPS
                                                              for k, v in d["act_f"].items()})}
    else:
        raise ValueError(mode)
    out["fc0900"] = fc
    return out


def with_states(inputs: dict, day_labels: pd.Series, partner: dict | None = None) -> dict:
    """A copy of a value table's per-day arrays whose market states are the given labels: today's state = the day's
    label, tomorrow's = the next day's; with partner, each day's (and the next day's) partner's label instead."""
    days = pd.DatetimeIndex(sorted(inputs["index"], key=inputs["index"].get))
    lab = day_labels.copy()
    lab.index = pd.DatetimeIndex(lab.index).normalize()

    def label(d):
        if partner is not None:
            d = partner.get(d, d)
        return float(lab.get(d, np.nan))

    out = dict(inputs)
    out["state_today"] = np.array([label(d) for d in days], float)
    out["state_tomorrow"] = np.array([label(d + pd.Timedelta(days=1)) for d in days], float)
    return out
