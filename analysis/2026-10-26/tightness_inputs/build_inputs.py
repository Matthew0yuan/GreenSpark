"""Step 2 data (PLAN.md): for each target trading day D, the inputs public at D-1 09:00 AWST and the ex-post labels
for D 16:00-21:00 (the ten 30-min trading intervals starting 16:00 .. 20:30).

Inputs and their public-time rules (PLAN.md section 3):
  predispatch  D-1's run (08:00; a later run where the archive lacks it), D's intervals; public = issue ID + 20 min;
               a run not public by D-1 09:00 leaves the day missing
  outages      D-1's Real-Time Outage snapshot (newest listed before 09:00); coal and gas Scheduled Facilities only,
               loss = Capacity Credits - lowest RAC of overlapping Approved/Accepted records (build_outages.day_state)
  capacity     thermal Capacity Credits of D's Capacity Year; assumed public by 1 Oct of the year before
  battery      max over trading days D-91 .. D-2 of the day's highest 30-min mean grid battery discharge (shift 2)
  stpasa       newest report (by report day) public at D-1 09:00, first public copy of that day
  weather      ECMWF from data/pit/weather.parquet, newest vintage public at D-1 09:00 per location/variable/hour
Labels: step 1's margin (tightness_label.half_hours) inside the window; spike (D's RTP >= $200); LRC (fixed, section 5);
IRCR peak day. Comparison only: high_risk_d1 (vpp.v2.risk, day-ahead call).

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/build_inputs.py
Writes out/inputs_d1.parquet, out/inputs_d1_30min.parquet, out/lrc_days.csv, out/lrc_advisories.csv, out/coverage.csv
"""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (ROOT, ROOT / "analysis" / "2026-10-17", ROOT / "analysis" / "2026-10-26" / "supply_tightness"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from build_outages import day_state  # noqa: E402  (analysis/2026-10-17, read only)
from common import fuel_of  # noqa: E402
from vpp.data import PROCESSED, RAW, trading_date  # noqa: E402
from vpp.spike.outages import ACTIVE, OutageFeatures, capacity_credits, capacity_year, load_snapshot  # noqa: E402

PIT = ROOT / "data" / "pit"
OUT = HERE / "out"
FIRST_DAY = pd.Timestamp("2024-01-01")
SLOTS = pd.timedelta_range("16:00:00", "20:30:00", freq="30min")      # interval starts in the window
MIN_VALID = 8                                                           # label needs 8 of the 10 margins
BAT_DAYS = 90
SPIKE = 200.0
THERMAL = ("coal", "gas_base", "peaker", "other")                       # capacity side, as step 1
PERIODS = {"history": ("2024-01-01", "2025-03-31"), "in_sample": ("2025-04-01", "2026-06-30"),
           "test_seen": ("2026-07-01", "2026-10-15"), "test_unseen": ("2026-10-16", "2026-10-22")}
LAGS = {"predispatch": pd.Timedelta(minutes=20), "weather": pd.Timedelta(hours=8)}  # = vpp.spike.protocol.AVAILABILITY
NEW_ADVISORIES = HERE / "raw" / "data.wa.aemo.com.au__public__market-data__wemde__marketAdvisory__marketAdvisoryReport__market-advisories.csv"


def issue_of(day) -> pd.Series | pd.Timestamp:
    return day - pd.Timedelta(days=1) + pd.Timedelta(hours=9)


def period_of(days) -> np.ndarray:
    d = pd.DatetimeIndex(days)
    out = np.full(len(d), "", dtype=object)
    for name, (a, b) in PERIODS.items():
        out[(d >= a) & (d <= b)] = name
    return out


def grid(days: pd.DatetimeIndex) -> pd.DataFrame:
    """One row per (D, window interval)."""
    d = np.repeat(days.to_numpy(), len(SLOTS))
    g = pd.DataFrame({"day": d, "target_time": d + np.tile(SLOTS.to_numpy(), len(days))})
    g["issue_at"] = issue_of(g["day"])
    return g


# ---------------------------------------------------------------- inputs

PD_KEYS = {("requirement", "energy"): "pd_req_mw", ("in_service_injection", "system"): "pd_inservice_mw",
           ("available_injection", "system"): "pd_avail_mw"}


def predispatch_inputs(g: pd.DataFrame, pdr: pd.DataFrame, run_lag: int = 1, enforce: bool = True) -> pd.DataFrame:
    """Values of the run made on D - run_lag (1: the day before) for D's window intervals. enforce: blank a run
    not public at the issue (the protocol); False only for the leak controls."""
    key = list(zip(pdr["variable"], pdr["facility_or_service"]))
    rec = pdr[[k in PD_KEYS for k in key]].copy()
    rec["col"] = [PD_KEYS[(v, f)] for v, f in zip(rec["variable"], rec["facility_or_service"])]
    rec["run_day"] = pd.to_datetime(rec["record_id"].str[:8], format="%Y%m%d")
    runs = rec.drop_duplicates("record_id")[["record_id", "run_day", "available_at"]]
    if runs["run_day"].duplicated().any():
        raise ValueError("more than one pre-dispatch run per day")
    wide = rec.pivot_table(index=["run_day", "target_time"], columns="col", values="value", aggfunc="first").reset_index()
    x = g.assign(run_day=g["day"] - pd.Timedelta(days=run_lag)).merge(runs, on="run_day", how="left")
    x = x.merge(wide, on=["run_day", "target_time"], how="left")
    ok = x["record_id"].notna() & ((x["available_at"] <= x["issue_at"]) if enforce else True)
    out = pd.DataFrame(index=g.index)
    for c in PD_KEYS.values():
        out[c] = x[c].where(ok).to_numpy()
    out["pd_run"] = x["record_id"].where(ok).to_numpy()
    out["predispatch_available_at"] = x["available_at"].where(ok).to_numpy()
    return out


def outage_inputs(g: pd.DataFrame, snap_lag: int = 1, loader=None) -> pd.DataFrame:
    """Thermal (coal + gas) outage loss per window interval from the snapshot of D - snap_lag (1 = D-1, public at
    D-1 09:00). loader(date, issue_at) -> snapshot frame or None; default vpp.spike.outages.load_snapshot."""
    of, cc = OutageFeatures(), capacity_credits()
    load = loader or (lambda date, issue: load_snapshot(date))
    parts = []
    for (d, issue), gd in g.groupby(["day", "issue_at"], sort=False):
        f = pd.DataFrame(index=gd.index, columns=["out_thermal_mw", "out_snapshot", "outages_available_at"])
        snap = load(d - pd.Timedelta(days=snap_lag), issue)
        if snap is not None and len(snap):
            cls = of.classes(snap)
            rec = snap[cls.eq("Scheduled Facility").to_numpy() & snap["Status"].isin(ACTIVE).to_numpy()]
            targets = pd.DatetimeIndex(gd["target_time"])
            if rec.empty:
                loss = np.zeros(len(targets))
            else:
                st = day_state(rec, targets, cc)
                loss = (st["out_coal_mw"] + st["out_gas_mw"]).to_numpy()
            f["out_thermal_mw"] = loss
            f["out_snapshot"] = snap["snapshot_file"].iloc[0]
            f["outages_available_at"] = snap["listed_at"].iloc[0]
        parts.append(f)
    out = pd.concat(parts).loc[g.index]
    out["out_thermal_mw"] = out["out_thermal_mw"].astype(float)
    out["outages_available_at"] = pd.to_datetime(out["outages_available_at"])
    return out


def thermal_credits() -> pd.Series:
    cc = capacity_credits()
    fuel = pd.Series([fuel_of(f) for f in cc.index], index=cc.index)
    return cc[fuel.isin(THERMAL)].sum()


def capacity_inputs(g: pd.DataFrame) -> pd.DataFrame:
    y = capacity_year(pd.DatetimeIndex(g["day"]))
    out = pd.DataFrame(index=g.index)
    out["thermal_cap_mw"] = thermal_credits().reindex(y).to_numpy()
    out["capacity_year"] = y
    # [assumption] Capacity Credits of year Y public by 1 Oct of Y-1 (local extracts: 2026-27 by 2024-11-01, 2027-28 by 2025-10-21)
    out["capacity_credits_available_at"] = pd.to_datetime([f"{yy - 1}-10-01" for yy in y])
    return out


def day_battery(scada5: pd.DataFrame | None = None) -> pd.Series:
    """Each trading day's highest 30-min mean grid battery discharge (MW), on a gap-free daily index."""
    if scada5 is None:
        scada5 = pd.read_parquet(ROOT / "analysis" / "2026-10-17" / "out" / "scada_5min.parquet", columns=["battery_dis_mw"])
    s30 = scada5["battery_dis_mw"].resample("30min").mean()
    s = s30.groupby(trading_date(s30.index)).max()
    return s.reindex(pd.date_range(s.index.min(), s.index.max()))


def battery_inputs(g: pd.DataFrame, day_bat: pd.Series, lag: int = 2) -> pd.DataFrame:
    """Max of the daily maxima over the 90 trading days ending D - lag (lag 2: D-2 ends at D-1 08:00)."""
    day_bat = day_bat.reindex(pd.date_range(day_bat.index.min(), max(day_bat.index.max(), g["day"].max())))
    cap = day_bat.shift(lag).rolling(BAT_DAYS, min_periods=BAT_DAYS).max()
    idx = pd.Series(day_bat.index, index=day_bat.index)
    last = idx.shift(lag).where(cap.notna())
    out = pd.DataFrame(index=g.index)
    out["bat_cap_mw"] = cap.reindex(g["day"]).to_numpy()
    out["bat_last_day"] = last.reindex(g["day"]).to_numpy()
    # [assumption, to verify] a trading day's SCADA is public once it ends (08:00 next day), i.e. by the 09:00 issue
    out["battery_available_at"] = out["bat_last_day"] + pd.Timedelta(days=1, hours=8)
    return out


ST_COLS = {"Reference": "st_ref_mw", "High": "st_high_mw", "Low": "st_low_mw"}


def stpasa_copies(st: pd.DataFrame) -> pd.DataFrame:
    dem = st[(st["variable"] == "demand_forecast") & st["scenario"].isin(ST_COLS)]
    ref = dem[dem["scenario"] == "Reference"].groupby(["record_id", "version"])["target_time"].agg(["min", "max"])
    c = dem.groupby(["record_id", "version"]).agg(available_at=("available_at", "min"), run_at=("run_at", "first"))
    return c.join(ref.rename(columns={"min": "tmin", "max": "tmax"})).reset_index()


def choose_stpasa(days: pd.DatetimeIndex, copies: pd.DataFrame, rule: str = "newest_public") -> pd.DataFrame:
    """The (report, copy) used for each D. newest_public: the newest report day among copies public at the issue
    whose Reference horizon covers D's window, its first public copy (a 2024-01-24 file holds 01-11..01-18 data).
    by_date: the report dated D-1, whenever it was public (leak control)."""
    rows = []
    for d in days:
        issue = issue_of(d)
        if rule == "newest_public":
            c = copies[(copies["available_at"] <= issue) & (copies["tmin"] <= d + SLOTS[0]) & (copies["tmax"] >= d + SLOTS[-1])]
            if c.empty:
                rows.append((d, None, None, pd.NaT))
                continue
            c = c[c["run_at"] == c["run_at"].max()].sort_values("available_at").iloc[0]
        else:
            c = copies[copies["record_id"] == f"{d - pd.Timedelta(days=1):%Y-%m-%d}"].sort_values("available_at")
            if c.empty:
                rows.append((d, None, None, pd.NaT))
                continue
            c = c.iloc[0]
        rows.append((d, c["record_id"], c["version"], c["available_at"]))
    return pd.DataFrame(rows, columns=["day", "st_report", "st_version", "stpasa_available_at"])


def stpasa_inputs(g: pd.DataFrame, st: pd.DataFrame, rule: str = "newest_public") -> pd.DataFrame:
    days = pd.DatetimeIndex(g["day"].drop_duplicates())
    pick = choose_stpasa(days, stpasa_copies(st), rule)
    dem = st[(st["variable"] == "demand_forecast") & st["scenario"].isin(ST_COLS)]
    wide = dem.pivot_table(index=["record_id", "version", "target_time"], columns="scenario", values="value",
                           aggfunc="first").rename(columns=ST_COLS).reset_index()
    x = g[["day", "target_time"]].merge(pick, on="day", how="left")
    x = x.merge(wide, left_on=["st_report", "st_version", "target_time"], right_on=["record_id", "version", "target_time"], how="left")
    out = pd.DataFrame(index=g.index)
    for c in ST_COLS.values():
        out[c] = x[c].to_numpy() if c in x else np.nan
    out["st_report"], out["st_version"] = x["st_report"].to_numpy(), x["st_version"].to_numpy()
    out["st_report_age_days"] = (x["day"] - pd.to_datetime(x["st_report"])).dt.days.to_numpy()
    out["stpasa_available_at"] = pd.to_datetime(x["stpasa_available_at"]).to_numpy()
    return out


WX = {"wx_temp_perth_c": ("perth", "temperature_2m"), "wx_ghi_perth_wm2": ("perth", "shortwave_radiation"),
      "wx_wind_midwest_kmh": ("midwest", "wind_speed_100m"), "wx_wind_merredin_kmh": ("merredin", "wind_speed_100m")}


def weather_hourly(days: pd.DatetimeIndex, wx: pd.DataFrame, rule: str = "newest_public") -> pd.DataFrame:
    """Per (D, hour of trading day D 08:00 .. 07:00, column): the value used, its run and public time.
    newest_public: newest run public at the issue; previous_day1: the fixed day-1 vintage (leak control)."""
    hours = pd.timedelta_range("08:00:00", periods=24, freq="h")
    left = pd.DataFrame({"day": np.repeat(days.to_numpy(), 24)})
    left["hour"] = left["day"] + np.tile(hours.to_numpy(), len(days))
    left["issue_at"] = issue_of(left["day"])
    parts = []
    for col, (loc, var) in WX.items():
        rec = wx[(wx["facility_or_service"] == loc) & (wx["variable"] == var)][
            ["target_time", "run_at", "available_at", "record_id", "version", "value"]]
        m = left.merge(rec, left_on="hour", right_on="target_time", how="inner")
        if rule == "newest_public":
            m = m[m["available_at"] <= m["issue_at"]]
            m = m.loc[m.groupby(["day", "hour"])["run_at"].idxmax()]
        else:
            m = m[m["version"] == "previous_day1"]
        parts.append(m.assign(col=col)[["day", "hour", "col", "value", "run_at", "available_at", "record_id"]])
    return pd.concat(parts, ignore_index=True)


def weather_inputs(g: pd.DataFrame, wx: pd.DataFrame, rule: str = "newest_public") -> pd.DataFrame:
    """Half-hour values in the window (instantaneous: mean of the two hours around a :30 interval; radiation: the
    hour it falls in, as vpp.spike.features.weather_features) and day values (Perth max temperature and mean
    radiation over trading day D). weather_available_at = the latest public time among all values used that day."""
    days = pd.DatetimeIndex(g["day"].drop_duplicates())
    h = weather_hourly(days, wx, rule)
    val = h.pivot_table(index=["day", "hour"], columns="col", values="value", aggfunc="first")
    t = pd.DatetimeIndex(g["target_time"])
    h0 = t.floor("h")
    key0 = pd.MultiIndex.from_arrays([g["day"], h0])
    key1 = pd.MultiIndex.from_arrays([g["day"], h0 + pd.Timedelta(hours=1)])
    on_hour = (t == h0)
    out = pd.DataFrame(index=g.index)
    for col in WX:
        v0 = val[col].reindex(key0).to_numpy() if col in val else np.full(len(g), np.nan)
        v1 = val[col].reindex(key1).to_numpy() if col in val else np.full(len(g), np.nan)
        out[col] = v0 if col == "wx_ghi_perth_wm2" else np.where(on_hour, v0, (v0 + v1) / 2)
    n = h.groupby(["day", "col"])["value"].count().unstack()
    full = (n.reindex(columns=list(WX)) == 24)
    day = pd.DataFrame(index=days)
    day["wx_temp_perth_max_day_c"] = val["wx_temp_perth_c"].groupby(level=0).max().where(full["wx_temp_perth_c"])
    day["wx_ghi_perth_mean_day_wm2"] = val["wx_ghi_perth_wm2"].groupby(level=0).mean().where(full["wx_ghi_perth_wm2"])
    newest = h.loc[h.groupby("day")["available_at"].idxmax()].set_index("day")
    day["wx_newest_run"] = newest["record_id"]
    day["weather_available_at"] = newest["available_at"]
    return out.join(day.reindex(g["day"]).set_index(g.index))


def high_risk(days: pd.DatetimeIndex) -> pd.DataFrame:
    """The existing day-ahead high-risk call (comparison only), as step 1 took it."""
    from vpp.v2 import risk as vr
    hist = vr.history(days_ahead=1)
    risk = vr.day_risk(hist)
    issued = hist.groupby("target_date")["issue_date"].max() + pd.Timedelta(hours=9)
    return pd.DataFrame({"risk_d1": risk.reindex(days).to_numpy(), "high_risk_d1": vr.recent_flags(risk).reindex(days).to_numpy(),
                         "high_risk_available_at": issued.reindex(days).to_numpy()}, index=days)


# ---------------------------------------------------------------- labels

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_PATTERNS = [
    (re.compile(r"(\d{4})-([A-Za-z]{3,9})[-\s]*(\d{1,2})"), lambda m: (int(m[1]), MONTHS.get(m[2][:3].lower()), int(m[3]))),
    (re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})"), lambda m: (int(m[3]), int(m[2]), int(m[1]))),
    (re.compile(r"(\d{1,2})[-\s]+([A-Za-z]{3,9})[-\s]+(\d{4})"), lambda m: (int(m[3]), MONTHS.get(m[2][:3].lower()), int(m[1]))),
]


