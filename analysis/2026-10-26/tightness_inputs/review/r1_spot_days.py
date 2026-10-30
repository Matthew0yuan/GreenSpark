"""R-A review I1/I2/I4: re-derive the inputs of random target days straight from the raw sources, written from the
PLAN text (does not import build_inputs or leak_audit). Compares against out/inputs_d1.parquet (read only).

Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
Writes review/r1_spot_days.csv
"""
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path("/Users/matthew.yuan/Desktop/HGS")
TASK = ROOT / "analysis" / "2026-10-26" / "tightness_inputs"
REV = TASK / "review"
RAW = ROOT / "data" / "raw"
PIT = ROOT / "data" / "pit"
SLOTS = pd.timedelta_range("16:00:00", "20:30:00", freq="30min")
TOL = 1e-6

# fuel groups copied by value from analysis/2026-10-17/common.py (step-1 definition)
COAL = {"BW1_BLUEWATERS_G2", "BW2_BLUEWATERS_G1", "COLLIE_G1", "MUJA_G5", "MUJA_G6", "MUJA_G7", "MUJA_G8"}
GAS_BASE = {"COCKBURN_CCG1", "NEWGEN_KWINANA_CCG1", "ALINTA_PNJ_U1", "ALINTA_PNJ_U2", "TIWEST_COG1", "ALCOA_WGP",
            "PPP_KCP_EG1", "SWCJV_WORSLEY_COGEN_COG1"}
OTHER = {"BIOGAS01", "HENDERSON_RENEWABLE_IG1", "RED_HILL", "ROCKINGHAM", "SOUTH_CARDUP", "TAMALA_PARK",
         "PRDSO_WALPOLE_HG1", "PHOENIX_KWINANA_WTE_G1", "KWINANA_WTE_G1", "EAST_ROCKINGHAM_WTE_G1"}


def fuel(code: str) -> str:
    c = code.upper()
    if c in COAL:
        return "coal"
    if "_ESR" in c or "_BESS" in c:
        return "battery"
    if "_WF" in c or c.endswith("WWF"):
        return "wind"
    if "_PV" in c or "SOLAR" in c:
        return "solar"
    if c in GAS_BASE:
        return "gas_base"
    if c in OTHER or c.endswith("_IL") or "DSP" in c:
        return "other"
    return "peaker"


def cap_year(t: pd.Timestamp) -> int:
    return t.year if t.month >= 10 else t.year - 1


def credits() -> pd.DataFrame:
    f = sorted((RAW / "facilities").glob("capacity-credits-since-market-start-up-to-capacity-year-*.csv"))[-1]
    cc = pd.read_csv(f).set_index("Facility")
    yrs = [c for c in cc.columns if len(c) == 7 and c[4] == "-"]
    cc = cc[yrs].apply(pd.to_numeric, errors="coerce")
    cc.columns = [int(c[:4]) for c in yrs]
    return cc


