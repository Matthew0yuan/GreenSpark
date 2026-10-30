"""R-A review: provenance of every row of out/inputs_d1.parquet re-derived from raw metadata, written independently
of leak_audit.py. Per day: snapshot listed <= issue and the newest such; pre-dispatch issued + 20 min <= issue (raw
CSV); ST PASA public (manifest) <= issue; battery last day == D-2; weather newest run public <= issue; 30-min table
weather values all from runs public at the issue.
Writes review/r4_full_provenance.csv.  Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
"""
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

ROOT = Path("/Users/matthew.yuan/Desktop/HGS")
TASK = ROOT / "analysis" / "2026-10-26" / "tightness_inputs"
RAW = ROOT / "data" / "raw"


def main() -> None:
    d = pd.read_parquet(TASK / "out" / "inputs_d1.parquet").set_index("day")
    issue = d["issue_at"]
    rows = []

    lst = pd.read_csv(RAW / "realtime-outages" / "listing.csv", parse_dates=["listed_at"])
    lst = lst[lst["name"].str.startswith("GeneratorOutages_")].sort_values("listed_at")
    li = lst.drop_duplicates("name").set_index("name")["listed_at"]
    has = d["out_snapshot"].notna()
    pub = d["out_snapshot"].map(li)
    newest = pd.Series([lst.loc[lst["listed_at"] <= t, "name"].iloc[-1] for t in issue], index=d.index)
    rows.append({"source": "outages", "rows_with_value": int(has.sum()), "late": int((has & (pub > issue)).sum()),
                 "unknown": int((has & pub.isna()).sum()), "not_newest": int((has & (d["out_snapshot"] != newest)).sum())})

    iss = {}
    for f in sorted((RAW / "predispatch-0800").glob("*.csv")):
        p = pd.read_csv(f, usecols=["run", "issued"], dtype=str, nrows=1)
        iss[p["run"].iloc[0]] = pd.to_datetime(p["issued"].iloc[0], format="%Y%m%d%H%M%S")
    has = d["pd_run"].notna()
    pubp = d["pd_run"].map(iss) + pd.Timedelta(minutes=20)
    runday = pd.to_datetime(d["pd_run"].str[:8], format="%Y%m%d")
    rows.append({"source": "predispatch", "rows_with_value": int(has.sum()), "late": int((has & (pubp > issue)).sum()),
                 "unknown": int((has & pubp.isna()).sum()),
                 "not_newest": int((has & (runday != d.index - pd.Timedelta(days=1))).sum())})
    # days without pre-dispatch: is the D-1 run really late (or absent)?
    miss = d.index[~has]
    late_ok = []
    for D in miss:
        f = RAW / "predispatch-0800" / f"{D - pd.Timedelta(days=1):%Y%m%d}.csv"
        if f.exists():
            p = pd.read_csv(f, usecols=["run", "issued"], dtype=str, nrows=1)
            late_ok.append((str(D.date()), p["run"].iloc[0], str(pd.to_datetime(p["issued"].iloc[0], format="%Y%m%d%H%M%S"))))
        else:
            late_ok.append((str(D.date()), "no file", ""))
    rows.append({"source": "predispatch missing days", "rows_with_value": len(miss), "note": late_ok})

    man = pd.read_csv(RAW / "st-pasa" / "manifest.csv", parse_dates=["listed_at", "member_newest"])
    man = man[man["name"].str.lower().str.endswith(".zip")].copy()
    man["key"] = man["sha256"].str[:12]
    man["public"] = man[["listed_at", "member_newest"]].max(axis=1)
    m = man.drop_duplicates("key").set_index("key")["public"]
    has = d["st_report"].notna()
    pubs = d["st_version"].map(m)
    rows.append({"source": "stpasa", "rows_with_value": int(has.sum()), "late": int((has & (pubs > issue)).sum()),
                 "unknown": int((has & pubs.isna()).sum()), "age_days": d.loc[has, "st_report_age_days"].value_counts().sort_index().to_dict()})

    has = d["bat_cap_mw"].notna()
    rows.append({"source": "battery", "rows_with_value": int(has.sum()),
                 "late": int((has & (pd.to_datetime(d["bat_last_day"]) != d.index - pd.Timedelta(days=2))).sum())})

    h = pd.read_parquet(TASK / "out" / "inputs_d1_30min.parquet")
    init = pd.to_datetime(h["wx_newest_run"].str.extract(r"_(\d{10})Z$")[0], format="%Y%m%d%H")
    has = h["wx_temp_perth_c"].notna()
    rows.append({"source": "weather (30-min table, newest run per day)", "rows_with_value": int(has.sum()),
                 "late": int((has & (init + pd.Timedelta(hours=16) > h["issue_at"])).sum())})
    # every *_available_at column of the 30-min table <= issue
    for c in [c for c in h.columns if c.endswith("_available_at")]:
        v = pd.to_datetime(h[c])
        rows.append({"source": f"30-min {c}", "rows_with_value": int(v.notna().sum()), "late": int((v > h["issue_at"]).sum())})
    # label columns present in the 30-min table that a modeller could pick up by mistake
    rows.append({"source": "30-min table label-like columns", "note": [c for c in h.columns if c.startswith("lab_") or c in
                                                                         ("margin_min_1621_mw", "spike", "lrc", "ircr_peak")]})
    out = pd.DataFrame(rows)
    out.to_csv(TASK / "review" / "r4_full_provenance.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 300)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