def text_date(details: str) -> pd.Timestamp:
    """The date the advisory names for its peak period ('peak period 2024-June 11', 'on 25/06/2024', '(LRC) for
    2023-Nov-21', 'between the 28 Sep 2024 ...'): the first date within 80 characters after the anchor."""
    s = str(details)
    pos = [i for i in (s.find("peak period"), s.find("(LRC) for")) if i >= 0]
    if not pos:
        return pd.NaT
    seg = s[min(pos):min(pos) + 80]
    best = None
    for rx, conv in DATE_PATTERNS:
        m = rx.search(seg)
        if m and (best is None or m.start() < best[0]):
            y, mo, d = conv(m)
            if mo:
                best = (m.start(), y, mo, d)
    if best is None:
        return pd.NaT
    try:
        return pd.Timestamp(year=best[1], month=best[2], day=best[3])
    except ValueError:
        return pd.NaT


def lrc_advisories() -> pd.DataFrame:
    """Every LRC advisory in the step-1 file and the fetched WEMDE file, with the day it is for (PLAN section 5)."""
    old = pd.read_csv(RAW / "market-advisories" / "market-advisories.csv", encoding="cp1252").assign(
        source="post-market-advisory/market-advisories.csv (step 1)")
    new = pd.read_csv(NEW_ADVISORIES, encoding="cp1252").rename(columns={"Id": "ID"}).assign(
        source="wemde/marketAdvisory/marketAdvisoryReport/market-advisories.csv (fetched 2026-10-26)")
    a = pd.concat([old, new], ignore_index=True).drop_duplicates("ID", keep="last")
    a = a[a["Details"].fillna("").str.lower().str.contains("low reserve condition")].copy()
    issued = pd.to_datetime(a["Issued At"])
    start = pd.to_datetime(a["Start Interval"], errors="coerce")
    typo = start.isna() | ((start - issued).abs() > pd.Timedelta(days=3))
    base = start.where(~typo, issued)
    td = a["Details"].map(text_date)
    plausible = td.notna() & (td >= issued.dt.normalize()) & (td <= issued.dt.normalize() + pd.Timedelta(days=2))
    a["issued"], a["start"], a["start_typo"] = issued, start, typo
    a["text_day"], a["text_day_used"] = td, plausible
    a["day"] = td.where(plausible, base.dt.normalize())
    a["step1_day"] = pd.DatetimeIndex(trading_date(pd.DatetimeIndex(base))).where(a["source"].str.contains("step 1"))
    low = a["Details"].str.lower()
    a["kind"] = np.where(low.str.contains("forecasting potential"), "forecast", np.where(low.str.contains("has declared"), "declared", "other"))
    a["reason"] = a["Details"].str.extract(r"(Due to[^,.]{0,140})", expand=False).fillna(a["Details"].str[:120])
    return a[["ID", "source", "issued", "Start Interval", "End Interval", "Withdrawn", "Withdrawn At", "start_typo",
              "text_day", "text_day_used", "day", "step1_day", "kind", "reason"]].sort_values("issued")


