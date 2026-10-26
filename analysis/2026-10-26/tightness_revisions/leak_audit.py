"""Leak audit of out/revisions_d1*.parquet (PLAN.md section 4). Public times are re-derived from raw metadata using only
the table's provenance columns (snapshot files, ST PASA report copies, pre-dispatch run, weather runs); the builder's own
*_available_at columns are only compared against them (A2).

A1 per source: rows with a value whose re-derived public time (current and previous version) is after issue_at
A2 builder's *_available_at vs re-derived
A3 brute-force recomputation on random days (outage revision and 24 h new loss by a loop over facilities; ST PASA
   revision; Perth daytime max temperature revision)
A4 against data/processed/spike_table.parquet (days_ahead 1)
A5 scrambled future: everything public after the issue perturbed, the new columns must not move
P1-P4 positive controls

Run from the repo root:  .venv/Scripts/python.exe analysis/2026-10-26/tightness_revisions/leak_audit.py
Writes out/leak_audit.csv and prints it.
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_revisions as br  # noqa: E402
import build_inputs as bi  # noqa: E402  (path set by build_revisions)
from common import fuel_of  # noqa: E402
from vpp.data import PROCESSED, RAW  # noqa: E402
from vpp.spike.outages import ACTIVE, OutageFeatures, capacity_credits, capacity_year, load_snapshot  # noqa: E402

SEED = 20261027
N_BRUTE, N_BRUTE_OUT, N_SCRAMBLE = 100, 20, 40
TOL = 1e-6
ROWS: list[dict] = []
REV_30 = ["out_thermal_rev_mw", "out_thermal_new24_mw", "out_thermal_forced_mw", "out_thermal_pending_mw", "out_forced24_n",
          "st_ref_rev_mw", "st_short_energy", "st_short_raise", "st_minus_pd_mw", "wx_wind_rev_kmh", "wx_temp_perth_max_day_rev_c"]


def add(check: str, source: str, **kw) -> None:
    ROWS.append({"check": check, "source": source, **kw})


# ---------------------------------------------------------------- re-derived public times (raw metadata only)

def raw_predispatch_issued() -> pd.Series:
    parts = [pd.read_csv(f, usecols=["run", "issued"], dtype=str).drop_duplicates() for f in sorted((RAW / "predispatch-0800").glob("*.csv"))]
    r = pd.concat(parts).drop_duplicates("run").set_index("run")["issued"]
    return pd.to_datetime(r, format="%Y%m%d%H%M%S")


def listing() -> pd.Series:
    lst = pd.read_csv(RAW / "realtime-outages" / "listing.csv", parse_dates=["listed_at"])
    return lst.drop_duplicates("name").set_index("name")["listed_at"]


def stpasa_public() -> pd.Series:
    man = pd.read_csv(RAW / "st-pasa" / "manifest.csv", parse_dates=["listed_at", "member_newest"])
    man = man[man["name"].str.lower().str.endswith(".zip")].copy()
    man["key"] = man["sha256"].str[:12]
    man["public"] = man[["listed_at", "member_newest"]].max(axis=1)
    return man.drop_duplicates("key").set_index("key")["public"]


def weather_public(runs: pd.Series) -> pd.Series:
    init = pd.to_datetime(runs.str.extract(r"_(\d{10})Z$")[0], format="%Y%m%d%H")
    return init + pd.Timedelta(hours=8) + bi.LAGS["weather"]          # UTC -> AWST, then the 8 h availability lag


def rederive(d: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """source -> frame of re-derived public times, one column per version it relies on."""
    lst, stp = listing(), stpasa_public()
    out = {}
    if "out_snapshot" in d:
        out["outages"] = pd.DataFrame({"current": d["out_snapshot"].map(lst), "previous": d["out_prev_snapshot"].map(lst)})
    if "st_version" in d:
        out["stpasa"] = pd.DataFrame({"current": d["st_version"].map(stp), "previous": d["st_prev_version"].map(stp)})
    if "pd_run" in d and "st_version" in d:
        out["cross"] = pd.DataFrame({"stpasa": d["st_version"].map(stp),
                                     "predispatch": d["pd_run"].map(raw_predispatch_issued()) + bi.LAGS["predispatch"]})
    if "wx_newest_run" in d:
        out["weather"] = pd.DataFrame({"current": weather_public(d["wx_newest_run"].astype("string")),
                                       "previous": weather_public(d["wx_prev_newest_run"].astype("string"))})
    return out


BUILDER_AV = {"outages": {"current": "outages_available_at", "previous": "outages_prev_available_at"},
              "stpasa": {"current": "stpasa_available_at", "previous": "stpasa_prev_available_at"},
              "cross": {"stpasa": "stpasa_available_at", "predispatch": "predispatch_available_at"},
              "weather": {"current": "weather_available_at", "previous": "weather_prev_available_at"}}


# columns that rely on a version, where not all of the source's columns do: the shortfall flags come from the
# current ST PASA report only, so a day without a previous report still has them
USES = {("stpasa", "previous"): ["st_ref_rev_max_1621_mw", "st_ref_rev_mean_1621_mw"]}


def a1(d: pd.DataFrame, r: dict, check: str, variant: str, per_period: bool = True, expect: str = "0") -> dict:
    res = {}
    for src, times in r.items():
        vals = [c for c in br.SOURCES[src][0] if c in d]
        has = d[vals].notna().any(axis=1)
        late = pd.Series(False, index=d.index)
        unknown = pd.Series(False, index=d.index)
        for ver in times.columns:
            uses = [c for c in USES.get((src, ver), vals) if c in d]
            used = d[uses].notna().any(axis=1)
            late |= used & (times[ver] > d["issue_at"])
            unknown |= used & times[ver].isna()
        groups = [("all", np.ones(len(d), bool))]
        if per_period:
            groups += [(p, (d["period"] == p).to_numpy()) for p in bi.PERIODS]
        for name, m in groups:
            n, nv = int(m.sum()), int((has & m).sum())
            add(check, src, variant=variant, period=name, days=n, with_value=nv,
                public_by_issue=int((has & m & ~late & ~unknown).sum()), violations=int((late & m).sum()),
                unknown_time=int((unknown & m).sum()), missing_share=round(1 - nv / n, 4) if n else np.nan, expect=expect)
        res[src] = (int(has.sum()), int((late | unknown).sum()))
    return res


# ---------------------------------------------------------------- A3 brute force

THERMAL_FUELS = ("coal", "gas_base", "peaker")


def brute_thermal(snap: pd.DataFrame, t: pd.Timestamp, cc: pd.DataFrame, of: OutageFeatures, issue=None) -> float:
    cls = of.classes(snap)
    rec = snap[cls.eq("Scheduled Facility").to_numpy() & snap["Status"].isin(ACTIVE).to_numpy()]
    rec = rec[rec["Facility"].map(fuel_of).isin(THERMAL_FUELS)]
    if issue is not None:
        rec = rec[rec["FirstSubmissionDate"] > issue - pd.Timedelta(hours=24)]
    y = int(capacity_year(pd.DatetimeIndex([t]))[0])
    total = 0.0
    for fac, r in rec.groupby("Facility"):
        ov = r[(r["CommencementInterval"] < t + pd.Timedelta(minutes=30)) & (r["EndInterval"] + pd.Timedelta(minutes=5) > t)]
        if ov.empty:
            continue
        credit = cc.at[fac, y] if fac in cc.index and y in cc.columns and pd.notna(cc.at[fac, y]) else 0.0
        total += max(0.0, credit - ov["rac"].fillna(0.0).min())
    return total


def brute_outages(x: pd.DataFrame, days) -> tuple[int, int]:
    of, cc = OutageFeatures(), capacity_credits()
    n = bad = 0
    for d in days:
        issue = bi.issue_of(d)
        s1, s2 = load_snapshot(d - pd.Timedelta(days=1)), load_snapshot(d - pd.Timedelta(days=2))
        for t in d + bi.SLOTS:
            row = x[(x["day"] == d) & (x["target_time"] == t)].iloc[0]
            rev = brute_thermal(s1, t, cc, of) - brute_thermal(s2, t, cc, of)
            new = brute_thermal(s1, t, cc, of, issue=issue)
            n += 2
            bad += abs(rev - row["out_thermal_rev_mw"]) > TOL
            bad += abs(new - row["out_thermal_new24_mw"]) > TOL
    return n, bad


def brute_stpasa(x: pd.DataFrame, st: pd.DataFrame, days) -> tuple[int, int]:
    ref = st[(st["variable"] == "demand_forecast") & (st["scenario"] == "Reference")]
    n = bad = 0
    for d in days:
        issue = bi.issue_of(d)
        window = list(d + bi.SLOTS)
        pub = ref[ref["available_at"] <= issue]
        cover = pub[pub["target_time"].isin(window)].groupby(["record_id", "version"])["target_time"].nunique()
        full = cover[cover == len(window)].reset_index()
        full = full.merge(pub.drop_duplicates(["record_id", "version"])[["record_id", "version", "run_at", "available_at"]],
                          on=["record_id", "version"])
        if full.empty:
            continue
        now = full[full["run_at"] == full["run_at"].max()].sort_values("available_at").iloc[0]
        older = full[full["run_at"] < now["run_at"]]
        for t in window:
            got = x.loc[(x["day"] == d) & (x["target_time"] == t), "st_ref_rev_mw"].iloc[0]
            if older.empty:
                want = np.nan
            else:
                prev = older[older["run_at"] == older["run_at"].max()].sort_values("available_at").iloc[0]
                v = lambda c: pub[(pub["record_id"] == c["record_id"]) & (pub["version"] == c["version"])
                                  & (pub["target_time"] == t)]["value"].iloc[0]
                want = v(now) - v(prev)
            n += 1
            bad += not ((np.isnan(want) and np.isnan(got)) or abs(want - got) <= TOL)
    return n, bad


def brute_temp(dd: pd.DataFrame, wx: pd.DataFrame, days) -> tuple[int, int]:
    rec = wx[(wx["facility_or_service"] == "perth") & (wx["variable"] == "temperature_2m")]
    n = bad = 0
    for d in days:
        issue = bi.issue_of(d)
        new, old = [], []
        for h in range(8, 20):
            c = rec[(rec["target_time"] == d + pd.Timedelta(hours=h)) & (rec["available_at"] <= issue)].sort_values("run_at", ascending=False)
            new.append(c["value"].iloc[0] if len(c) > 0 else np.nan)
            old.append(c["value"].iloc[1] if len(c) > 1 else np.nan)
        want = max(new) - max(old) if not (np.isnan(new).any() or np.isnan(old).any()) else np.nan
        got = dd.loc[d, "wx_temp_perth_max_day_rev_c"]
        n += 1
        bad += not ((np.isnan(want) and np.isnan(got)) or abs(want - got) <= TOL)
    return n, bad


# ---------------------------------------------------------------- A5 scrambled future

def scramble(days, pdr, st, wx, variant: dict | None = None) -> tuple[int, int]:
    n = changed = 0
    for d in days:
        issue = bi.issue_of(d)
        lo, hi = d - pd.Timedelta(days=12), d + pd.Timedelta(days=3)
        p0, s0, w0 = (f[(f["target_time"] >= lo) & (f["target_time"] < hi)] for f in (pdr, st, wx))
        p1, s1, w1 = (f.assign(value=f["value"].where(f["available_at"] <= issue, f["value"] + 9999.0)) for f in (p0, s0, w0))
        # future reports also get an energy and a raise shortfall flag in every window interval
        fut = s0[(s0["available_at"] > issue) & (s0["variable"] == "demand_forecast")].drop_duplicates(["record_id", "version"])
        fake = [fut.assign(variable="short_flag_any", facility_or_service=svc, target_time=t, value=1.0, scenario="Reference")
                for t in d + bi.SLOTS for svc in ("energy", "cont_raise")]
        s1 = pd.concat([s1] + fake, ignore_index=True) if len(fut) else s1

        def loader(date, iss, _issue=issue):
            snap = load_snapshot(date)
            if snap is not None and len(snap) and snap["listed_at"].iloc[0] > _issue:
                snap = snap.assign(rac=0.0)
            return snap

        g = bi.grid(pd.DatetimeIndex([d]))
        base = br.revisions_30min(g, p0, s0, w0, variant)
        pert = br.revisions_30min(g, p1, s1, w1, {**(variant or {}), "loader": loader})
        a, b = base[REV_30].to_numpy(float), pert[REV_30].to_numpy(float)
        same = (np.isnan(a) & np.isnan(b)) | (np.abs(a - b) <= TOL)
        n += a.size
        changed += int((~same).sum())
    return n, changed


# ---------------------------------------------------------------- main

def main() -> None:
    d = pd.read_parquet(br.OUT / "revisions_d1.parquet").set_index("day")
    x = pd.read_parquet(br.OUT / "revisions_d1_30min.parquet")
    pdr, st, wx, _ = bi.load_sources()
    days = pd.DatetimeIndex(d.index)
    g = bi.grid(days)
    rng = np.random.default_rng(SEED)

    r = rederive(d)
    a1(d, r, "A1", "delivered")
    for src, times in r.items():
        for ver, col in BUILDER_AV[src].items():
            has = d[USES.get((src, ver), br.SOURCES[src][0])].notna().any(axis=1)
            built = pd.to_datetime(d[col])
            mism = has & ~((built == times[ver]) | (built.isna() & times[ver].isna()))
            add("A2", f"{src}: {ver}", variant="delivered", period="all", with_value=int(has.sum()), mismatches=int(mism.sum()), expect="0")

    def sample(col, k):
        pool = d.index[d[col].notna()]
        return pd.DatetimeIndex(rng.choice(pool, size=min(k, len(pool)), replace=False)).sort_values()
    n, bad = brute_outages(x, sample("out_thermal_rev_max_1621_mw", N_BRUTE_OUT))
    add("A3", "outages: revision and 24 h new loss", variant="delivered", period=f"{N_BRUTE_OUT} random days", compared=n, mismatches=bad, expect="0")
    n, bad = brute_stpasa(x, st, sample("st_ref_rev_max_1621_mw", N_BRUTE))
    add("A3", "stpasa: Reference revision", variant="delivered", period=f"{N_BRUTE} random days", compared=n, mismatches=bad, expect="0")
    n, bad = brute_temp(d, wx, sample("wx_temp_perth_max_day_rev_c", N_BRUTE))
    add("A3", "weather: Perth daytime max revision", variant="delivered", period=f"{N_BRUTE} random days", compared=n, mismatches=bad, expect="0")

    stb = pd.read_parquet(PROCESSED / "spike_table.parquet", columns=["issue_date", "target_time", "days_ahead", "out_forced24_n",
                                                                       "out_new24_mw", "st_ref_rev", "st_short_energy", "src_st_report"])
    stb = stb[stb["days_ahead"] == 1].assign(day=lambda t: t["issue_date"] + pd.Timedelta(days=1))
    j = x.merge(stb, on=["day", "target_time"], how="inner", suffixes=("", "_stb"))
    both = j["out_forced24_n"].notna() & j["out_forced24_n_stb"].notna()
    add("A4", "outages: forced records in 24 h (facilities)", variant="vs spike_table", period="overlap", compared=int(both.sum()),
        mismatches=int((both & (j["out_forced24_n"] != j["out_forced24_n_stb"])).sum()), expect="0")
    hn = j["out_thermal_new24_mw"].notna() & j["out_new24_mw"].notna()
    add("A4", "outages: thermal 24 h new <= all scheduled", variant="vs spike_table", period="overlap", compared=int(hn.sum()),
        mismatches=int((hn & (j["out_thermal_new24_mw"] > j["out_new24_mw"] + TOL)).sum()), expect="0")
    hr = j["st_ref_rev_mw"].notna() | j["st_ref_rev"].notna()
    dif = hr & ~((j["st_ref_rev_mw"] - j["st_ref_rev"]).abs() <= TOL)
    add("A4", "stpasa: Reference revision", variant="vs spike_table", period="overlap", compared=int(hr.sum()), mismatches=int(dif.sum()),
        note=f"{int((dif & (j['src_st_report'] != j['st_report'])).sum())} with a different report used; "
             f"days {sorted(set(j.loc[dif, 'day'].dt.strftime('%Y-%m-%d')))[:10]}", expect="report only")
    hf = j["st_short_energy"].notna() & j["st_short_energy_stb"].notna()
    add("A4", "stpasa: energy shortfall flag", variant="vs spike_table", period="overlap", compared=int(hf.sum()),
        mismatches=int((hf & (j["st_short_energy"] != j["st_short_energy_stb"])).sum()), expect="report only")

    full = d.index[d["revisions_complete"]]
    sdays = pd.DatetimeIndex(rng.choice(full, size=min(N_SCRAMBLE, len(full)), replace=False)).sort_values()
    n, ch = scramble(sdays, pdr, st, wx)
    add("A5", "all revision columns", variant="delivered", period=f"{len(sdays)} random days", compared=n, changed=ch, expect="0")
    n, ch = scramble(sdays, pdr, st, wx, {"snap_lags": (0, 1)})
    add("P4", "A5 on the D / D-1 snapshot variant", variant="leak control", period=f"{len(sdays)} random days", compared=n, changed=ch, expect="> 0")

    controls = {
        "P1": ("outages", "snapshots of D and D-1", {"snap_lags": (0, 1)}, ">= 99%"),
        "P2": ("stpasa", "reports dated D-1 and D-2", {"st_rule": "by_date"}, "> 0"),
        "P3": ("weather", "fixed day-1 / day-2 vintages", {"wx_rule": "previous_day1"}, "> 0"),
    }
    for pc, (src, label, variant, expect) in controls.items():
        if src == "outages":
            part = br.outage_revisions(g, lags=variant["snap_lags"])
        elif src == "stpasa":
            part = br.stpasa_revisions(g, st, rule="by_date")
        else:
            part = br.weather_revisions(g, wx, rule="previous_day1")
        xv = pd.concat([g, part], axis=1)
        dv = variant_days(xv, src)
        res = a1(dv, {src: rederive(dv)[src]}, pc, label, per_period=False, expect=expect)
        ROWS[-1]["share_flagged"] = round(res[src][1] / res[src][0], 4) if res[src][0] else np.nan

    res = pd.DataFrame(ROWS)
    res["pass"] = res.apply(verdict, axis=1)
    res.to_csv(br.OUT / "leak_audit.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 110)
    print(res.to_string(index=False))
    judged = res[res["expect"] != "report only"]
    print(f"\nall judged checks pass: {bool(judged['pass'].all())} ({int(judged['pass'].sum())}/{len(judged)})")


def variant_days(xv: pd.DataFrame, src: str) -> pd.DataFrame:
    gb = xv.groupby("day")
    d = pd.DataFrame({"issue_at": gb["issue_at"].first()})
    prov = {"outages": ["out_snapshot", "out_prev_snapshot"], "stpasa": ["st_version", "st_prev_version"],
            "weather": ["wx_newest_run", "wx_prev_newest_run"]}[src]
    val = {"outages": "out_thermal_rev_mw", "stpasa": "st_ref_rev_mw", "weather": "wx_wind_rev_kmh"}[src]
    d[br.SOURCES[src][0][0]] = gb[val].max()
    for c in prov:
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
    if c == "P1":
        return num(row, "share_flagged") >= 0.99
    if c in ("P2", "P3"):
        return num(row, "violations") > 0
    if c == "P4":
        return num(row, "changed") > 0
    if c == "A5":
        return num(row, "changed") == 0
    return num(row, "mismatches") == 0


if __name__ == "__main__":
    main()