def main() -> None:
    d = pd.read_parquet(TASK / "out" / "inputs_d1.parquet").set_index("day")
    rng = np.random.default_rng(424242)
    pick = []
    for per, k in (("history", 2), ("in_sample", 4), ("test_seen", 2), ("test_unseen", 1)):
        pool = d.index[(d["period"] == per) & d["inputs_complete"]] if per != "test_unseen" else d.index[d["period"] == per]
        pick += list(rng.choice(pool, size=k, replace=False))
    pick += [pd.Timestamp("2024-06-12"), pd.Timestamp("2025-07-15")]       # an LRC day and a winter day, fixed
    days = pd.DatetimeIndex(sorted(set(pd.DatetimeIndex(pick))))

    pdr = pd.read_parquet(PIT / "predispatch.parquet")
    st = pd.read_parquet(PIT / "stpasa.parquet")
    wx = pd.read_parquet(PIT / "weather.parquet")
    lst = pd.read_csv(RAW / "realtime-outages" / "listing.csv", parse_dates=["listed_at", "label"])
    lst = lst[lst["name"].str.startswith("GeneratorOutages_")]
    man = pd.read_csv(RAW / "st-pasa" / "manifest.csv", parse_dates=["listed_at", "member_newest"])
    man = man[man["name"].str.lower().str.endswith(".zip")]
    fac = pd.read_csv(RAW / "facilities" / "facilities.csv").set_index("Facility Code")["Facility Class"]
    cc = credits()
    thermal = cc[[fuel(f) in ("coal", "gas_base", "peaker", "other") for f in cc.index]].sum()
    sc = pd.read_parquet(ROOT / "analysis" / "2026-10-17" / "out" / "scada_5min.parquet", columns=["battery_dis_mw"])
    s30 = sc["battery_dis_mw"].resample("30min").mean()
    dmax = s30.groupby((s30.index - pd.Timedelta(hours=8)).normalize()).max()
    stb = pd.read_parquet(ROOT / "data" / "processed" / "spike_table.parquet",
                          columns=["issue_date", "target_time", "days_ahead", "pd_req", "src_pd_run", "st_ref", "src_st_report",
                                   "wx_temp", "src_out_snapshot", "out_loss_mw"])
    stb = stb[stb["days_ahead"] == 1]

    rows = []

    def rec(day, item, mine, theirs, note=""):
        if isinstance(mine, (float, int, np.floating, np.integer)) and isinstance(theirs, (float, int, np.floating, np.integer)):
            ok = (np.isnan(mine) and np.isnan(theirs)) or abs(float(mine) - float(theirs)) <= TOL
        else:
            ok = (pd.isna(mine) and pd.isna(theirs)) or str(mine) == str(theirs)
        rows.append({"day": day.date(), "item": item, "mine": mine, "table": theirs, "match": bool(ok), "note": note})

    for D in days:
        issue = D - pd.Timedelta(days=1) + pd.Timedelta(hours=9)
        T = D + SLOTS
        row = d.loc[D]

        # ---- predispatch: raw per-day CSV of D-1
        f = RAW / "predispatch-0800" / f"{D - pd.Timedelta(days=1):%Y%m%d}.csv"
        if f.exists():
            p = pd.read_csv(f, dtype={"run": str, "issued": str})
            p["t"] = pd.to_datetime(p["interval"], utc=True).dt.tz_convert("Australia/Perth").dt.tz_localize(None)
            issued = pd.to_datetime(p["issued"].iloc[0], format="%Y%m%d%H%M%S")
            pub = issued + pd.Timedelta(minutes=20)
            w = p.set_index("t").reindex(T)
            ok = pub <= issue
            rec(D, "pd: run id", p["run"].iloc[0] if ok else None, row["pd_run"], f"issued {issued}, public {pub}, issue {issue}")
            for col, how, mine_col in (("pd_req_max_1621_mw", "max", "req_energy_mw"), ("pd_req_mean_1621_mw", "mean", "req_energy_mw"),
                                       ("pd_inservice_min_1621_mw", "min", "in_service_injection_mw"),
                                       ("pd_avail_mean_1621_mw", "mean", "available_injection_mw")):
                v = getattr(w[mine_col], how)() if ok and w[mine_col].notna().all() else np.nan
                rec(D, col, float(v), float(row[col]))
            # pit table: is there any other run that covers D and is public at the issue?
            cand = pdr[(pdr["target_time"].isin(T)) & (pdr["available_at"] <= issue) & (pdr["variable"] == "requirement")]
            rec(D, "pd: public runs covering D", ",".join(sorted(cand["record_id"].unique())), row["pd_run"] if ok else "",
                "if another public run covers D the table should use the newest one")
        else:
            rec(D, "pd: raw file", "missing", row["pd_run"])

        # ---- outages: listing -> newest GeneratorOutages listed before the issue
        L = lst[lst["listed_at"] <= issue].sort_values("listed_at")
        mine_snap = L["name"].iloc[-1] if len(L) else None
        ext = RAW / "realtime-outages" / "extracts" / f"{D - pd.Timedelta(days=1):%Y%m%d}.parquet"
        if ext.exists():
            snap = pd.read_parquet(ext)
            rec(D, "outage: newest snapshot listed <= issue (listing.csv)", mine_snap, row["out_snapshot"],
                f"listed {L['listed_at'].iloc[-1] if len(L) else None}; extract file holds {snap['snapshot_file'].iloc[0]}")
            rac = pd.to_numeric(snap["FacilityRAC"], errors="coerce")
            cls = snap["Facility"].map(fac)
            firm = snap["FTT_NonIntermittentGenerationSystem_RAC"].notna() | snap["FTT_ElectricStorageResource_Capacity_RAC"].notna()
            cls = cls.fillna(pd.Series(np.where(firm, "Scheduled Facility", "x"), index=snap.index))
            keep = (cls == "Scheduled Facility") & snap["Status"].isin(["Approved", "Accepted"]) & \
                   snap["Facility"].map(fuel).isin(["coal", "gas_base", "peaker"])
            r = snap[keep].assign(rac=rac[keep])
            vals = []
            for t in T:
                tot = 0.0
                for fc_, g in r.groupby("Facility"):
                    ov = g[(g["CommencementInterval"] < t + pd.Timedelta(minutes=30)) & (g["EndInterval"] + pd.Timedelta(minutes=5) > t)]
                    if len(ov):
                        cr = cc.at[fc_, cap_year(t)] if fc_ in cc.index and pd.notna(cc.at[fc_, cap_year(t)]) else 0.0
                        tot += max(0.0, cr - ov["rac"].fillna(0.0).min())
                vals.append(tot)
            rec(D, "out_thermal_max_1621_mw", float(max(vals)), float(row["out_thermal_max_1621_mw"]))
            rec(D, "out_thermal_mean_1621_mw", float(np.mean(vals)), float(row["out_thermal_mean_1621_mw"]))
            rec(D, "outage: listed_at <= issue", bool(snap["listed_at"].iloc[0] <= issue), True, str(snap["listed_at"].iloc[0]))
        else:
            rec(D, "outage: extract", "missing", row["out_snapshot"])

        # ---- capacity credits
        rec(D, "thermal_cap_mw", float(thermal[cap_year(D)]), float(row["thermal_cap_mw"]))

        # ---- battery: daily max of 30-min means over D-91..D-2, all 90 days needed
        win = dmax.reindex(pd.date_range(D - pd.Timedelta(days=91), D - pd.Timedelta(days=2)))
        rec(D, "bat_cap_mw (D-91..D-2)", float(win.max()) if win.notna().all() else np.nan, float(row["bat_cap_mw"]),
            f"shift(1) would give {dmax.reindex(pd.date_range(D - pd.Timedelta(days=90), D - pd.Timedelta(days=1))).max():.1f}")

        # ---- ST PASA: newest report (run_at) public at the issue whose Reference covers the window; first public copy
        dem = st[(st["variable"] == "demand_forecast") & (st["available_at"] <= issue)]
        ref = dem[dem["scenario"] == "Reference"]
        cov = ref.groupby(["record_id", "version"])["target_time"].agg(["min", "max"])
        cov = cov[(cov["min"] <= T[0]) & (cov["max"] >= T[-1])]
        if len(cov):
            c = ref.set_index(["record_id", "version"]).loc[cov.index].reset_index()
            c = c[c["run_at"] == c["run_at"].max()].sort_values("available_at")
            rid, ver = c["record_id"].iloc[0], c["version"].iloc[0]
            mm = man.assign(key=man["sha256"].str[:12]).drop_duplicates("key").set_index("key")
            pub = max(mm.at[ver, "listed_at"], mm.at[ver, "member_newest"])
            rec(D, "st: report", rid, row["st_report"], f"version {ver}, public {pub} (manifest), issue {issue}")
            rec(D, "st: public <= issue (manifest)", bool(pub <= issue), True)
            for sc_, col in (("Reference", "st_ref_max_1621_mw"), ("High", "st_high_max_1621_mw"), ("Low", "st_low_max_1621_mw")):
                v = dem[(dem["record_id"] == rid) & (dem["version"] == ver) & (dem["scenario"] == sc_)].set_index("target_time")["value"]
                v = v[~v.index.duplicated()].reindex(T)
                rec(D, col, float(v.max()) if v.notna().all() else np.nan, float(row[col]))
        else:
            rec(D, "st: report", None, row["st_report"])

        # ---- weather: newest run public at the issue, per hour; public time re-derived from the record id
        hours = D + pd.timedelta_range("08:00:00", periods=24, freq="h")
        vals = {}
        newest_pub = pd.NaT
        for loc, var in (("perth", "temperature_2m"), ("perth", "shortwave_radiation"), ("midwest", "wind_speed_100m"),
                         ("merredin", "wind_speed_100m")):
            r = wx[(wx["facility_or_service"] == loc) & (wx["variable"] == var) & wx["target_time"].isin(hours)].copy()
            init = pd.to_datetime(r["record_id"].str.extract(r"_(\d{10})Z$")[0], format="%Y%m%d%H")
            r["pub"] = init + pd.Timedelta(hours=16)               # UTC -> AWST (+8) and the 8-hour availability rule
            bad_pub = int((r["pub"] != r["available_at"]).sum())
            r = r[r["pub"] <= issue]
            r = r.sort_values("run_at").groupby("target_time").tail(1).set_index("target_time")["value"].reindex(hours)
            vals[(loc, var)] = r
            if bad_pub:
                rec(D, f"wx {loc} {var}: available_at == init+16h", bad_pub, 0)
        for loc, var in vals:
            pass
        h = vals[("perth", "temperature_2m")]
        rec(D, "wx_temp_perth_max_day_c", float(h.max()) if h.notna().all() else np.nan, float(row["wx_temp_perth_max_day_c"]))

        def half(series, inst=True):
            out = []
            for t in T:
                h0 = t.floor("h")
                out.append(series[h0] if (t == h0 or not inst) else (series[h0] + series[h0 + pd.Timedelta(hours=1)]) / 2)
            return np.array(out, float)
        tw = half(h)
        rec(D, "wx_temp_perth_max_1621_c", float(np.max(tw)) if not np.isnan(tw).any() else np.nan, float(row["wx_temp_perth_max_1621_c"]))
        for loc, col in (("midwest", "wx_wind_midwest_1621_kmh"), ("merredin", "wx_wind_merredin_1621_kmh")):
            ww = half(vals[(loc, "wind_speed_100m")])
            rec(D, col, float(np.mean(ww)) if not np.isnan(ww).any() else np.nan, float(row[col]))
        gg = half(vals[("perth", "shortwave_radiation")], inst=False)
        rec(D, "wx_ghi_perth_mean_1621_wm2", float(np.mean(gg)) if not np.isnan(gg).any() else np.nan, float(row["wx_ghi_perth_mean_1621_wm2"]))
        g = vals[("perth", "shortwave_radiation")]
        rec(D, "wx_ghi_perth_mean_day_wm2", float(g.mean()) if g.notna().all() else np.nan, float(row["wx_ghi_perth_mean_day_wm2"]))

        # ---- every *_available_at in the table <= issue_at
        for c in [c for c in d.columns if c.endswith("_available_at")]:
            v = row[c]
            if pd.notna(v):
                rec(D, f"{c} <= issue_at", bool(pd.Timestamp(v) <= issue), True, str(v))

        # ---- I2 spike_table, same day
        s = stb[(stb["issue_date"] == D - pd.Timedelta(days=1)) & stb["target_time"].isin(T)].set_index("target_time").reindex(T)
        if s["pd_req"].notna().any():
            rec(D, "I2 spike_table pd_req max", float(s["pd_req"].max()), float(row["pd_req_max_1621_mw"]))
            rec(D, "I2 spike_table wx_temp max (half-hours)", float(s["wx_temp"].max()), float(row["wx_temp_perth_max_1621_c"]))
            rec(D, "I2 spike_table snapshot", s["src_out_snapshot"].iloc[0], row["out_snapshot"])
            rec(D, "I2 spike_table out_loss_mw max >= thermal max", bool(s["out_loss_mw"].max() + TOL >= row["out_thermal_max_1621_mw"]), True)
            rec(D, "I2 spike_table st_ref max", float(s["st_ref"].max()), float(row["st_ref_max_1621_mw"]), f"spike_table report {s['src_st_report'].iloc[0]}")

    out = pd.DataFrame(rows)
    out.to_csv(REV / "r1_spot_days.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    pd.set_option("display.max_colwidth", 110)
    print("days:", [str(x.date()) for x in days])
    print(out.to_string(index=False))
    print(f"\n{int(out['match'].sum())}/{len(out)} match; mismatches:")
    print(out[~out["match"]].to_string(index=False))


if __name__ == "__main__":
    main()
