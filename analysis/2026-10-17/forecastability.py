"""Can "unusual weather + little wind" be seen in advance? Forecast skill and spike detection by lead time.

Leads (issue at 09:00 AWST, as vpp.spike.protocol):
  L0  当天早上    09:00 on the onset's trading day   (target 0-22.5 h ahead)
  L1  前一天早上  09:00 one trading day before        (24-46.5 h)
  L2  前两天早上  09:00 two trading days before       (48-70.5 h)
Every forecast is the version public at the issue, by the as-of logic of vpp.spike.features: ECMWF weather (newest
vintage), the AEMO 08:00 pre-dispatch run (requirement, price, headroom; it covers only D and D+1, so nothing at L2)
and the ST PASA reference demand. L0 and L1 come from data/processed/spike_table.parquet; L2 is built here with
the same functions.

Derived forecasts:
  f_wind_mw   wind output expected from the forecast 100 m wind speed (mean of Midwest and Merredin): isotonic
              fit to SCADA wind output, L1 forecasts, training period (2024-02-04 .. 2025-03-31, weather starts then)
  f_solar_mw  the same for utility solar from the Perth radiation forecast
  f_demand    AEMO requirement forecast at L0 and L1; ST PASA reference demand at L2
  f_resid     f_demand - f_wind_mw - f_solar_mw: what coal, gas and batteries are expected to cover
Actual residual demand = unscheduled demand - wind - utility solar (SCADA), as extract.py.

Outputs (out/):
  forecast_skill.csv          by lead and period: correlation and mean absolute error of each forecast against
                              the actual, all intervals and 16:00-20:00; how many of the low-wind / high-residual
                              evenings the forecast also put in its own bottom 20% / top 10%
  forecast_matched.csv        top-20% spikes against their matched controls (analyze.py sets): matched AUC of each
                              forecast at the onset, by lead and period, next to the actual value (perfect foresight)
  forecast_day_flags.csv      day level: flag the 10% / 20% of trading days with the highest forecast residual
                              demand; share of top-spike days caught and share of flagged days with one
  forecast_model.csv          LightGBM on forecasts only, fitted on train, matched AUC by lead

Run:  python forecastability.py
"""

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from analyze import LGB, RNG, Study, boot_ci, matched_ranks
from common import OUT, PROCESSED, pc, period_of
from vpp.spike import features as sf

LEADS = {0: "当天早上", 1: "前一天早上", 2: "前两天早上"}
COLS = ["issue_date", "issue_time", "target_time", "target_date", "days_ahead", "pd_req", "aemo_energy", "pd_headroom", "st_ref",
        "wx_temp", "wx_ghi", "wx_cloud", "wx_wind_midwest", "wx_wind_merredin", "wx_tmax_day"]
WX_START = pd.Timestamp("2024-02-05")


def two_days_ahead(first: pd.Timestamp, last: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for d in pd.date_range(first, last):
        start = d + pd.Timedelta(days=2) + pc.DAY_START
        ts = pd.date_range(start, start + pd.Timedelta(days=1), freq="30min", inclusive="left")
        rows.append(pd.DataFrame({"issue_date": d, "issue_time": pc.issue_time(d), "target_time": ts, "days_ahead": 2}))
    r = pd.concat(rows, ignore_index=True)
    r["lead_hours"] = (r["target_time"] - r["issue_time"]).dt.total_seconds() / 3600
    r["target_date"] = pc.trading_day(r["target_time"])
    r["pd_req"] = np.nan                                  # no 08:00 run covers two days ahead
    r = sf.stpasa_features(r, pc.AVAILABILITY)
    r = sf.weather_features(r, pc.AVAILABILITY)
    for c in ("aemo_energy", "pd_headroom"):
        r[c] = np.nan
    return r[COLS]


def forecast_table() -> pd.DataFrame:
    t = pd.read_parquet(PROCESSED / "spike_table.parquet", columns=COLS)
    t2 = two_days_ahead(t["issue_date"].min(), t["issue_date"].max())
    f = pd.concat([t, t2], ignore_index=True)
    f["wind_speed_fc"] = (f["wx_wind_midwest"] + f["wx_wind_merredin"]) / 2
    f["f_demand"] = np.where(f["days_ahead"] < 2, f["pd_req"], f["st_ref"])
    return f


def actuals() -> pd.DataFrame:
    w = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["unscheduled_demand_mw", "energy"])
    s = pd.read_parquet(OUT / "scada_5min.parquet", columns=["wind_mw", "solar_mw"]).resample("30min").mean()
    a = w.join(s)
    a["resid"] = a["unscheduled_demand_mw"] - a["wind_mw"] - a["solar_mw"]
    return a


