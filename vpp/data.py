"""Clean raw WEM CSVs into analysis-ready parquet tables.

All timestamps are AWST (no daylight saving) and mark the START of the interval.
A WEM trading day runs 08:00 -> 07:55 the next calendar day.

Run:  .venv/bin/python -m vpp.data
"""

import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
# GS_DATA: another data folder with the same layout (raw/, processed/, pit/); the app's live data use one (app/live)
DATA = Path(os.environ.get("GS_DATA") or ROOT / "data")
RAW = DATA / "raw"
PROCESSED = DATA / "processed"

TS_FORMAT = "%d/%m/%Y %H:%M:%S"

PRICE_COLS = {
    "Energy Clearing Price": "energy",
    "Contingency Raise Clearing Price": "cont_raise",
    "Contingency Lower Clearing Price": "cont_lower",
    "Regulation Raise Clearing Price": "reg_raise",
    "Regulation Lower Clearing Price": "reg_lower",
    "RoCoF Clearing Price": "rocof",
}
PRODUCTS = list(PRICE_COLS.values())

FEATURE_COLS = {
    "operational-demand": {
        "Operational Demand": "demand_mw",
        "Unscheduled Operational Demand": "unscheduled_demand_mw",
        "Operational Withdrawal": "withdrawal_mw",
    },
    "estimated-dpv": {"Estimated DPV Generation(MW)": "dpv_mw"},
    "market-requirements": {
        "Energy Requirement": "req_energy_mw",
        "Contingency Raise Requirement": "req_cont_raise_mw",
        "Contingency Lower Requirement": "req_cont_lower_mw",
        "Regulation Raise Requirement": "req_reg_raise_mw",
        "Regulation Lower Requirement": "req_reg_lower_mw",
        "RoCoF Requirement": "req_rocof",
    },
}


def _read_5min(dataset: str, columns: dict[str, str]) -> pd.DataFrame:
    """Read every yearly file of a dataset; the first column is the interval timestamp."""
    frames = []
    for f in sorted((RAW / dataset).glob("*.csv")):
        df = pd.read_csv(f, low_memory=False)
        ts = pd.to_datetime(df.iloc[:, 0], format=TS_FORMAT, errors="coerce")
        frames.append(df[list(columns)].rename(columns=columns).assign(ts=ts).dropna(subset=["ts"]))
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates("ts", keep="last").set_index("ts").sort_index()


