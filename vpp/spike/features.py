"""Point-in-time training table: one row per (issue, target interval).

Rows: every trading day D from 2023-10-02, issued at 09:00 D; targets 09:00 D .. 07:30 D+1 (days_ahead 0)
and 08:00 D+1 .. 07:30 D+2 (days_ahead 1). Every feature comes from records public at the issue
(available_at <= issue_time; for each target, scenario and entity the newest such record), and each source
leaves provenance columns (src_*: which run / report / snapshot, when it was public) so the leak audit can
check every row. Prices are used up to the end of trading day D-1 (08:00 D), an hour before the issue.

Groups (vpp.spike.models adds them in this order):
  time     slot, dow, month, weekend
  price    same-slot history, recent spikes, volatility, last day's peak
  system   AEMO 08:00 pre-dispatch quantities (demand, rooftop PV, capacity, margins, ramps, deficit) and
           ST PASA demand scenarios and shortfall flags
  outage   vpp.spike.outages, plus revisions of the same target between two runs both public at the issue
  weather  ECMWF, newest vintage public at the issue
  aemo_price  AEMO pre-dispatch prices (AEMO's own forecast, also scored on its own as a baseline)
  aemo_error  AEMO's recent errors at the same slot (uncertainty about the system forecast)

Run:  python -m vpp.spike.features [--delayed]   -> data/processed/spike_table[_delayed].parquet
"""

import sys

import numpy as np
import pandas as pd

from vpp.data import PROCESSED
from vpp.spike import protocol as pc
from vpp.spike.outages import OutageFeatures
from vpp.spike.sources import PIT

HALF = pd.Timedelta(minutes=30)
FIRST_ISSUE = pd.Timestamp("2023-10-02")


# ---------------------------------------------------------------- rows and labels

def rows(first: pd.Timestamp, last: pd.Timestamp) -> pd.DataFrame:
    out = []
    for d in pd.date_range(first, last):
        t = pc.issue_time(d)
        for days_ahead, start in ((0, t), (1, d + pd.Timedelta(days=1) + pc.DAY_START)):
            ts = pd.date_range(start, d + pd.Timedelta(days=1 + days_ahead) + pc.DAY_START, freq="30min", inclusive="left")
            out.append(pd.DataFrame({"issue_date": d, "issue_time": t, "target_time": ts, "days_ahead": days_ahead}))
    r = pd.concat(out, ignore_index=True)
    r["lead_hours"] = (r["target_time"] - r["issue_time"]).dt.total_seconds() / 3600
    r["target_date"] = pc.trading_day(r["target_time"])
    r["slot"] = ((r["target_time"] - r["target_date"] - pc.DAY_START) / HALF).astype(int)
    return r


def labels(r: pd.DataFrame, df30: pd.DataFrame, p5: pd.Series) -> pd.DataFrame:
    max5 = p5.resample("30min").max()
    r["rtp"] = df30["energy"].reindex(r["target_time"]).to_numpy()
    r["max5"] = max5.reindex(r["target_time"]).to_numpy()
    r["spike"] = (r["rtp"] >= pc.SPIKE).astype(float).where(r["rtp"].notna())
    r["spike300"] = (r["rtp"] >= pc.SPIKE_HIGH).astype(float).where(r["rtp"].notna())
    r["any5"] = (r["max5"] >= pc.SPIKE).astype(float).where(r["max5"].notna())
    return r


# ---------------------------------------------------------------- time and price history (known at issue)

def time_features(r: pd.DataFrame) -> pd.DataFrame:
    r["dow"] = r["target_date"].dt.dayofweek
    r["month"] = r["target_date"].dt.month
    r["weekend"] = (r["dow"] >= 5).astype(int)
    return r


