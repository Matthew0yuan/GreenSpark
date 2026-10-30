"""Price forecast = AEMO pre-dispatch forecast + model-predicted AEMO error.

Setup: one forecast per trading day D, issued ~08:15 once AEMO's 08:00 pre-dispatch run is out.
  days_ahead=0 -> every 30-min interval of trading day D   (0-24h ahead)
  days_ahead=1 -> every 30-min interval of trading day D+1 (24-48h ahead)
Features only use what is known at issue time: the 08:00 run itself, actual prices up to the
end of trading day D-1, and AEMO's errors on trading days that have already finished.

Each (product, days_ahead) gets its own LightGBM model on the residual (actual - AEMO), with an
L1 loss because we are scored on MAE. Walk-forward: refit at the start of every month on all data
whose actuals were known by then (never after TRAIN_END), then forecast that month.

Two variants are fit side by side so the effect of weather is measured on identical rows:
  aemo+ml     AEMO forecast + recent prices/errors + calendar
  aemo+ml+wx  the same plus ECMWF weather forecasts (scripts/download_weather.py). Intervals of D
              use the forecast made 24h before valid time, intervals of D+1 the one made 48h
              before.

Timing (found 2026-10-17, reports/spike/findings.md): the 08:00 run is public ~08:35, not by 08:15,
and the fixed "24h / 48h before" weather vintages include ECMWF runs not yet public on the morning
of D (valid times 18-24 UTC). build_frame(issue=...) builds the frame as of a strict issue time
instead (vpp.spike.protocol: 09:00): weather from the newest run public by then, runs not public by
then dropped. The default (issue=None) is the original frame.

Run:  .venv/bin/python -m vpp.aemo_correction
"""

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import lightgbm as lgb
import numpy as np
import pandas as pd

from vpp.data import PROCESSED, PRODUCTS, ROOT, trading_date
from vpp.evaluate import CUTOFF, TEST_START, TRAIN_END, period_of

REPORTS = ROOT / "reports"

AEMO_SYSTEM = [
    "aemo_req_energy_mw", "aemo_demand_level_mw", "aemo_dpv_level_mw", "aemo_in_service_injection_mw",
    "aemo_available_injection_mw", "aemo_dispatch_injection_mw", "aemo_dispatch_withdrawal_mw",
    "aemo_energy_deficit_mw",
]
WEATHER = [
    "wx_temp", "wx_ghi", "wx_cloud", "wx_wind_midwest", "wx_wind_merredin",
    "wx_tmax_day", "wx_ghi_day", "wx_wind_evening",
]
# Also predict rows whose actual is not known yet (the trading day in progress, tomorrow). Off in the research runs; the
# app's live data turn it on (app/live/build.py). Only rows with an actual train, so no other prediction changes.
UNLABELLED = False
LGB_PARAMS = dict(
    objective="l1", n_estimators=400, learning_rate=0.03, num_leaves=31, min_child_samples=50,
    subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1,
    random_state=0, n_jobs=1,
)
# Monthly refits are independent, so they train in parallel threads (LightGBM releases the GIL),
# one LightGBM thread each.
N_WORKERS = os.cpu_count() or 1


def monthly_refits(data: pd.DataFrame, days_ahead: int, start: pd.Timestamp,
                   fit_predict: Callable[[pd.Series, pd.Series], pd.Series]) -> pd.Series:
    """Walk-forward: for every month from `start`, fit_predict(train, test) on boolean row masks."""
    def one_month(month_start: pd.Timestamp) -> pd.Series | None:
        month_end = month_start + pd.offsets.MonthBegin(1)
        # labels must be known when the month's first forecast is issued, and no later than TRAIN_END
        train = ((data["target_date"] < month_start - pd.Timedelta(days=days_ahead))
                 & (data["target_date"] <= TRAIN_END))
        test = (data["target_date"] >= month_start) & (data["target_date"] < month_end)
        return fit_predict(train, test) if test.any() else None

    months = pd.date_range(start.normalize(), data["target_date"].max(), freq="MS")
    with ThreadPoolExecutor(N_WORKERS) as pool:
        return pd.concat([p for p in pool.map(one_month, months) if p is not None])


