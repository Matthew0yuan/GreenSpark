"""Leak audit of out/inputs_d1*.parquet (PLAN.md section 4). Public times are re-derived from raw metadata using only
the provenance columns of the table (which run, snapshot, report, weather run, last battery day, Capacity Year);
the builder's own *_available_at columns are only compared against them (A2).

A1 per source: rows with a value whose re-derived public time is after issue_at (must be 0)
A2 builder's *_available_at vs re-derived (must agree)
A3 brute-force recomputation from the raw pit tables / snapshots on random days (must agree)
A4 cross-check against data/processed/spike_table.parquet (days_ahead 1)
A5 scrambled future: perturb everything public after the issue, rebuild, inputs must not move
A6 no input column equals an ex-post label column
P1-P7 positive controls: the same checks on deliberately leaky variants must fail
C1 (not a leak check) the label agrees with step 1 where step 1's daily minimum fell in 16:00-20:30

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_inputs/leak_audit.py
Writes out/leak_audit.csv and prints it.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_inputs as bi  # noqa: E402
from common import fuel_of  # noqa: E402
from vpp.data import PROCESSED, RAW  # noqa: E402
from vpp.spike.outages import ACTIVE, OutageFeatures, capacity_credits, capacity_year, load_snapshot  # noqa: E402

SEED = 20261026
N_BRUTE, N_BRUTE_OUT, N_SCRAMBLE = 100, 20, 40
TOL = 1e-6
ROWS: list[dict] = []


def add(check: str, source: str, **kw) -> None:
    ROWS.append({"check": check, "source": source, **kw})


# ---------------------------------------------------------------- re-derived public times (raw metadata only)

def raw_predispatch_issued() -> pd.Series:
    """run id -> its issue ID time, from the raw per-day CSVs (not the pit table)."""
    parts = [pd.read_csv(f, usecols=["run", "issued"], dtype=str).drop_duplicates() for f in sorted((RAW / "predispatch-0800").glob("*.csv"))]
    r = pd.concat(parts).drop_duplicates("run").set_index("run")["issued"]
    return pd.to_datetime(r, format="%Y%m%d%H%M%S")


def rederive(d: pd.DataFrame) -> pd.DataFrame:
    r = pd.DataFrame(index=d.index)
    if "pd_run" in d:
        r["predispatch"] = d["pd_run"].map(raw_predispatch_issued()) + bi.LAGS["predispatch"]
    if "out_snapshot" in d:
        lst = pd.read_csv(RAW / "realtime-outages" / "listing.csv", parse_dates=["listed_at"]).drop_duplicates("name").set_index("name")
        r["outages"] = d["out_snapshot"].map(lst["listed_at"])
    if "capacity_year" in d:
        r["capacity_credits"] = pd.to_datetime([f"{int(y) - 1}-10-01" if pd.notna(y) else None for y in d["capacity_year"]])
    if "bat_last_day" in d:
        r["battery"] = pd.to_datetime(d["bat_last_day"]) + pd.Timedelta(days=1, hours=8)
    if "st_report" in d:
        man = pd.read_csv(RAW / "st-pasa" / "manifest.csv", parse_dates=["listed_at", "member_newest"])
        man = man[man["name"].str.lower().str.endswith(".zip")].copy()
        man["key"] = man["sha256"].str[:12]
        man["public"] = man[["listed_at", "member_newest"]].max(axis=1)
        r["stpasa"] = d["st_version"].map(man.drop_duplicates("key").set_index("key")["public"])
    if "wx_newest_run" in d:
        init = pd.to_datetime(d["wx_newest_run"].str.extract(r"_(\d{10})Z$")[0], format="%Y%m%d%H")
        r["weather"] = init + pd.Timedelta(hours=8) + bi.LAGS["weather"]
    return r


SRC_KEY = {"predispatch": "pd_req_max_1621_mw", "outages": "out_thermal_max_1621_mw", "capacity_credits": "thermal_cap_mw",
           "battery": "bat_cap_mw", "stpasa": "st_ref_max_1621_mw", "weather": "wx_temp_perth_max_day_c"}


def a1(d: pd.DataFrame, r: pd.DataFrame, check: str, variant: str, per_period: bool = True, expect: str = "0") -> dict:
    """Rows with a value (any of the source's value columns) whose re-derived public time > issue_at."""
    out = {}
    for src in r.columns:
        vals = [c for c in bi.SOURCES[src][0] if c in d]
        has = d[vals].notna().any(axis=1)
        late = has & (r[src] > d["issue_at"])
        unknown = has & r[src].isna()
        groups = [("all", d.index == d.index)]
        if per_period:
            groups += [(p, d["period"] == p) for p in bi.PERIODS]
        for name, m in groups:
            n, nv = int(m.sum()), int((has & m).sum())
            add(check, src, variant=variant, period=name, days=n, with_value=nv,
                public_by_issue=int((has & m & ~late & ~unknown).sum()), violations=int((late & m).sum()),
                unknown_time=int((unknown & m).sum()), missing_share=round(1 - nv / n, 4) if n else np.nan, expect=expect)
        out[src] = (int(has.sum()), int(late.sum()) + int(unknown.sum()))
    return out


# ---------------------------------------------------------------- A3 brute force

def brute_predispatch(x: pd.DataFrame, pdr: pd.DataFrame, days) -> tuple[int, int]:
    rec = pdr[(pdr["variable"] == "requirement") & (pdr["facility_or_service"] == "energy")]
    n = bad = 0
    for d in days:
        issue = bi.issue_of(d)
        for t in d + bi.SLOTS:
            c = rec[(rec["target_time"] == t) & (rec["available_at"] <= issue)]
            want = c.loc[c["available_at"].idxmax(), "value"] if len(c) else np.nan
            got = x.loc[(x["day"] == d) & (x["target_time"] == t), "pd_req_mw"].iloc[0]
            n += 1
            bad += not ((np.isnan(want) and np.isnan(got)) or abs(want - got) <= TOL)
    return n, bad


def brute_stpasa(x: pd.DataFrame, st: pd.DataFrame, days) -> tuple[int, int]:
    rec = st[(st["variable"] == "demand_forecast") & (st["scenario"] == "Reference")]
    n = bad = 0
    for d in days:
        issue = bi.issue_of(d)
        for t in d + bi.SLOTS:
            c = rec[(rec["target_time"] == t) & (rec["available_at"] <= issue)]
            if len(c):
                c = c[c["run_at"] == c["run_at"].max()].sort_values("available_at")
                want = c["value"].iloc[0]
            else:
                want = np.nan
            got = x.loc[(x["day"] == d) & (x["target_time"] == t), "st_ref_mw"].iloc[0]
            n += 1
            bad += not ((np.isnan(want) and np.isnan(got)) or abs(want - got) <= TOL)
    return n, bad


def brute_weather(x: pd.DataFrame, wx: pd.DataFrame, days, loc: str, var: str, col: str) -> tuple[int, int]:
    rec = wx[(wx["facility_or_service"] == loc) & (wx["variable"] == var)]
    n = bad = 0
    for d in days:
        issue = bi.issue_of(d)
        for h in range(16, 21):
            t = d + pd.Timedelta(hours=h)
            c = rec[(rec["target_time"] == t) & (rec["available_at"] <= issue)]
            want = c.loc[c["run_at"].idxmax(), "value"] if len(c) else np.nan
            got = x.loc[(x["day"] == d) & (x["target_time"] == t), col].iloc[0]
            n += 1
            bad += not ((np.isnan(want) and np.isnan(got)) or abs(want - got) <= TOL)
    return n, bad


def brute_outages(x: pd.DataFrame, days) -> tuple[int, int]:
    """Thermal loss by a plain loop over facilities and intervals from the D-1 snapshot."""
    of, cc = OutageFeatures(), capacity_credits()
    n = bad = 0
    for d in days:
        snap = load_snapshot(d - pd.Timedelta(days=1))
        cls = of.classes(snap)
        rec = snap[cls.eq("Scheduled Facility").to_numpy() & snap["Status"].isin(ACTIVE).to_numpy()]
        rec = rec[rec["Facility"].map(fuel_of).isin(["coal", "gas_base", "peaker"])]
        for t in d + bi.SLOTS:
            y = int(capacity_year(pd.DatetimeIndex([t]))[0])
            total = 0.0
            for fac, r in rec.groupby("Facility"):
                ov = r[(r["CommencementInterval"] < t + pd.Timedelta(minutes=30)) & (r["EndInterval"] + pd.Timedelta(minutes=5) > t)]
                if ov.empty:
                    continue
                credit = cc.at[fac, y] if fac in cc.index and y in cc.columns and pd.notna(cc.at[fac, y]) else 0.0
                total += max(0.0, credit - ov["rac"].fillna(0.0).min())
            got = x.loc[(x["day"] == d) & (x["target_time"] == t), "out_thermal_mw"].iloc[0]
            n += 1
            bad += abs(total - got) > 1e-6
    return n, bad


# ---------------------------------------------------------------- A5 scrambled future

INPUT_30 = ["pd_req_mw", "pd_inservice_mw", "pd_avail_mw", "out_thermal_mw", "thermal_cap_mw", "bat_cap_mw", "st_ref_mw",
            "st_high_mw", "st_low_mw", "wx_temp_perth_c", "wx_ghi_perth_wm2", "wx_wind_midwest_kmh", "wx_wind_merredin_kmh",
            "wx_temp_perth_max_day_c", "wx_ghi_perth_mean_day_wm2"]


def scramble(days, pdr, st, wx, day_bat, variant: dict | None = None) -> tuple[int, int]:
    """(values compared, values changed) when everything public after each day's issue is perturbed."""
    n = changed = 0
    for d in days:
        issue = bi.issue_of(d)
        lo, hi = d - pd.Timedelta(days=12), d + pd.Timedelta(days=3)
        p0, s0, w0 = (f[(f["target_time"] >= lo) & (f["target_time"] < hi)] for f in (pdr, st, wx))
        p1, s1, w1 = (f.assign(value=f["value"].where(f["available_at"] <= issue, f["value"] + 9999.0)) for f in (p0, s0, w0))
        b1 = day_bat.copy()
        b1[b1.index >= d - pd.Timedelta(days=1)] *= 10.0

        def loader(date, iss, _issue=issue):
            snap = load_snapshot(date)
            if snap is not None and len(snap) and snap["listed_at"].iloc[0] > _issue:
                snap = snap.assign(rac=0.0)          # a future snapshot would show every unit fully out
            return snap

        g = bi.grid(pd.DatetimeIndex([d]))
        base = bi.inputs_30min(g, p0, s0, w0, day_bat, variant)
        pert = bi.inputs_30min(g, p1, s1, w1, b1, {**(variant or {}), "loader": loader})
        a, b = base[INPUT_30].to_numpy(float), pert[INPUT_30].to_numpy(float)
        same = (np.isnan(a) & np.isnan(b)) | (np.abs(a - b) <= TOL)
        n += a.size
        changed += int((~same).sum())
    return n, changed


# ---------------------------------------------------------------- main

def main() -> None:
    d = pd.read_parquet(bi.OUT / "inputs_d1.parquet").set_index("day")
    x = pd.read_parquet(bi.OUT / "inputs_d1_30min.parquet")
    pdr, st, wx, day_bat = bi.load_sources()
    days = pd.DatetimeIndex(d.index)
    g = bi.grid(days)
    rng = np.random.default_rng(SEED)

    # A1, A2 on the delivered table
    r = rederive(d)
    a1(d, r, "A1", "delivered")
    for src in r.columns:
        av = bi.SOURCES[src][1]
        has = d[bi.SOURCES[src][0]].notna().any(axis=1)
        mism = has & ~((pd.to_datetime(d[av]) == r[src]) | (d[av].isna() & r[src].isna()))
        add("A2", src, variant="delivered", period="all", with_value=int(has.sum()), mismatches=int(mism.sum()), expect="0")
    hr = d["high_risk_d1"].notna()
    add("A1", "high_risk_d1 (comparison only)", variant="delivered", period="all", with_value=int(hr.sum()),
        violations=int((hr & (d["high_risk_available_at"] > d["issue_at"])).sum()), expect="0")

    # A3 brute force on random days with the source present
    def sample(col, k):
        pool = d.index[d[col].notna()]
        return pd.DatetimeIndex(rng.choice(pool, size=min(k, len(pool)), replace=False)).sort_values()
    for name, fn in (("predispatch", lambda s: brute_predispatch(x, pdr, s)), ("stpasa", lambda s: brute_stpasa(x, st, s)),
                     ("weather: Perth temperature", lambda s: brute_weather(x, wx, s, "perth", "temperature_2m", "wx_temp_perth_c")),
                     ("weather: Midwest wind", lambda s: brute_weather(x, wx, s, "midwest", "wind_speed_100m", "wx_wind_midwest_kmh"))):
        key = {"predispatch": "pd_req_max_1621_mw", "stpasa": "st_ref_max_1621_mw"}.get(name, "wx_wind_midwest_1621_kmh")
        n, bad = fn(sample(key, N_BRUTE))
        add("A3", name, variant="delivered", period=f"{N_BRUTE} random days", compared=n, mismatches=bad, expect="0")
    n, bad = brute_outages(x, sample("out_thermal_max_1621_mw", N_BRUTE_OUT))
    add("A3", "outages", variant="delivered", period=f"{N_BRUTE_OUT} random days", compared=n, mismatches=bad, expect="0")

    # A4 against spike_table (days_ahead 1, issued at D-1 09:00, targets in the window)
    stb = pd.read_parquet(PROCESSED / "spike_table.parquet",
                          columns=["issue_date", "target_time", "days_ahead", "pd_req", "src_out_snapshot", "wx_temp", "st_ref",
                                   "src_st_report", "out_loss_mw"])
    stb = stb[stb["days_ahead"] == 1].assign(day=lambda t: t["issue_date"] + pd.Timedelta(days=1))
    j = x.merge(stb, on=["day", "target_time"], how="inner")
    j = j[(j["target_time"] - j["day"]).isin(bi.SLOTS)]
    both = j["pd_req"].notna() & j["pd_req_mw"].notna()
    add("A4", "predispatch", variant="vs spike_table", period="overlap", compared=int(both.sum()),
        mismatches=int((both & ((j["pd_req"] - j["pd_req_mw"]).abs() > TOL)).sum())
        + int((j["pd_req"].isna() != j["pd_req_mw"].isna()).sum()), expect="0")
    hs = j["out_snapshot"].notna() | j["src_out_snapshot"].notna()
    add("A4", "outages: snapshot file", variant="vs spike_table", period="overlap", compared=int(hs.sum()),
        mismatches=int((hs & (j["out_snapshot"] != j["src_out_snapshot"])).sum()), expect="0")
    ho = j["out_thermal_mw"].notna() & j["out_loss_mw"].notna()
    add("A4", "outages: thermal <= all scheduled", variant="vs spike_table", period="overlap", compared=int(ho.sum()),
        mismatches=int((ho & (j["out_thermal_mw"] > j["out_loss_mw"] + TOL)).sum()), expect="0")
    hw = j["wx_temp"].notna() | j["wx_temp_perth_c"].notna()
    add("A4", "weather: Perth temperature", variant="vs spike_table", period="overlap", compared=int(hw.sum()),
        mismatches=int((hw & ~((j["wx_temp"] - j["wx_temp_perth_c"]).abs() <= TOL)).sum()), expect="0")
    hst = j["st_ref"].notna() | j["st_ref_mw"].notna()
    diff = hst & ~((j["st_ref"] - j["st_ref_mw"]).abs() <= TOL)
    other_report = diff & (j["src_st_report"] != j["st_report"])
    add("A4", "stpasa: Reference", variant="vs spike_table", period="overlap", compared=int(hst.sum()), mismatches=int(diff.sum()),
        note=f"{int(other_report.sum())} of them use a different report; days {sorted(set(j.loc[diff, 'day'].dt.strftime('%Y-%m-%d')))[:12]}",
        expect="report only")

    # A5 scrambled future, and P7 (the same on the shift-1 battery)
    full = d.index[d["inputs_complete"]]
    sdays = pd.DatetimeIndex(rng.choice(full, size=min(N_SCRAMBLE, len(full)), replace=False)).sort_values()
    n, ch = scramble(sdays, pdr, st, wx, day_bat)
    add("A5", "all inputs", variant="delivered", period=f"{len(sdays)} random days", compared=n, changed=ch, expect="0")
    n, ch = scramble(sdays, pdr, st, wx, day_bat, {"bat_lag": 1})
    add("P7", "battery shift(1)", variant="leak control", period=f"{len(sdays)} random days", compared=n, changed=ch, expect="> 0")

    # A6 no input column equals an ex-post label column
    labels = ["margin_min_1621_mw", "residual_at_min_mw", "outage_at_min_mw", "battery_at_min_mw", "rtp_max", "rtp_max_1621",
              "spike", "lrc", "ircr_peak"]
    eq = []
    for a in bi.INPUT_DAY:
        for b in labels + ["thermal_cap_at_min_mw"]:
            m = d[a].notna() & d[b].notna()
            if m.sum() >= 10 and (d.loc[m, a].astype(float) - d.loc[m, b].astype(float)).abs().max() <= TOL:
                eq.append(f"{a}={b}")
    expost = [e for e in eq if not e.endswith("thermal_cap_at_min_mw")]
    add("A6", "inputs vs ex-post labels", variant="delivered", period="all", pairs_equal=len(expost),
        note=f"ex-ante by definition, listed only: {[e for e in eq if e.endswith('thermal_cap_at_min_mw')]}", expect="0")

    # positive controls P1-P5 (A1 on leaky variants), P6 (LRC as an input)
    controls = {
        "P1": ("outages", "snapshot of D itself (08:30 on D)", bi.outage_inputs(g, snap_lag=0)),
        "P2": ("battery", "battery shift(1)", bi.battery_inputs(g, day_bat, lag=1)),
        "P3": ("predispatch", "D's own 08:00 run", bi.predispatch_inputs(g, pdr, run_lag=0, enforce=False)),
        "P4": ("weather", "fixed previous_day1 vintage", bi.weather_inputs(g, wx, rule="previous_day1")),
        "P5": ("stpasa", "report dated D-1", bi.stpasa_inputs(g, st, rule="by_date")),
    }
    expect = {"P1": ">= 99%", "P2": ">= 99%", "P3": "> 0", "P4": "> 0", "P5": "> 0"}
    for pc, (src, label, part) in controls.items():
        xv = pd.concat([g, part], axis=1)
        dv = variant_days(xv, src)
        rv = rederive(dv)[[src]]
        res = a1(dv, rv, pc, label, per_period=False, expect=expect[pc])
        ROWS[-1]["share_flagged"] = round(res[src][1] / res[src][0], 4) if res[src][0] else np.nan
    lrc = d[d["lrc"]]
    late = (lrc["lrc_first_issued"] > lrc["issue_at"])
    add("P6", "LRC advisory as input", variant="leak control", period="all LRC days", with_value=len(lrc), violations=int(late.sum()),
        share_flagged=round(late.mean(), 4) if len(lrc) else np.nan, expect="report only")

    # C1 label vs step 1
    s1 = pd.read_csv(bi.ROOT / "analysis" / "2026-10-26" / "supply_tightness" / "out" / "daily.csv", parse_dates=["day"]).set_index("day")
    k = s1.index[s1["min_time"].between("16:00", "20:30")].intersection(d.index[d["n_valid_1621"] == len(bi.SLOTS)])
    dif = (d.loc[k, "margin_min_1621_mw"] - s1.loc[k, "margin_min_mw"]).abs()
    add("C1", "label vs step 1", variant="consistency", period="days with step-1 minimum in 16:00-20:30", compared=len(k),
        mismatches=int((dif > 0.01).sum()), expect="0")

    res = pd.DataFrame(ROWS)
    res["pass"] = res.apply(verdict, axis=1)
    res.to_csv(bi.OUT / "leak_audit.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 120)
    print(res.to_string(index=False))
    judged = res[res["expect"] != "report only"]
    print(f"\nall judged checks pass: {bool(judged['pass'].all())} ({int(judged['pass'].sum())}/{len(judged)})")


def variant_days(xv: pd.DataFrame, src: str) -> pd.DataFrame:
    """Day table of one source from a (possibly leaky) per-interval frame: value columns + provenance."""
    gb = xv.groupby("day")
    d = pd.DataFrame({"issue_at": gb["issue_at"].first()})
    first_cols = {"predispatch": ["pd_run"], "outages": ["out_snapshot"], "battery": ["bat_last_day"],
                  "stpasa": ["st_report", "st_version"], "weather": ["wx_newest_run"]}[src]
    val = {"predispatch": "pd_req_mw", "outages": "out_thermal_mw", "battery": "bat_cap_mw", "stpasa": "st_ref_mw",
           "weather": "wx_temp_perth_c"}[src]
    key = bi.SOURCES[src][0][0]
    d[key] = gb[val].max()
    for c in first_cols:
        d[c] = gb[c].first()
    d["period"] = bi.period_of(d.index)
    return d


def num(row, k) -> float:
    v = row.get(k)
    return 0.0 if v is None or pd.isna(v) else float(v)


def verdict(row) -> bool:
    c = row["check"]
    if row["expect"] == "report only":
        return True
    if c == "A1":
        return num(row, "violations") == 0 and num(row, "unknown_time") == 0
    if c in ("P1", "P2"):
        return num(row, "share_flagged") >= 0.99
    if c in ("P3", "P4", "P5"):
        return num(row, "violations") > 0
    if c == "P7":
        return num(row, "changed") > 0
    if c == "A5":
        return num(row, "changed") == 0
    if c == "A6":
        return num(row, "pairs_equal") == 0
    return num(row, "mismatches") == 0


if __name__ == "__main__":
    main()
