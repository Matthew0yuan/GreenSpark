"""Forecast evaluation shared by the baseline and every model we try.

A forecast is a DataFrame indexed like the actuals with one column per product.
"""

import numpy as np
import pandas as pd

# Held-out test window: capacity year 2025-26 onwards. Models may only train on data before it.
TEST_START = pd.Timestamp("2025-10-01 08:00")


def test_slice(df: pd.DataFrame, start: pd.Timestamp = TEST_START, end: pd.Timestamp | None = None) -> pd.DataFrame:
    return df.loc[start:end]


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