def price_features(r: pd.DataFrame, df30: pd.DataFrame, p5: pd.Series) -> pd.DataFrame:
    """Prices of complete trading days before the issue day (the last one ends 08:00 D, an hour before)."""
    rtp = df30["energy"]
    day, slot = pc.trading_day(rtp.index), ((rtp.index - pc.trading_day(rtp.index) - pc.DAY_START) / HALF).astype(int)
    grid = rtp.groupby([day, slot]).mean().unstack()                      # trading day x slot
    spike = (grid >= pc.SPIKE).astype(float).where(grid.notna())
    last = r["issue_date"] - pd.Timedelta(days=1)                         # last complete trading day
    key = pd.MultiIndex.from_arrays([last, r["slot"]])

    def at(table: pd.DataFrame) -> np.ndarray:
        return table.stack().reindex(key).to_numpy()

    near = spike.T.rolling(3, center=True, min_periods=1).sum().T        # slots s-1..s+1
    r["p_prev1"] = at(grid)
    r["p_prev7_mean"] = at(grid.rolling(7, min_periods=4).mean())
    r["p_prev7_max"] = at(grid.rolling(7, min_periods=4).max())
    r["spk_slot_7"] = at(spike.rolling(7, min_periods=4).sum())
    r["spk_near_7"] = at(near.rolling(7, min_periods=4).sum())
    r["spk_slot_28"] = at(spike.rolling(28, min_periods=14).sum())
    daily = pd.DataFrame({
        "p_day_max_prev1": grid.max(axis=1), "p_day_mean_prev1": grid.mean(axis=1),
        "p_evening_prev1": grid.loc[:, 18:25].mean(axis=1),               # 17:00-21:00
        "p_max5_prev1": p5.groupby(pc.trading_day(p5.index)).max(),
        "spk_days_7": (grid.max(axis=1) >= pc.SPIKE).astype(float).rolling(7, min_periods=4).sum(),
        "spk_int_7": spike.sum(axis=1).rolling(7, min_periods=4).sum(),
        "spk_int_28": spike.sum(axis=1).rolling(28, min_periods=14).sum(),
        "p_std_7": rtp.groupby(day).std().rolling(7, min_periods=4).mean(),
    })
    r = pd.concat([r, daily.reindex(last).reset_index(drop=True)], axis=1)
    ends = (rtp.index[rtp >= pc.SPIKE] + HALF).to_numpy()                # end of each spike interval
    k = np.searchsorted(ends, (r["issue_date"] + pc.DAY_START).to_numpy(), side="right") - 1
    last_end = np.where(k >= 0, ends[np.maximum(k, 0)], np.datetime64("NaT"))
    r["h_since_spike"] = (r["issue_time"].to_numpy() - last_end) / np.timedelta64(1, "h")
    return r


# ---------------------------------------------------------------- as-of joins

def available(rec: pd.DataFrame, source: str, lags: dict) -> pd.DataFrame:
    return rec.assign(available_at=rec["evidence_at"] + lags[source])


def asof(left: pd.DataFrame, rec: pd.DataFrame, on_time: str = "target_time") -> pd.DataFrame:
    """For each left row (issue_time, on_time) the newest record of the same target with available_at <=
    issue_time; also that record's previous version (value_prev, run_at_prev). One entity per call."""
    rec = rec.sort_values(["target_time", "run_at"]).copy()
    left = left.copy()
    for df, cols in ((rec, ["target_time", "available_at", "run_at"]), (left, ["issue_time", on_time])):
        for c in cols:
            df[c] = df[c].astype("datetime64[ns]")
    g = rec.groupby("target_time")
    rec["value_prev"], rec["run_at_prev"], rec["available_prev"] = g["value"].shift(), g["run_at"].shift(), g["available_at"].shift()
    keep = ["target_time", "available_at", "run_at", "record_id", "value", "value_prev", "run_at_prev", "available_prev"]
    out = pd.merge_asof(left.sort_values("issue_time").rename(columns={on_time: "target_time"}),
                        rec[keep].sort_values("available_at"), left_on="issue_time", right_on="available_at",
                        by="target_time", direction="backward")
    return out.rename(columns={"target_time": on_time}).set_index("_row").sort_index()


