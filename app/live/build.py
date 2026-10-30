"""Rebuild the model inputs in var/live/data from the live raw files, as the forward test did (reports/findings.md
section 26.12): the models stay frozen (trained on labels up to 2026-06-30), and rows already built keep their first
values, so a day's inputs never move once it has been built (AEMO revises a few old half-hours afterwards).

The day in progress (the "live day") is built too: its 09:00 price forecast and spike risk come from AEMO's 08:00
pre-dispatch run and the weather public by then, like any other day; only its actual prices are not known yet
(vpp.aemo_correction.UNLABELLED). Its value-table row needs only the 28 days before it.

Must run in its own process with GS_DATA = var/live/data (run.py does):
    GS_DATA=var/live/data .venv/Scripts/python.exe -m app.live.build
Writes var/live/state.json."""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from app.live import fetch

if Path(os.environ.get("GS_DATA") or ".").resolve() != fetch.DATA.resolve():
    raise SystemExit(f"run with GS_DATA={fetch.DATA}: vpp reads its data folder when it is imported")

from vpp import aemo_correction as ac  # noqa: E402
from vpp import backtest as bt  # noqa: E402
from vpp import data as vd  # noqa: E402
from vpp import forecast as fo  # noqa: E402
from vpp import spike_risk as sr  # noqa: E402
from vpp import storage_value as sv  # noqa: E402
from vpp.spike import sources as ss  # noqa: E402
from vpp.v2 import risk as rk  # noqa: E402
from vpp.v2 import value as vl  # noqa: E402

STATE = fetch.LIVE / "state.json"
P = vd.PROCESSED
H = pd.Timedelta(hours=8)
# the two tables the app's e5 runs read (app/engine/common.py HURDLE 50): behind the factory, and standalone
TABLES = (["--hurdle", "50", "--state"], ["--standalone", "--hurdle", "50", "--state"])
FRAMES = {  # model inputs kept by key: rows built before keep their values
    "forecast_dispatch_0900.parquet": ["issue_date", "ts"], "spike_risk_0900.parquet": ["issue_date", "ts"],
    rk.HISTORY.name: ["issue_date", "ts"], rk.HISTORY_D1.name: ["issue_date", "ts"],
    "aemo_predispatch.parquet": ["issue_date", "ts"], "esroi.parquet": ["trading_date"],
}


def _read(name: str):
    p = P / name
    return pd.read_parquet(p) if p.exists() else None


def keep_indexed(name: str, before: pd.DataFrame | None) -> dict:
    """wem_5min / wem_30min: every timestamp built before keeps its row."""
    new = pd.read_parquet(P / name)
    if before is None:
        return {"kept": 0, "changed": 0}
    common = new.index.intersection(before.index)
    a, b = new.loc[common, before.columns], before.loc[common]
    changed = int((~((a == b) | (a.isna() & b.isna()))).any(axis=1).sum())
    new.loc[common, before.columns] = b
    new.to_parquet(P / name)
    return {"kept": int(len(common)), "changed": changed, "added": int(len(new.index.difference(before.index)))}


def keep_keyed(name: str, before: pd.DataFrame | None, key: list) -> dict:
    new = pd.read_parquet(P / name)
    if before is None:
        return {"kept": 0, "changed": 0}
    k_new, k_old = pd.MultiIndex.from_frame(new[key]), pd.MultiIndex.from_frame(before[key])
    fresh = new[~k_new.isin(k_old)]
    old_in_new = new[k_new.isin(k_old)].set_index(key)
    ref = before.set_index(key).reindex(old_in_new.index)
    num = [c for c in ref.columns if c in old_in_new and pd.api.types.is_numeric_dtype(ref[c])
           and not pd.api.types.is_bool_dtype(ref[c])]
    diff = (old_in_new[num] - ref[num]).abs().max().max() if num and len(ref) else 0.0
    out = pd.concat([before, fresh], ignore_index=True).sort_values(key).reset_index(drop=True)
    out.to_parquet(P / name)
    return {"kept": int(len(before)), "added": int(len(fresh)), "max_diff_old_rows": float(0.0 if pd.isna(diff) else diff)}


def spike_risk_0900() -> None:
    """vpp.spike_risk.main's 09:00 output (its printed scores are left out: they need actuals)."""
    df30, runs, weather = (pd.read_parquet(P / n) for n in ("wem_30min.parquet", "aemo_predispatch.parquet",
                                                             "weather_30min.parquet"))
    f = sr.add_signals(ac.build_frame(df30, runs, weather, issue=fo.ISSUE), df30)
    p = sr.walk_forward_risk(f)
    out = f.loc[p.index, ["issue_date", "ts", "days_ahead", "target_date", "actual_energy"]].assign(p_spike=p)
    out.loc[(out["ts"] < out["issue_date"] + fo.ISSUE).to_numpy(), "p_spike"] = np.nan
    out.sort_values("ts").reset_index(drop=True).to_parquet(sr.OUT_0900)


