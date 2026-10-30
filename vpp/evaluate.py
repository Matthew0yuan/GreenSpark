"""Forecast evaluation shared by the baseline and every model we try.

A forecast is a DataFrame indexed like the actuals with one column per product.

Split (from 2026-10-20): a strict time split at CUTOFF. Every model trains, and every choice is made, on
trading days before CUTOFF (the backtests compare variants on those days: "validation"); the trading days
from CUTOFF on are the test, scored once. Until 2026-10-20 the test was every other month of 2025-26 with
validation months around them, so choices had seen data after the test months (reports/findings.md,
section 16).
"""

import numpy as np
import pandas as pd

# First month the Q2A models forecast (walk-forward, refit monthly on everything known by then).
TEST_START = pd.Timestamp("2025-10-01 08:00")
CUTOFF = pd.Timestamp("2026-07-01")  # first test trading day
# Last trading day any model trains on: refits after it reuse the same training data, so the models
# are frozen from here and forecast the test without seeing it.
TRAIN_END = CUTOFF - pd.Timedelta(days=1)


def period_of(trading_day) -> np.ndarray:
    """'test' for trading days from CUTOFF on, 'validation' before."""
    return np.where(pd.DatetimeIndex(trading_day) >= CUTOFF, "test", "validation")


def test_slice(df: pd.DataFrame) -> pd.DataFrame:
    """Intervals (30-min index) whose trading day is in the test."""
    return df[period_of((df.index - pd.Timedelta(hours=8)).normalize()) == "test"]


def metrics(actual: pd.DataFrame, forecast: pd.DataFrame) -> pd.DataFrame:
    """MAE / RMSE / bias per product, over intervals where both are present."""
    rows = {}
    for col in forecast.columns:
        a, f = actual[col].align(forecast[col], join="inner")
        ok = a.notna() & f.notna()
        err = f[ok] - a[ok]
        rows[col] = {
            "mae": err.abs().mean(),
            "rmse": np.sqrt((err**2).mean()),
            "bias": err.mean(),
            "mean_price": a[ok].mean(),
            "n": int(ok.sum()),
        }
    return pd.DataFrame(rows).T


def skill(model: pd.DataFrame, baseline: pd.DataFrame, metric: str = "mae") -> pd.Series:
    """1 - model/baseline: >0 means the model beats the baseline (0.2 = 20% lower error)."""
    return 1 - model[metric] / baseline[metric]


def mae_by_hour(actual: pd.DataFrame, forecast: pd.DataFrame) -> pd.DataFrame:
    err = (forecast - actual[forecast.columns]).abs()
    return err.groupby(err.index.strftime("%H:%M")).mean()
