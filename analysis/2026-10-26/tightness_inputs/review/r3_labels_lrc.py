"""R-A review I4/I5/I6.
I4  label: window minimum of step 1's half-hour margin on random days (step-1 code read only)
I5  LRC fixes against the raw advisory text
I6  which snapshot the ex-post outage label used (D+3, or a fallback D+2/D+4..D+6) -> matters for the
    5-day training embargo downstream
Writes review/r3_labels_lrc.csv.  Run:  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python <this file>
"""
import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path("/Users/matthew.yuan/Desktop/HGS")
TASK = ROOT / "analysis" / "2026-10-26" / "tightness_inputs"
for p in (ROOT, ROOT / "analysis" / "2026-10-17", ROOT / "analysis" / "2026-10-26" / "supply_tightness"):
    sys.path.insert(0, str(p))

import tightness_label as tl  # noqa: E402

SLOTS = pd.timedelta_range("16:00:00", "20:30:00", freq="30min")


def main() -> None:
    d = pd.read_parquet(TASK / "out" / "inputs_d1.parquet").set_index("day")
    rows = []
    # I4
    hh = tl.half_hours()
    rng = np.random.default_rng(99)
    pool = d.index[d["label_complete"]]
    days = pd.DatetimeIndex(rng.choice(pool, 25, replace=False)).sort_values()
    bad = 0
    for D in days:
        m = hh["margin_mw"].reindex(D + SLOTS)
        mine = m.min() if m.notna().sum() >= 8 else np.nan
        t = m.idxmin()
        ok = abs(mine - d.at[D, "margin_min_1621_mw"]) <= 0.01 and t.strftime("%H:%M") == d.at[D, "margin_min_time"]
        # input battery (shift 2) must not equal the label battery (shift 1) by construction everywhere: just record
        bad += not ok
    rows.append({"check": "I4 label window minimum (25 random days)", "result": f"{25 - bad}/25 match"})
    # label never available before issue: spike label's own day is D (RTP of D), so just check n_valid rule
    nv = d["n_valid_1621"]
    rows.append({"check": "I4 days with 8-9 valid half-hours (label kept)", "result": int(((nv >= 8) & (nv < 10)).sum())})

    # I5 LRC
    adv = pd.read_csv(TASK / "out" / "lrc_advisories.csv", parse_dates=["issued", "day", "step1_day", "text_day"])
    ld = pd.read_csv(TASK / "out" / "lrc_days.csv", parse_dates=["day"]).set_index("day")
    old = pd.read_csv(ROOT / "data" / "raw" / "market-advisories" / "market-advisories.csv", encoding="cp1252")
    new = pd.read_csv(TASK / "raw" / "data.wa.aemo.com.au__public__market-data__wemde__marketAdvisory__marketAdvisoryReport__market-advisories.csv",
                      encoding="cp1252").rename(columns={"Id": "ID"})
    allv = pd.concat([old, new]).drop_duplicates("ID", keep="last").set_index("ID")
    for day in ("2024-05-05", "2024-05-06", "2024-06-10", "2024-06-11", "2024-06-12", "2024-09-27", "2024-09-28",
                "2026-06-09", "2026-07-21", "2026-10-13"):
        D = pd.Timestamp(day)
        st = ld.at[D, "status"] if D in ld.index else "absent"
        ids = adv.loc[adv["day"] == D, "ID"].tolist()
        txt = " || ".join(f"{i}: start={allv.at[i, 'Start Interval']} issued={allv.at[i, 'Issued At']} :: {str(allv.at[i, 'Details'])[:170]}" for i in ids)
        rows.append({"check": f"I5 LRC {day}", "result": f"status={st}; lrc={ld.at[D, 'lrc'] if D in ld.index else None}", "note": txt})
    # table lrc flags vs lrc_days
    on = set(ld.index[ld["lrc"]])
    tab = set(d.index[d["lrc"]])
    rows.append({"check": "I5 table lrc == lrc_days (days in table)", "result": tab == (on & set(d.index)),
                 "note": f"table {len(tab)}, lrc_days in range {len(on & set(d.index))}, all {len(on)}"})
    # all LRC advisories published after the issue of their day?
    early = d[d["lrc"] & (d["lrc_first_issued"] <= d["issue_at"])]
    rows.append({"check": "I5 LRC first advisory <= D-1 09:00 (would be usable as input)", "result": len(early)})

    # I6 ex-post outage snapshot lag
    src = pd.read_csv(ROOT / "analysis" / "2026-10-17" / "out" / "outages_30min_sources.csv", parse_dates=["trading_day"])
    src["snap_day"] = pd.to_datetime(src["snapshot"].str.extract(r"_(\d{8})\d{4}\.csv")[0], format="%Y%m%d")
    src["lag"] = (src["snap_day"] - src["trading_day"]).dt.days
    src = src[src["trading_day"] >= "2024-01-01"]
    vc = src["lag"].value_counts(dropna=False).sort_index()
    rows.append({"check": "I6 ex-post outage snapshot lag (days after D), 2024-01-01..", "result": vc.to_dict()})
    late = src[src["lag"] >= 5]
    rows.append({"check": "I6 days whose label used a snapshot >= D+5 (after the 5-day embargo)",
                 "result": len(late), "note": ",".join(late["trading_day"].dt.strftime("%Y-%m-%d"))})
    out = pd.DataFrame(rows)
    out.to_csv(TASK / "review" / "r3_labels_lrc.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 400)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