def _to_regular_grid(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Reindex onto a complete grid and linearly fill short gaps (a few missing intervals)."""
    grid = pd.date_range(df.index[0], df.index[-1], freq=freq, name="ts")
    out = df.reindex(grid)
    out["filled"] = out.isna().any(axis=1)
    value_cols = out.columns.drop("filled")
    out[value_cols] = out[value_cols].interpolate(method="time", limit=12)
    return out


def load_5min() -> pd.DataFrame:
    prices = _to_regular_grid(_read_5min("market-clearing-prices", PRICE_COLS), "5min")
    features = [_read_5min(ds, cols) for ds, cols in FEATURE_COLS.items()]
    return prices.join(features, how="left")


def to_30min(df5: pd.DataFrame) -> pd.DataFrame:
    """Average 5-min dispatch intervals into 30-min trading intervals (label = interval start)."""
    agg = df5.drop(columns="filled").resample("30min", label="left", closed="left").mean()
    agg["filled"] = df5["filled"].resample("30min", label="left", closed="left").max().astype(bool)
    return agg


def load_reference_trading_price() -> pd.Series:
    frames = [pd.read_csv(f, usecols=["Trading Interval", "Reference Trading Price"])
              for f in sorted((RAW / "reference-trading-price").glob("*.csv"))]
    df = pd.concat(frames, ignore_index=True).dropna(subset=["Trading Interval"])
    df["ts"] = pd.to_datetime(df["Trading Interval"], format=TS_FORMAT)
    return df.drop_duplicates("ts", keep="last").set_index("ts").sort_index()["Reference Trading Price"]


def load_esroi() -> pd.DataFrame:
    """ESR obligation window per trading date.

    Start/End name the first and last obligated trading intervals (inclusive), e.g.
    16:30-20:00 covers 16:30..20:29 = 4 hours. From 2026-27 AEMO publishes a window per
    storage component; we keep the most common window per date as the system window.
    """
    rows = []
    for f in sorted((RAW / "esr-obligation-intervals").glob("*.csv")):
        df = pd.read_csv(f, dtype=str).dropna(subset=["Trading Date"])
        date_fmt = "%d/%m/%Y" if "/" in df["Trading Date"].iloc[0] else "%Y-%m-%d"
        df["trading_date"] = pd.to_datetime(df["Trading Date"], format=date_fmt)
        rows.append(df[["trading_date", "ESROI Trading Interval Start", "ESROI Trading Interval End"]])
    df = pd.concat(rows, ignore_index=True)
    df.columns = ["trading_date", "start", "end"]
    df = df.groupby("trading_date")[["start", "end"]].agg(lambda s: s.mode().iloc[0]).reset_index()
    df["start_ts"] = df["trading_date"] + pd.to_timedelta(df["start"] + ":00")
    df["end_ts"] = df["trading_date"] + pd.to_timedelta(df["end"] + ":00") + pd.Timedelta("30min")
    return df


def mark_esroi(df30: pd.DataFrame, esroi: pd.DataFrame) -> pd.Series:
    flag = pd.Series(False, index=df30.index, name="esroi")
    for start, end in zip(esroi["start_ts"], esroi["end_ts"]):
        flag[(flag.index >= start) & (flag.index < end)] = True
    return flag


def trading_date(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Trading date of each interval (intervals before 08:00 belong to the previous day)."""
    return (index - pd.Timedelta(hours=8)).normalize()


def load_predispatch() -> pd.DataFrame:
    """AEMO's 08:00 Reference Pre-Dispatch run of each trading day (scripts/download_predispatch.py).

    One row per (issue_date, ts): the run issued at the start of trading day `issue_date`
    forecasting interval `ts`; days_ahead is 0 for intervals of that day and 1 for the next.
    Forecast values are prefixed `aemo_`.
    """
    files = sorted((RAW / "predispatch-0800").glob("*.csv"))
    df = pd.concat([pd.read_csv(f, dtype={"run": str, "issued": str}) for f in files], ignore_index=True)
    ts = pd.to_datetime(df.pop("interval"), utc=True).dt.tz_convert("Australia/Perth").dt.tz_localize(None)
    out = pd.DataFrame({
        "issue_date": pd.to_datetime(df.pop("run").str[:8], format="%Y%m%d"),
        "issued_at": pd.to_datetime(df.pop("issued"), format="%Y%m%d%H%M%S"),
        "ts": ts,
    })
    out["days_ahead"] = (trading_date(pd.DatetimeIndex(ts)) - pd.DatetimeIndex(out["issue_date"])).days
    out = out.join(df.add_prefix("aemo_"))
    return out.drop_duplicates(["issue_date", "ts"]).sort_values(["issue_date", "ts"]).reset_index(drop=True)


def load_weather() -> pd.DataFrame:
    """Archived ECMWF weather forecasts on the 30-min grid (scripts/download_weather.py).

    Columns are wx_<location>_<variable>_previous_day<N>: the forecast made N days before the
    valid time. Radiation is a mean over the preceding hour, so it is relabelled to the start
    of that hour and held for both half-hours; instantaneous variables are interpolated.
    """
    frames = []
    for f in sorted((RAW / "weather-previous-runs").glob("*.csv")):
        df = pd.read_csv(f, parse_dates=["time"], index_col="time")
        df.columns = [f"wx_{f.stem}_{c}" for c in df.columns]
        frames.append(df)
    hourly = pd.concat(frames, axis=1)
    grid = pd.date_range(hourly.index[0], hourly.index[-1], freq="30min", name="ts")
    radiation = [c for c in hourly.columns if "radiation" in c]
    out = hourly.drop(columns=radiation).reindex(grid).interpolate(method="time", limit=1, limit_area="inside")
    out[radiation] = hourly[radiation].shift(-1, freq="1h").reindex(grid).ffill(limit=1)
    return out


def build() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    df5 = load_5min()
    df30 = to_30min(df5)

    # A handful of intervals were re-priced after the 5-min file was cut (see "Failure Reason");
    # the Reference Trading Price is what settles, so it is the 30-min energy price of record.
    rtp = load_reference_trading_price().reindex(df30.index)
    n_diff = ((df30["energy"] - rtp).abs() > 0.01).sum()
    print(f"30-min energy: {n_diff} intervals differ from the Reference Trading Price by >1c; using RTP")
    df30["energy"] = rtp.fillna(df30["energy"])

    esroi = load_esroi()
    df30["esroi"] = mark_esroi(df30, esroi)

    df5.to_parquet(PROCESSED / "wem_5min.parquet")
    df30.to_parquet(PROCESSED / "wem_30min.parquet")
    esroi.to_parquet(PROCESSED / "esroi.parquet")
    print(f"5-min : {len(df5):,} rows, {df5['filled'].sum()} filled, {df5.index[0]} -> {df5.index[-1]}")
    print(f"30-min: {len(df30):,} rows, {df30['filled'].sum()} with a filled 5-min value")
    print(f"ESROI : {len(esroi)} trading dates, windows {sorted(set(zip(esroi['start'], esroi['end'])))}")

    if any((RAW / "weather-previous-runs").glob("*.csv")):
        wx = load_weather()
        wx.to_parquet(PROCESSED / "weather_30min.parquet")
        complete = wx.dropna()
        print(f"Weather forecasts: {wx.shape[1]} columns, complete from {complete.index[0]} to {complete.index[-1]}")

    if any((RAW / "predispatch-0800").glob("*.csv")):
        pdr = load_predispatch()
        pdr.to_parquet(PROCESSED / "aemo_predispatch.parquet")
        print(f"AEMO pre-dispatch: {pdr['issue_date'].nunique()} runs, {len(pdr):,} rows, "
              f"{(pdr.groupby('issue_date').size() != 96).sum()} runs without 96 intervals")


if __name__ == "__main__":
    build()