def lrc_days(adv: pd.DataFrame) -> pd.DataFrame:
    new = adv.groupby("day").agg(n_advisories=("ID", "size"), ids=("ID", lambda s: "|".join(map(str, s))),
                                 first_issued=("issued", "min"), kinds=("kind", lambda s: "|".join(sorted(set(s)))),
                                 reason=("reason", "first"))
    old = set(adv["step1_day"].dropna())
    new["in_step1"] = new.index.isin(old)
    new_file = adv.groupby("day")["source"].apply(lambda s: s.str.contains("fetched").all())
    new["status"] = np.where(new["in_step1"], "unchanged", np.where(new_file.reindex(new.index), "added_from_new_file", "added_by_fix"))
    removed = sorted(old - set(new.index))
    gone = pd.DataFrame({"n_advisories": 0, "in_step1": True, "status": "removed_by_fix"}, index=pd.DatetimeIndex(removed))
    out = pd.concat([new, gone]).sort_index()
    out.index.name = "day"
    out["lrc"] = out["status"] != "removed_by_fix"
    # how far the advisories reach: the newest issue time in the fetched file (all advisories, not only LRC)
    out["advisories_issued_to"] = pd.read_csv(NEW_ADVISORIES, encoding="cp1252")["Issued At"].max()
    return out


