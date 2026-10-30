"""Round 4, B5 (reports/v2_plan.md, section 11.7): the IRCR flag rule on three Hot Seasons, leave one season out.

The rule is unchanged (vpp.site_bill.dynamic_flags): a Hot Season day is flagged as a possible system peak day if
AEMO's forecast of its highest energy requirement, public at 09:00 on the day (same day) or on the day before, reaches
the 4th-highest actual daily maximum of the season's days known by then, less a tolerance: the q-quantile of how far
the forecast fell short of the actual on the Hot Season days of the other two seasons. A peak day counts as covered
when either call flags it; covering 3 of the 4 peak days (9 of the 12 intervals) brings the median, and so the IRCR, to 0.

Pass: every season covers at least 3 of its 4 peak days at q = 0.90. Else (fixed beforehand) q = 0.95, then the
largest shortfall (q = 1.0): the smallest that passes in every season; none passing, report only.
The 12 Peak SWIS Trading Intervals: AEMO's published ones for 2025-26, chosen on operational demand for the others.

Run:  python -m vpp.v2.ircr
"""

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import site_bill as sb
from vpp.data import PROCESSED

SEASONS = (2023, 2024, 2025)
QUANTILES = (sb.FLAG_QUANTILE, 0.95, 1.0)  # the rule's, then the fallbacks in order [fixed, section 11.7]


def season_rows(q: float) -> list[dict]:
    """Each season's flags at tolerance quantile q from the other two seasons, and its peak days covered."""
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["demand_mw", "req_energy_mw"])
    actual = sb.daily_max(df30["req_energy_mw"])
    p0, p1 = sb.peak_day_predictor(0), sb.peak_day_predictor(1)
    rows = []
    for y in SEASONS:
        others = [x for x in SEASONS if x != y]
        tol0 = sb.forecast_tolerance(p0, actual, others, q)
        tol1 = sb.forecast_tolerance(p1, actual, others, q)
        f0 = sb.dynamic_flags(p0, actual, tol0, 1)
        f1 = sb.dynamic_flags(p1, actual, tol1, 2)
        f0, f1 = f0[sb.hot_season(f0) == y], f1[sb.hot_season(f1) == y]
        peaks = sorted({(t - bt.H).normalize() for t in sb.ircr_intervals(df30["demand_mw"], y)})
        covered = [d for d in peaks if d in f0 or d in f1]
        rows.append({"quantile": q, "season": f"{y}-{(y + 1) % 100:02d}", "tol_same_day_mw": tol0, "tol_day_before_mw": tol1,
                     "flagged_same_day": len(f0), "flagged_day_before": len(f1), "flagged_days": len(f0.union(f1)),
                     "season_days": int((sb.hot_season(actual.index) == y).sum()),
                     "peak_days": " ".join(str(d.date()) for d in peaks),
                     "covered": len(covered), "missed": " ".join(str(d.date()) for d in peaks if d not in covered)})
    return rows


def main() -> None:
    rows, chosen = [], None
    for q in QUANTILES:
        r = season_rows(q)
        rows += r
        if chosen is None and all(x["covered"] >= 3 for x in r):
            chosen = q
    out = pd.DataFrame(rows)
    out.to_csv(bt.REPORTS / "v2_b5_ircr.csv", index=False)
    pd.set_option("display.width", 250)
    print(out.drop(columns=["peak_days"]).round({"tol_same_day_mw": 1, "tol_day_before_mw": 1}).to_string(index=False))
    print(f"\npass: {'q = ' + format(chosen, 'g') if chosen is not None else 'none'}"
          f"{' (the rule as it is)' if chosen == sb.FLAG_QUANTILE else ''}")


if __name__ == "__main__":
    main()
