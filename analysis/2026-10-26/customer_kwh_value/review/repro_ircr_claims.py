"""R-B2 review: the IRCR claims in RESULTS.md sections 4 and 6 (S2a is 0; the reference rule runs out of calls before
the 2023-24 peak days; where the Peak SWIS intervals fall; the pooled IRCR c/kWh by window).

Run (from the repository root):  .venv/bin/python analysis/2026-10-26/customer_kwh_value/review/repro_ircr_claims.py
Writes nothing; prints.
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp.policy import policy_optimizer as po  # noqa: E402
from vpp.policy import policy_simulator as ps  # noqa: E402

m, fc = ps.load_market(), ps.load_forecasts()
HH = ps.Household()
KEY = (2.0, 2.0, 0.2, HH, (15.0, 21.0), str(ps.LAST_DAY.date()))
fam = ps.family(*KEY)
peaks = ps.peak_intervals()
years = ps.contract_years(m)


def clock(s):
    t = 8 * 60 + int(s) * 30
    return f"{(t // 60) % 24:02d}:{t % 60:02d}"


for y, (idx, slots) in peaks.items():
    print(f"season {y}: peak days {sorted({str(m.days[d].date()) for d in idx})}; interval starts "
          f"{sorted({clock(s) for s in slots})}")

# reference rule (B) in S2b, 2023-24: when were its calls?
pol, obl = ps.SYNERGY_LIKE, po.SCENARIOS["S2b"]
es = np.flatnonzero(fam.starts + 4 <= 2 * (21 - 8))
scores = ps.energy_scores(KEY, pol, obl, fc, m, es)
calls = ps.select_calls(years[2023], fam, pol, ps.CallRule("optimised", 0.0, 0.9), fc, scores, True, False, m)
print(f"B S2b FY2023: {len(calls.day)} calls, first {m.days[calls.day[0]].date()}, last {m.days[calls.day[-1]].date()}; "
      f"peak days covered {len(set(peaks[2023][0].tolist()) & set(calls.day.tolist()))}")

# C rule call days per season, then the same days at every window (S2b, S2a)
pol_c = po.make_policy(0.85, "event", 45, 2.0, 2.0, 0.2, "optimised S2b")
rule_c = ps.CallRule("optimised", 0.0, 0.75)
sc_c = ps.energy_scores(KEY, pol_c, obl, fc, m, es)
cdays = {}
for y, per in ((2023, 2023), (2024, 2024), (2025, 2025)):
    c = ps.select_calls(years[per], fam, pol_c, rule_c, fc, sc_c, True, y != 2025, m) if y != 2025 else \
        ps.select_calls(years[2025], fam, pol_c, rule_c, fc, sc_c, True, True, m)
    cdays[y] = c.day[c.kind == "ircr"]
rows = []
for jj, s in enumerate(fam.starts):
    tot = {"S2a": 0.0, "S2b": 0.0}
    qs, n = 0.0, 0
    for y in ps.SEASONS:
        d = cdays[y]
        cl = ps.Calls(d, np.full(len(d), jj), np.full(len(d), "ircr", object))
        for sc in tot:
            tot[sc] += ps.ircr_value(cl, fam, po.SCENARIOS[sc], m, y, peaks)
        qs += fam.paid_kwh[jj, d].sum()
        n += len(d)
    rows.append({"window": f"{clock(s)}-{clock(s + 4)}", "calls": n, "Q_mean": round(qs / n, 2),
                 "S2b_c_per_kWh": round(100 * tot["S2b"] / qs, 1), "S2a_c_per_kWh": round(100 * tot["S2a"] / qs, 2)})
print(pd.DataFrame(rows).to_string(index=False))
alloc = pd.read_csv(HERE / "out" / "season_ircr.csv")
print("S2a rows in season_ircr.csv: max |zenith_c_per_kwh| =", alloc[alloc["scenario"] == "S2a"]["zenith_c_per_kwh"].abs().max())