def trading_slot(idx: pd.DatetimeIndex) -> np.ndarray:
    """Half-hour slot within the trading day: 0 = 08:00, 47 = 07:30."""
    shifted = idx - pd.Timedelta(hours=8)
    return (shifted.hour * 2 + shifted.minute // 30).to_numpy(dtype="int64")


def _by_day_slot(values: pd.Series) -> pd.DataFrame:
    """Pivot a 30-min series into (trading date x slot 0..47)."""
    idx = pd.DatetimeIndex(values.index)
    return values.groupby([trading_date(idx), trading_slot(idx)]).mean().unstack()


def _lookup(table: pd.DataFrame, dates: pd.Series, slots: pd.Series) -> np.ndarray:
    """table.loc[date, slot] for each row, NaN where missing."""
    stacked = table.stack()
    keys = pd.MultiIndex.from_arrays([dates, slots])
    return stacked.reindex(keys).to_numpy()


def weather_features(weather: pd.DataFrame, lead: str) -> pd.DataFrame:
    """Per-interval weather features from one forecast vintage (lead = previous_day1/2).

    Daily aggregates cover the interval's trading day and use the same vintage.
    """
    w = pd.DataFrame({
        "wx_temp": weather[f"wx_perth_temperature_2m_{lead}"],
        "wx_ghi": weather[f"wx_perth_shortwave_radiation_{lead}"],
        "wx_cloud": weather[f"wx_perth_cloud_cover_{lead}"],
        "wx_wind_midwest": weather[f"wx_midwest_wind_speed_100m_{lead}"],
        "wx_wind_merredin": weather[f"wx_merredin_wind_speed_100m_{lead}"],
    })
    day = trading_date(pd.DatetimeIndex(w.index))
    evening_wind = ((w["wx_wind_midwest"] + w["wx_wind_merredin"]) / 2).where(w.index.hour.isin([17, 18, 19, 20]))
    w["wx_tmax_day"] = w["wx_temp"].groupby(day).transform("max")
    w["wx_ghi_day"] = w["wx_ghi"].groupby(day).transform("mean")
    w["wx_wind_evening"] = evening_wind.groupby(day).transform("mean")
    return w[WEATHER]


def build_frame(df30: pd.DataFrame, runs: pd.DataFrame, weather: pd.DataFrame | None = None,
                issue: pd.Timedelta | None = None) -> pd.DataFrame:
    """One row per (run, interval) with the model features. issue: build as of issue_date + issue only
    (point-in-time weather from data/pit, runs public by then); `weather` is then not used."""
    f = runs.copy()
    ts = pd.DatetimeIndex(f["ts"])
    f["target_date"] = trading_date(ts)
    f["slot"] = trading_slot(ts)
    f["dow"] = f["target_date"].dt.dayofweek
    f["month"] = f["target_date"].dt.month
    last_complete = f["issue_date"] - pd.Timedelta(days=1)

    day0 = runs[runs["days_ahead"] == 0].set_index("ts")
    for p in PRODUCTS:
        f[f"actual_{p}"] = df30[p].reindex(ts).to_numpy()
        actual = _by_day_slot(df30[p])
        aemo_err = _by_day_slot(df30[p].reindex(day0.index) - day0[f"aemo_{p}"])
        # "same as yesterday" baseline: last complete trading day, same slot
        f[f"prev_{p}"] = _lookup(actual, last_complete, f["slot"])
        f[f"prev7_{p}"] = _lookup(actual.rolling(7, min_periods=3).mean(), last_complete, f["slot"])
        f[f"aemo_err1_{p}"] = _lookup(aemo_err, last_complete, f["slot"])
        f[f"aemo_err7_{p}"] = _lookup(aemo_err.rolling(7, min_periods=3).mean(), last_complete, f["slot"])
        # reference forecast only (not a model input): 28-day average profile
        f[f"prof28_{p}"] = _lookup(actual.rolling(28, min_periods=14).mean(), last_complete, f["slot"])

    if issue is not None:
        from vpp.spike import features as sf
        from vpp.spike import protocol as pc
        f["issue_time"] = f["issue_date"] + issue
        r = pd.DataFrame({"issue_date": f["issue_date"].to_numpy(), "target_date": f["target_date"].to_numpy(),
                          "target_time": ts, "issue_time": f["issue_time"].to_numpy()})
        r = sf.weather_features(r, pc.AVAILABILITY)
        f[WEATHER] = r[WEATHER].to_numpy()
        f["src_wx_available_at"] = r["src_wx_available_at"].to_numpy()
        # runs public after the issue (their issue ID + 20 min): ~8 days, all before the backtest
        f = f[(f["issued_at"] + pc.AVAILABILITY["predispatch"] <= f["issue_time"]).to_numpy()]
    elif weather is not None:
        same_day = weather_features(weather, "previous_day1").reindex(ts).to_numpy()
        next_day = weather_features(weather, "previous_day2").reindex(ts).to_numpy()
        f[WEATHER] = np.where((f["days_ahead"].to_numpy() == 0)[:, None], same_day, next_day)
    return f


def features_for(product: str, weather: bool = False) -> list[str]:
    own = [f"prev_{product}", f"prev7_{product}", f"aemo_err1_{product}", f"aemo_err7_{product}"]
    energy = [] if product == "energy" else ["prev_energy", "aemo_err1_energy"]
    cols = [f"aemo_{p}" for p in PRODUCTS] + AEMO_SYSTEM + own + energy + ["slot", "dow", "month"]
    return cols + WEATHER if weather else cols


def walk_forward(frame: pd.DataFrame, product: str, days_ahead: int, weather: bool = False,
                 start: pd.Timestamp = TEST_START, cols: list[str] | None = None, residual: bool = True) -> pd.Series:
    """Out-of-sample predictions for every month from `start`, refitting at the start of each month.

    residual=True learns actual - AEMO and returns that correction; residual=False learns the price
    itself. `cols` overrides the default feature list.
    """
    data = frame[(frame["days_ahead"] == days_ahead) & (frame[f"actual_{product}"].notna() | UNLABELLED)]
    known = data[f"actual_{product}"].notna()
    cols = features_for(product, weather) if cols is None else cols
    target = data[f"actual_{product}"] - (data[f"aemo_{product}"] if residual else 0)

    def fit_predict(train: pd.Series, test: pd.Series) -> pd.Series:
        train = train & known
        model = lgb.LGBMRegressor(**LGB_PARAMS).fit(data.loc[train, cols], target[train])
        return pd.Series(model.predict(data.loc[test, cols]), index=data.index[test])

    return monthly_refits(data, days_ahead, start, fit_predict)


def score(err: pd.Series) -> dict:
    return {"mae": err.abs().mean(), "rmse": np.sqrt((err**2).mean()), "bias": err.mean()}


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
    weather = pd.read_parquet(PROCESSED / "weather_30min.parquet")
    frame = build_frame(df30, runs, weather)

    rows, by_slot, forecasts = [], [], []
    for days_ahead in (0, 1):
        for p in PRODUCTS:
            correction = walk_forward(frame, p, days_ahead)
            correction_wx = walk_forward(frame, p, days_ahead, weather=True)
            sub = frame.loc[correction.index]
            methods = {
                "baseline": sub[f"prev_{p}"],
                "profile28": sub[f"prof28_{p}"],
                "aemo": sub[f"aemo_{p}"],
                "aemo+ml": sub[f"aemo_{p}"] + correction,
                "aemo+ml+wx": sub[f"aemo_{p}"] + correction_wx.reindex(sub.index),
            }
            ok = pd.concat(methods, axis=1).notna().all(axis=1)
            actual = sub[f"actual_{p}"][ok]
            test = ok & (period_of(sub["target_date"]) == "test")  # metrics on the test only
            for name, fc in methods.items():
                rows.append({"days_ahead": days_ahead, "product": p, "method": name, "n": int(test.sum()),
                             **score(fc[test] - sub[f"actual_{p}"][test])})
            if p == "energy":
                errs = pd.DataFrame({k: (v[test] - sub[f"actual_{p}"][test]).abs() for k, v in methods.items()})
                by_slot.append(errs.groupby(sub.loc[test, "ts"].dt.strftime("%H:%M")).mean().add_suffix(f"_d{days_ahead}"))
            forecasts.append(sub.loc[ok, ["issue_date", "ts", "days_ahead"]].assign(
                product=p, actual=actual, baseline=methods["baseline"][ok], profile28=methods["profile28"][ok],
                aemo=methods["aemo"][ok], aemo_ml=methods["aemo+ml"][ok], aemo_ml_wx=methods["aemo+ml+wx"][ok]))

    table = pd.DataFrame(rows).set_index(["days_ahead", "product", "method"])
    table.to_csv(REPORTS / "aemo_correction_metrics.csv")
    pd.concat(by_slot, axis=1).to_csv(REPORTS / "aemo_correction_mae_by_hour.csv")
    pd.concat(forecasts, ignore_index=True).to_parquet(PROCESSED / "forecast_aemo_ml.parquet")

    order = ["baseline", "profile28", "aemo", "aemo+ml", "aemo+ml+wx"]
    mae = table["mae"].unstack("method")[order]
    mae["skill_vs_baseline"] = 1 - mae["aemo+ml+wx"] / mae["baseline"]
    mae["skill_vs_profile28"] = 1 - mae["aemo+ml+wx"] / mae["profile28"]
    mae["weather_gain"] = 1 - mae["aemo+ml+wx"] / mae["aemo+ml"]
    print(f"Test: trading days from {CUTOFF.date()}; walk-forward monthly refits from {TEST_START.date()}, "
          f"trained on data up to {TRAIN_END.date()} at most")
    print("MAE ($/MWh energy, $/MW/h FCESS); skill = 1 - MAE/MAE_ref\n")
    print(mae.round(3).to_string())
    print("\nRMSE:")
    print(table["rmse"].unstack("method")[order].round(2).to_string())
    print("\nBias (forecast - actual):")
    print(table["bias"].unstack("method")[order].round(2).to_string())


if __name__ == "__main__":
    main()
