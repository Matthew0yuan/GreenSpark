"""Customer fleet (VPP) export for the redesigned UI: writes analysis/2026-10-26/ui_redesign/data/fleet.json
(DATA_CONTRACT.md section 3).

Read-only use of vpp/policy: the saved outputs in reports/policy/ and analysis/2026-10-26/customer_kwh_value/out/ are
read, and the call rule and the accounts are replayed with the saved terms and the saved fitted rules (no sweep, no
re-selection) to get what the saved files do not hold:
  - the per-day 09:00 forecast, threshold and call decision in the 2025-26 Hot Season;
  - the split of the model's "retailer" column into the retail bill and the other holders' value;
  - constraint checks per call (reserve, power, state of charge, control time, window, cap).
Every replayed total is asserted against reports/policy/policy_backtest.csv before it is written.

Run from the repository root:  .venv/Scripts/python.exe analysis/2026-10-26/ui_redesign/export/fleet_export.py
Writes only data/fleet.json.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
import math  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import contracts as ct  # noqa: E402
from vpp import site_bill as sb  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402
from vpp.policy import policy_simulator as ps  # noqa: E402

T0 = time.time()
OUT = ROOT / "analysis" / "2026-10-26" / "ui_redesign" / "data" / "fleet.json"
POL = ROOT / "reports" / "policy"
KWH = ROOT / "analysis" / "2026-10-26" / "customer_kwh_value"
REL_POL = "reports/policy"
REL_KWH = "analysis/2026-10-26/customer_kwh_value"
CENTRAL = ps.Participation()
HH = ps.Household()
A = CENTRAL.availability
# the scenario objects exactly as vpp/policy/policy_optimizer.py SCENARIOS (checked against the saved names below)
OBL = {
    "S2b": ps.Obligations("S2b retailer, 2027 IRCR", capacity="net"),
    "S3": ps.Obligations("S3 Synergy TPA", energy_to_zenith=False, capacity="net", capacity_share=0.5),
}
CASES = {"ref": "B reference", "custom": "C optimised"}

# ------------------------------------------------------------------------------------------------ periods
P_FY25 = {"label": "in-sample", "segment": "contract year 2025-26", "from": "2025-07-01", "to": "2026-06-30", "days": 365}
P_HS25 = {"label": "in-sample", "segment": "Hot Season 2025-26", "from": "2025-12-01", "to": "2026-03-31", "days": 121}
P_FY24 = {"label": "in-sample", "segment": "contract year 2024-25, Hot Season calls only (policy building data, before "
          "the 456-day window; no 09:00 price forecast then)", "from": "2024-07-01", "to": "2025-06-30", "days": 365}
P_FY23 = {"label": "in-sample", "segment": "Hot Season 2023-24, data from 2023-10-01 (policy building data, before the "
          "456-day window; annual costs x 274/365)", "from": "2023-10-01", "to": "2024-06-30", "days": 274}
P_TEST = {"label": "test", "from": "2026-07-01", "to": "2026-10-22", "days": 114}
P_TS = {"label": "test-seen", "from": "2026-07-01", "to": "2026-10-15", "days": 107}
P_TU = {"label": "test-unseen", "from": "2026-10-16", "to": "2026-10-22", "days": 7}
P_EXP = {"label": "in-sample", "segment": "expected year: contract year 2025-26 energy part + mean of the Hot Season "
         "2023-24, 2024-25 and 2025-26 capacity parts (the policy study's selection objective)",
         "from": "2023-10-01", "to": "2026-06-30", "days": 1004}
P_ALL = {"label": "all data (in-sample and test mixed; candidate calls, not real calls)", "from": "2023-10-01",
         "to": "2026-10-22", "days": 1118}
SAVED_PERIOD = {"in-sample": P_FY25, "FY2024": P_FY24, "FY2023": P_FY23, "test": P_TEST}
SEASON_OF_PERIOD = {"in-sample": "2025-26", "FY2024": "2024-25", "FY2023": "2023-24"}

GAPS: list[dict] = []


def gap(field: str, reason: str) -> None:
    GAPS.append({"field": field, "reason": reason})


def r(x, nd: int = 2):
    """JSON-safe rounding: numpy scalars to Python, NaN and inf to None."""
    if x is None:
        return None
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    x = float(x)
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd)


def close(a: float, b: float, tol: float, what: str) -> None:
    if not abs(float(a) - float(b)) <= tol:
        raise AssertionError(f"{what}: {a} vs {b} (tol {tol})")


# ------------------------------------------------------------------------------------------------ saved outputs
bt = pd.read_csv(POL / "policy_backtest.csv")
loso = pd.read_csv(POL / "policy_loso.csv")
stress = pd.read_csv(POL / "policy_stress.csv")
s3share = pd.read_csv(POL / "policy_s3_share.csv")
refinement = pd.read_csv(POL / "policy_refinement.csv")
season_ircr = pd.read_csv(KWH / "out" / "season_ircr.csv")
breakeven = pd.read_csv(KWH / "out" / "annual_breakeven.csv")
energy_balance = pd.read_csv(KWH / "out" / "energy_balance.csv")


def saved(sc: str, case: str, period: str) -> pd.Series:
    x = bt[(bt["scenario"] == sc) & (bt["case"] == case) & (bt["period"] == period)]
    assert len(x) == 1, (sc, case, period)
    return x.iloc[0]


def policy_from(row: pd.Series) -> ps.Policy:
    return ps.Policy(name=row["policy_name"], comp_type=row["policy_comp_type"], pay_per_kwh=float(row["policy_pay_per_kwh"]),
                     pay_per_event=float(row["policy_pay_per_event"]), annual_fee=float(row["policy_annual_fee"]),
                     max_events=int(row["policy_max_events"]), event_h=float(row["policy_event_h"]),
                     standby_h=float(row["policy_standby_h"]), max_control_h=float(row["policy_max_control_h"]),
                     reserve_soc=float(row["policy_reserve_soc"]), notice_h=float(row["policy_notice_h"]),
                     window=tuple(float(v) for v in str(row["policy_window"]).strip("()").split(",")),
                     max_events_per_day=int(row["policy_max_events_per_day"]),
                     offset_rate=float(row["policy_offset_rate"]), min_usable_kwh=float(row["policy_min_usable_kwh"]))


def rule_from(row: pd.Series) -> ps.CallRule:
    return ps.CallRule(kind=row["rule_kind"], theta=float(row["rule_theta"]), ircr_q=float(row["rule_ircr_q"]),
                       reserve_for_ircr=bool(row["rule_reserve_for_ircr"]))


# ------------------------------------------------------------------------------------------------ replay
m, fc = ps.load_market(), ps.load_forecasts()
years = ps.contract_years(m)
peaks = ps.peak_intervals()
b = ps.baseline(HH)
print(f"market and forecasts loaded ({time.time() - T0:.0f} s)", flush=True)


def check_call(fam: ps.Family, pol: ps.Policy, d: int, j: int) -> dict:
    """One activation re-stepped exactly as policy_simulator.family() does, with each kind of breach counted apart."""
    s = int(fam.starts[j])
    n_ev, n_sb = int(round(2 * pol.event_h)), int(round(2 * pol.standby_h))
    reserve = pol.reserve_soc * HH.usable_kwh
    s0, i0 = s - n_sb, d * ps.N
    soc = np.array([b.soc[i0 + s0]])
    rate = np.zeros(1)
    out = {"reserve": 0, "soc_bounds": 0, "power": 0, "min_event_soc_kwh": np.inf, "max_kw": 0.0, "paid": 0.0}
    for k in range(s0, ps.HORIZON):
        i = i0 + k
        mode = ps.STANDBY if k < s else ps.EVENT if k < s + n_ev else ps.SELF
        if k == s:
            rate = np.minimum(HH.power_kw, np.maximum(soc - reserve, 0.0) * HH.eta / (n_ev * ps.DT))
        soc, imp, exp, dis, ch, _ = ps.step(soc, b.pv[i:i + 1], b.load[i:i + 1], np.array([mode]), rate, HH, reserve)
        if mode == ps.EVENT:
            out["min_event_soc_kwh"] = min(out["min_event_soc_kwh"], float(soc[0]))
            out["reserve"] += int(soc[0] < reserve - 1e-6)
            out["paid"] += float(exp[0]) * ps.DT
        out["soc_bounds"] += int(soc[0] < -1e-9 or soc[0] > HH.usable_kwh + 1e-9)
        out["power"] += int(abs(dis[0]) > HH.power_kw + 1e-9) + int(abs(ch[0]) > HH.power_kw + 1e-9)
        out["max_kw"] = max(out["max_kw"], float(abs(dis[0])), float(abs(ch[0])))
    close(min(out["paid"], HH.usable_kwh), fam.paid_kwh[j, d], 1e-9, "re-stepped billed export")
    assert out["reserve"] + out["soc_bounds"] + out["power"] == fam.violations[j, d]
    return out


REPLAY: dict = {}      # (offer, scenario, period) -> dict
for offer, case in CASES.items():
    for sc, obl in OBL.items():
        row0 = saved(sc, case, "in-sample")
        assert row0["obligations"] == obl.name, (row0["obligations"], obl.name)
        pol, rule = policy_from(row0), rule_from(row0)
        fit = ps.fit_rule(pol, obl, CENTRAL, rules=[rule])
        close(fit.zen, row0["fit_zen"], 1e-6, f"{offer} {sc} expected Zenith per enrolled")
        close(fit.cust, row0["fit_cust"], 1e-6, f"{offer} {sc} expected customer per enrolled")
        key = (pol.event_h, pol.standby_h, pol.reserve_soc, HH, tuple(pol.window), str(ps.LAST_DAY.date()))
        fam = ps.family(*key)
        e_starts = np.flatnonzero(fam.starts + 2 * pol.event_h <= 2 * (pol.window[1] - 8))
        scores = ps.energy_scores(key, pol, obl, fc, m, e_starts)
        for period in ("in-sample", "FY2024", "FY2023", "test"):
            srow = saved(sc, case, period)
            e = ps.evaluate_policy(pol, market_obligations=obl, period=period, fit=fit)
            close(e.zenith_profit, srow["zenith_profit"], 0.01, f"{offer} {sc} {period} zenith")
            close(e.customer_benefit, srow["customer_benefit"], 1e-6, f"{offer} {sc} {period} customer")
            close(e.enrolled, srow["enrolled"], 1e-6, f"{offer} {sc} {period} enrolled")
            assert e.activations == int(srow["activations"]) and e.days == int(srow["days"])
            # the same calls and accounts evaluate_policy builds internally
            if period == "in-sample":
                y, days, energy_on = 2025, years[2025], obl.energy_to_zenith
            elif period == "test":
                y, energy_on = 2026, obl.energy_to_zenith
                days = years[2026][m.days[years[2026]] >= pd.Timestamp("2026-07-01")]
            else:
                y, days, energy_on = int(period[2:]), years[int(period[2:])], False
            calls = ps.select_calls(days, fam, pol, rule, fc, scores, True, energy_on, m)
            season = y if y in ps.SEASONS else None
            acc = ps.account(calls, fam, pol, obl, CENTRAL, m, season, peaks)
            assert len(calls.day) == e.activations
            n = e.enrolled
            cap_full = A * ps.ircr_value(calls, fam, obl, m, season, peaks) if season is not None else 0.0
            w_all = A * float(fam.w_actual[calls.start, calls.day].sum())
            close(cap_full * obl.capacity_share * n, e.revenue["capacity"], 0.01, "capacity value")
            checks = [check_call(fam, pol, int(d), int(j)) for d, j in zip(calls.day, calls.start)]
            REPLAY[(offer, sc, period)] = {"e": e, "acc": acc, "calls": calls, "fam": fam, "pol": pol, "rule": rule,
                                           "fit": fit, "cap_full_pc": cap_full, "w_all_pc": w_all, "checks": checks}
        print(f"replayed {offer} {sc} ({time.time() - T0:.0f} s)", flush=True)

# ------------------------------------------------------------------------------------------------ offers
ref_row, cus_s2b, cus_s3 = saved("S2b", "B reference", "in-sample"), saved("S2b", "C optimised", "in-sample"), \
    saved("S3", "C optimised", "in-sample")
nominal_kwh = ps.Policy(reserve_soc=0.2).nominal_kwh(HH)
close(float(cus_s2b["policy_pay_per_event"]), round(0.85 * nominal_kwh, 4), 1e-9, "S2b per-event payment = 85c x nominal")
offers = [
    {
        "id": "ref", "name": "Reference terms (Synergy Battery Rewards V3)",
        "what_it_is": "Synergy's published household VPP contract, used only as a reference policy. NOT a Zenith tariff; "
                      "Zenith has not offered or approved any customer-battery tariff.",
        "pay_c_per_kwh": 70, "pay_basis": "event net export at the meter, capped at the battery's kWh per event",
        "max_calls_per_year": 30, "max_event_h": 4, "max_control_h": 6,
        "status": "researched",
        "status_note": "Contract terms (70c/kWh, at most 30 events per 12 months, event at most 4 h, control at most 6 h incl. "
                       "standby) are marked VERIFIED in POLICY_ASSUMPTIONS.md section 2 (V3 PD 4, PD 6(a), PD 6(c); checked "
                       "2026-10-24). This export did not re-read the contract, so they are 'researched' here.",
        "as_modelled": {"event_h": 2.0, "event_window": "17:30-19:30", "standby_h": 2.0, "standby_window": "15:30-17:30",
                        "reserve_soc_pct": 20, "offset_credit_c_per_kwh": 33.26, "status": "assumed",
                        "note": "modelling choices for the reference (Synergy states standby 'generally, approximately "
                                "2 hours'; the event length, window and 20% reserve are the study's settings)"},
        "call_rule_by_scenario": {
            "S2b": {"ircr_q": r(saved("S2b", "B reference", "in-sample")["rule_ircr_q"], 2),
                    "energy_calls_allowed_if_margin_aud_at_least": r(saved("S2b", "B reference", "in-sample")["rule_theta"], 2)},
            "S3": {"ircr_q": r(saved("S3", "B reference", "in-sample")["rule_ircr_q"], 2),
                   "energy_calls_allowed_if_margin_aud_at_least": None, "energy_calls": "never (energy value goes to Synergy)"}},
        "source": f"{REL_POL}/POLICY_ASSUMPTIONS.md section 2; {REL_POL}/policy_backtest.csv columns policy_*, rule_* "
                  "(case 'B reference')",
    },
    {
        "id": "custom", "name": "Model-suggested offer (policy C)",
        "what_it_is": "The policy study's optimised design. A model suggestion, NOT a contract or an approved tariff; the "
                      "payment level follows from an assumed participation curve and must come from a pilot.",
        "pay_c_per_kwh": 85,
        "pay_by_scenario": {
            "S2b": {"form": "per event", "pay_aud_per_event": r(cus_s2b["policy_pay_per_event"], 4),
                    "nominal": "85c x 10.25 kWh (what a full battery delivers to the 20% reserve)",
                    "effective_c_per_billed_kwh": None},
            "S3": {"form": "per kWh of event net export", "pay_c_per_kwh": r(100 * cus_s3["policy_pay_per_kwh"], 2)}},
        "max_calls_per_year": int(cus_s2b["policy_max_events"]), "max_event_h": 2.0, "max_control_h": 6,
        "as_modelled": {"event_h": 2.0, "event_window": "17:30-19:30", "standby_h": 2.0, "standby_window": "15:30-17:30",
                        "reserve_soc_pct": 20, "offset_credit_c_per_kwh": 33.26},
        "call_rule_by_scenario": {
            "S2b": {"ircr_q": r(cus_s2b["rule_ircr_q"], 2),
                    "energy_calls_allowed_if_margin_aud_at_least": r(cus_s2b["rule_theta"], 2)},
            "S3": {"ircr_q": r(cus_s3["rule_ircr_q"], 2), "energy_calls_allowed_if_margin_aud_at_least": None,
                   "energy_calls": "never (energy value goes to Synergy)"}},
        "status": "assumed",
        "status_note": "Every term is a model output under assumed participation, response (0.9), wear, hassle and "
                       "operating costs (POLICY_ASSUMPTIONS.md section 4). Robust across participation curves: peak-day "
                       "calls only, cap >= 45, 2-h events centred on 18:30, 20% reserve. Not robust: the payment level.",
        "source": f"{REL_POL}/RECOMMENDATION.md section 1 item 4 and section 3; {REL_POL}/policy_backtest.csv columns "
                  "policy_*, rule_* (case 'C optimised'); policy_refinement.csv",
    },
]

# ------------------------------------------------------------------------------------------------ per call
per_call_rows = []
for offer, case in CASES.items():
    for sc in OBL:
        for period in ("in-sample", "FY2024", "FY2023"):
            s = saved(sc, case, period)
            calls = int(s["activations"])
            responding = float(s["enrolled"]) * A
            q = float(s["energy_kwh"]) / (responding * calls)
            pay = float(s["compensation_pay_energy"]) + float(s["compensation_pay_event"])
            eff = 100 * pay / float(s["energy_kwh"])
            # cross-check with the kWh-value study's own table
            pol_key = "B" if offer == "ref" else f"C-{sc}"
            be = breakeven[(breakeven["policy"] == pol_key) & (breakeven["scenario"] == sc)]
            be = be[be["calls"] == calls]
            assert len(be) == 1, (offer, sc, period, len(be))
            close(be.iloc[0]["Q_sum_kwh_per_responding"] / calls, q, 1e-6, "billed kWh per call vs annual_breakeven")
            close(be.iloc[0]["paid_c_per_billed_kwh"], eff, 1e-6, "c per billed kWh vs annual_breakeven")
            per_call_rows.append({
                "offer": offer, "scenario": sc, "period": SAVED_PERIOD[period], "calls": calls,
                "billed_export_kwh_per_responding_home_per_call": r(q, 3),
                "payment_aud_per_responding_home_per_call": r(pay / (responding * calls), 3),
                "effective_c_per_billed_kwh": r(eff, 1),
                "offset_credit_aud_per_responding_home_per_call": r(float(s["compensation_offset"]) / (responding * calls), 3),
                "status": "derived"})
pc_main = next(x for x in per_call_rows if x["offer"] == "custom" and x["scenario"] == "S2b" and x["period"] is P_FY25)
offers[1]["pay_by_scenario"]["S2b"]["effective_c_per_billed_kwh"] = pc_main["effective_c_per_billed_kwh"]
eb = energy_balance[energy_balance["window"] == "17:30-19:30"].iloc[0]
per_call = {
    "billed_export_kwh": pc_main["billed_export_kwh_per_responding_home_per_call"],
    "effective_c_per_billed_kwh": pc_main["effective_c_per_billed_kwh"],
    "basis": "custom offer under S2b (paid $8.71 per event); per responding home per call",
    "period": P_FY25, "calls": pc_main["calls"],
    "nominal_kwh_per_event": r(nominal_kwh, 2),
    "why_not_85c": "The per-event payment is 85c x 10.25 kWh (a full battery to the 20% reserve), but the home uses part "
                   "of the discharge during the event, so the billed net export is about 7.5 kWh and the same payment is "
                   "about 116c per billed kWh.",
    "by_offer": per_call_rows,
    "event_energy_balance_candidate_call_mean": {
        "period": P_ALL, "basis": "per responding home, one candidate 17:30-19:30 call on every day (not real calls)",
        "available_above_reserve_kwh": r(eb["mean_event_avail_kwh"], 2), "event_pv_kwh": r(eb["mean_event_pv_kwh"], 2),
        "home_use_kwh": r(eb["mean_event_home_kwh"], 2), "meter_export_kwh": r(eb["mean_event_export_kwh"], 2),
        "discharge_loss_kwh": r(eb["mean_event_loss_kwh"], 2), "left_above_reserve_kwh": r(eb["mean_event_retained_kwh"], 2),
        "net_export_change_vs_no_call_kwh": r(eb["mean_dN_kwh"], 3), "status": "backtest",
        "source": f"{REL_KWH}/out/energy_balance.csv row window=17:30-19:30"},
    "status": "derived",
    "formula": "billed kWh per responding home per call = energy_kwh / (enrolled x 0.9 x calls); effective c per billed "
               "kWh = 100 x (compensation_pay_energy + compensation_pay_event) / energy_kwh (offset credits excluded, "
               "reported apart)",
    "source": f"{REL_POL}/policy_backtest.csv columns energy_kwh, compensation_pay_energy, compensation_pay_event, "
              f"compensation_offset, enrolled, activations; cross-checked with {REL_KWH}/out/annual_breakeven.csv "
              "(Q_sum_kwh_per_responding, paid_c_per_billed_kwh)",
}

# ------------------------------------------------------------------------------------------------ value by party
ITEMS = {  # id: (label, kind, payer, receiver by scenario)
    "wholesale": ("Wholesale value of the change in the meters' net export (standby charging and rebound included)",
                  "value_created", "WEM settlement", {"S2b": "Zenith", "S3": "Synergy (market participant for the meters)"}),
    "capacity": ("Capacity (IRCR) value: lower median meter import in the 12 Peak SWIS intervals x RCP", "value_created",
                 "capacity cost (IRCR) saved", {"S2b": "Zenith", "S3": "Synergy (holds the meters' capacity cost)"}),
    "capacity_share": ("Share of the capacity value passed to Zenith (s = 0.5, assumed TPA term)", "transfer",
                       "Synergy", {"S2b": None, "S3": "Zenith"}),
    "pay_energy": ("Customer payment per kWh of event net export", "transfer", "Zenith", {"S2b": "customers", "S3": "customers"}),
    "pay_event": ("Customer payment per event", "transfer", "Zenith", {"S2b": "customers", "S3": "customers"}),
    "annual_fee": ("Customer annual fee", "transfer", "Zenith", {"S2b": "customers", "S3": "customers"}),
    "offset": ("Standby offset credit (extra standby import at the retail tariff)", "transfer", "Zenith",
               {"S2b": "customers", "S3": "customers"}),
    "bill": ("Customer's retail bill change (extra imports at 33.26c less DEBS 10c/2c on exports)", "transfer", "customers",
             {"S2b": "retailer (Zenith's own retail arm; left out of Zenith's policy profit)", "S3": "retailer (Synergy)"}),
    "ops_event": ("Zenith operating cost per responding customer per event ($0.05, assumed)", "cost", "Zenith", None),
    "ops_customer": ("Zenith operating cost per enrolled customer ($25/yr, assumed)", "cost", "Zenith", None),
    "fixed": ("Programme fixed cost ($0 in the base case)", "cost", "Zenith", None),
    "wear": ("Customer battery wear ($0.05/kWh extra throughput, assumed)", "cost", "customers", None),
    "hassle": ("Customer disutility ($0.50 per event, assumed)", "cost", "customers", None),
}


def party_row(offer: str, sc: str, period: str) -> tuple[dict, list[dict]]:
    x = REPLAY[(offer, sc, period)]
    e, acc, obl = x["e"], x["acc"], OBL[sc]
    n = e.enrolled
    items = {
        "wholesale": n * x["w_all_pc"], "capacity": n * x["cap_full_pc"],
        "capacity_share": e.revenue["capacity"] if obl.capacity_share < 1 else 0.0,
        "pay_energy": e.compensation["pay_energy"], "pay_event": e.compensation["pay_event"],
        "annual_fee": e.compensation["annual_fee"], "offset": e.compensation["offset"],
        "bill": n * acc["bill"], "ops_event": e.costs["ops_event"], "ops_customer": e.costs["ops_customer"],
        "fixed": e.costs["fixed"], "wear": n * acc["wear"], "hassle": n * acc["hassle"],
    }
    transfers_to_customers = items["pay_energy"] + items["pay_event"] + items["annual_fee"] + items["offset"]
    zen = (items["wholesale"] if obl.energy_to_zenith else 0.0) + n * x["cap_full_pc"] * obl.capacity_share \
        - transfers_to_customers - items["ops_event"] - items["ops_customer"] - items["fixed"]
    cust = transfers_to_customers - items["bill"] - items["wear"] - items["hassle"]
    retailer = items["bill"]
    other = (0.0 if obl.energy_to_zenith else items["wholesale"]) + items["capacity"] - n * x["cap_full_pc"] * obl.capacity_share
    created = items["wholesale"] + items["capacity"] - items["ops_event"] - items["ops_customer"] - items["fixed"] \
        - items["wear"] - items["hassle"]
    close(zen, e.parties["zenith"], 0.01, "zenith ledger")
    close(cust, e.parties["customers"], 0.01, "customer ledger")
    close(retailer + other, e.parties["retailer"], 0.01, "retailer + other = model retailer column")
    close(created, e.parties["system"], 0.01, "value created = model system value")
    close(zen + cust + retailer + other, created, 0.01, "parties sum to value created")
    per = SAVED_PERIOD[period]
    row = {
        "offer": offer, "scenario": sc, "period": per, "calls": e.activations,
        "zenith_aud": r(zen), "customers_aud": r(cust), "retailer_aud": r(retailer), "other_aud": r(other),
        "value_created_aud": r(created), "transfers_aud": r(transfers_to_customers),
        "transfers_detail_aud": {"zenith_to_customers": r(transfers_to_customers), "customers_to_retailer_bill": r(items["bill"]),
                                 "synergy_to_zenith_capacity_share": r(items["capacity_share"])},
        "per_home_basis": "enrolled", "homes": r(n, 1), "homes_status": "assumed",
        "responding_homes_per_call": r(n * A, 1),
        "zenith_aud_per_enrolled_home": r(zen / n), "customers_aud_per_enrolled_home": r(cust / n),
        "peak_days_covered": (None if period == "test" else int(next(iter(e.peak_days_covered.values())))),
        "status": "backtest",
    }
    lines = []
    for k, (label, kind, payer, recv) in ITEMS.items():
        if k == "capacity_share" and obl.capacity_share >= 1:
            continue
        lines.append({"offer": offer, "scenario": sc, "period": per, "item": k, "kind": kind,
                      "payer": payer, "receiver": recv.get(sc) if recv else None, "aud": r(items[k])})
    return row, lines


vbp_rows, ledger = [], []
for offer in CASES:
    for sc in OBL:
        for period in ("in-sample", "FY2024", "FY2023"):
            row, lines = party_row(offer, sc, period)
            vbp_rows.append(row)
            ledger += lines
        # test: 0 calls in 2026-07-01..2026-10-22; only the annual operating cost, pro rata. Split by segment.
        x = REPLAY[(offer, sc, "test")]
        e = x["e"]
        assert e.activations == 0
        n = e.enrolled
        parts = []
        for per in (P_TS, P_TU):
            ops = OBL[sc].ops_per_customer * per["days"] / 365 * n
            parts.append(-ops)
            vbp_rows.append({
                "offer": offer, "scenario": sc, "period": per, "calls": 0,
                "zenith_aud": r(-ops), "customers_aud": 0.0, "retailer_aud": 0.0, "other_aud": 0.0,
                "value_created_aud": r(-ops), "transfers_aud": 0.0,
                "transfers_detail_aud": {"zenith_to_customers": 0.0, "customers_to_retailer_bill": 0.0,
                                         "synergy_to_zenith_capacity_share": 0.0},
                "per_home_basis": "enrolled", "homes": r(n, 1), "homes_status": "assumed",
                "responding_homes_per_call": None,
                "zenith_aud_per_enrolled_home": r(-ops / n), "customers_aud_per_enrolled_home": 0.0,
                "peak_days_covered": None,
                "note": "No Hot Season, no calls: only Zenith's $25 per enrolled customer per year operating cost, pro rata. "
                        "The capacity value cannot be tested here; the first test is the 2026-27 Hot Season "
                        "(2026-12-01 to 2027-03-31).",
                "status": "derived",
                "formula": "zenith_aud = -25 x days / 365 x enrolled; the two segments sum to the saved test row "
                           f"({REL_POL}/policy_backtest.csv period=test, 114 days, activations 0)"})
            ledger.append({"offer": offer, "scenario": sc, "period": per, "item": "ops_customer", "kind": "cost",
                           "payer": "Zenith", "receiver": None, "aud": r(ops)})
        close(sum(parts), e.zenith_profit, 0.01, "test segments sum to the saved test total")

expected_rows = []
for offer, case in CASES.items():
    for sc, obl in OBL.items():
        s = saved(sc, case, "in-sample")
        n = float(s["enrolled"])
        fleet_exp = n * float(s["fit_zen"])
        if offer == "custom":
            close(fleet_exp, refinement.loc[refinement["scenario"] == sc, "grid_profit"].iloc[0], 0.01, "expected fleet profit")
        expected_rows.append({
            "offer": offer, "scenario": sc, "period": P_EXP,
            "zenith_aud": r(fleet_exp), "zenith_aud_per_enrolled_home": r(s["fit_zen"]),
            "customers_aud": r(n * float(s["fit_cust"])), "customers_aud_per_enrolled_home": r(s["fit_cust"]),
            "zenith_risk_adjusted_aud": r(n * float(s["fit_zen_risk"])),
            "retailer_aud": None, "other_aud": None, "value_created_aud": None, "transfers_aud": None,
            "per_home_basis": "enrolled", "homes": r(n, 1), "homes_status": "assumed",
            "status": "backtest",
            "source": f"{REL_POL}/policy_backtest.csv columns fit_zen, fit_cust, fit_zen_risk, enrolled (in-sample row); "
                      f"custom also equals {REL_POL}/policy_refinement.csv grid_profit"})
gap("value_by_party.expected_year[].retailer_aud, other_aud, value_created_aud, transfers_aud",
    "The policy study's expected year (Fit in policy_simulator.rule_fits) keeps only Zenith and customer totals; it does "
    "not split the retailer, other holders or transfers. The per-season rows above have the full split.")

value_by_party = {
    "rows": vbp_rows,
    "expected_year": expected_rows,
    "ledger_lines": ledger,
    "ledger_items": {k: {"label": label, "kind": kind, "payer": payer, "receiver_by_scenario": recv}
                     for k, (label, kind, payer, recv) in ITEMS.items()},
    "sign_convention": {"value_created": "aud = value created (may be negative), received by 'receiver'",
                        "transfer": "aud = amount the payer pays the receiver; creates no value",
                        "cost": "aud = cost borne by the payer; reduces value created"},
    "party_notes": {
        "S2b": {"retailer_aud": "Zenith's own retail arm: the customers' extra retail bill. Its revenue must also pay "
                                "network charges and fees that are not modelled, so it is kept out of Zenith's policy "
                                "profit (zenith_aud) - conservative.",
                "other_aud": "0 by construction: energy and the full capacity value go to Zenith in S2b."},
        "S3": {"retailer_aud": "Synergy as the households' retailer: the customers' extra retail bill.",
               "other_aud": "Synergy as market participant and capacity-cost holder: the wholesale value of the change in "
                            "net export plus the half of the capacity value it does not pass to Zenith."}},
    "how_to_read": "zenith_aud + customers_aud + retailer_aud + other_aud = value_created_aud for every row. transfers_aud "
                   "is what Zenith pays customers (payments, fees, offset credits): a transfer, it creates no value. The "
                   "customers' bill change (customers -> retailer) and, in S3, the capacity share (Synergy -> Zenith) are "
                   "transfers too (transfers_detail_aud). Every figure is relative to the same batteries in normal "
                   "self-consumption with no call. Zenith's own 10 MW / 40 MWh site battery is not in these rows and "
                   "must not be added to them here.",
    "scenarios": {
        "S2b": "Zenith as retailer of contestable customers under the 2027 Peak IRCR (net export counts negative; "
               "companion text, gazetted rules not checked; modelled with the old 4 days x 3 intervals definition). "
               "Energy and the full capacity value go to Zenith.",
        "S3": "Zenith as a Synergy Third Party Aggregator. Energy value stays with Synergy; Zenith receives a share "
              "s = 0.5 of the capacity value (assumed: no TPA price exists; break-even s about 31% on the expected year)."},
    "enrolment_note": "Enrolled homes come from an assumed participation curve (10,000 households offered; half of the "
                      "maximum 50% enrol at $100/yr expected benefit) and are fractional model outputs, not measured. "
                      "0.9 of enrolled batteries are assumed to respond to each call.",
    "status": "backtest",
    "source": "vpp/policy/policy_simulator.py evaluate_policy() parties, account(), ircr_value() replayed read-only with "
              f"the saved terms and rules; zenith_aud and customers_aud equal {REL_POL}/policy_backtest.csv zenith_profit "
              "and customer_benefit x enrolled (cases 'B reference', 'C optimised'); retailer_aud + other_aud = the "
              "model's 'retailer' party; the four parties sum to the model's 'system' value",
}

# ------------------------------------------------------------------------------------------------ IRCR
si = season_ircr[(season_ircr["window"] == "17:30-19:30") & (season_ircr["scenario"] == "S2b")
                 & season_ircr["method"].isin(["B", "C"]) & season_ircr["season"].isin(["2023-24", "2024-25", "2025-26"])]
ircr_seasons = []
for _, x in si.iterrows():
    y = int(x["season"][:4])
    close(x["rcp_A$_per_MW_yr"], ct.RCP[y + 1], 1e-6, "RCP as-if convention")
    ircr_seasons.append({"offer": "ref" if x["method"] == "B" else "custom", "season": x["season"], "calls": int(x["calls"]),
                         "kw_per_responding_home": r(x["ircr_kw_reduction"], 3),
                         "price_aud_per_mw_yr": r(x["rcp_A$_per_MW_yr"], 2), "price_capacity_year": f"{y + 1}-{(y + 2) % 100:02d}",
                         "value_aud_per_responding_home_full": r(x["value_full_A$"], 2),
                         "billed_export_kwh_per_responding_home_season": r(x["Q_sum_kwh"], 1)})
c25 = si[(si["method"] == "C") & (si["season"] == "2025-26")].iloc[0]
# the same kW from the replay
x = REPLAY[("custom", "S2b", "in-sample")]
close(x["cap_full_pc"] / A, c25["value_full_A$"], 1e-6, "IRCR value per responding home")
ircr = {
    "kw_per_responding_home": r(c25["ircr_kw_reduction"], 2), "season": "2025-26", "offer": "custom",
    "price_aud_per_mw_yr": r(c25["rcp_A$_per_MW_yr"], 2), "price_capacity_year": "2026-27",
    "value_aud_per_responding_home": r(c25["value_full_A$"], 2),
    "forward_price_aud_per_mw_yr": r(ct.RCP[2027], 2), "forward_price_capacity_year": "2027-28",
    "period": P_HS25,
    "basis": "lower median net import over the season's 12 Peak SWIS intervals (AEMO's published intervals for 2025-26; "
             "old 4 days x 3 intervals definition), per responding home, net export counted negative (S2b; S3 the same kW, "
             "Zenith gets half the value)",
    "price_status": "researched",
    "price_note": "Reserve Capacity Prices from vpp/contracts.py RCP (marked VERIFIED, AEMO/ERA, in POLICY_ASSUMPTIONS.md "
                  "section 4; not re-read here). Valuing the 2025-26 season at the 2026-27 price is an assumed 'as if' "
                  "convention; a programme starting now first affects capacity year 2027-28 ($360,700/MW-yr, about 1.7x).",
    "ratio_note": "IRCR scaling ratio 1.0 assumed for homes (the factory's ratio is 1.57; not checked for residential load).",
    "by_season": ircr_seasons,
    "status": "backtest",
    "source": f"{REL_KWH}/out/season_ircr.csv (scenario S2b, window 17:30-19:30, methods B and C), equal to "
              "vpp/policy/policy_simulator.py ircr_value() in the replay; vpp/contracts.py RCP",
}

# ------------------------------------------------------------------------------------------------ season calls
df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["req_energy_mw"])
actual = sb.daily_max(df30["req_energy_mw"])
p0, p1 = sb.peak_day_predictor(0), sb.peak_day_predictor(1)
TRAIN_SEASONS = [2023, 2024]   # for 2025-26 the tolerance uses the two earlier seasons (chronological)


def season_table(pred: pd.Series, lag: int, tol: float) -> pd.DataFrame:
    """policy_simulator.ircr_flags -> site_bill.dynamic_flags for the 2025-26 season, keeping the threshold."""
    df = pd.concat({"f": pred, "a": actual}, axis=1)
    df = df[sb.hot_season(df.index) >= 0]
    rows = []
    for yr, g in df.groupby(sb.hot_season(df.index)):
        if yr != 2025:
            continue
        for i, d in enumerate(g.index):
            known = g["a"].iloc[:max(0, i - (lag - 1))].dropna()
            kth = known.nlargest(4).iloc[-1] if len(known) >= 4 else -np.inf
            f = g.loc[d, "f"]
            rows.append({"day": d, "f": f, "kth": kth, "thr": kth - tol, "flag": bool(pd.notna(f) and f >= kth - tol)})
    return pd.DataFrame(rows).set_index("day")


Q = {"custom": 0.75, "ref_s2b": 0.9}
tol = {name: (sb.forecast_tolerance(p0, actual, TRAIN_SEASONS, q), sb.forecast_tolerance(p1, actual, TRAIN_SEASONS, q))
       for name, q in Q.items()}
tabs = {name: (season_table(p0, 1, tol[name][0]), season_table(p1, 2, tol[name][1])) for name in Q}
hs_idx = np.flatnonzero((m.days >= "2025-12-01") & (m.days <= "2026-03-31"))
assert len(hs_idx) == 121
for name, q in Q.items():
    t0, t1 = tabs[name]
    mine = np.array([bool(t0["flag"].get(m.days[i], False) or t1["flag"].get(m.days[i], False)) for i in hs_idx])
    assert (mine == fc.flags[q][hs_idx]).all(), f"flag replay differs for q={q}"
called = {}
for offer in CASES:
    for sc in OBL:
        c = REPLAY[(offer, sc, "in-sample")]["calls"]
        called[(offer, sc)] = set(int(d) for d in c.day)
        assert all(fc.flags[REPLAY[(offer, sc, "in-sample")]["rule"].ircr_q][d] for d in c.day)
        assert all(ps.season_of(m)[d] == 2025 for d in c.day), "every 2025-26 contract-year call is in the Hot Season"
peak_days = sorted({pd.Timestamp(t[:10]) for t in sb.OFFICIAL_PEAK_INTERVALS[2025]})
peak_slots = {}
for t in sb.OFFICIAL_PEAK_INTERVALS[2025]:
    peak_slots.setdefault(pd.Timestamp(t[:10]), []).append(t[11:])
season_actual = actual[(actual.index >= "2025-12-01") & (actual.index <= "2026-03-31")]
rank = season_actual.rank(ascending=False, method="min")
days_out = []
used = {k: 0 for k in called}
caps = {(o, s): REPLAY[(o, s, "in-sample")]["pol"].max_events for o in CASES for s in OBL}
missing_fc = []
for i in hs_idx:
    d = m.days[i]
    a0, a1 = tabs["custom"]
    r0, r1 = tabs["ref_s2b"]
    f0, f1 = a0["f"].get(d, np.nan), a1["f"].get(d, np.nan)
    if pd.isna(f0):
        missing_fc.append(str(d.date()))
    left = {k: caps[k] - used[k] for k in called}
    rec = {
        "day": str(d.date()),
        "forecast_peak_mw": r(f0, 1),
        "threshold_mw": r(a0["thr"].get(d, np.nan), 1),
        "forecast_dayahead_mw": r(f1, 1),
        "threshold_dayahead_mw": r(a1["thr"].get(d, np.nan), 1),
        "threshold_ref_s2b_mw": r(r0["thr"].get(d, np.nan), 1),
        "threshold_ref_s2b_dayahead_mw": r(r1["thr"].get(d, np.nan), 1),
        "auto_flag_first_days": bool((np.isinf(a0["kth"].get(d, 0.0)) and pd.notna(f0))
                                     or (np.isinf(a1["kth"].get(d, 0.0)) and pd.notna(f1))),
        "flag_route_custom": [n for n, t in (("same_day", a0), ("day_ahead", a1)) if bool(t["flag"].get(d, False))],
        "flag_route_ref_s2b": [n for n, t in (("same_day", r0), ("day_ahead", r1)) if bool(t["flag"].get(d, False))],
        "flagged_custom": bool(fc.flags[0.75][i]), "flagged_ref_s2b": bool(fc.flags[0.9][i]),
        "called_ref": i in called[("ref", "S2b")], "called_custom": i in called[("custom", "S2b")],
        "called_ref_s3": i in called[("ref", "S3")], "called_custom_s3": i in called[("custom", "S3")],
        "calls_left_before_ref": left[("ref", "S2b")], "calls_left_before_custom": left[("custom", "S2b")],
        "actual_peak_mw": r(season_actual.get(d, np.nan), 1),
        "actual_rank_in_season": (int(rank[d]) if d in rank.index and pd.notna(rank[d]) else None),
        "ircr_peak_day": d in peak_days,
        "peak_intervals": peak_slots.get(d, []),
    }
    for k in called:
        used[k] += int(i in called[k])
    days_out.append(rec)
counts = {f"{o}_{s}": len(called[(o, s)]) for (o, s) in called}
for (o, s), k in called.items():
    assert len(k) == int(saved(s, CASES[o], "in-sample")["activations"])
peak_cov = {f"{o}_{s}": sum(1 for d in peak_days if m.days.get_loc(d) in called[(o, s)]) for (o, s) in called}
for (o, s) in called:
    assert peak_cov[f"{o}_{s}"] == int(saved(s, CASES[o], "in-sample")["covered_2025"])
missed = {f"{o}_{s}": [str(d.date()) for d in peak_days if m.days.get_loc(d) not in called[(o, s)]] for (o, s) in called}
if missing_fc:
    gap("season_calls.days[].forecast_peak_mw", f"no same-day 09:00 forecast on {missing_fc}; the day-ahead forecast "
        "alone decides the flag there")
season_calls = {
    "period": P_HS25,
    "days": days_out,
    "summary": {"calls": counts, "flags": {"custom_q0.75": int(fc.flags[0.75][hs_idx].sum()),
                                           "ref_s2b_q0.9": int(fc.flags[0.9][hs_idx].sum())},
                "official_peak_days": [str(d.date()) for d in peak_days], "peak_days_covered": peak_cov,
                "peak_days_missed": missed,
                "tolerance_mw": {"custom_q0.75": {"same_day": r(tol["custom"][0], 1), "day_ahead": r(tol["custom"][1], 1)},
                                 "ref_s2b_q0.9": {"same_day": r(tol["ref_s2b"][0], 1), "day_ahead": r(tol["ref_s2b"][1], 1)}}},
    "rule": "At 09:00 on each Hot Season day, AEMO's forecast of the day's highest energy requirement (the pre-dispatch run "
            "public by 09:00 that day) is compared with the 4th-highest actual daily maximum of the season so far, less a "
            "tolerance learnt from the 2023-24 and 2024-25 Hot Seasons (the q-quantile of how far the forecast fell short). "
            "The forecast made at 09:00 the day before is checked the same way against what was known then. If either "
            "reaches its threshold, the day is flagged as a possible system peak day and the offer calls a 2-hour event "
            "17:30-19:30 (standby 15:30-17:30) while calls remain under its yearly cap. Until four days of the season are "
            "known every day is flagged (threshold null, auto_flag_first_days true). The custom offer uses q = 0.75; the "
            "reference terms use q = 0.9 under S2b and q = 0.75 under S3 (each policy's rule was fitted to it on data up "
            "to 2026-06-30). The rule never calls on price, and the supply-margin forecast is not used (it failed its "
            "pre-set test). Calls are decided at 09:00 with no look-ahead; actual_peak_mw, actual_rank_in_season and "
            "ircr_peak_day are known only afterwards.",
    "fields": {"forecast_peak_mw / threshold_mw": "same-day forecast and the custom offer's threshold (q = 0.75), MW",
               "forecast_dayahead_mw / threshold_dayahead_mw": "forecast public at 09:00 the day before and its threshold "
                                                               "(q = 0.75)",
               "threshold_ref_s2b_*": "the reference terms' thresholds under S2b (q = 0.9); under S3 the reference uses "
                                      "the custom thresholds",
               "called_ref / called_custom": "S2b; called_ref_s3 / called_custom_s3: S3",
               "auto_flag_first_days": "a route had fewer than four season days known, so it flagged the day "
                                       "automatically (its threshold is null)",
               "flag_route_*": "which forecast reached its threshold: same_day, day_ahead (empty = not flagged)",
               "calls_left_before_*": "calls left in the contract-year cap before the day (S2b)",
               "actual_peak_mw": "actual daily maximum energy requirement (the quantity the rule forecasts), MW"},
    "status": "backtest",
    "source": "Re-run (read-only) of vpp/policy/policy_simulator.py ircr_flags() -> vpp/site_bill.py dynamic_flags(), "
              "forecast_tolerance(), peak_day_predictor() (data/processed/spike_table.parquet pd_day_req_max, days_ahead "
              "0 and 1), daily_max(wem_30min.parquet req_energy_mw), and select_calls() with the saved rules; flags equal "
              "load_forecasts().flags, call counts and peak-day coverage equal reports/policy/policy_backtest.csv. "
              "No saved per-day file existed (customer_kwh_value/out/daily.csv has the custom offer's calls only, without "
              "forecast or threshold).",
}

# ------------------------------------------------------------------------------------------------ validation
holdouts = []
for _, h in loso.iterrows():
    y = int(h["held_out"][:4])
    holdouts.append({
        "scenario": h["scenario"], "held_out_season": h["held_out"], "chronological": bool(h["chronological"]),
        "counts_as_forward_validation": bool(h["chronological"]),
        "case": "custom (chosen without the held-out season)" if h["case"].startswith("C") else "ref",
        "chosen_terms": {"pay_c_per_kwh_or_equivalent": r(100 * h["pay"], 1), "form": h["comp"], "cap": int(h["cap"]),
                         "event_h": r(h["event_h"], 1), "standby_h": r(h["standby_h"], 1),
                         "reserve_pct": r(100 * h["reserve"], 0), "ircr_q": r(h["ircr_q"], 2)},
        "enrolled_homes": r(h["enrolled"], 1), "homes_status": "assumed",
        "zenith_expected_from_training_aud_per_year": r(h["expected_profit_from_training"]),
        "zenith_realised_held_out_aud": r(h["held_out_profit"]),
        "customers_realised_held_out_aud_per_enrolled_home": r(h["held_out_customer_benefit"]),
        "peak_days_covered_of_4": int(h["covered_held_out"]), "feasible_on_training": bool(h["feasible_on_training"]),
        "period": {"label": "in-sample", "segment": f"Hot Season {h['held_out']} held out (as-if contract year: 2025-26 "
                   f"energy part + the held-out season's capacity part, full-year operating cost)",
                   "from": f"{y}-12-01", "to": f"{y + 1}-03-31", "days": int((pd.Timestamp(f'{y + 1}-03-31') - pd.Timestamp(f'{y}-12-01')).days) + 1},
        "status": "backtest",
    })
constraints = []
SYN = "Synergy Battery Rewards V3 (reference terms, researched)"
for offer in CASES:
    for sc in OBL:
        for period, per in (("in-sample", P_FY25), ("FY2024", P_FY24), ("FY2023", P_FY23), ("test", P_TS),
                            ("test", P_TU)):
            x = REPLAY[(offer, sc, period)]
            e, pol, ch = x["e"], x["pol"], x["checks"]
            assert period != "test" or e.activations == 0   # no calls in either test segment
            min_soc = min((c["min_event_soc_kwh"] for c in ch), default=np.nan)
            max_kw = max((c["max_kw"] for c in ch), default=np.nan)
            base = {"offer": offer, "scenario": sc, "period": per, "calls": e.activations}
            constraints += [
                {**base, "id": "activation_cap", "label": f"Calls per contract year <= {pol.max_events}",
                 "violations": int(e.violations["cap"]), "worst": f"{e.activations} of {pol.max_events} used",
                 "limit_source": SYN if offer == "ref" else "model suggestion (45)"},
                {**base, "id": "control_time", "label": "Standby + event <= 6 h per call",
                 "violations": int(e.violations["control"]),
                 "worst": f"{pol.standby_h + pol.event_h:g} h per call" if e.activations else "no calls",
                 "limit_source": SYN + "; event length <= 4 h (2 h used)"},
                {**base, "id": "customer_reserve", "label": f"Battery never below the {pol.reserve_soc:.0%} reserve in an event",
                 "violations": int(sum(c["reserve"] for c in ch)),
                 "worst": (f"lowest event state of charge {min_soc:.2f} kWh = {100 * min_soc / HH.usable_kwh:.1f}% of 13.5 kWh"
                           if ch else "no calls"),
                 "limit_source": "policy term (Synergy: customer may set a reserved minimum)"},
                {**base, "id": "battery_power", "label": "Charge and discharge <= 5 kW (inverter)",
                 "violations": int(sum(c["power"] for c in ch)),
                 "worst": f"{max_kw:.2f} kW" if ch else "no calls", "limit_source": "physics / Western Power export limit"},
                {**base, "id": "soc_bounds", "label": "State of charge within 0-13.5 kWh",
                 "violations": int(sum(c["soc_bounds"] for c in ch)), "worst": None if ch else "no calls",
                 "limit_source": "physics"},
                {**base, "id": "one_call_per_day", "label": "At most 1 call a day", "violations": int(e.violations["per_day"]),
                 "worst": None, "limit_source": "model setting"},
                {**base, "id": "event_window", "label": "Events inside 15:00-21:00", "violations": int(e.violations["window"]),
                 "worst": "17:30-19:30" if e.activations else "no calls", "limit_source": "model setting (Synergy's practice)"},
                {**base, "id": "decision_time", "label": "Standby starts after the 09:00 decision",
                 "violations": int(e.violations["notice"]), "worst": "standby from 15:30" if e.activations else "no calls",
                 "limit_source": "project protocol: decisions at 09:00 with public data only"},
            ]
for c in constraints:
    c["status"] = "backtest"
stress_rows = []
for _, s in stress[stress["period"] == "in-sample"].iterrows():
    stress_rows.append({"scenario": s["scenario"], "offer": "ref" if s["case"].startswith("B") else "custom",
                        "stress": s["stress"], "period": P_FY25, "enrolled_homes": r(s["enrolled"], 1),
                        "calls": int(s["activations"]), "zenith_aud": r(s["zenith_profit"]),
                        "customers_aud_per_enrolled_home": r(s["customer_benefit"]),
                        "peak_days_covered_of_4": r(s["peak_days_covered"], 0), "fails": bool(s["fails"]),
                        "status": "backtest"})
validation = {
    "holdouts": holdouts,
    "holdout_note": "The whole choice (rule per policy, then policy) was redone without one Hot Season and scored on it. "
                    "Only holding out 2025-26 is chronological (chosen on 2023-24 and 2024-25 only); holding out an "
                    "earlier season uses a later one in the choice and is not forward validation. Realised held-out "
                    "figures carry no programme fixed cost.",
    "constraints": constraints,
    "constraints_note": "Zero violations is a property of the model's own dispatch, not a field measurement. The test "
                        "period (2026-07-01 to 2026-10-22) has no calls, so its constraint rows are trivially zero.",
    "constraints_source": "vpp/policy/policy_simulator.py evaluate_policy() violations (cap, per_day, window, notice, "
                          "control); reserve / power / state-of-charge split by re-stepping each call exactly as family() "
                          "(analysis/2026-10-26/ui_redesign/export/fleet_export.py check_call), asserted to sum to "
                          "Family.violations",
    "stress": stress_rows,
    "stress_note": "Fails = Zenith loses money, customers are worse off, or fewer than 3 of the season's 4 peak days are "
                   "called (contract year 2025-26).",
    "s3_break_even_share": {"rows": [{"share": r(x["share"], 2), "zenith_aud_per_year": r(x["fleet_profit"]),
                                      "passes_downside_test": bool(x["feasible"])} for _, x in s3share.iterrows()],
                            "period": P_EXP, "status": "backtest",
                            "note": "Expected-year fleet profit of the custom offer under S3 at each assumed capacity "
                                    "share; break-even between 0.30 and 0.35 (about 31%), downside test passed from 0.50. "
                                    "Grid of 0.05 steps, rule not refitted.",
                            "source": f"{REL_POL}/policy_s3_share.csv"},
    "call_signal_tests": [
        {"name": "Supply-margin forecast (M1 structural, M2 statistical) as a call trigger",
         "result": "failed its pre-set test; not used. Calls stay demand-driven (the peak-day rule above).",
         "detail": "Recall of the 46 tightest days: M1 and M2 29 (63.0%), demand-only baseline B0 15 (32.6%); gain "
                   "+30.4 percentage points, 95% month-block interval -15.0 to +60.0 (lower bound <= 0 = fail). In the "
                   "Hot Season B0 caught all 4 tight days, M1 1.",
         "period": {"label": "in-sample", "from": "2025-04-01", "to": "2026-06-30", "days": 456},
         "status": "backtest", "source": "analysis/2026-10-26/tightness_forecast/RESULTS.md lines 9-12, 48-52, 60-65"},
        {"name": "Peak-day probability model M3 (about 17 calls a season)",
         "result": "failed its pre-set call test J2 (30-call budget): covered 3 of 4 peak days in 2024-25 against 4 for "
                   "the current rule; not adopted, shadow-run only in 2026-27.",
         "period": {"label": "in-sample", "segment": "Hot Seasons 2024-25 (2024-12-01 to 2025-03-31) and 2025-26 "
                    "(2025-12-01 to 2026-03-31); days = the two seasons only", "from": "2024-12-01",
                    "to": "2026-03-31", "days": 242},
         "status": "backtest", "source": "analysis/2026-10-24/peak_day_probability/RESULTS.md lines 10-12"},
    ],
}

# ------------------------------------------------------------------------------------------------ do not use
do_not_use = [
    {"number": "315c/kWh (S2b) and 158c/kWh (S3): '09:00' IRCR value per billed kWh on flagged days; and the derived "
               "'Zenith can pay at most' 319c (S2b) / 157c (S3) on flagged days",
     "why": "hindsight: pooled over three Hot Seasons including 2025-26's own peak intervals, and the 2023-24 / 2024-25 "
            "call days come from tolerances fitted on later seasons; labelled '09:00 ex-ante' but it is not forward-looking",
     "source": f"{REL_KWH}/REVIEW.md:80-90 (numbers at {REL_KWH}/RESULTS.md:67-72 and 123-129; NUMBERS.csv:22-24)"},
    {"number": "Test-period 09:00 values of the kWh-value study (customer minimum 23.1c/23.4c, energy value 3.10c/3.01c, "
               "S1 Zenith maximum -2.65c/+1.73c, 09:00 bias +1.4c / +2.5c)",
     "why": "leak: load_market() normalises household load with calendar-month medians over all data (incl. 2026-07..10) "
            "and rooftop PV with a centred one-year maximum, so 09:00 values use test-period data; about 1c/kWh in the test "
            "period (e.g. bias +1.4c -> about +1.05c). Conclusions unchanged.",
     "source": f"{REL_KWH}/REVIEW.md:59-78 (numbers at {REL_KWH}/NUMBERS.csv:115-123, RESULTS.md:98-102 and 174-176)"},
    {"number": "8.13 kWh expected billed export per call on flagged days (09:00, same-month mean)",
     "why": "mixed basis: the realised mean on those days is 7.50 kWh; the 09:00 per-kWh figure mixes an expected "
            "denominator with realised capacity amortisation",
     "source": f"{REL_KWH}/REVIEW.md:118-125 (NUMBERS.csv:12)"},
    {"number": "'About 17 calls a season' from the peak-day probability model (M3)",
     "why": "failed its pre-set adoption test (J2); not an adopted call budget",
     "source": "analysis/2026-10-24/peak_day_probability/RESULTS.md:10-12"},
    {"number": "Supply-margin forecast recall 63.0% vs 32.6% (M1/M2 vs demand-only)",
     "why": "failed its pre-set test (interval crosses 0); not a call signal",
     "source": "analysis/2026-10-26/tightness_forecast/RESULTS.md:9-10, 51-52"},
]

# ------------------------------------------------------------------------------------------------ inconsistencies
inconsistencies = [
    {"topic": "85c/kWh vs $8.70 per event",
     "detail": "RECOMMENDATION.md:46 says the payment is 'about 85c per kWh of event net export, or the same money as "
               "about $8.70 per event'. It is not the same money: the per-event form pays for the nominal 10.25 kWh but "
               f"billed export averages {pc_main['billed_export_kwh_per_responding_home_per_call']} kWh, i.e. about "
               f"{pc_main['effective_c_per_billed_kwh']}c per billed kWh (customer_kwh_value RESULTS.md:151). S2b's custom "
               "offer uses the per-event form; S3's uses 85c/kWh.",
     "sources": [f"{REL_POL}/RECOMMENDATION.md:46", f"{REL_KWH}/RESULTS.md:140,151"]},
    {"topic": "Two different 'a year' profits",
     "detail": "RECOMMENDATION.md:49-50 gives $0.55M (S3) and $2.08M (S2b) 'a year': these are the single contract year "
               "2025-26 backtest. Section 3 gives $0.41M and $1.88M as 'best expected fleet profit': the expected year "
               "over three Hot Seasons. Customers: $94 / $145 per enrolled home in 2025-26 vs $126 / $185 expected. "
               "Show them with their periods; do not mix.",
     "sources": [f"{REL_POL}/RECOMMENDATION.md:48-50,121-124", "analysis/2026-10-25/customer_profit_verify/RESULTS.md:9-16"]},
    {"topic": "Reference call counts depend on the scenario",
     "detail": "30 / 28 / 27 calls (2023-24 / 2024-25 / 2025-26) hold for the reference under S2b (rule q = 0.9). Under "
               "S3 the reference's own fitted rule is q = 0.75 and it calls 30 / 25 / 24 - in 2025-26 the same 24 days "
               "as the custom offer. The custom offer calls 42 / 25 / 24 in both scenarios.",
     "sources": [f"{REL_POL}/policy_backtest.csv (activations, rule_ircr_q)", f"{REL_KWH}/RESULTS.md:118-126",
                 f"{REL_KWH}/out/season_ircr.csv"]},
    {"topic": "Custom offer covered 3 of 4 peak days in 2025-26",
     "detail": f"The custom offer's 24 calls in 2025-26 missed {', '.join(missed['custom_S2b'])} (q = 0.75 did not flag "
               "it: same-day forecast 3,688 MW against a 3,731 MW threshold); the "
               "reference under S2b (q = 0.9, 27 calls) covered all 4. The IRCR reduction is still 3.41 kW per responding "
               "home (median of 12 intervals) against 3.42 kW for the reference. RECOMMENDATION.md's '4 of 4' for the "
               "custom offer refers to the 2023-24 season only. The missed day, 2026-03-18, is also the day Synergy made "
               "its one real activation of the season.",
     "sources": [f"{REL_POL}/policy_backtest.csv covered_2025", f"{REL_POL}/RECOMMENDATION.md:177,181",
                 f"{REL_POL}/POLICY_ASSUMPTIONS.md:64"]},
    {"topic": "Holdout vs backtest for the reference in 2023-24",
     "detail": "The reference's 2023-24 result is -$0.28M (S2b) in the leave-one-season-out table but -$311,754 in the "
               "backtest: the holdout re-derives enrolment without 2023-24 (1,899 vs 2,234 homes) and charges a full "
               "year's operating cost, the backtest 274/365. Different set-ups, not an error.",
     "sources": [f"{REL_POL}/policy_loso.csv", f"{REL_POL}/policy_backtest.csv period FY2023"]},
]

# ------------------------------------------------------------------------------------------------ gaps
gap("season_calls.days[] per-call value at 09:00",
    "The model gives no forward-looking per-call capacity value at 09:00: the IRCR value is a season-level median over "
    "12 intervals, known only after the season. The kWh-value study's 315c/158c is hindsight (do_not_use).")
gap("value_by_party: network charges on the extra imports",
    "Not modelled; the retailer column (S2b: Zenith's retail arm) is gross of network charges and fees.")
gap("value_by_party: network support, Supplementary Capacity, reserve capacity credits, FCESS",
    "Not modelled or restricted (FCESS 0; no credits before 2029-30; SCM none procured for 2026-27; network-support "
    "prices not found) per POLICY_ASSUMPTIONS.md section 3.")
gap("value_by_party: test period capacity value",
    "The test period (2026-07-01 to 2026-10-22) has no Hot Season; the capacity value is untested out of sample until "
    "the 2026-27 Hot Season (2026-12-01 to 2027-03-31).")
gap("validation: correlated failure on peak days",
    "A correlated response failure on a peak day (e.g. a communications outage) is not modelled; only an average "
    "response of 0.9 (and a 0.5 stress) is.")
gap("all household figures",
    "Synthetic household (13.5 kWh, 5 kW, 6.6 kWp PV, 18 kWh/day), one archetype; no household meter data. load_market() "
    "also normalises household load and PV with data after 2026-06-30 (customer_kwh_value REVIEW.md:59-78); its effect "
    "on the fleet backtest was not measured.")
gap("ircr: 2027 Peak IRCR definition",
    "Modelled with the old 4 days x 3 intervals definition; the 2027 rule picks the 12 highest-generation intervals over "
    "at least 3 days and adds a Flexible IRCR. Gazetted text not checked.")

sources = {
    "policy_backtest": f"{REL_POL}/policy_backtest.csv (cases 'B reference', 'C optimised'; periods in-sample, FY2024, FY2023, test)",
    "policy_loso": f"{REL_POL}/policy_loso.csv",
    "policy_stress": f"{REL_POL}/policy_stress.csv (period in-sample)",
    "policy_s3_share": f"{REL_POL}/policy_s3_share.csv",
    "policy_refinement": f"{REL_POL}/policy_refinement.csv",
    "recommendation": f"{REL_POL}/RECOMMENDATION.md",
    "assumptions": f"{REL_POL}/POLICY_ASSUMPTIONS.md (sections 2, 4, 8)",
    "model": f"{REL_POL}/POLICY_MODEL.md; vpp/policy/policy_simulator.py (read-only replay: load_market, load_forecasts, "
             "family, energy_scores, select_calls, account, ircr_value, evaluate_policy, fit_rule with the saved rule)",
    "flag_rule": "vpp/site_bill.py peak_day_predictor, daily_max, forecast_tolerance, dynamic_flags, OFFICIAL_PEAK_INTERVALS",
    "kwh_value": f"{REL_KWH}/RESULTS.md, REVIEW.md, out/season_ircr.csv, out/annual_breakeven.csv, out/energy_balance.csv",
    "profit_verify": "analysis/2026-10-25/customer_profit_verify/RESULTS.md, LOCAL_RESULTS.json (Codex local recomputation, "
                     "not a Claude review; its totals equal the backtest)",
    "tightness_forecast": "analysis/2026-10-26/tightness_forecast/RESULTS.md",
    "peak_day_probability": "analysis/2026-10-24/peak_day_probability/RESULTS.md",
    "value_params": "analysis/2026-10-26/value_params/RESULTS.md, REVIEW.md: site-battery storage-value tables; no "
                    "customer-fleet numbers, nothing used here",
    "power_cap_fix": "analysis/2026-10-26/power_cap_fix: site-battery dispatcher fix (vpp/dispatch.py); does not touch "
                     "vpp/policy, so no fleet figure changes",
    "exporter": "analysis/2026-10-26/ui_redesign/export/fleet_export.py",
}

fleet = {
    "generated": "2026-10-26",
    "scope": "Customer-owned household batteries (VPP), relative to the same batteries in normal self-consumption. "
             "Separate from Zenith's own 10 MW / 40 MWh site battery; never summed with it in this file.",
    "offers": offers,
    "season_calls": season_calls,
    "value_by_party": value_by_party,
    "per_call": per_call,
    "ircr": ircr,
    "validation": validation,
    "do_not_use": do_not_use,
    "inconsistencies": inconsistencies,
    "gaps": GAPS,
    "sources": sources,
}


def strict(o):
    """Reject NaN / inf anywhere before writing."""
    if isinstance(o, dict):
        for v in o.values():
            strict(v)
    elif isinstance(o, list):
        for v in o:
            strict(v)
    elif isinstance(o, float):
        assert math.isfinite(o), o
    elif isinstance(o, (np.floating, np.integer, np.bool_)):
        raise TypeError(f"numpy scalar left in output: {o!r}")


strict(fleet)
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(fleet, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB) in {time.time() - T0:.0f} s", flush=True)
print("calls 2025-26:", counts, "peak days covered:", peak_cov, "missed:", missed, flush=True)
print("per call (custom S2b, 2025-26):", per_call["billed_export_kwh"], "kWh,", per_call["effective_c_per_billed_kwh"], "c", flush=True)