def labels(g: pd.DataFrame, days: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per-interval labels, per-day labels)."""
    import tightness_label as tl  # step 1, read only
    from vpp import site_bill as sb
    df = tl.half_hours()
    cols = {"margin_mw": "lab_margin_mw", "residual_mw": "lab_residual_mw", "outage_mw": "lab_outage_mw",
            "battery_cap_mw": "lab_battery_mw", "thermal_cap_mw": "lab_thermal_cap_mw"}
    hh = df[list(cols)].rename(columns=cols).reindex(pd.DatetimeIndex(g["target_time"]))
    w30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["energy", "demand_mw"])
    per = pd.DataFrame(hh.to_numpy(), columns=hh.columns, index=g.index)
    per["lab_rtp"] = w30["energy"].reindex(pd.DatetimeIndex(g["target_time"])).to_numpy()
    x = pd.concat([g[["day", "target_time"]], per], axis=1)
    ok = x.dropna(subset=["lab_margin_mw"])
    at = ok.loc[ok.groupby("day")["lab_margin_mw"].idxmin()].set_index("day")
    d = pd.DataFrame(index=days)
    d["n_valid_1621"] = ok.groupby("day")["lab_margin_mw"].count().reindex(days).fillna(0).astype(int)
    keep = d["n_valid_1621"] >= MIN_VALID
    d["margin_min_1621_mw"] = at["lab_margin_mw"].reindex(days).where(keep)
    d["margin_min_time"] = at["target_time"].dt.strftime("%H:%M").reindex(days).where(keep)
    for src, dst in (("lab_residual_mw", "residual_at_min_mw"), ("lab_outage_mw", "outage_at_min_mw"),
                     ("lab_battery_mw", "battery_at_min_mw"), ("lab_thermal_cap_mw", "thermal_cap_at_min_mw")):
        d[dst] = at[src].reindex(days).where(keep)
    rtp_day = w30["energy"].groupby(trading_date(w30.index))
    d["rtp_max"] = rtp_day.max().reindex(days)
    d["spike"] = (d["rtp_max"] >= SPIKE).astype("boolean").mask(d["rtp_max"].isna())
    d["rtp_max_1621"] = x.groupby("day")["lab_rtp"].max().reindex(days)
    adv = lrc_advisories()
    ld = lrc_days(adv)
    on = ld[ld["lrc"]]
    d["lrc"] = d.index.isin(on.index)
    d["lrc_first_issued"] = on["first_issued"].reindex(days)
    peaks = set()
    for y in (2023, 2024, 2025):
        peaks |= set(trading_date(sb.ircr_intervals(w30["demand_mw"], y)))
    d["ircr_peak"] = d.index.isin(peaks)
    return per, d


# ---------------------------------------------------------------- assemble

INPUT_DAY = ["pd_req_max_1621_mw", "pd_req_mean_1621_mw", "pd_inservice_min_1621_mw", "pd_inservice_mean_1621_mw",
             "pd_avail_min_1621_mw", "pd_avail_mean_1621_mw", "out_thermal_max_1621_mw", "out_thermal_mean_1621_mw",
             "thermal_cap_mw", "bat_cap_mw", "st_ref_max_1621_mw", "st_high_max_1621_mw", "st_low_max_1621_mw",
             "wx_wind_midwest_1621_kmh", "wx_wind_merredin_1621_kmh", "wx_temp_perth_max_day_c", "wx_temp_perth_max_1621_c",
             "wx_ghi_perth_mean_day_wm2", "wx_ghi_perth_mean_1621_wm2"]
SOURCES = {  # source -> (its value columns in the day table, its available_at column)
    "predispatch": (INPUT_DAY[0:6], "predispatch_available_at"),
    "outages": (INPUT_DAY[6:8], "outages_available_at"),
    "capacity_credits": (["thermal_cap_mw"], "capacity_credits_available_at"),
    "battery": (["bat_cap_mw"], "battery_available_at"),
    "stpasa": (INPUT_DAY[10:13], "stpasa_available_at"),
    "weather": (INPUT_DAY[13:19], "weather_available_at"),
}


def inputs_30min(g: pd.DataFrame, pdr, st, wx, day_bat, variant: dict | None = None) -> pd.DataFrame:
    """All inputs per (D, interval). variant switches one source to a leaky rule (leak controls only)."""
    v = variant or {}
    parts = [g,
             predispatch_inputs(g, pdr, run_lag=v.get("pd_run_lag", 1), enforce=v.get("pd_enforce", True)),
             outage_inputs(g, snap_lag=v.get("snap_lag", 1), loader=v.get("loader")),
             capacity_inputs(g),
             battery_inputs(g, day_bat, lag=v.get("bat_lag", 2)),
             stpasa_inputs(g, st, rule=v.get("st_rule", "newest_public")),
             weather_inputs(g, wx, rule=v.get("wx_rule", "newest_public"))]
    return pd.concat(parts, axis=1)


def to_days(x: pd.DataFrame) -> pd.DataFrame:
    """Window aggregates (all 10 intervals required) and the per-day provenance columns."""
    gb = x.groupby("day")
    n = len(SLOTS)

    def agg(col, how):
        s = getattr(gb[col], how)()
        return s.where(gb[col].count() == n)

    d = pd.DataFrame({"issue_at": gb["issue_at"].first()})
    d["pd_req_max_1621_mw"], d["pd_req_mean_1621_mw"] = agg("pd_req_mw", "max"), agg("pd_req_mw", "mean")
    d["pd_inservice_min_1621_mw"], d["pd_inservice_mean_1621_mw"] = agg("pd_inservice_mw", "min"), agg("pd_inservice_mw", "mean")
    d["pd_avail_min_1621_mw"], d["pd_avail_mean_1621_mw"] = agg("pd_avail_mw", "min"), agg("pd_avail_mw", "mean")
    d["out_thermal_max_1621_mw"], d["out_thermal_mean_1621_mw"] = agg("out_thermal_mw", "max"), agg("out_thermal_mw", "mean")
    d["thermal_cap_mw"], d["bat_cap_mw"] = gb["thermal_cap_mw"].first(), gb["bat_cap_mw"].first()
    d["st_ref_max_1621_mw"], d["st_high_max_1621_mw"], d["st_low_max_1621_mw"] = (
        agg("st_ref_mw", "max"), agg("st_high_mw", "max"), agg("st_low_mw", "max"))
    d["wx_wind_midwest_1621_kmh"], d["wx_wind_merredin_1621_kmh"] = agg("wx_wind_midwest_kmh", "mean"), agg("wx_wind_merredin_kmh", "mean")
    d["wx_temp_perth_max_day_c"], d["wx_temp_perth_max_1621_c"] = gb["wx_temp_perth_max_day_c"].first(), agg("wx_temp_perth_c", "max")
    d["wx_ghi_perth_mean_day_wm2"], d["wx_ghi_perth_mean_1621_wm2"] = gb["wx_ghi_perth_mean_day_wm2"].first(), agg("wx_ghi_perth_wm2", "mean")
    for c in ("pd_run", "predispatch_available_at", "out_snapshot", "outages_available_at", "capacity_year",
              "capacity_credits_available_at", "bat_last_day", "battery_available_at", "st_report", "st_version", "st_report_age_days",
              "stpasa_available_at", "wx_newest_run", "weather_available_at"):
        d[c] = gb[c].first()
    # a source's provenance only where it supplied values
    for src, (vals, av) in SOURCES.items():
        has = d[vals].notna().any(axis=1)
        d[av] = d[av].where(has)
    return d


def load_sources():
    pdr = pd.read_parquet(PIT / "predispatch.parquet")
    st = pd.read_parquet(PIT / "stpasa.parquet")
    wx = pd.read_parquet(PIT / "weather.parquet")
    return pdr, st, wx, day_battery()


def target_days() -> pd.DatetimeIndex:
    w = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["energy"])
    last = trading_date(w.index[-1:])[0]
    if w.index[-1] != last + pd.Timedelta(hours=31, minutes=30):   # last trading day incomplete
        last -= pd.Timedelta(days=1)
    return pd.date_range(FIRST_DAY, last)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    days = target_days()
    g = grid(days)
    pdr, st, wx, day_bat = load_sources()
    gaps = day_bat.index[day_bat.isna()]
    print(f"days {days[0]:%Y-%m-%d} .. {days[-1]:%Y-%m-%d} ({len(days)}); battery days without SCADA: {len(gaps)}")
    x = inputs_30min(g, pdr, st, wx, day_bat)
    per, lab = labels(g, days)
    x = pd.concat([x, per], axis=1)
    d = to_days(x)
    d = d.join(lab).join(high_risk(days))
    d.index.name = "day"
    d.insert(1, "period", period_of(d.index))
    d["inputs_complete"] = d[INPUT_DAY].notna().all(axis=1)
    d["label_complete"] = d["margin_min_1621_mw"].notna()
    label_cols = ["margin_min_1621_mw", "margin_min_time", "residual_at_min_mw", "outage_at_min_mw", "battery_at_min_mw",
                  "thermal_cap_at_min_mw", "n_valid_1621", "spike", "rtp_max", "rtp_max_1621", "lrc", "lrc_first_issued", "ircr_peak"]
    prov = [c for c in d.columns if c not in INPUT_DAY + label_cols + ["issue_at", "period"]]
    d = d[["issue_at", "period"] + label_cols + INPUT_DAY + prov]
    d.reset_index().to_parquet(OUT / "inputs_d1.parquet", index=False)

    x["slot"] = x["target_time"].dt.strftime("%H:%M")
    x["period"] = period_of(x["day"])
    day_cols = ["wx_temp_perth_max_day_c", "wx_ghi_perth_mean_day_wm2"]
    x = x.join(d[["margin_min_1621_mw", "spike", "lrc", "ircr_peak", "high_risk_d1"]], on="day")
    first = ["day", "issue_at", "period", "target_time", "slot"]
    x = x[first + [c for c in x.columns if c not in first]]
    for c in ("weather_available_at", "outages_available_at", "predispatch_available_at", "stpasa_available_at"):
        x[c] = pd.to_datetime(x[c])
    x.to_parquet(OUT / "inputs_d1_30min.parquet", index=False)

    adv = lrc_advisories()
    adv.to_csv(OUT / "lrc_advisories.csv", index=False)
    lrc_days(adv).to_csv(OUT / "lrc_days.csv")

    cov = []
    for p, (a, b) in PERIODS.items():
        s = d[(d.index >= a) & (d.index <= b)]
        row = {"period": p, "start": a, "end": b, "days": len(s)}
        for src, (vals, _) in SOURCES.items():
            row[f"with_{src}"] = int(s[vals].notna().all(axis=1).sum())
        row["inputs_complete"] = int(s["inputs_complete"].sum())
        row["label_complete"] = int(s["label_complete"].sum())
        row["inputs_and_label"] = int((s["inputs_complete"] & s["label_complete"]).sum())
        row["spike_known"] = int(s["spike"].notna().sum())
        row["lrc_days"] = int(s["lrc"].sum())
        cov.append(row)
    cov = pd.DataFrame(cov)
    cov.to_csv(OUT / "coverage.csv", index=False)
    pd.set_option("display.width", 250)
    print(cov.to_string(index=False))
    print(f"written: {len(d)} days, {len(x)} intervals; day-input columns {len(INPUT_DAY)}; {day_cols} repeated per interval")


if __name__ == "__main__":
    main()
