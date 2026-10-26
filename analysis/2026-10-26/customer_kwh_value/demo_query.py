"""Calls the one-day query kwh_value(day, scenario, window) on a few days, to show it works on its own.

Run (from the repository root):  .venv/bin/python analysis/2026-10-26/customer_kwh_value/demo_query.py
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kwh_value import kwh_value  # noqa: E402

for day, sc, win in (("2026-01-21", "S2b", "17:30-19:30"), ("2026-01-21", "S3", "17:30-19:30"),
                     ("2026-01-21", "S1", "17:30-19:30"), ("2026-01-10", "S2b", "17:30-19:30"),
                     ("2026-10-20", "S2b", "17:30-19:30"), ("2026-01-21", "S2b", "19:00-21:00")):
    r = kwh_value(day, sc, win)
    print(f"{r['day']} {r['scenario']:3s} {r['window']} flagged={r['flagged_peak_day']!s:5s} "
          f"Q={r['Q_expected_kwh']:.2f}kWh  a={r['a_self_use_c']:.1f}c  b={r['b_energy_c']:.1f}c  c={r['c_ircr_c']:.1f}c  "
          f"Zenith max={r['d_zenith_max_c']:.1f}c  customer min={r['d_customer_min_c']:.1f}c")
