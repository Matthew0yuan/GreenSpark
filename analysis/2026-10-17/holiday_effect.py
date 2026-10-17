"""Do holidays go with spikes? Day-level counts, the forecast error on holidays, and holiday features in the model.

Days are calendar days (a spike belongs to the calendar date of its onset t0), 2023-10-01 .. 2026-10-15, typed by
wa_calendar.DAY_TYPES (public holiday > weekend > working day next to a public holiday > school-holiday working day
> school-term working day) with Christmas-New Year (24 Dec .. 1 Jan) as an extra flag.

1. counts      per day type and regime: days, days with a top-20% spike onset, days with any spike onset
2. observed vs expected   for each group of days, the top-spike days expected if each of them had the rate of the
               other days of the same calendar month and regime (so summer and winter are compared like with like);
               ratio with an exact Poisson 95% interval
3. forecast error   actual energy requirement minus the 08:00 pre-dispatch forecast of the same day (D+0), mean over
               16:00-20:00, per day type; 95% interval by resampling days
4. supply      16:00-20:00 forecast headroom, in-service capacity, outages, coal and wind output on each kind of day,
               against school-term working days of the same month and regime
5. the spikes   every top-20% spike on a public holiday, next to one, or over Christmas-New Year, with what its matched
               controls looked like
6. model       holiday features (known in advance) added to the actionable LightGBM of analyze.py, fitted on train;
               the gain in matched AUC on validation with a paired 95% interval (kept only if above 0), test once

Run:  python holiday_effect.py   -> out/holiday_*.csv
"""

import numpy as np
import pandas as pd
from scipy.stats import chi2

from analyze import RNG, Study, feature_sets, matched_ranks, scores_for
from common import OUT, PROCESSED, pc, period_of, regime_of
from wa_calendar import DAY_TYPES, day_table

GROUPS = {  # name -> selector on the day table
    **{t: (lambda d, t=t: d["day_type"] == t) for t in DAY_TYPES},
    "圣诞到元旦（12-24 至 01-01）": lambda d: d["xmas_newyear"],
}
_ordinary_weekday = lambda d: ~d["weekend"] & ~d["public_holiday"] & ~d["bridge"]  # noqa: E731
# what each group is compared with (always the same calendar month and regime): the days it would otherwise have
# been. A whole-month "other days" pool fails for school holidays, which fill most of December and January.
REFERENCE = {
    DAY_TYPES[0]: ("普通工作日", _ordinary_weekday),
    DAY_TYPES[1]: ("普通工作日", _ordinary_weekday),
    DAY_TYPES[2]: ("普通工作日", _ordinary_weekday),
    DAY_TYPES[3]: ("上学期间的工作日", lambda d: _ordinary_weekday(d) & ~d["school_holiday"]),
    "圣诞到元旦（12-24 至 01-01）": ("同月份其余的日子", lambda d: ~d["xmas_newyear"]),
}


def poisson_ci(o: int, e: float) -> tuple[float, float]:
    lo = chi2.ppf(0.025, 2 * o) / 2 if o > 0 else 0.0
    hi = chi2.ppf(0.975, 2 * o + 2) / 2
    return lo / e, hi / e


def days_with_spikes() -> pd.DataFrame:
    d = day_table("2023-10-01", "2026-10-15")
    d["regime"] = regime_of(d.index)
    d["period"] = period_of(d.index)
    e = pd.read_csv(OUT / "events.csv", parse_dates=["t0"])
    e["date"] = e["t0"].dt.normalize()
    d["any_spike"] = d.index.isin(e["date"])
    d["top_spike"] = d.index.isin(e.loc[e["top_regime"], "date"])
    d["month"] = d.index.month
    return d


def observed_expected(d: pd.DataFrame, outcome: str) -> pd.DataFrame:
    """Observed spike days in a group against those expected at its reference's rate in the same month and regime.
    Days in a month with no reference day are left out (counted in `days_without_reference`)."""
    rows = []
    for regime in ("old", "new"):
        r = d[d["regime"] == regime]
        for name, (ref_name, ref_sel) in REFERENCE.items():
            g = GROUPS[name](r)
            if not g.any():
                continue
            ref = r[ref_sel(r) & ~g]
            rate = ref.groupby("month")[outcome].mean()
            p = rate.reindex(r.loc[g, "month"]).to_numpy()
            ok = ~np.isnan(p)
            e = p[ok].sum()
            o = int(r.loc[g, outcome].to_numpy()[ok].sum())
            lo, hi = poisson_ci(o, e) if e > 0 else (np.nan, np.nan)
            rows.append({"regime": regime, "group": name, "reference": ref_name, "days": int(ok.sum()),
                         "days_without_reference": int((~ok).sum()), "observed": o, "expected": e,
                         "ratio": o / e if e > 0 else np.nan, "lo": lo, "hi": hi})
    return pd.DataFrame(rows)