def add_derived(f: pd.DataFrame, a: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    f = f.join(a[["wind_mw", "solar_mw", "unscheduled_demand_mw", "resid"]].rename(columns=lambda c: f"act_{c}"), on="target_time")
    fit = f[(f["days_ahead"] == 1) & (f["target_date"] >= WX_START) & (f["target_date"] < pc.EVAL_START)]
    curves = {}
    for name, x, y in (("wind", "wind_speed_fc", "act_wind_mw"), ("solar", "wx_ghi", "act_solar_mw")):
        ok = fit[[x, y]].notna().all(axis=1)
        curves[name] = IsotonicRegression(out_of_bounds="clip").fit(fit.loc[ok, x], fit.loc[ok, y])
        has = f[x].notna()
        f[f"f_{name}_mw"] = np.nan
        f.loc[has, f"f_{name}_mw"] = curves[name].predict(f.loc[has, x])
    f["f_resid"] = f["f_demand"] - f["f_wind_mw"] - f["f_solar_mw"]
    f["period"] = period_of(f["target_date"])
    return f, curves


def skill(f: pd.DataFrame) -> pd.DataFrame:
    rows = []
    f = f[f["target_date"] >= WX_START]
    for (lead, period), g in f.groupby(["days_ahead", "period"]):
        for window, h in (("全部时段", g), ("16-20 点", g[(g["target_time"].dt.hour >= 16) & (g["target_time"].dt.hour < 20)])):
            for name, fc, act in (("风电", "f_wind_mw", "act_wind_mw"), ("需求", "f_demand", "act_unscheduled_demand_mw"),
                                  ("剩余需求", "f_resid", "act_resid")):
                x = h[[fc, act]].dropna()
                if len(x) < 50:
                    continue
                row = {"lead": LEADS[lead], "period": period, "window": window, "quantity": name, "n": len(x),
                       "corr": x[fc].corr(x[act]), "mae": (x[fc] - x[act]).abs().mean(), "bias": (x[fc] - x[act]).mean()}
                if window != "全部时段":
                    # the extreme evenings: low wind = bottom 20%, high residual = top 10% (actual vs forecast, same share)
                    q, low = (0.2, True) if name == "风电" else (0.1, False)
                    if low:
                        hit_act, hit_fc = x[act] <= x[act].quantile(q), x[fc] <= x[fc].quantile(q)
                    else:
                        hit_act, hit_fc = x[act] >= x[act].quantile(1 - q), x[fc] >= x[fc].quantile(1 - q)
                    row["extreme_share"] = q
                    row["extreme_caught"] = (hit_act & hit_fc).sum() / hit_act.sum()
                rows.append(row)
    return pd.DataFrame(rows)


def matched(st: Study, f: pd.DataFrame, a: pd.DataFrame) -> pd.DataFrame:
    win = st.win
    rows = []
    act_resid = a["resid"].reindex(win["t0"]).set_axis(win.index)
    act_wind = a["wind_mw"].reindex(win["t0"]).set_axis(win.index)
    for lead in LEADS:
        g = f[f["days_ahead"] == lead].set_index("target_time")
        g = g[~g.index.duplicated()]
        look = g.reindex(win["t0"]).set_axis(win.index)
        feats = {"预报剩余需求": look["f_resid"], "预报风电": look["f_wind_mw"], "预报需求": look["f_demand"],
                 "ST PASA 需求": look["st_ref"], "预报气温": look["wx_temp"], "AEMO 预测价": look["aemo_energy"]}
        if lead == 0:
            feats.update({"实际剩余需求（完全预知）": act_resid, "实际风电（完全预知）": act_wind})
        for period in ("train", "validation", "test"):
            ps = st.sets[st.sets["period"] == period]
            if period == "train":
                ps = ps[ps["case_day"] >= WX_START]
            for name, x in feats.items():
                r = matched_ranks(x, ps)
                if r.notna().sum() < 5:
                    continue
                lo, hi = boot_ci(r.to_numpy())
                rows.append({"lead": LEADS[lead] if "完全预知" not in name else "—", "period": period, "forecast": name,
                             "cases": int(r.notna().sum()), "matched_auc": r.mean(), "lo": lo, "hi": hi})
    return pd.DataFrame(rows).drop_duplicates(["lead", "period", "forecast"])


def day_flags(f: pd.DataFrame, a: pd.DataFrame) -> pd.DataFrame:
    e = pd.read_csv(OUT / "events.csv", parse_dates=["t0", "trading_day"])
    top_days = set(e.loc[e["top_regime"], "trading_day"])
    any_days = set(e["trading_day"])
    rows = []
    act = a["resid"].groupby(pc.trading_day(a.index)).max()
    for lead in list(LEADS) + ["完全预知"]:
        if lead == "完全预知":
            risk = act
        else:
            g = f[f["days_ahead"] == lead]
            risk = g.groupby("target_date")["f_resid"].max()
        risk = risk[(risk.index >= WX_START) & (risk.index <= pd.Timestamp("2026-10-15"))].dropna()
        per = pd.Series(period_of(risk.index), index=risk.index)
        for period in ("train", "validation", "test"):
            x = risk[per == period]
            top = x.index.isin(list(top_days))
            anyd = x.index.isin(list(any_days))
            for share in (0.1, 0.2):
                flag = (x.rank(ascending=False, method="first") <= round(share * len(x))).to_numpy()
                rows.append({"lead": LEADS.get(lead, lead), "period": period, "flag_share": share, "days": len(x),
                             "top_days": int(top.sum()), "top_caught": int((flag & top).sum()),
                             "top_recall": (flag & top).sum() / max(1, top.sum()),
                             "flagged_with_top": (flag & top).sum() / max(1, flag.sum()), "base_top": top.mean(),
                             "flagged_with_any": (flag & anyd).sum() / max(1, flag.sum()), "base_any": anyd.mean()})
    return pd.DataFrame(rows)


def model(st: Study, f: pd.DataFrame) -> pd.DataFrame:
    import lightgbm as lgb
    win = st.win
    rows = []
    fits = st.fit_sets()
    for lead in LEADS:
        g = f[f["days_ahead"] == lead].set_index("target_time")
        g = g[~g.index.duplicated()]
        X = g.reindex(win["t0"]).set_axis(win.index)[["f_resid", "f_wind_mw", "f_solar_mw", "f_demand", "st_ref", "wx_temp", "wx_tmax_day",
                                                      "wx_cloud", "wx_ghi", "aemo_energy", "pd_headroom"]]
        X["month_sin"], X["month_cos"] = np.sin(2 * np.pi * win["t0"].dt.month / 12), np.cos(2 * np.pi * win["t0"].dt.month / 12)
        X["dow"] = win["t0"].dt.dayofweek
        fs = fits["T"]
        fs = fs[fs["case_day"] >= WX_START]
        d = st.design(fs)
        m = lgb.LGBMClassifier(**LGB).fit(X.loc[d.index], d["y"])
        s = pd.Series(m.predict_proba(X)[:, 1], index=X.index)
        for period in ("validation", "test"):
            ps = st.sets[st.sets["period"] == period]
            r = matched_ranks(s, ps)
            lo, hi = boot_ci(r.to_numpy())
            rows.append({"lead": LEADS[lead], "period": period, "fit_cases": int(d["y"].sum()), "matched_auc": r.mean(), "lo": lo, "hi": hi})
    return pd.DataFrame(rows)


def main() -> None:
    f = forecast_table()
    a = actuals()
    f, _ = add_derived(f, a)
    sk = skill(f)
    sk.to_csv(OUT / "forecast_skill.csv", index=False)
    st = Study()
    mt = matched(st, f, a)
    mt.to_csv(OUT / "forecast_matched.csv", index=False)
    df = day_flags(f, a)
    df.to_csv(OUT / "forecast_day_flags.csv", index=False)
    md = model(st, f)
    md.to_csv(OUT / "forecast_model.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 300)
    for name, t in (("skill", sk), ("matched", mt), ("day flags", df), ("model", md)):
        print(f"== {name}")
        print(t.round(3).to_string())


if __name__ == "__main__":
    main()