def tables(live_day: pd.Timestamp) -> dict:
    """The app's two value tables, through the live day, checked against the rows built before."""
    base_prices = vl.prices
    vl.prices = lambda: (t := base_prices()).reindex(t.index.union(pd.DatetimeIndex([live_day])))
    vl.LAST = live_day
    sv.MONTHS = pd.period_range(sv.MONTHS[0], live_day.to_period("M"), freq="M")
    out = {}
    for argv in TABLES:
        params = vl.standalone_params(vl.TableParams().asset()) if "--standalone" in argv else vl.TableParams()
        p = vl.path("median", 40.0, 50.0, True, False, **{k: v for k, v in params.kwargs().items() if k != "energy_mwh"})
        before = np.load(p) if p.exists() else None
        old = {k: before[k] for k in before.files} if before is not None else None
        vl.main([*argv, "--rebuild"])
        z = np.load(p)
        diff = 0.0
        if old is not None:
            i_old = {d: i for i, d in enumerate(old["day"].astype(str))}
            both = [(i_old[d], j) for j, d in enumerate(z["day"].astype(str)) if d in i_old]
            for k in ("level", "curve", "marg", "curve_state", "marg_state", "state_today"):
                d = np.abs(old[k][[i for i, _ in both]].astype(float) - z[k][[j for _, j in both]].astype(float))
                same_nan = np.isnan(old[k][[i for i, _ in both]].astype(float)) == np.isnan(z[k][[j for _, j in both]].astype(float))
                diff = max(diff, float(d[np.isfinite(d)].max()) if np.isfinite(d).any() else 0.0,
                           0.0 if same_nan.all() else float("inf"))
        out[p.name] = {"days": int(len(z["day"])), "last": str(z["day"][-1]), "max_diff_old_days": diff}
    return out


def main() -> dict:
    t0, steps = time.time(), {}
    now = fetch.now()
    live_day = pd.Timestamp(fetch.trading_day(now))
    ac.UNLABELLED = True
    before = {n: _read(n) for n in ("wem_5min.parquet", "wem_30min.parquet", *FRAMES)}

    def step(name, fn):
        s = time.time()
        r = fn()
        steps[name] = round(time.time() - s, 1)
        print(f"{name}: {steps[name]} s", flush=True)
        return r

    step("market tables", vd.build)
    kept = {n: keep_indexed(n, before[n]) for n in ("wem_5min.parquet", "wem_30min.parquet")}
    for n in ("aemo_predispatch.parquet", "esroi.parquet"):
        kept[n] = keep_keyed(n, before[n], FRAMES[n])
    df30 = pd.read_parquet(P / "wem_30min.parquet", columns=["energy"])
    complete = vd.trading_date(df30.index[-1:])[0]  # the yearly files hold whole trading days
    bt.LAST_DAY = str(complete.date())
    step("weather at 09:00", lambda: ss.weather().to_parquet(ss.PIT / "weather.parquet", index=False))
    step("price forecast", lambda: fo.build(issue=fo.ISSUE).to_parquet(fo.OUT_0900))
    step("spike risk", spike_risk_0900)
    step("risk history", lambda: (rk.history(True, 1), rk.history(True, 0)))
    for n in ("forecast_dispatch_0900.parquet", "spike_risk_0900.parquet", rk.HISTORY.name, rk.HISTORY_D1.name):
        kept[n] = keep_keyed(n, before[n], FRAMES[n])
    kept.update(step("value tables", lambda: tables(live_day)))

    fc = pd.read_parquet(fo.OUT_0900)
    live = fc[(fc["target_date"] == live_day) & (fc["days_ahead"] == 0)]
    runs = pd.read_parquet(P / "aemo_predispatch.parquet")
    run = runs[(runs["issue_date"] == live_day) & (runs["days_ahead"] == 0)]
    state = {
        "built_at": now.isoformat(timespec="seconds"), "live_day": str(live_day.date()),
        "complete_through": str(complete.date()),
        "live_forecast_halfhours": int(live["energy"].notna().sum()),
        "predispatch_issued_at": None if run.empty else str(run["issued_at"].iloc[0]),
        "steps_s": steps, "total_s": round(time.time() - t0, 1), "kept": kept,
    }
    STATE.write_text(json.dumps(state, indent=1) + "\n")
    print(json.dumps({k: v for k, v in state.items() if k != "kept"}, indent=1))
    return state


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