def forecast_error(d: pd.DataFrame) -> pd.DataFrame:
    """Actual requirement minus the D+0 08:00 forecast, 16:00-20:00 mean per calendar day."""
    pdr = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet", columns=["issue_date", "ts", "days_ahead", "aemo_req_energy_mw"])
    pdr = pdr[(pdr["days_ahead"] == 0) & (pdr["ts"].dt.hour >= 16) & (pdr["ts"].dt.hour < 20)]
    act = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["req_energy_mw"])["req_energy_mw"]
    pdr["err"] = act.reindex(pdr["ts"]).to_numpy() - pdr["aemo_req_energy_mw"].to_numpy()
    pdr["actual"] = act.reindex(pdr["ts"]).to_numpy()
    day = pdr.groupby(pdr["ts"].dt.normalize())[["err", "actual"]].mean()
    day = day.join(d[["day_type", "regime", "xmas_newyear", "school_holiday"]], how="inner")
    rows = []
    for regime in ("old", "new"):
        x = day[day["regime"] == regime]
        for name, sel in GROUPS.items():
            g = x[sel(x)]
            if len(g) < 3:
                continue
            b = g["err"].to_numpy()[RNG.integers(0, len(g), (2000, len(g)))].mean(axis=1)
            rows.append({"regime": regime, "group": name, "days": len(g), "mean_error_mw": g["err"].mean(),
                         "lo": np.percentile(b, 2.5), "hi": np.percentile(b, 97.5), "mean_abs_error_mw": g["err"].abs().mean(),
                         "actual_mw": g["actual"].mean()})
    return pd.DataFrame(rows)


def supply_by_day_type(d: pd.DataFrame) -> pd.DataFrame:
    """16:00-20:00 means per day, minus the mean of school-term working days of the same month and regime: AEMO's
    D+0 forecast of in-service capacity, requirement and their difference (headroom), outages (ex post) and SCADA
    coal and wind output. 95% intervals by resampling days."""
    pdr = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet",
                          columns=["ts", "days_ahead", "aemo_in_service_injection_mw", "aemo_req_energy_mw"])
    pdr = pdr[(pdr["days_ahead"] == 0) & (pdr["ts"].dt.hour >= 16) & (pdr["ts"].dt.hour < 20)]
    pdr["headroom_fc"] = pdr["aemo_in_service_injection_mw"] - pdr["aemo_req_energy_mw"]
    x = pdr.rename(columns={"aemo_in_service_injection_mw": "in_service_fc", "aemo_req_energy_mw": "requirement_fc"})
    x = x.groupby(x["ts"].dt.normalize())[["headroom_fc", "in_service_fc", "requirement_fc"]].mean()
    for path, cols in ((OUT / "outages_30min.parquet", ["out_planned_mw", "out_forced_mw"]),
                       (OUT / "scada_5min.parquet", ["coal_mw", "coal_units_on", "wind_mw"])):
        t = pd.read_parquet(path, columns=cols)
        t = t[(t.index.hour >= 16) & (t.index.hour < 20)]
        x = x.join(t.groupby(t.index.normalize()).mean())
    x = d[["day_type", "regime", "xmas_newyear", "school_holiday"]].assign(month=d.index.month).join(x, how="inner")
    cols = ["headroom_fc", "in_service_fc", "requirement_fc", "out_planned_mw", "out_forced_mw", "coal_mw", "coal_units_on", "wind_mw"]
    rows = []
    for regime in ("old", "new"):
        r = x[x["regime"] == regime]
        ref = r[r["day_type"] == DAY_TYPES[4]].groupby("month")[cols].mean()
        for name, sel in GROUPS.items():
            g = r[sel(r)]
            if len(g) < 3 or name == DAY_TYPES[4]:
                continue
            dev = g[cols] - ref.reindex(g["month"]).to_numpy()
            row = {"regime": regime, "group": name, "days": len(g)}
            for c in cols:
                v = dev[c].dropna().to_numpy()
                b = v[RNG.integers(0, len(v), (2000, len(v)))].mean(axis=1)
                row[c], row[f"{c}_lo"], row[f"{c}_hi"] = v.mean(), np.percentile(b, 2.5), np.percentile(b, 97.5)
            rows.append(row)
    return pd.DataFrame(rows)


def holiday_features(st: Study, d: pd.DataFrame) -> pd.DataFrame:
    date = st.win["t0"].dt.normalize()
    cal = d.reindex(date)
    return pd.DataFrame({
        "hol_public": cal["public_holiday"].astype(float).to_numpy(),
        "hol_bridge": cal["bridge"].astype(float).to_numpy(),
        "hol_school": cal["school_holiday"].astype(float).to_numpy(),
        "hol_xmas": cal["xmas_newyear"].astype(float).to_numpy(),
        "hol_days_to": np.minimum(cal["days_to_holiday"].to_numpy(), 30.0),
    }, index=st.win.index)


