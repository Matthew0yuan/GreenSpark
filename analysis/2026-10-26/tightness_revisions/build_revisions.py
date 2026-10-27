"""Revision features (PLAN.md section 3): for each target trading day D, what changed in the sources public at D-1
09:00 AWST, for D 16:00-21:00. Joins onto analysis/2026-10-26/tightness_inputs/out/inputs_d1.parquet by `day`.

  outages   D-1's snapshot against D-2's (both the newest listed before 09:00 that day); thermal = coal + gas
            Scheduled Facilities as the input table (build_outages.day_state); records first submitted in the 24 h
            before the issue; the forced share; planned outages awaiting assessment; facilities with a new forced record
  stpasa    the report the input table uses against the previous report public at the issue (older report day,
            covering the window, its first public copy); shortfall flags of the report used
  weather   per hour, the newest vintage public at the issue against the second newest
  cross     ST PASA Reference - pre-dispatch requirement (both as the input table takes them)

Run from the repo root:  .venv/Scripts/python.exe analysis/2026-10-26/tightness_revisions/build_revisions.py
Writes out/revisions_d1.parquet, out/revisions_d1_30min.parquet, out/coverage.csv
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INPUTS = ROOT / "analysis" / "2026-10-26" / "tightness_inputs"
for p in (ROOT, ROOT / "analysis" / "2026-10-17", ROOT / "analysis" / "2026-10-26" / "supply_tightness", INPUTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_inputs as bi  # noqa: E402  (tightness_inputs, read only)
from build_outages import day_state  # noqa: E402  (analysis/2026-10-17, read only)
from vpp.spike.outages import ACTIVE, GENERATING, PENDING, OutageFeatures, capacity_credits, load_snapshot  # noqa: E402

OUT = HERE / "out"
DAY24 = pd.Timedelta(hours=24)


# ---------------------------------------------------------------- outages

def thermal(st: pd.DataFrame, forced: bool = False) -> np.ndarray:
    if forced:
        return (st["out_forced_coal_mw"] + st["out_forced_gas_mw"]).to_numpy()
    return (st["out_coal_mw"] + st["out_gas_mw"]).to_numpy()


def snapshot_parts(snap: pd.DataFrame | None, of: OutageFeatures):
    """(scheduled records, generating records) of a snapshot, or (None, None)."""
    if snap is None or not len(snap):
        return None, None
    cls = of.classes(snap)
    return snap[cls.eq("Scheduled Facility").to_numpy()], snap[cls.isin(GENERATING).to_numpy()]


def loss(rec: pd.DataFrame, targets: pd.DatetimeIndex, cc: pd.DataFrame, forced: bool = False) -> np.ndarray:
    if rec.empty:
        return np.zeros(len(targets))
    return thermal(day_state(rec, targets, cc), forced)


def outage_revisions(g: pd.DataFrame, lags: tuple[int, int] = (1, 2), loader=None) -> pd.DataFrame:
    """Per (D, window interval). lags = (current, previous) snapshot days before D; (1, 2) is the protocol,
    (0, 1) a leak control. loader(date, issue) -> snapshot or None."""
    of, cc = OutageFeatures(), capacity_credits()
    load = loader or (lambda date, issue: load_snapshot(date))
    cols = ["out_thermal_rev_mw", "out_thermal_new24_mw", "out_thermal_forced_mw", "out_thermal_pending_mw", "out_forced24_n",
            "out_snapshot", "outages_available_at", "out_prev_snapshot", "outages_prev_available_at"]
    parts = []
    for (d, issue), gd in g.groupby(["day", "issue_at"], sort=False):
        f = pd.DataFrame(index=gd.index, columns=cols, dtype=object)
        targets = pd.DatetimeIndex(gd["target_time"])
        snap = load(d - pd.Timedelta(days=lags[0]), issue)
        prev = load(d - pd.Timedelta(days=lags[1]), issue)
        sched, gen = snapshot_parts(snap, of)
        psched, _ = snapshot_parts(prev, of)
        if sched is not None:
            active = sched[sched["Status"].isin(ACTIVE)]
            now = loss(active, targets, cc)
            f["out_thermal_new24_mw"] = loss(active[active["FirstSubmissionDate"] > issue - DAY24], targets, cc)
            f["out_thermal_forced_mw"] = loss(active, targets, cc, forced=True)
            f["out_thermal_pending_mw"] = loss(sched[sched["Status"].isin(PENDING) & (sched["OutageType"] != "Forced")], targets, cc)
            new_forced = gen[(gen["OutageType"] == "Forced") & gen["Status"].isin(ACTIVE) & (gen["FirstSubmissionDate"] > issue - DAY24)]
            f["out_forced24_n"] = new_forced["Facility"].nunique()
            f["out_snapshot"], f["outages_available_at"] = snap["snapshot_file"].iloc[0], snap["listed_at"].iloc[0]
            if psched is not None:
                f["out_thermal_rev_mw"] = now - loss(psched[psched["Status"].isin(ACTIVE)], targets, cc)
                f["out_prev_snapshot"], f["outages_prev_available_at"] = prev["snapshot_file"].iloc[0], prev["listed_at"].iloc[0]
        parts.append(f)
    out = pd.concat(parts).loc[g.index]
    for c in cols:
        if c.endswith("_mw") or c.endswith("_n"):
            out[c] = out[c].astype(float)
        elif c.endswith("_at"):
            out[c] = pd.to_datetime(out[c])
    return out


# ---------------------------------------------------------------- ST PASA

def choose_previous(pick: pd.DataFrame, copies: pd.DataFrame, rule: str = "newest_public") -> pd.DataFrame:
    """The report before the one used: older report day (run_at), public at the issue, covering the whole window,
    its first public copy. by_date (leak control): the report dated D-2, whenever it was public."""
    run_of = copies.drop_duplicates("record_id").set_index("record_id")["run_at"]
    rows = []
    for _, r in pick.iterrows():
        d, issue = r["day"], bi.issue_of(r["day"])
        if rule == "newest_public":
            if r["st_report"] is None or pd.isna(r["st_report"]):
                rows.append((d, None, None, pd.NaT))
                continue
            c = copies[(copies["run_at"] < run_of[r["st_report"]]) & (copies["available_at"] <= issue)
                       & (copies["tmin"] <= d + bi.SLOTS[0]) & (copies["tmax"] >= d + bi.SLOTS[-1])]
            if c.empty:
                rows.append((d, None, None, pd.NaT))
                continue
            c = c[c["run_at"] == c["run_at"].max()].sort_values("available_at").iloc[0]
        else:
            c = copies[copies["record_id"] == f"{d - pd.Timedelta(days=2):%Y-%m-%d}"].sort_values("available_at")
            if c.empty:
                rows.append((d, None, None, pd.NaT))
                continue
            c = c.iloc[0]
        rows.append((d, c["record_id"], c["version"], c["available_at"]))
    return pd.DataFrame(rows, columns=["day", "st_prev_report", "st_prev_version", "stpasa_prev_available_at"])


def stpasa_revisions(g: pd.DataFrame, st: pd.DataFrame, rule: str = "newest_public") -> pd.DataFrame:
    days = pd.DatetimeIndex(g["day"].drop_duplicates())
    copies = bi.stpasa_copies(st)
    pick = bi.choose_stpasa(days, copies, rule)
    prev = choose_previous(pick, copies, rule)
    ref = st[(st["variable"] == "demand_forecast") & (st["scenario"] == "Reference")]
    ref = ref.drop_duplicates(["record_id", "version", "target_time"]).set_index(["record_id", "version", "target_time"])["value"]
    x = g[["day", "target_time"]].merge(pick, on="day", how="left").merge(prev, on="day", how="left")
    now = ref.reindex(pd.MultiIndex.from_arrays([x["st_report"], x["st_version"], x["target_time"]])).to_numpy()
    before = ref.reindex(pd.MultiIndex.from_arrays([x["st_prev_report"], x["st_prev_version"], x["target_time"]])).to_numpy()
    out = pd.DataFrame(index=g.index)
    out["st_ref_rev_mw"] = now - before
    flags = st[st["variable"].str.startswith("short_flag")]
    has = x["st_report"].notna().to_numpy()
    key = x["st_report"].astype(str) + "|" + x["st_version"].astype(str) + "|" + x["target_time"].astype(str)
    fkey = flags["record_id"] + "|" + flags["version"] + "|" + flags["target_time"].astype(str)
    for col, sel in (("st_short_energy", flags["facility_or_service"] == "energy"),
                     ("st_short_raise", flags["facility_or_service"].isin(["reg_raise", "cont_raise"]))):
        out[col] = np.where(has, key.isin(set(fkey[sel])).astype(float), np.nan)
    for c in ("st_report", "st_version", "stpasa_available_at", "st_prev_report", "st_prev_version", "stpasa_prev_available_at"):
        out[c] = x[c].to_numpy()
    for c in ("stpasa_available_at", "stpasa_prev_available_at"):
        out[c] = pd.to_datetime(out[c])
    return out


# ---------------------------------------------------------------- weather

WX_REV = {"wx_temp_perth_c": ("perth", "temperature_2m"), "wx_wind_midwest_kmh": ("midwest", "wind_speed_100m"),
          "wx_wind_merredin_kmh": ("merredin", "wind_speed_100m")}


def weather_two(days: pd.DatetimeIndex, wx: pd.DataFrame, rule: str = "newest_public") -> pd.DataFrame:
    """Per (D, hour of trading day D, column): the newest and second-newest values public at the issue
    (rank 0, 1). previous_day1 (leak control): rank 0 = the fixed day-1 vintage, rank 1 = day-2."""
    hours = pd.timedelta_range("08:00:00", periods=24, freq="h")
    left = pd.DataFrame({"day": np.repeat(days.to_numpy(), 24)})
    left["hour"] = left["day"] + np.tile(hours.to_numpy(), len(days))
    left["issue_at"] = bi.issue_of(left["day"])
    parts = []
    for col, (loc, var) in WX_REV.items():
        rec = wx[(wx["facility_or_service"] == loc) & (wx["variable"] == var)][
            ["target_time", "run_at", "available_at", "record_id", "version", "value"]]
        m = left.merge(rec, left_on="hour", right_on="target_time", how="inner")
        if rule == "newest_public":
            m = m[m["available_at"] <= m["issue_at"]].sort_values("run_at", ascending=False)
            m["rank"] = m.groupby(["day", "hour"]).cumcount()
        else:
            m = m[m["version"].isin(["previous_day1", "previous_day2"])].copy()
            m["rank"] = (m["version"] == "previous_day2").astype(int)
        m = m[m["rank"] <= 1]
        parts.append(m.assign(col=col)[["day", "hour", "col", "rank", "value", "available_at", "record_id"]])
    return pd.concat(parts, ignore_index=True)


def weather_revisions(g: pd.DataFrame, wx: pd.DataFrame, rule: str = "newest_public") -> pd.DataFrame:
    days = pd.DatetimeIndex(g["day"].drop_duplicates())
    h = weather_two(days, wx, rule)
    t = pd.DatetimeIndex(g["target_time"])
    h0 = t.floor("h")
    on_hour = (t == h0)
    key0 = pd.MultiIndex.from_arrays([g["day"], h0])
    key1 = pd.MultiIndex.from_arrays([g["day"], h0 + pd.Timedelta(hours=1)])
    out = pd.DataFrame(index=g.index)
    day = pd.DataFrame(index=days)
    for rank, tag in ((0, "new"), (1, "old")):
        val = h[h["rank"] == rank].pivot_table(index=["day", "hour"], columns="col", values="value", aggfunc="first")
        for col in ("wx_wind_midwest_kmh", "wx_wind_merredin_kmh"):
            v0 = val[col].reindex(key0).to_numpy() if col in val else np.full(len(g), np.nan)
            v1 = val[col].reindex(key1).to_numpy() if col in val else np.full(len(g), np.nan)
            out[f"{col}_{tag}"] = np.where(on_hour, v0, (v0 + v1) / 2)
        # daytime max (08:00-19:00, 12 hours): the archive keeps whole-day leads only, so 02:00-07:00 of D has no
        # second-newest vintage at D-1 09:00 (PLAN change log)
        temp = val["wx_temp_perth_c"] if "wx_temp_perth_c" in val else pd.Series(dtype=float)
        hr = temp.index.get_level_values(1) - temp.index.get_level_values(0)
        temp = temp[(hr >= pd.Timedelta(hours=8)) & (hr < pd.Timedelta(hours=20))]
        n = temp.groupby(level=0).count()
        day[f"tmax_{tag}"] = temp.groupby(level=0).max().where(n == 12)
    out["wx_wind_rev_kmh"] = ((out["wx_wind_midwest_kmh_new"] + out["wx_wind_merredin_kmh_new"])
                              - (out["wx_wind_midwest_kmh_old"] + out["wx_wind_merredin_kmh_old"])) / 2
    day["wx_temp_perth_max_day_rev_c"] = day["tmax_new"] - day["tmax_old"]
    for rank, run_col, av_col in ((0, "wx_newest_run", "weather_available_at"), (1, "wx_prev_newest_run", "weather_prev_available_at")):
        hr = h[h["rank"] == rank]
        latest = hr.loc[hr.groupby("day")["available_at"].idxmax()].set_index("day")
        day[run_col], day[av_col] = latest["record_id"], latest["available_at"]
    keep = ["wx_wind_rev_kmh"]
    return out[keep].join(day[["wx_temp_perth_max_day_rev_c", "wx_newest_run", "weather_available_at", "wx_prev_newest_run",
                               "weather_prev_available_at"]].reindex(g["day"]).set_index(g.index))


# ---------------------------------------------------------------- cross-source

def cross_inputs(g: pd.DataFrame, pdr: pd.DataFrame, st: pd.DataFrame) -> pd.DataFrame:
    p = bi.predispatch_inputs(g, pdr)
    s = bi.stpasa_inputs(g, st)
    out = pd.DataFrame(index=g.index)
    out["st_minus_pd_mw"] = s["st_ref_mw"] - p["pd_req_mw"]
    out["pd_run"], out["predispatch_available_at"] = p["pd_run"], p["predispatch_available_at"]
    return out


# ---------------------------------------------------------------- assemble

REV_DAY = ["out_thermal_rev_max_1621_mw", "out_thermal_rev_mean_1621_mw", "out_thermal_new24_max_1621_mw",
           "out_thermal_forced_max_1621_mw", "out_thermal_pending_max_1621_mw", "out_forced24_n",
           "st_ref_rev_max_1621_mw", "st_ref_rev_mean_1621_mw", "st_short_energy_1621_n", "st_short_raise_1621_n",
           "st_minus_pd_max_1621_mw", "wx_temp_perth_max_day_rev_c", "wx_wind_1621_rev_kmh"]
SOURCES = {  # source -> (value columns, [available_at columns that must all be <= issue_at])
    "outages": (REV_DAY[0:6], ["outages_available_at", "outages_prev_available_at"]),
    "stpasa": (REV_DAY[6:10], ["stpasa_available_at", "stpasa_prev_available_at"]),
    "cross": (REV_DAY[10:11], ["stpasa_available_at", "predispatch_available_at"]),
    "weather": (REV_DAY[11:13], ["weather_available_at", "weather_prev_available_at"]),
}
PROV = ["out_snapshot", "outages_available_at", "out_prev_snapshot", "outages_prev_available_at", "st_report", "st_version",
        "stpasa_available_at", "st_prev_report", "st_prev_version", "stpasa_prev_available_at", "pd_run",
        "predispatch_available_at", "wx_newest_run", "weather_available_at", "wx_prev_newest_run", "weather_prev_available_at"]


def revisions_30min(g: pd.DataFrame, pdr, st, wx, variant: dict | None = None) -> pd.DataFrame:
    v = variant or {}
    return pd.concat([g,
                      outage_revisions(g, lags=v.get("snap_lags", (1, 2)), loader=v.get("loader")),
                      stpasa_revisions(g, st, rule=v.get("st_rule", "newest_public")),
                      cross_inputs(g, pdr, st),
                      weather_revisions(g, wx, rule=v.get("wx_rule", "newest_public"))], axis=1)


def to_days(x: pd.DataFrame) -> pd.DataFrame:
    gb = x.groupby("day")
    n = len(bi.SLOTS)

    def agg(col, how):
        s = getattr(gb[col], how)()
        return s.where(gb[col].count() == n)

    d = pd.DataFrame({"issue_at": gb["issue_at"].first()})
    d["out_thermal_rev_max_1621_mw"], d["out_thermal_rev_mean_1621_mw"] = agg("out_thermal_rev_mw", "max"), agg("out_thermal_rev_mw", "mean")
    d["out_thermal_new24_max_1621_mw"] = agg("out_thermal_new24_mw", "max")
    d["out_thermal_forced_max_1621_mw"] = agg("out_thermal_forced_mw", "max")
    d["out_thermal_pending_max_1621_mw"] = agg("out_thermal_pending_mw", "max")
    d["out_forced24_n"] = gb["out_forced24_n"].first()
    d["st_ref_rev_max_1621_mw"], d["st_ref_rev_mean_1621_mw"] = agg("st_ref_rev_mw", "max"), agg("st_ref_rev_mw", "mean")
    d["st_short_energy_1621_n"], d["st_short_raise_1621_n"] = agg("st_short_energy", "sum"), agg("st_short_raise", "sum")
    d["st_minus_pd_max_1621_mw"] = agg("st_minus_pd_mw", "max")
    d["wx_temp_perth_max_day_rev_c"] = gb["wx_temp_perth_max_day_rev_c"].first()
    d["wx_wind_1621_rev_kmh"] = agg("wx_wind_rev_kmh", "mean")
    for c in PROV:
        d[c] = gb[c].first()
    for src, (vals, avs) in SOURCES.items():
        has = d[vals].notna().any(axis=1)
        for av in avs:
            if src != "cross":
                d[av] = d[av].where(has)
    return d


def main() -> None:
    OUT.mkdir(exist_ok=True)
    days = bi.target_days()
    g = bi.grid(days)
    pdr, st, wx, _ = bi.load_sources()
    print(f"days {days[0]:%Y-%m-%d} .. {days[-1]:%Y-%m-%d} ({len(days)})")
    x = revisions_30min(g, pdr, st, wx)
    d = to_days(x)
    d.index.name = "day"
    d.insert(1, "period", bi.period_of(d.index))
    d["revisions_complete"] = d[REV_DAY].notna().all(axis=1)
    d = d[["issue_at", "period"] + REV_DAY + ["revisions_complete"] + PROV]
    d.reset_index().to_parquet(OUT / "revisions_d1.parquet", index=False)
    x["period"] = bi.period_of(x["day"])
    x.to_parquet(OUT / "revisions_d1_30min.parquet", index=False)

    cov = []
    for p, (a, b) in bi.PERIODS.items():
        s = d[(d.index >= a) & (d.index <= b)]
        row = {"period": p, "start": a, "end": b, "days": len(s)}
        for src, (vals, _) in SOURCES.items():
            row[f"with_{src}"] = int(s[vals].notna().all(axis=1).sum())
        row["revisions_complete"] = int(s["revisions_complete"].sum())
        cov.append(row)
    cov = pd.DataFrame(cov)
    cov.to_csv(OUT / "coverage.csv", index=False)
    pd.set_option("display.width", 250)
    print(cov.to_string(index=False))
    print(d[REV_DAY].describe().T.round(2).to_string())


if __name__ == "__main__":
    main()
