"""Coverage of the 09:00 weather forecast (ECMWF vintages in spike_table) on warm-season days, by month.

Run:  .venv/Scripts/python.exe analysis/2026-10-24/peak_day_probability/inspect_weather.py
"""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from vpp.data import PROCESSED  # noqa: E402


def main() -> None:
    t = pd.read_parquet(PROCESSED / "spike_table.parquet", columns=["target_date", "days_ahead", "wx_tmax_day", "wx_temp", "target_time",
                                                                    "src_wx_available_at", "issue_time", "pd_age_h"])
    for da in (0, 1):
        u = t[t["days_ahead"] == da]
        g = u.groupby("target_date").agg(tmax=("wx_tmax_day", "max"), n_nan=("wx_temp", lambda s: s.isna().sum()))
        m = g.groupby(g.index.to_period("M")).agg(days=("tmax", "size"), with_tmax=("tmax", lambda s: s.notna().sum()),
                                                  any_nan_slot=("n_nan", lambda s: (s > 0).sum()))
        print(f"days_ahead {da}")
        print(m.loc["2023-10":"2026-04"].T.to_string())
        late = (pd.to_datetime(u["src_wx_available_at"]) > u["issue_time"]).sum()
        print("weather rows available after the issue:", late)
    old = t[(t["days_ahead"] == 0) & (t["pd_age_h"] > 1.5)].drop_duplicates("target_date")[["target_date", "pd_age_h"]]
    print("same-day rows whose pre-dispatch run is older than the day's 08:00 run:\n", old.to_string(index=False))


if __name__ == "__main__":
    main()