def model_check(st: Study, d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    F, avail = st.features(0)
    H = holiday_features(st, d)
    base = feature_sets(avail)["actionable"]
    F2 = F.join(H)
    s0 = scores_for(st, "lgb", F, base, "T", 0.1)
    s1 = scores_for(st, "lgb", F2, base + list(H.columns), "T", 0.1)
    rows, shares = [], []
    for period in ("train", "validation", "test"):
        ps = st.sets[st.sets["period"] == period]
        r0, r1 = matched_ranks(s0, ps), matched_ranks(s1, ps)
        diff = (r1 - r0).to_numpy()
        b = diff[RNG.integers(0, len(diff), (2000, len(diff)))].mean(axis=1)
        rows.append({"period": period, "cases": len(r0), "base_auc": r0.mean(), "with_holidays_auc": r1.mean(),
                     "gain": diff.mean(), "lo": np.percentile(b, 2.5), "hi": np.percentile(b, 97.5),
                     "scored": "in fit set (out of fold)" if period == "train" else "out of sample"})
        cw, kw = ps["case_window"].unique(), ps["control_window"]
        for c in H.columns[:4]:
            shares.append({"period": period, "feature": c, "cases_share": H.loc[cw, c].mean(),
                           "controls_share": H.loc[kw, c].mean(), "cases_n": int(H.loc[cw, c].sum())})
    return pd.DataFrame(rows), pd.DataFrame(shares)


def special_spikes(st: Study, d: pd.DataFrame) -> pd.DataFrame:
    F, _ = st.features(0)
    e = pd.read_csv(OUT / "events.csv", parse_dates=["t0"])
    e = e[e["top_regime"]].copy()
    e["date"] = e["t0"].dt.normalize()
    cal = d.reindex(e["date"])
    keep = (cal["public_holiday"] | cal["bridge"] | cal["xmas_newyear"]).to_numpy()
    e = e[keep].assign(day_type=cal["day_type"].to_numpy()[keep], xmas=cal["xmas_newyear"].to_numpy()[keep])
    cols = {"unscheduled_demand_mw_last30": "demand", "wx_temp_t0": "temp_fc", "wind_mw_last30": "wind",
            "residual_mw_last30": "residual", "coal_units_on_last30": "coal_units", "pd_headroom_t0": "headroom_fc",
            "battery_mw_last30": "battery", "bat_dis_mwh_5h": "battery_mwh_5h", "peaker_mw_last30": "peakers",
            "surprise_req_last30": "req_minus_forecast", "pd_price_t0": "aemo_price_fc", "energy_last30": "price_last30"}
    rows = []
    for _, r in e.iterrows():
        w = f"E{r['event_id']}"
        ctl = st.sets.loc[st.sets["case_window"] == w, "control_window"]
        row = {"t0": r["t0"], "weekday": r["t0"].day_name(), "day_type": r["day_type"], "xmas_newyear": r["xmas"],
               "peak": r["peak"], "duration_h": r["duration_h"], "period": r["period"]}
        for f, name in cols.items():
            row[name] = F.loc[w, f]
            row[f"{name}_controls"] = F.loc[ctl, f].median()
        rows.append(row)
    return pd.DataFrame(rows).sort_values("t0")


def main() -> None:
    d = days_with_spikes()
    counts = d.groupby(["regime", "day_type"]).agg(days=("top_spike", "size"), top_days=("top_spike", "sum"),
                                                  any_days=("any_spike", "sum"))
    counts["top_per_100_days"] = 100 * counts["top_days"] / counts["days"]
    counts["any_per_100_days"] = 100 * counts["any_days"] / counts["days"]
    counts.to_csv(OUT / "holiday_counts.csv")
    oe = pd.concat([observed_expected(d, "top_spike").assign(outcome="前 20% 尖峰"),
                    observed_expected(d, "any_spike").assign(outcome="任何尖峰")])
    oe.to_csv(OUT / "holiday_observed_expected.csv", index=False)
    fe = forecast_error(d)
    fe.to_csv(OUT / "holiday_forecast_error.csv", index=False)
    su = supply_by_day_type(d)
    su.to_csv(OUT / "holiday_supply.csv", index=False)
    st = Study()
    sp = special_spikes(st, d)
    sp.to_csv(OUT / "holiday_spikes.csv", index=False)
    mc, sh = model_check(st, d)
    mc.to_csv(OUT / "holiday_model_check.csv", index=False)
    sh.to_csv(OUT / "holiday_feature_shares.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    for name, t in (("counts", counts), ("observed vs expected", oe), ("forecast error 16-20h", fe),
                    ("supply vs term weekdays 16-20h", su[["regime", "group", "days", "headroom_fc", "in_service_fc", "requirement_fc",
                                                           "out_planned_mw", "coal_mw", "coal_units_on", "wind_mw"]]),
                    ("special spikes", sp), ("model", mc), ("shares", sh)):
        print(f"== {name}")
        print(t.round(3).to_string())


if __name__ == "__main__":
    main()