def system_features(r: pd.DataFrame, lags: dict) -> pd.DataFrame:
    pdr = available(pd.read_parquet(PIT / "predispatch.parquet"), "predispatch", lags)
    left = r[["issue_time", "target_time"]].assign(_row=np.arange(len(r)))
    # demandLevel (200 MW steps, a contingency input), dpvLevel (only from 2025-10-31) and dispatchTotal
    # injection are kept only for the replica of the existing classifier ("existing" group)
    names = {("requirement", "energy"): "pd_req", ("in_service_injection", "system"): "pd_inservice",
             ("available_injection", "system"): "pd_avail", ("dispatch_withdrawal", "system"): "pd_withdraw",
             ("demand_level", "system"): "pd_demand", ("dpv_level", "system"): "pd_dpv", ("dispatch_injection", "system"): "pd_inject",
             ("energy_deficit", "system"): "pd_deficit", ("price", "energy"): "aemo_energy", ("price", "reg_raise"): "aemo_reg_raise",
             ("price", "reg_lower"): "aemo_reg_lower", ("price", "cont_raise"): "aemo_cont_raise", ("price", "cont_lower"): "aemo_cont_lower"}
    for (var, fac), col in names.items():
        a = asof(left, pdr[(pdr["variable"] == var) & (pdr["facility_or_service"] == fac)])
        r[col] = a["value"].to_numpy()
        if col in ("pd_req", "pd_inservice", "aemo_energy"):
            r[f"{col}_rev"] = (a["value"] - a["value_prev"]).to_numpy()   # same target, two runs both public
        if col == "pd_req":
            r["src_pd_run"], r["src_pd_available_at"] = a["record_id"].to_numpy(), a["available_at"].to_numpy()
            r["pd_age_h"] = ((r["issue_time"] - a["run_at"].to_numpy()).dt.total_seconds() / 3600).to_numpy()
    # pd_avail is availableQuantities.energyInjectionCapacity: in WEM terms most likely capacity that is
    # available but not in service (817 MW against a 2,054 MW requirement at 17:00 on 2026-10-16), so
    # "available - requirement" is not a supply margin; the in-service headroom is.
    r["pd_headroom"] = r["pd_inservice"] - r["pd_req"]
    r["pd_headroom_all"] = r["pd_inservice"] + r["pd_avail"] - r["pd_req"]
    r["c_margin_avail"] = r["pd_avail"] - r["pd_req"]            # the existing classifier's "margin" (see above)
    r["c_net_demand"] = r["pd_demand"] - r["pd_dpv"]
    run = r.groupby(["issue_date", "target_date"])
    r["c_day_margin_min"] = run["c_margin_avail"].transform("min")
    r["pd_day_headroom_min"] = run["pd_headroom"].transform("min")
    r["pd_day_req_max"] = run["pd_req"].transform("max")
    r["pd_req_vs_daymax"] = r["pd_req"] - r["pd_day_req_max"]
    r["aemo_day_max"] = run["aemo_energy"].transform("max")
    # ramps inside the same run: this target minus 1h / 3h earlier (rows are consecutive targets of one issue)
    by_issue = r.groupby("issue_date")
    for k, h in ((2, "1h"), (6, "3h")):
        r[f"pd_req_ramp{h}"] = r["pd_req"] - by_issue["pd_req"].shift(k)
    return r


def stpasa_features(r: pd.DataFrame, lags: dict) -> pd.DataFrame:
    st = available(pd.read_parquet(PIT / "stpasa.parquet"), "stpasa", lags)
    # a few report days were uploaded twice (re-uploads, the 2024-06 format change): keep the first copy public
    dem = st[st["variable"] == "demand_forecast"].sort_values("available_at").drop_duplicates(
        ["record_id", "scenario", "target_time"], keep="first")
    left = r[["issue_time", "target_time"]].assign(_row=np.arange(len(r)))
    ref = asof(left, dem[dem["scenario"] == "Reference"])
    r["st_ref"] = ref["value"].to_numpy()
    r["st_ref_rev"] = (ref["value"] - ref["value_prev"]).to_numpy()
    r["src_st_report"], r["src_st_available_at"] = ref["record_id"].to_numpy(), ref["available_at"].to_numpy()
    r["st_age_h"] = ((r["issue_time"] - ref["run_at"].to_numpy()).dt.total_seconds() / 3600).to_numpy()
    for sc, col in (("High", "st_high"), ("Low", "st_low")):
        a = asof(left, dem[dem["scenario"] == sc])
        # only from the same report as the Reference value
        r[col] = np.where(a["record_id"].to_numpy() == ref["record_id"].to_numpy(), a["value"].to_numpy(), np.nan)
    r["st_spread"] = r["st_high"] - r["st_low"]
    r["st_minus_pd"] = r["st_ref"] - r["pd_req"]
    # shortfall flags: 1 if the report used for this target flags it, 0 if that report covers it without a flag
    flags = st[st["variable"].str.startswith("short_flag")]
    key = flags.assign(k=flags["record_id"] + "|" + flags["target_time"].astype(str))
    has_report = r["src_st_report"].notna()
    k = r["src_st_report"].astype(str) + "|" + r["target_time"].astype(str)
    for name, sel in (("st_short_energy", key["facility_or_service"] == "energy"),
                      ("st_short_raise", key["facility_or_service"].isin(["reg_raise", "cont_raise"]))):
        r[name] = np.where(has_report, k.isin(set(key.loc[sel, "k"])).astype(float), np.nan)
    r["st_short_energy_day"] = r.groupby(["issue_date", "target_date"])["st_short_energy"].transform("sum").where(has_report)
    return r


