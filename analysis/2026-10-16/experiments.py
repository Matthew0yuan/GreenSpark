"""Q2B experiments: how much is there to earn, and how much of it do forecasts capture?

A) Test period (target trading days 2025-10-01 .. 2026-10-15), one plan per trading day, settled
   at actual prices: perfect prices / yesterday replay / 28-day profile / our forecast
   x energy only / energy+FCESS  x  with / without the capacity-credit obligation, days_ahead 0 and 1.
   Our forecast = energy 50/50 blend of aemo+ml+wx and ml+wx; FCESS aemo+ml (weather hurt FCESS).
B) Perfect-price opportunity for every trading day since 2023-10-01 (trend by quarter).
"""

import sys

import numpy as np
import pandas as pd

SP = "/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-16"
sys.path.insert(0, SP)
import dispatch_lib as dl  # noqa: E402

PROC = "/Users/matthew.yuan/Desktop/HGS/data/processed/"
H, HALF = pd.Timedelta(hours=8), pd.Timedelta("30min")
METHODS = ["baseline", "profile28", "ours"]

df30 = pd.read_parquet(PROC + "wem_30min.parquet")
es = pd.read_parquet(PROC + "esroi.parquet").set_index("trading_date")
fc = pd.read_parquet(PROC + "forecast_aemo_ml.parquet")
bl = pd.read_parquet(SP + "/energy_blend.parquet")

tabs = {}
for (p, da), g in fc.groupby(["product", "days_ahead"]):
    g = g.set_index("ts")
    tabs[p, da] = pd.DataFrame({"baseline": g.baseline, "profile28": g.profile28, "ours": g.aemo_ml})
e = fc[fc["product"] == "energy"].merge(bl, on=["issue_date", "ts", "days_ahead"], how="left")
e["ours"] = (e.aemo_ml_wx + e.ml_wx) / 2
for da, g in e.groupby("days_ahead"):
    g = g.set_index("ts")
    tabs["energy", da] = pd.DataFrame({"baseline": g.baseline, "profile28": g.profile28, "ours": g.ours})


def window(day):
    w = es.loc[day]
    return int((w.start_ts - day - H) / HALF), int((w.end_ts - day - H) / HALF)


def actual_day(day):
    a = df30.loc[day + H: day + H + pd.Timedelta(hours=23, minutes=30), ["energy"] + dl.FCESS]
    return a if len(a) == dl.N and not a.isna().any().any() else None


rev, plans = [], []


def record(day, da, oblig, market, method, p, a):
    en, fe = dl.settle(p, a["energy"], {k: a[k] for k in dl.FCESS})
    rev.append(dict(day=day, da=da, oblig=oblig, market=market, method=method, energy=en, fcess=fe))
    plans.append(pd.DataFrame({"day": day, "slot": np.arange(dl.N), "da": da, "oblig": oblig, "market": market,
                               "method": method, "net": p["net"], "soc": p["soc"], **{k: p[k] for k in dl.FCESS}}))


for day in pd.date_range("2025-10-01", "2026-10-15"):
    a = actual_day(day)
    if a is None:
        continue
    af = {k: a[k].to_numpy() for k in dl.FCESS}
    w = window(day)
    for oblig in (False, True):
        win = w if oblig else None
        record(day, -1, oblig, "energy", "perfect", dl.plan(a["energy"].to_numpy(), None, win), a)
        record(day, -1, oblig, "joint", "perfect", dl.plan(a["energy"].to_numpy(), af, win), a)
    for da in (0, 1):
        fe = tabs["energy", da].reindex(a.index)
        ff = {k: tabs[k, da].reindex(a.index) for k in dl.FCESS}
        if fe.isna().any().any() or any(f.isna().any().any() for f in ff.values()):
            continue
        for method in METHODS:
            pe = fe[method].to_numpy()
            # offer spare headroom at $0: never worse than nothing, since FCESS prices are >= 0
            pf = {k: np.maximum(ff[k][method].to_numpy(), 0) + 0.01 for k in dl.FCESS}
            for oblig in (False, True):
                win = w if oblig else None
                record(day, da, oblig, "energy", method, dl.plan(pe, None, win), a)
                record(day, da, oblig, "joint", method, dl.plan(pe, pf, win), a)

rev = pd.DataFrame(rev)
rev.to_parquet(SP + "/exp_revenue.parquet")
pd.concat(plans, ignore_index=True).to_parquet(SP + "/exp_plans.parquet")
print(f"A) test period: {rev.day.nunique()} days")

trend = []
for day in pd.date_range("2023-10-01", "2026-10-15"):
    a = actual_day(day)
    if a is None:
        continue
    af = {k: a[k].to_numpy() for k in dl.FCESS}
    for oblig, s0 in [(False, 0.0), (True, 0.0), (False, 1.0)]:
        for market in ("energy", "joint"):
            p = dl.plan(a["energy"].to_numpy(), af if market == "joint" else None, window(day) if oblig else None, s0)
            en, fe = dl.settle(p, a["energy"], af)
            trend.append(dict(day=day, oblig=oblig, s0=s0, market=market, energy=en, fcess=fe))
trend = pd.DataFrame(trend)
trend.to_parquet(SP + "/exp_trend.parquet")
print(f"B) perfect-price trend: {trend.day.nunique()} days")
