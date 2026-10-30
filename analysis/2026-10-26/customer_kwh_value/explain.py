"""Mechanism view for RESULTS.md: where the customer's self-use value and the market value come from, by phase
(standby / event / after the event), default window 17:30-19:30, contract year 2025-26 (in-sample), at actual prices,
per kWh of billed event net export (ratio of sums). Reads the module only; writes out/mechanism.csv.

Run (from the repository root):  .venv/bin/python analysis/2026-10-26/customer_kwh_value/explain.py
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import kwh_value as kv  # noqa: E402
from vpp.policy import policy_simulator as ps  # noqa: E402

m, fc = ps.load_market(), ps.load_forecasts()
p = kv.decompose()
j = p.index(kv.DEFAULT_START)
ins = np.flatnonzero(kv.period_label(m.days) == "样本内 合同年2025-26")
fit = kv.fit_of("C-S2b", "S2b")
flag = ins[fc.flags[fit.rule.ircr_q][ins]]
rows = []
for name, days in (("全部日子", ins), ("热季旗标日", flag)):
    q = p.q[j, days].sum()
    r = {"days": name, "n": len(days), "Q_mean_kwh": p.q[j, days].mean()}
    for ph in kv.PHASES:
        r[f"extra_import_{ph}_kwh_per_kwhQ"] = p.d_imp[ph][j, days].sum() / q
        r[f"extra_export_{ph}_kwh_per_kwhQ"] = p.d_exp[ph][j, days].sum() / q
        r[f"retail_on_import_{ph}_c"] = 100 * ps.RETAIL * p.d_imp[ph][j, days].sum() / q
        r[f"debs_{ph}_c"] = 100 * p.debs[ph][j, days].sum() / q
        r[f"market_{ph}_c"] = 100 * p.w_phase[ph][j, days].sum() / q
    r["market_gap_c"] = 100 * p.w_phase["gap"][j, days].sum() / q
    r["gap_retail_c"] = -100 * (p.gap[j, days] * kv.HH.eta * ps.RETAIL).sum() / q
    r["offset_c"] = 100 * ps.RETAIL * p.standby_kwh[j, days].sum() / q
    r["market_total_c"] = 100 * p.w[j, days].sum() / q
    rows.append(r)
df = pd.DataFrame(rows)
df.round(3).T.to_csv(HERE / "out" / "mechanism.csv", header=False)
pd.set_option("display.width", 200)
print(df.round(3).T.to_string(header=False))
# mean evening prices the event sells at vs the hours the rebound buys at (actual, in-sample, flat by slot)
rtp = m.rtp[:m.n_days * ps.N].reshape(-1, ps.N)
print("mean RTP 17:30-19:30, in-sample:", round(float(rtp[ins][:, 19:23].mean()), 1), "$/MWh")
print("mean RTP 19:30-08:00, in-sample:", round(float(rtp[ins][:, 23:48].mean()), 1), "$/MWh")
print("mean RTP 15:30-17:30, in-sample:", round(float(rtp[ins][:, 15:19].mean()), 1), "$/MWh")
# where the 12 Peak SWIS intervals of each season fall (start of each half hour)
for y, (idx, slots) in ps.peak_intervals().items():
    days = sorted({str(m.days[d].date()) for d in idx})
    print(f"Hot Season {y}-{str(y + 1)[2:]}: peak days {days}; interval starts "
          f"{sorted({kv.clock(s) for s in slots})}")
