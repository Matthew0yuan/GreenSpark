"""Item 1: is midday / evening hard because prices move a lot, or because the model fails there,
and where does the battery actually lose money? Energy-only plans, no capacity obligation."""

import numpy as np
import pandas as pd

SP = "/Users/matthew.yuan/Desktop/HGS/analysis/2026-10-16"
PROC = "/Users/matthew.yuan/Desktop/HGS/data/processed/"
H, HALF, DT = pd.Timedelta(hours=8), pd.Timedelta("30min"), 0.5
df30 = pd.read_parquet(PROC + "wem_30min.parquet")
fc = pd.read_parquet(PROC + "forecast_aemo_ml.parquet")
e = fc[fc["product"] == "energy"].merge(pd.read_parquet(SP + "/energy_blend.parquet"), on=["issue_date", "ts", "days_ahead"])
e["ours"] = (e.aemo_ml_wx + e.ml_wx) / 2
plans = pd.read_parquet(SP + "/exp_plans.parquet")
plans = plans[(plans.market == "energy") & (~plans.oblig)]
plans["ts"] = plans.day + H + plans.slot * HALF
perfect = plans[plans.method == "perfect"].set_index("ts")["net"]


def period(ts):
    h = ts.dt.hour
    return np.select([(h >= 10) & (h < 16), (h >= 16) & (h < 21)], ["中午 10-16", "傍晚 16-21"], "其他 21-10")


for da in (0, 1):
    x = e[e.days_ahead == da].copy()
    x["period"] = period(x.ts)
    rows = {}
    for p, g in x.groupby("period"):
        rows[p] = {"实际价格均值": g.actual.mean(), "价格标准差": g.actual.std(),
                   "昨天重播MAE": (g.baseline - g.actual).abs().mean(), "模型MAE": (g.ours - g.actual).abs().mean()}
    t = pd.DataFrame(rows).T
    t["改善率"] = 1 - t["模型MAE"] / t["昨天重播MAE"]
    ndays = plans[plans.da == da].day.nunique()
    for m, label in [("baseline", "重播损失$/天"), ("ours", "模型损失$/天")]:
        pl = plans[(plans.da == da) & (plans.method == m)].set_index("ts")
        a = df30.energy.reindex(pl.index)
        regret = a * (perfect.reindex(pl.index) - pl["net"]) * DT
        t[label] = regret.groupby(period(pd.Series(pl.index, index=pl.index))).sum() / ndays
    t.loc["合计"] = [np.nan] * 5 + [t["重播损失$/天"].sum(), t["模型损失$/天"].sum()]
    print(f"\n=== D+{da} (test period, {ndays} days)")
    print(t.round(2).to_string())

    # timing: centre of mass of discharge / charge, plan minus perfect (hours)
    def centre(net, sign):
        w = np.clip(sign * net, 0, None)
        return (w * np.arange(48)).sum() / w.sum() * 0.5 if w.sum() > 1e-6 else np.nan
    res = []
    pf = plans[plans.method == "perfect"].groupby("day")["net"].apply(np.array)
    for m in ["baseline", "ours"]:
        pm = plans[(plans.da == da) & (plans.method == m)].groupby("day")["net"].apply(np.array)
        for kind, sign in [("放电", 1), ("充电", -1)]:
            dif = np.array([centre(pm[d], sign) - centre(pf[d], sign) for d in pm.index])
            dif = dif[~np.isnan(dif)]
            res.append({"方法": m, "动作": kind, "中位偏差(小时)": np.median(dif),
                        "提前>=1h": (dif <= -1).mean(), "推迟>=1h": (dif >= 1).mean()})
    print(pd.DataFrame(res).round(2).to_string(index=False))

# A) by quarter, no obligation
rev = pd.read_parquet(SP + "/exp_revenue.parquet")
rev = rev[~rev.oblig]
rev["total"] = rev.energy + rev.fcess
rev["q"] = rev.day.dt.to_period("Q")
for da in (0, 1):
    r = rev[rev.da.isin([-1, da])]
    w = r.pivot_table(index="q", columns=["method", "market"], values="total")
    cols = [("perfect", "energy"), ("perfect", "joint"), ("baseline", "joint"), ("profile28", "joint"), ("ours", "joint"), ("ours", "energy")]
    print(f"\n=== A by quarter, D+{da}, no obligation ($/day)")
    print(w[cols].round(1).to_string())
