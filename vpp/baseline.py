"""Baseline forecaster: "same as yesterday".

The forecast for each interval is the actual price in the same interval `lag_days` earlier.
lag_days=1 is the headline baseline (yesterday predicts today). lag_days=2 is the same idea
applied to the day after tomorrow, for the 24-48h horizon.

Run:  .venv/bin/python -m vpp.baseline
"""

import pandas as pd

from vpp.data import PROCESSED, PRODUCTS, ROOT
from vpp.evaluate import CUTOFF, mae_by_hour, metrics, test_slice

REPORTS = ROOT / "reports"


def naive_forecast(df30: pd.DataFrame, lag_days: int = 1) -> pd.DataFrame:
    return df30[PRODUCTS].shift(freq=pd.Timedelta(days=lag_days)).reindex(df30.index)


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    actual = test_slice(df30)

    tables = {}
    for lag in (1, 2):
        tables[f"D-{lag}"] = metrics(actual, test_slice(naive_forecast(df30, lag)))
    table = pd.concat(tables, names=["baseline", "product"])
    table.to_csv(REPORTS / "baseline_metrics.csv")

    mae_by_hour(actual, test_slice(naive_forecast(df30, 1))).to_csv(REPORTS / "baseline_mae_by_hour.csv")

    print(f"Test: trading days from {CUTOFF.date()}  ({len(actual) // 48} trading days, 30-min intervals)")
    print("Prices in $/MWh (energy) or $/MW/h (FCESS)\n")
    print(table.round(2).to_string())


if __name__ == "__main__":
    main()