def weather_features(r: pd.DataFrame, lags: dict) -> pd.DataFrame:
    """Per 30-min target: instantaneous variables are the mean of the two hourly values around it, radiation
    the hourly mean it falls in (as vpp.data.load_weather); every hourly value is the newest vintage public
    at the issue."""
    wx = available(pd.read_parquet(PIT / "weather.parquet"), "weather", lags)
    h0 = r["target_time"].dt.floor("h")
    on_hour = (r["target_time"] == h0).to_numpy()
    spec = {"wx_temp": ("perth", "temperature_2m"), "wx_ghi": ("perth", "shortwave_radiation"), "wx_cloud": ("perth", "cloud_cover"),
            "wx_wind_midwest": ("midwest", "wind_speed_100m"), "wx_wind_merredin": ("merredin", "wind_speed_100m")}
    newest = np.full(len(r), np.datetime64("NaT"), dtype="datetime64[ns]")  # latest available_at among values used
    for col, (loc, var) in spec.items():
        rec = wx[(wx["facility_or_service"] == loc) & (wx["variable"] == var)]
        a0 = asof(pd.DataFrame({"issue_time": r["issue_time"], "hour": h0, "_row": np.arange(len(r))}), rec, on_time="hour")
        used = [a0]
        if var == "shortwave_radiation":
            r[col] = a0["value"].to_numpy()
        else:
            a1 = asof(pd.DataFrame({"issue_time": r["issue_time"], "hour": h0 + pd.Timedelta(hours=1), "_row": np.arange(len(r))}), rec, on_time="hour")
            r[col] = np.where(on_hour, a0["value"].to_numpy(), (a0["value"].to_numpy() + a1["value"].to_numpy()) / 2)
            used.append(a1)
        for a in used:
            newest = np.fmax(newest, a["available_at"].to_numpy().astype("datetime64[ns]"))
        if col == "wx_temp":
            r["src_wx_run"] = a0["record_id"].to_numpy()
            r["wx_age_h"] = ((r["issue_time"] - a0["run_at"].to_numpy()).dt.total_seconds() / 3600).to_numpy()
    r["src_wx_available_at"] = newest
    day = r.groupby(["issue_date", "target_date"])
    r["wx_tmax_day"] = day["wx_temp"].transform("max")
    r["wx_ghi_day"] = day["wx_ghi"].transform("mean")
    evening = ((r["wx_wind_midwest"] + r["wx_wind_merredin"]) / 2).where(r["target_time"].dt.hour.isin([17, 18, 19, 20]))
    r["wx_wind_evening"] = evening.groupby([r["issue_date"], r["target_date"]]).transform("mean")
    return r


def aemo_error_features(r: pd.DataFrame, df30: pd.DataFrame, lags: dict) -> pd.DataFrame:
    """AEMO's error (actual - forecast) at the same slot on complete days before the issue, from each day's
    own 08:00 run (days_ahead 0). Both the run and the actual price were public before the issue."""
    pdr = available(pd.read_parquet(PIT / "predispatch.parquet"), "predispatch", lags)
    e = pdr[(pdr["variable"] == "price") & (pdr["facility_or_service"] == "energy")]
    e = e[pc.trading_day(e["target_time"]) == pd.to_datetime(e["record_id"].str[:8], format="%Y%m%d")]
    err = (df30["energy"].reindex(e["target_time"]).to_numpy() - e["value"].to_numpy())
    t = pd.DatetimeIndex(e["target_time"])
    day = pc.trading_day(t)
    grid = pd.Series(err, index=pd.MultiIndex.from_arrays([day, ((t - day - pc.DAY_START) / HALF).astype(int)])).groupby(level=[0, 1]).mean().unstack()
    key = pd.MultiIndex.from_arrays([r["issue_date"] - pd.Timedelta(days=1), r["slot"]])
    r["aemo_err1"] = grid.stack().reindex(key).to_numpy()
    r["aemo_err7"] = grid.rolling(7, min_periods=3).mean().stack().reindex(key).to_numpy()
    r["aemo_abserr7"] = grid.abs().rolling(7, min_periods=3).mean().stack().reindex(key).to_numpy()
    return r


