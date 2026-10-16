"""Forecast evaluation shared by the baseline and every model we try.

A forecast is a DataFrame indexed like the actuals with one column per product.
"""

import numpy as np
import pandas as pd

# First month the Q2A models forecast (walk-forward, refit monthly on everything known by then).
TEST_START = pd.Timestamp("2025-10-01 08:00")
# Test months: every other month of 2025-26. Every other trading day from 2025-04 is validation, so
# both periods cover the market with FCESS income and the one without it.
TEST_MONTHS = ("2025-10", "2025-12", "2026-02", "2026-04", "2026-06", "2026-08")


def period_of(trading_day) -> np.ndarray:
    """'test' for trading days in TEST_MONTHS, 'validation' otherwise."""
    months = pd.DatetimeIndex(trading_day).strftime("%Y-%m")
    return np.where(np.isin(months, TEST_MONTHS), "test", "validation")


def test_slice(df: pd.DataFrame) -> pd.DataFrame:
    """Intervals (30-min index) whose trading day is in a test month."""
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
