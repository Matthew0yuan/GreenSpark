"""spikev3 step 1b features (analysis/2026-10-28/spikev3/PLAN.md section 5), for one task (days_ahead) at a time.

  B  stress       Kaminsky signal approach: each weak signal fires beyond the threshold (one of SHARES of its
                  "tight" tail) with the lowest noise-to-signal ratio (NSR) on the training rows; weight 1 / NSR
                  (signals with NSR >= 1 dropped); stress = sum of weights of the firing signals / sum of weights of
                  the signals with a value. Thresholds for the rows of issue month M are fitted on target days
                  before M - EMBARGO_DAYS and not after the cut-off, so every row's score is out-of-sample.
  C  sur_28/60    ln((spikes + 1) / (expected + 1)) over a base model's calibrated predictions for the target
                  days D - W .. D - 1 of issue day D (all issued before D, labels settled by 08:00 on D).
"""

import numpy as np
import pandas as pd

from vpp.spike import protocol as pc
from vpp.spikev3.models import TRAIN_END

SIGNALS = {"hr_norm": "low", "hr_daymin_norm": "low", "rsi": "low", "syn_loss_mw": "high", "wrev_evening": "low",
           "stem_premium": "high", "stem_cover": "low", "fx_rr_jumps7": "high", "fx_cr_jumps7": "high",
           "pdx_rr_ratio": "high", "pdx_cr_ratio": "high", "st_short_energy_day": "high", "out_loss_mw": "high"}
SHARES = (0.01, 0.025, 0.05, 0.10, 0.15, 0.20, 0.25)
MIN_HITS = 10           # spike rows a threshold must fire on
MIN_SPIKES = 50         # spike rows in the fitting window
MIN_MONTHS = 6          # months of history before the first score
WINDOWS = (28, 60)


# ---------------------------------------------------------------- B stress

def fires(x: np.ndarray, tau: float, side: str) -> np.ndarray:
    return x > tau if side == "high" else x < tau


def fit_signal(x: np.ndarray, y: np.ndarray, side: str) -> tuple[float, float] | None:
    """(threshold, NSR) with the lowest NSR over SHARES, or None if no threshold fires on MIN_HITS spikes."""
    ok = np.isfinite(x)
    x, y = x[ok], y[ok]
    pos, neg = y == 1, y == 0
    if not pos.any() or not neg.any():
        return None
    best = None
    for q in SHARES:
        tau = float(np.quantile(x, 1 - q if side == "high" else q))
        f = fires(x, tau, side)
        hits = int((f & pos).sum())
        if hits < MIN_HITS:
            continue
        nsr = ((f & neg).sum() / neg.sum()) / (hits / pos.sum())
        if best is None or nsr < best[1]:
            best = (tau, float(nsr))
    return best


def fit_stress(data: pd.DataFrame, month: pd.Timestamp, label: str = "spike", train_end: pd.Timestamp = TRAIN_END,
               window_end: pd.Timestamp | None = None) -> dict[str, tuple[float, float]]:
    """{signal: (threshold, weight)} for the rows of issue month `month`; window_end overrides the window (leak audit)."""
    end = window_end if window_end is not None else \
        min(month - pd.Timedelta(days=pc.EMBARGO_DAYS), train_end + pd.Timedelta(days=1))
    first = data["target_date"].min().to_period("M").to_timestamp()
    tr = (data["target_date"] < end) & data[label].notna()
    if month < first + pd.DateOffset(months=MIN_MONTHS) or data.loc[tr, label].sum() < MIN_SPIKES:
        return {}
    y = data.loc[tr, label].to_numpy(int)
    out = {}
    for s, side in SIGNALS.items():
        b = fit_signal(data.loc[tr, s].to_numpy(float), y, side)
        if b is not None and b[1] < 1:
            out[s] = (b[0], 1.0 / b[1])
    return out


def apply_stress(rows: pd.DataFrame, params: dict[str, tuple[float, float]]) -> np.ndarray:
    num, den = np.zeros(len(rows)), np.zeros(len(rows))
    for s, (tau, w) in params.items():
        x = rows[s].to_numpy(float)
        has = np.isfinite(x)
        num += w * (fires(x, tau, SIGNALS[s]) & has)
        den += w * has
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def stress(data: pd.DataFrame, label: str = "spike", train_end: pd.Timestamp = TRAIN_END) -> tuple[pd.Series, pd.DataFrame]:
    """Score of every row (one task) from its issue month's parameters, and the parameters per month."""
    month = data["issue_date"].dt.to_period("M").dt.to_timestamp()
    out = pd.Series(np.nan, index=data.index)
    rows = []
    for m, idx in data.groupby(month).groups.items():
        params = fit_stress(data, m, label, train_end)
        out[idx] = apply_stress(data.loc[idx], params)
        rows += [{"month": m, "signal": s, "threshold": t, "weight": w} for s, (t, w) in params.items()]
    return out, pd.DataFrame(rows, columns=["month", "signal", "threshold", "weight"])


# ---------------------------------------------------------------- C recent surprise

def surprise(data: pd.DataFrame, base: pd.DataFrame, windows=WINDOWS, shift: int = 1) -> pd.DataFrame:
    """sur_W for the rows of data (one task). base: the base model's predictions for the same task with columns
    target_date, spike, p_cal. shift=1 sums target days D-W .. D-1; shift=0 (D-W+1 .. D) is the planted leak."""
    b = base[base["spike"].notna()]
    daily = b.groupby("target_date").agg(obs=("spike", "sum"), exp=("p_cal", "sum"))
    days = pd.date_range(daily.index.min(), data["issue_date"].max())
    daily = daily.reindex(days)
    have = daily["obs"].notna().astype(float)
    daily = daily.fillna(0.0)
    out = pd.DataFrame(index=data.index)
    for w in windows:
        obs = daily["obs"].rolling(w, min_periods=1).sum().shift(shift)
        exp = daily["exp"].rolling(w, min_periods=1).sum().shift(shift)
        n = have.rolling(w, min_periods=1).sum().shift(shift)
        s = np.log((obs + 1) / (exp + 1)).where(n >= w / 2)
        out[f"sur_{w}"] = s.reindex(data["issue_date"]).to_numpy()
    return out
