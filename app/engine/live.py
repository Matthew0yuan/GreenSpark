"""The live data (app/live) as the engines see them: the complete trading days, and the trading day in progress.

With GS_DATA = var/live/data the engines read the live copy of every input. vpp.backtest.load_days gives the complete
days; the day in progress is put together here in the same form (day_input), from its 09:00 forecast and the 5-minute
prices AEMO has published so far (app/live/today.py). Its later prices are not known: the replay needs a price for
every interval, so they are filled with the 09:00 forecast, and the outcome shows only the half-hours already over.
The 09:00 plan does not depend on them (the plan's info_timing check multiplies them by 3)."""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp import dispatch as dp
from vpp import site as st
from vpp.data import DATA, PROCESSED

from .common import FIRST, N, ROOT

LIVE = ROOT / "var" / "live"
STATE = LIVE / "state.json"
TODAY = LIVE / "data" / "today"
H, HALF = pd.Timedelta(hours=8), pd.Timedelta(minutes=30)
READY_5MIN = FIRST * dp.STEPS  # 08:00-08:55: the 09:00 plan starts from the battery after them


def enabled() -> bool:
    return bool(os.environ.get("GS_DATA")) and DATA.resolve() == (LIVE / "data").resolve() and STATE.exists()


def state() -> dict | None:
    return json.loads(STATE.read_text()) if enabled() else None


def today_prices(day: pd.Timestamp) -> pd.DataFrame:
    """The day's 5-minute prices published so far, indexed by interval start (app/live/today.py)."""
    p = TODAY / f"{day:%Y%m%d}.csv"
    cols = ["energy", *dp.FCESS]
    if not p.exists():
        return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], name="ts"))
    x = pd.read_csv(p, parse_dates=["interval"]).drop_duplicates("interval", keep="last")
    return x.set_index("interval").sort_index()[cols].rename_axis("ts")


class Inputs:
    """What vpp.backtest.load_days reads, kept to build one day at a time."""

    def __init__(self, last: pd.Timestamp):
        self.df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
        self.p5 = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=["energy"] + dp.FCESS)
        self.esroi = pd.read_parquet(PROCESSED / "esroi.parquet").set_index("trading_date")
        self.fc = pd.read_parquet(PROCESSED / "forecast_dispatch_0900.parquet").set_index("ts")
        self.risk = pd.read_parquet(PROCESSED / "spike_risk_0900.parquet").set_index("ts")["p_spike"]
        ts_all = pd.date_range(pd.Timestamp(bt.FIRST_DAY) + H, last + H + pd.Timedelta(days=1), freq="30min",
                               inclusive="left")
        self.load_fc, self.load_act = (pd.Series(x, index=ts_all) for x in st.example_load(ts_all))


def day_input(x: Inputs, day: pd.Timestamp, p5_known: pd.DataFrame | None = None) -> tuple[dict | None, dict]:
    """vpp.backtest.load_days' dict of one day (issue "0900"), and how much of it is known. p5_known: the day's
    5-minute prices so far (None: a complete day, from the yearly files). Returns (None, info) if the day cannot be
    planned yet; info gives the reason."""
    ts = pd.date_range(day + H, periods=dp.N, freq="30min")
    ts5 = pd.date_range(day + H, periods=dp.N * dp.STEPS, freq="5min")
    f = x.fc.reindex(ts)
    planning = lambda v: np.maximum(v, 0) + dp.OFFER_EPS  # noqa: E731  FCESS headroom offered at $0
    cols = ["yday_energy"] + dp.FCESS + [f"yday_{k}" for k in dp.FCESS]
    if p5_known is None:
        a = x.df30.reindex(ts)
        price5 = x.p5["energy"].reindex(ts5).to_numpy()
        fcess5 = {k: x.p5[k].reindex(ts5).to_numpy() for k in dp.FCESS}
        known5 = int(np.isfinite(price5).sum())
        rtp, act_f = a["energy"].to_numpy(), {k: a[k].to_numpy() for k in dp.FCESS}
        missing = a[["energy"] + dp.FCESS].isna().any().any() or np.isnan(price5).any() or \
            any(np.isnan(v).any() for v in fcess5.values())
        if missing:
            return None, {"reason": "prices missing"}
    else:
        k5 = p5_known.reindex(ts5)
        known5 = int(k5["energy"].notna().cumprod().sum())  # the run of intervals from 08:00 with a price
        fill_e = np.repeat(np.r_[f["yday_energy"].to_numpy()[:FIRST], f["energy"].to_numpy()[FIRST:]], dp.STEPS)
        price5 = np.where(np.arange(len(ts5)) < known5, k5["energy"].to_numpy(), fill_e)
        fcess5 = {k: np.where(np.arange(len(ts5)) < known5, k5[k].to_numpy(), np.repeat(f[k].to_numpy(), dp.STEPS))
                  for k in dp.FCESS}
        rtp = price5.reshape(dp.N, dp.STEPS).mean(axis=1)  # the Reference Trading Price is their time-weighted mean
        act_f = {k: v.reshape(dp.N, dp.STEPS).mean(axis=1) for k, v in fcess5.items()}
        if known5 < READY_5MIN:
            return None, {"reason": "waiting for 08:00-09:00 prices", "known_5min": known5}
    if f[cols].isna().any().any() or f["energy"].iloc[FIRST:].isna().any():
        return None, {"reason": "no 09:00 forecast"}
    if day not in x.esroi.index:
        return None, {"reason": "no ESR obligation window"}
    w = x.esroi.loc[day]
    d = {
        "day": day, "rtp": rtp, "act_f": act_f, "p5": price5, "p5_f": fcess5,
        "window": (int((w["start_ts"] - day - H) / HALF), int((w["end_ts"] - day - H) / HALF)),
        "risk": x.risk.reindex(ts).fillna(0.0).to_numpy(),
        "load_fc": x.load_fc.reindex(ts).to_numpy(), "load_act": x.load_act.reindex(ts).to_numpy(),
        "prices": {
            "actual": (rtp, act_f),
            "forecast": (f["energy"].to_numpy(), {k: planning(f[k].to_numpy()) for k in dp.FCESS}),
            "yesterday": (f["yday_energy"].to_numpy(), {k: planning(f[f"yday_{k}"].to_numpy()) for k in dp.FCESS}),
        },
    }
    yday = f["yday_energy"].to_numpy()
    d["fc0900"] = np.r_[yday[:2], f["energy"].to_numpy()[2:]]
    d["prices"]["bridge"] = (yday, d["prices"]["forecast"][1])
    d["window7"] = ct.window_7h(day)
    d["slot_month"] = ts.strftime("%Y-%m").to_numpy()
    info = {"known_5min": known5, "known_halfhours": known5 // dp.STEPS,
            "as_of": None if known5 == 0 else str((day + H + pd.Timedelta(minutes=5 * known5)).strftime("%H:%M"))}
    return d, info


def path_of(day: pd.Timestamp) -> Path:
    return TODAY / f"{day:%Y%m%d}.csv"
