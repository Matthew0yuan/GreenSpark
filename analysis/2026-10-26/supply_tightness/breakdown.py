"""Descriptive breakdown after tightness_label.py (not part of the PLAN.md verdicts): on low-margin days (in-sample)
and on LRC days (history), what drove the low margin, and how often the existing day-ahead high-risk flag
(vpp.v2.risk, recent 28-day threshold, issued 09:00 the day before) and a spike came with it.

Cause of a low margin, at the day's minimum: "supply" when the coal/gas outage loss is further above the in-sample
median than the residual demand is, else "demand" (same rule as main_cause in out/low_days.csv).

Run from the repo root:  .venv/Scripts/python.exe analysis/2026-10-26/supply_tightness/breakdown.py
"""

from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent / "out"


def table(x: pd.DataFrame) -> pd.DataFrame:
    g = x.groupby("cause")
    return pd.DataFrame({"days": g.size(), "spike_days": g["spike"].sum(),
                         "high_risk_known": g["high_risk_d1"].count(),
                         "high_risk_flagged": g["high_risk_d1"].sum().astype(int)})


def main() -> None:
    d = pd.read_csv(OUT / "daily.csv", index_col=0, parse_dates=True)
    ins = d[(d.index >= "2025-04-01") & (d.index <= "2026-06-30")]
    med = ins[["residual_at_min_mw", "outage_at_min_mw"]].median()
    d["cause"] = ((d["outage_at_min_mw"] - med["outage_at_min_mw"]) > (d["residual_at_min_mw"] - med["residual_at_min_mw"])
                  ).map({True: "supply", False: "demand"})

    low = pd.read_csv(OUT / "low_days.csv", index_col=0, parse_dates=True)
    x = d.loc[low.index]
    print("In-sample 2025-04-01..2026-06-30, the 46 lowest-margin days (full-sample bottom 10%):")
    t1 = table(x)
    print(t1.to_string())

    hist = d[(d.index >= "2024-01-01") & (d.index <= "2025-03-31") & d["lrc"]]
    print("\nHistory 2024-01-01..2025-03-31, LRC days:")
    t2 = table(hist)
    print(t2.to_string())
    print("\nHistory LRC days with a high-risk call on record (risk history starts 2024-04):")
    print(hist.dropna(subset=["high_risk_d1"])[["cause", "margin_min_mw", "residual_max_mw", "residual_at_min_mw",
                                                "outage_at_min_mw", "spike", "high_risk_d1"]].round(2).to_string())
    pd.concat({"in_sample_low_days": t1, "history_lrc_days": t2}).to_csv(OUT / "breakdown.csv")


if __name__ == "__main__":
    main()
