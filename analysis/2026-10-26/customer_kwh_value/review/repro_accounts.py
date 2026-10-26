"""R-B2 review: independent reproduction of the per-kWh split against account(), the IRCR allocation and the
contract-year break-even prices. Uses only vpp.policy (not the reviewed kwh_value module) and the call rules saved in
reports/policy/policy_backtest.csv (not the deliverable's refit).

Run (from the repository root):  .venv/bin/python analysis/2026-10-26/customer_kwh_value/review/repro_accounts.py
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

from vpp.evaluate import CUTOFF  # noqa: E402
from vpp.policy import policy_optimizer as po  # noqa: E402
from vpp.policy import policy_simulator as ps  # noqa: E402

pd.set_option("display.width", 250)
m, fc = ps.load_market(), ps.load_forecasts()
HH = ps.Household()
KEY = (2.0, 2.0, 0.2, HH, (15.0, 21.0), str(ps.LAST_DAY.date()))
fam = ps.family(*KEY)
PART = ps.Participation()
A = PART.availability
saved = pd.read_csv(ROOT / "reports" / "policy" / "policy_backtest.csv")
POL = {"B": ps.SYNERGY_LIKE,
       "C-S2b": po.make_policy(0.85, "event", 45, 2.0, 2.0, 0.2, "optimised S2b"),
       "C-S3": po.make_policy(0.85, "energy", 45, 2.0, 2.0, 0.2, "optimised S3")}
CASE = {"B": "B reference", "C-S2b": "C optimised", "C-S3": "C optimised"}
years = ps.contract_years(m)
peaks = ps.peak_intervals()


_FITS = {}


def saved_rule(pol_name, sc):
    """The saved rule (S2b, S3); S1 and S2a are not in the saved backtest, so the existing fit_rule is used there."""
    r = saved[(saved["scenario"] == sc) & (saved["case"] == CASE[pol_name]) & (saved["period"] == "in-sample")]
    if len(r):
        return ps.CallRule("optimised", float(r["rule_theta"].iloc[0]), float(r["rule_ircr_q"].iloc[0]))
    if (pol_name, sc) not in _FITS:
        _FITS[(pol_name, sc)] = ps.fit_rule(POL[pol_name], po.SCENARIOS[sc], PART).rule
        print(f"   (rule for {pol_name} {sc} not saved; fit_rule gives theta={_FITS[(pol_name, sc)].theta}, "
              f"q={_FITS[(pol_name, sc)].ircr_q})")
    return _FITS[(pol_name, sc)]


def calls_of(pol_name, sc, period):
    pol, obl = POL[pol_name], po.SCENARIOS[sc]
    rule = saved_rule(pol_name, sc)
    es = np.flatnonzero(fam.starts + 2 * pol.event_h <= 2 * (pol.window[1] - 8))
    scores = ps.energy_scores(KEY, pol, obl, fc, m, es)
    cap_on = obl.capacity != "none"
    if period == "in-sample":
        y, days, e_on = 2025, years[2025], obl.energy_to_zenith
    elif period == "test":
        y, days, e_on = 2026, years[2026][m.days[years[2026]] >= CUTOFF], obl.energy_to_zenith
    else:
        y = int(period[2:])
        days, e_on = years[y], False
    calls = ps.select_calls(days, fam, pol, rule, fc, scores, cap_on, e_on, m)
    return calls, y, days, rule


def split(pol, obl, j, d):
    """My own per-kWh split, A$ per kWh of billed event net export Q, per responding customer per call."""
    q = fam.paid_kwh[j, d]
    offset = pol.offset_rate * fam.standby_kwh[j, d]
    v_self = (ps.RETAIL * fam.d_import_kwh[j, d] - fam.d_feedin_other[j, d]
              - fam.gap_kwh[j, d] * HH.eta * ps.RETAIL - offset) / q
    debs = fam.d_feedin_event[j, d] / q
    wear = PART.deg_per_kwh * np.maximum(fam.d_through_kwh[j, d], 0.0) / q
    hassle = PART.hassle_per_event / q
    p_c = v_self - debs + wear + hassle
    v_energy = fam.w_actual[j, d] / q
    p_z0 = v_energy * float(obl.energy_to_zenith) - offset / q - obl.ops_per_event / q
    pay = (pol.pay_per_kwh * q + pol.pay_per_event) / q
    return dict(q=q, v_self=v_self, debs=debs, wear=wear, hassle=hassle, p_c=p_c, v_energy=v_energy, p_z0=p_z0, pay=pay)


print("=== 1. per-call reconciliation with account(): my split x Q x 0.9 vs per_call_zenith / per_call_customer")
dd = pd.read_csv(HERE / "out" / "daily.csv")
rows = []
for pol_name, sc, period in (("C-S2b", "S2b", "in-sample"), ("C-S3", "S3", "in-sample"), ("C-S3", "S3", "FY2023"),
                             ("B", "S2a", "in-sample"), ("B", "S2b", "FY2023"), ("B", "S3", "FY2023"),
                             ("B", "S1", "in-sample"), ("C-S2b", "S2b", "test")):
    pol, obl = POL[pol_name], po.SCENARIOS[sc]
    calls, y, days, rule = calls_of(pol_name, sc, period)
    acc = ps.account(calls, fam, pol, obl, PART, m, y if y in ps.SEASONS else None,
                     peaks if obl.capacity != "none" else None)
    j, d = calls.start, calls.day
    if len(d) == 0:
        rows.append({"case": f"{pol_name} {sc} {period}", "rule": (rule.theta, rule.ircr_q), "calls": 0})
        continue
    s = split(pol, obl, j, d)
    zen = A * s["q"] * (s["p_z0"] - s["pay"])
    cust = A * s["q"] * (s["pay"] - s["p_c"])
    rows.append({"case": f"{pol_name} {sc} {period}", "rule": (rule.theta, rule.ircr_q), "calls": len(d),
                 "sumQ": s["q"].sum(), "max|dz|": np.abs(zen - acc["per_call_zenith"]).max(),
                 "max|dc|": np.abs(cust - acc["per_call_customer"]).max(),
                 "zen_calls_A$": float(zen.sum()), "acc_zen_calls_excl_cap": float(acc["zenith_calls"] - acc["capacity"])})
print(pd.DataFrame(rows).to_string(index=False))

print("\n=== 1b. my after-the-fact split vs the deliverable's daily.csv on chosen days (default window, c/kWh)")
j0 = fam.index(ps.ircr_start(2.0))
win = "17:30-19:30"
for day, sc in (("2025-12-17", "S2b"), ("2026-01-21", "S2b"), ("2025-08-14", "S1"), ("2026-10-20", "S1"),
                ("2026-03-05", "S3")):
    d = int(np.flatnonzero(m.days == pd.Timestamp(day))[0])
    pol = POL["C-S3"] if sc == "S3" else POL["C-S2b"] if sc in ("S2a", "S2b") else POL["B"]
    s = split(pol, po.SCENARIOS[sc], np.array([j0]), np.array([d]))
    r = dd[(dd["day"] == day) & (dd["scenario"] == sc) & (dd["window"] == win)].iloc[0]
    print(f"{day} {sc}: Q mine {s['q'][0]:.4f} vs {r['Q_actual_kwh']:.4f}; self-use mine {100*s['v_self'][0]:.3f} vs "
          f"{r['a_self_use_actual_c']:.3f}; cust-min mine {100*s['p_c'][0]:.3f} vs {r['d_customer_min_actual_c']:.3f}; "
          f"energy mine {100*s['v_energy'][0]:.3f} vs {r['b_energy_actual_c']:.3f}")

print("\n=== 2. IRCR allocation per kWh of Q (C rule from the saved backtest, default window)")
out = {}
for sc, pol_name in (("S2b", "C-S2b"), ("S3", "C-S3")):
    obl = po.SCENARIOS[sc]
    tot_v, tot_q, tot_n = 0.0, 0.0, 0
    for y, per in ((2023, "FY2023"), (2024, "FY2024"), (2025, "in-sample")):
        calls, _, _, rule = calls_of(pol_name, sc, per)
        sel = calls.kind == "ircr"
        ic = ps.Calls(calls.day[sel], calls.start[sel], calls.kind[sel])
        val = ps.ircr_value(ic, fam, obl, m, y, peaks) * obl.capacity_share     # per responding customer
        qs = float(fam.paid_kwh[ic.start, ic.day].sum())
        tot_v, tot_q, tot_n = tot_v + val, tot_q + qs, tot_n + len(ic.day)
        print(f"{sc} season {y}-{str(y+1)[2:]}: rule q={rule.ircr_q}, ircr calls {len(ic.day)}, sumQ {qs:.1f} kWh, "
              f"value to Zenith ${val:.1f}, {100*val/qs:.1f} c/kWh, ${val/len(ic.day):.2f}/call")
    out[sc] = 100 * tot_v / tot_q
    print(f"{sc} pooled: {tot_n} calls, sumQ {tot_q:.1f}, ${tot_v:.1f}, {out[sc]:.2f} c/kWh")
    # B rule
for sc in ("S2b", "S3"):
    obl = po.SCENARIOS[sc]
    for y, per in ((2023, "FY2023"), (2024, "FY2024"), (2025, "in-sample")):
        calls, _, _, rule = calls_of("B", sc, per)
        sel = calls.kind == "ircr"
        ic = ps.Calls(calls.day[sel], calls.start[sel], calls.kind[sel])
        val = ps.ircr_value(ic, fam, obl, m, y, peaks) * obl.capacity_share
        qs = float(fam.paid_kwh[ic.start, ic.day].sum())
        print(f"B {sc} season {y}: q={rule.ircr_q} calls {len(ic.day)} sumQ {qs:.1f} value ${val:.1f} "
              f"{(100*val/qs if qs else 0):.1f} c/kWh")

print("\n=== 3. contract-year break-even via evaluate_policy: p_Z = paid + Zenith/(0.9 sumQ); p_C = paid - customer/(0.9 sumQ)")
ann = pd.read_csv(HERE / "out" / "annual_breakeven.csv")
for pol_name, sc, period in (("C-S2b", "S2b", "in-sample"), ("C-S2b", "S2b", "FY2023"), ("C-S2b", "S2b", "FY2024"),
                             ("C-S3", "S3", "in-sample"), ("C-S3", "S3", "FY2023"), ("C-S3", "S3", "FY2024"),
                             ("B", "S2b", "in-sample"), ("B", "S2b", "FY2023"), ("B", "S3", "FY2023")):
    pol, obl = POL[pol_name], po.SCENARIOS[sc]
    rule = saved_rule(pol_name, sc)
    e = ps.evaluate_policy(pol, market_obligations=obl, period=period, rule=rule)
    calls, y, days, _ = calls_of(pol_name, sc, period)
    q = fam.paid_kwh[calls.start, calls.day]
    qs = float(q.sum())
    paid = (pol.pay_per_kwh * qs + pol.pay_per_event * len(q)) / qs
    pz = 100 * (paid + e.zenith_per_customer / (A * qs))
    pc = 100 * (paid - (e.customer_benefit - pol.annual_fee * len(days) / 365) / (A * qs))
    lab = {"in-sample": "合同年2025-26", "FY2023": "合同年2023-24", "FY2024": "合同年2024-25"}[period]
    r = ann[(ann["policy"] == pol_name) & (ann["scenario"] == sc) & ann["period"].str.startswith(lab)].iloc[0]
    print(f"{pol_name} {sc} {period}: calls {len(q)} (theirs {r['calls']}), p_Z mine {pz:.2f} vs {r['zenith_max_c_per_kwh_year']:.2f}; "
          f"p_C mine {pc:.2f} vs {r['customer_min_c_per_kwh_year']:.2f}; paid {100*paid:.2f} vs {r['paid_c_per_billed_kwh']:.2f}; "
          f"evaluate_policy Zenith/enrolled {e.zenith_per_customer:.3f}, saved "
          f"{saved[(saved['scenario']==sc)&(saved['case']==CASE[pol_name])&(saved['period']==period)]['zenith_per_customer'].values}")