def outage_features(r: pd.DataFrame, delayed: bool) -> pd.DataFrame:
    of = OutageFeatures()
    parts = []
    for (d, t), g in r.groupby(["issue_date", "issue_time"], sort=True):
        f = of.for_issue(d, t, pd.DatetimeIndex(g["target_time"]), lag_days=1 if delayed else 0)
        parts.append(f.set_index(g.index))
    return r.join(pd.concat(parts))


GROUPS = {
    "time": ["slot", "dow", "month", "weekend"],
    "price": ["p_prev1", "p_prev7_mean", "p_prev7_max", "spk_slot_7", "spk_near_7", "spk_slot_28", "p_day_max_prev1",
              "p_day_mean_prev1", "p_evening_prev1", "p_max5_prev1", "spk_days_7", "spk_int_7", "spk_int_28", "p_std_7", "h_since_spike"],
    "system": ["pd_req", "pd_inservice", "pd_avail", "pd_withdraw", "pd_deficit", "pd_headroom", "pd_headroom_all",
               "pd_day_headroom_min", "pd_day_req_max", "pd_req_vs_daymax", "pd_req_ramp1h", "pd_req_ramp3h", "pd_age_h",
               "st_ref", "st_high", "st_low", "st_spread", "st_minus_pd", "st_short_energy", "st_short_raise",
               "st_short_energy_day", "st_age_h"],
    "outage": ["out_loss_mw", "out_loss_forced_mw", "out_loss_planned_mw", "out_n_gen", "out_n_full", "out_pending_mw",
               "out_new24_mw", "out_rev_mw", "out_now_mw", "out_forced24_n", "out_age_h", "pd_req_rev", "pd_inservice_rev",
               "st_ref_rev"],
    "weather": ["wx_temp", "wx_ghi", "wx_cloud", "wx_wind_midwest", "wx_wind_merredin", "wx_tmax_day", "wx_ghi_day",
                "wx_wind_evening", "wx_age_h"],
    "aemo_price": ["aemo_energy", "aemo_reg_raise", "aemo_reg_lower", "aemo_cont_raise", "aemo_cont_lower", "aemo_day_max",
                   "aemo_energy_rev"],
    "aemo_error": ["aemo_err1", "aemo_err7", "aemo_abserr7"],
}
# the existing classifier's 36 features (vpp.spike_risk), rebuilt under this protocol (RoCoF price dropped: always 0)
GROUPS["existing"] = [
    "aemo_energy", "aemo_reg_raise", "aemo_reg_lower", "aemo_cont_raise", "aemo_cont_lower", "pd_req", "pd_demand", "pd_dpv",
    "pd_inservice", "pd_avail", "pd_inject", "pd_withdraw", "pd_deficit", "p_prev1", "p_prev7_mean", "aemo_err1", "aemo_err7",
    "slot", "dow", "month", "wx_temp", "wx_ghi", "wx_cloud", "wx_wind_midwest", "wx_wind_merredin", "wx_tmax_day", "wx_ghi_day",
    "wx_wind_evening", "c_margin_avail", "pd_headroom", "c_day_margin_min", "aemo_day_max", "c_net_demand", "p_day_max_prev1",
    "spk_days_7"]
GROUPS["stpasa"] = [c for c in GROUPS["system"] if c.startswith("st_")] + ["st_ref_rev"]
GROUPS["outage_only"] = [c for c in GROUPS["outage"] if c.startswith("out_")]
GROUPS["price_extra"] = [c for c in GROUPS["price"] if c not in GROUPS["existing"]]


def build(delayed: bool = False) -> pd.DataFrame:
    lags = pc.DELAYED if delayed else pc.AVAILABILITY
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    p5 = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=["energy"])["energy"]
    last = pc.trading_day(df30.index[-1:])[0]                             # last trading day with prices
    r = rows(FIRST_ISSUE, last)
    r = labels(r, df30, p5)
    r = time_features(r)
    r = price_features(r, df30, p5)
    r = system_features(r, lags)
    r = stpasa_features(r, lags)
    r = weather_features(r, lags)
    r = aemo_error_features(r, df30, lags)
    r = outage_features(r, delayed)
    return r


def main() -> None:
    delayed = "--delayed" in sys.argv
    r = build(delayed)
    out = PROCESSED / f"spike_table{'_delayed' if delayed else ''}.parquet"
    r.to_parquet(out, index=False)
    print(f"{len(r):,} rows, {r['issue_date'].nunique()} issues -> {out}")


if __name__ == "__main__":
    main()
