"""Policy search for customer-owned battery activations (reports/policy/POLICY_MODEL.md, section 7, written before
the run): a transparent grid sweep (phase 1), the feasible and attractive policies (phase 2), a local refinement of
the winner (phase 3); then the backtest A-E, the exactness check of the activation approximation, the stress tests,
reports/policy/policy_results.csv and the charts.

Run:  python -m vpp.policy.policy_optimizer [--quick] [--reuse]
      --quick: a small grid, for code checks only; --reuse: read phase 1 from data/processed/policy_sweep.parquet
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from itertools import product

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

from vpp.backtest import REPORTS
from vpp.data import PROCESSED
from vpp.policy import policy_simulator as ps

OUT = REPORTS / "policy"
SWEEP = PROCESSED / "policy_sweep.parquet"
HH = ps.Household()

GRID = {
    "pay": (0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.85, 1.00, 1.20),
    "comp": ("energy", "event", "fixed50+variable", "fixed100+variable"),
    "cap": (10, 20, 30, 45, 60),
    "event_h": (1.0, 2.0, 3.0, 4.0),
    "standby_h": (1.0, 2.0),
    "reserve": (0.2, 0.3, 0.4, 0.5, 0.6),
}
QUICK = {"pay": (0.30, 0.70), "comp": ("energy", "fixed50+variable"), "cap": (10, 30), "event_h": (2.0,),
         "standby_h": (2.0,), "reserve": (0.2, 0.4)}
SCENARIOS = {
    "S1": ps.Obligations("S1 energy only"),
    "S2a": ps.Obligations("S2a retailer, current IRCR", capacity="floor"),
    "S2b": ps.Obligations("S2b retailer, 2027 IRCR", capacity="net"),
    "S3": ps.Obligations("S3 Synergy TPA", energy_to_zenith=False, capacity="net", capacity_share=0.5),
}
PARTICIPATION = {
    "central": ps.Participation(),
    "pessimistic": ps.Participation("pessimistic", b50=200.0, slope=60.0),
    "optimistic": ps.Participation("optimistic", b50=50.0, slope=25.0),
}
CENTRAL = PARTICIPATION["central"]


def make_policy(pay: float, comp: str, cap: int, event_h: float, standby_h: float, reserve: float,
                name: str = "grid") -> ps.Policy:
    """A grid point. event: p x the kWh a full battery delivers, per event; fixedF+variable: $F a year plus p per kWh."""
    base = ps.Policy(name=name, comp_type=comp, max_events=int(cap), event_h=event_h, standby_h=standby_h,
                     reserve_soc=reserve)
    if comp == "energy":
        return replace(base, pay_per_kwh=pay)
    if comp == "event":
        return replace(base, pay_per_kwh=0.0, pay_per_event=round(pay * base.nominal_kwh(HH), 4))
    fee = float(comp.split("+")[0].removeprefix("fixed"))
    return replace(base, pay_per_kwh=pay, annual_fee=fee)


# ------------------------------------------------------------------------------------------------------- phase 1
def _family_task(task: tuple) -> list[dict]:
    """Every economic variant and scenario of one physical design: every candidate call rule's outcome."""
    (event_h, standby_h, reserve), grid = task
    rows = []
    for pay, comp, cap in product(grid["pay"], grid["comp"], grid["cap"]):
        pol = make_policy(pay, comp, cap, event_h, standby_h, reserve)
        for sc, obl in SCENARIOS.items():
            for f in ps.rule_fits(pol, obl, CENTRAL):
                rows.append({"scenario": sc, "pay": pay, "comp": comp, "cap": cap, "event_h": event_h,
                             "standby_h": standby_h, "reserve": reserve, "theta": f.rule.theta, "ircr_q": f.rule.ircr_q,
                             "zen": f.zen, "zen_risk": f.zen_risk, "cust": f.cust, "cust_worst": f.cust_worst,
                             "violations": f.violations, "energy_zen": f.parts["energy"][0], "energy_cust": f.parts["energy"][1],
                             "annual_zen": pol.annual_fee + obl.ops_per_customer, "annual_fee": pol.annual_fee,
                             **{f"season{y}_zen": f.parts[y][0] for y in ps.SEASONS if y in f.parts},
                             **{f"season{y}_cust": f.parts[y][1] for y in ps.SEASONS if y in f.parts},
                             **{f"covered{y}": f.covered.get(y, np.nan) for y in ps.SEASONS}})
    return rows


def sweep(grid: dict) -> pd.DataFrame:
    tasks = [((e, s, r), grid) for e, s, r in product(grid["event_h"], grid["standby_h"], grid["reserve"])]
    rows = []
    with ProcessPoolExecutor(max_workers=min(20, len(tasks))) as ex:
        for out in ex.map(_family_task, tasks):
            rows += out
    return pd.DataFrame(rows)


KEYS = ["scenario", "pay", "comp", "cap", "event_h", "standby_h", "reserve"]


def choose(rules: pd.DataFrame, part: ps.Participation) -> pd.DataFrame:
    """Per policy, the call rule with the highest expected fleet profit under a participation curve (ties: margin per
    customer), with the fleet figures and the feasibility test of section 7."""
    r = rules.copy()
    share = np.where(r["cust"] >= part.min_benefit,
                     part.max_share / (1 + np.exp(-(r["cust"] - part.b50) / part.slope)), 0.0)
    r["enrolled"] = part.eligible * share
    fixed = r["scenario"].map({k: v.fixed_cost for k, v in SCENARIOS.items()})
    r["fleet_profit"] = np.where(r["enrolled"] > 0, r["enrolled"] * r["zen"] - fixed, 0.0)
    r["fleet_risk"] = np.where(r["enrolled"] > 0, r["enrolled"] * r["zen_risk"] - fixed, 0.0)
    r["_k"] = r["fleet_profit"].round(2)
    r = r.sort_values(KEYS + ["_k", "zen"]).groupby(KEYS, sort=False).tail(1).drop(columns="_k")
    down = r["scenario"].map({k: v.downside_limit for k, v in SCENARIOS.items()})
    r["feasible"] = (r["cust"] >= part.min_benefit) & (r["violations"] == 0) & (r["fleet_risk"] >= -down)
    return r.reset_index(drop=True)


def results_table(rules: pd.DataFrame) -> pd.DataFrame:
    """policy_results.csv: one row per scenario and policy, the central curve's choice, plus the fleet profit and
    feasibility under the other two curves."""
    c = choose(rules, CENTRAL)
    for name in ("pessimistic", "optimistic"):
        o = choose(rules, PARTICIPATION[name])[KEYS + ["fleet_profit", "enrolled", "feasible"]]
        c = c.merge(o.rename(columns={"fleet_profit": f"fleet_profit_{name}", "enrolled": f"enrolled_{name}",
                                      "feasible": f"feasible_{name}"}), on=KEYS)
    c["attractive"] = c["feasible"] & (c["fleet_profit"] > 0)
    c["rank"] = c.groupby("scenario")["fleet_profit"].rank(ascending=False, method="first").where(c["feasible"])
    return c.sort_values(["scenario", "rank"])


# ------------------------------------------------------------------------------------------------- phases 2 and 3
def winner(table: pd.DataFrame, scenario: str) -> pd.Series | None:
    t = table[(table["scenario"] == scenario) & table["feasible"]]
    return None if t.empty else t.sort_values("fleet_profit", ascending=False).iloc[0]


def policy_of(row: pd.Series, name: str) -> ps.Policy:
    return make_policy(row["pay"], row["comp"], row["cap"], row["event_h"], row["standby_h"], row["reserve"], name)


def fleet_of(pol: ps.Policy, obl: ps.Obligations, part: ps.Participation = CENTRAL,
             port: ps.Portfolio = ps.Portfolio()) -> tuple[float, bool, ps.Fit]:
    fit = ps.fit_rule(pol, obl, part, port)
    profit, risk, _ = ps.fleet(fit, part, obl)
    feasible = fit.cust >= part.min_benefit and fit.violations == 0 and risk >= -obl.downside_limit
    return profit, feasible, fit


def refine(row: pd.Series, scenario: str) -> tuple[ps.Policy, dict]:
    """Phase 3: p continuous within +-$0.10 (bounded scalar search), then K within one grid step; adopted only if
    feasible and more than 1% better (section 7)."""
    obl = SCENARIOS[scenario]
    start = policy_of(row, f"optimised {scenario}")
    base, _, _ = fleet_of(start, obl)

    def neg(p: float) -> float:
        prof, ok, _ = fleet_of(make_policy(p, row["comp"], row["cap"], row["event_h"], row["standby_h"],
                                           row["reserve"]), obl)
        return -prof if ok else 1e12

    lo, hi = max(0.0, row["pay"] - 0.10), row["pay"] + 0.10
    res = minimize_scalar(neg, bounds=(lo, hi), method="bounded", options={"xatol": 0.005, "maxiter": 20})
    best_p, best = (res.x, -res.fun) if -res.fun > base * 1.01 else (row["pay"], base)
    caps = sorted(GRID["cap"])
    k = caps.index(int(row["cap"]))
    k_lo, k_hi = caps[max(0, k - 1)], caps[min(len(caps) - 1, k + 1)]
    best_k = int(row["cap"])
    for cap in range(k_lo, k_hi + 1):
        prof, ok, _ = fleet_of(make_policy(best_p, row["comp"], cap, row["event_h"], row["standby_h"], row["reserve"]), obl)
        if ok and prof > best * 1.01:
            best, best_k = prof, cap
    pol = replace(make_policy(round(best_p, 3), row["comp"], best_k, row["event_h"], row["standby_h"], row["reserve"]),
                  name=f"optimised {scenario}")
    return pol, {"scenario": scenario, "grid_pay": row["pay"], "grid_cap": int(row["cap"]), "grid_profit": base,
                 "refined_pay": round(best_p, 3), "refined_cap": best_k, "refined_profit": best,
                 "adopted": bool(best > base * 1.01)}


# ---------------------------------------------------------------------------------------------- backtest A-E
PERIODS = ("in-sample", "test", "FY2023", "FY2024")


def backtest(policies: dict) -> pd.DataFrame:
    """B, C, D, E on every period; A is ps.zenith_only (added in the report). Same market and weather history."""
    rows = []
    for sc, (ref, opt) in policies.items():
        obl = SCENARIOS[sc]
        for label, pol, rule in (("B reference", ref, None), ("C optimised", opt, None),
                                 ("D optimised policy, simple rule", opt, ps.CallRule("simple")),
                                 ("E optimised policy, perfect foresight", opt, ps.CallRule("perfect")),
                                 ("B' reference, simple rule", ref, ps.CallRule("simple")),
                                 ("E' reference, perfect foresight", ref, ps.CallRule("perfect"))):
            fit = ps.fit_rule(pol, obl, CENTRAL)
            for period in PERIODS:
                if period.startswith("FY") and obl.capacity == "none":
                    continue
                e = ps.evaluate_policy(pol, market_obligations=obl, rule=rule, period=period, fit=fit)
                rows.append({"scenario": sc, "case": label, **e.row(), "fit_zen": fit.zen, "fit_zen_risk": fit.zen_risk,
                             "fit_cust": fit.cust, "fit_cust_worst": fit.cust_worst})
    return pd.DataFrame(rows)


def exactness(policies: dict) -> pd.DataFrame:
    """The activation approximation (each call from the baseline's state) against an exact sequential run of every
    interval of contract year 2025-26, per responding customer."""
    rows = []
    m, b = ps.load_market(), ps.baseline(HH)
    years = ps.contract_years(m)
    for sc, (ref, opt) in policies.items():
        obl = SCENARIOS[sc]
        for label, pol in (("B reference", ref), ("C optimised", opt)):
            fit = ps.fit_rule(pol, obl, CENTRAL)
            key = (pol.event_h, pol.standby_h, pol.reserve_soc, HH, tuple(pol.window), str(ps.LAST_DAY.date()))
            fam = ps.family(*key)
            starts = np.flatnonzero(fam.starts + 2 * pol.event_h <= 2 * (pol.window[1] - 8))
            scores = ps.energy_scores(key, pol, obl, ps.load_forecasts(), m, starts)
            calls = ps.select_calls(years[2025], fam, pol, fit.rule, ps.load_forecasts(), scores,
                                    obl.capacity != "none", obl.energy_to_zenith, m)
            if not len(calls.day):
                rows.append({"scenario": sc, "case": label, "calls": 0})
                continue
            a, z = years[2025][0] * ps.N, (years[2025][-1] + 2) * ps.N
            modes = np.zeros(z - a, int)
            rates = {}
            n_sb, n_ev = int(2 * pol.standby_h), int(2 * pol.event_h)
            for d, j in zip(calls.day, calls.start):
                s = d * ps.N - a + fam.starts[j]
                modes[s - n_sb:s] = ps.STANDBY
                modes[s:s + n_ev] = ps.EVENT
                rates[s] = n_ev
            run = ps.simulate(b.pv[a:z], b.load[a:z], HH, modes, rates, pol.reserve_soc * HH.usable_kwh, soc0=b.soc[a])
            d_net = (run.exp - run.imp) - (b.exp[a:z] - b.imp[a:z])
            w_seq = (d_net * ps.DT * m.rtp[a:z]).sum() / 1000 + (run.soc[-1] - b.soc[z]) * HH.eta \
                * m.slot_price[14:26].mean() / 1000
            ev = modes == ps.EVENT
            paid_seq = 0.0
            for s in rates:
                paid_seq += min((run.exp[s:s + n_ev] * ps.DT).sum(), HH.usable_kwh)
            w_ind = fam.w_actual[calls.start, calls.day].sum()
            paid_ind = fam.paid_kwh[calls.start, calls.day].sum()
            imp_seq = ((run.imp - b.imp[a:z]) * ps.DT).sum()
            imp_ind = fam.d_import_kwh[calls.start, calls.day].sum()
            consecutive = int((np.diff(np.sort(calls.day)) == 1).sum())
            rows.append({"scenario": sc, "case": label, "calls": len(calls.day), "consecutive_day_pairs": consecutive,
                         "wholesale_independent": w_ind, "wholesale_sequential": w_seq,
                         "paid_kwh_independent": paid_ind, "paid_kwh_sequential": paid_seq,
                         "extra_import_kwh_independent": imp_ind, "extra_import_kwh_sequential": imp_seq,
                         "event_slots": int(ev.sum())})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------------------- stress tests
def stress(policies: dict) -> pd.DataFrame:
    """Section 7's stress tests on B and C, settled on contract year 2025-26 (and on the test where it differs)."""
    m, fc = ps.load_market(), ps.load_forecasts()
    rows = []
    slots = np.arange(len(m.rtp)) % ps.N
    standby_hours = (slots >= 10) & (slots < 18)  # 13:00-17:00
    tests = {
        "base": {},
        "false-positive spike warnings (p_spike x3)": {"fc": replace(fc, p_spike=np.minimum(fc.p_spike * 3, 1.0))},
        "rare spikes (prices capped at $300)": {"prices": np.minimum(m.rtp, 300.0)},
        "spike timing 1 h later than forecast": {"prices": np.r_[m.rtp[:2], m.rtp[:-2]]},
        "batteries unavailable (50% respond)": {"part": replace(CENTRAL, availability=0.5)},
        "low participation (pessimistic curve)": {"part_curve": PARTICIPATION["pessimistic"]},
        "high participation (optimistic curve)": {"part_curve": PARTICIPATION["optimistic"]},
        "compensation x1.5 (rule refitted)": {"policy": lambda p: replace(p, pay_per_kwh=p.pay_per_kwh * 1.5,
                                                                           pay_per_event=p.pay_per_event * 1.5,
                                                                           annual_fee=p.annual_fee * 1.5)},
        "only 10 activations (rule refitted)": {"policy": lambda p: replace(p, max_events=10)},
        "energy calls compete for the cap": {"rule": lambda r: replace(r, theta=0.0, reserve_for_ircr=False)},
        "standby charging dear (+$300 13:00-17:00, offset x2)": {"prices": m.rtp + 300.0 * standby_hours,
                                                                  "policy_fixed": lambda p: replace(p, offset_rate=p.offset_rate * 2)},
        "programme fixed cost $100k/yr": {"obl": lambda o: replace(o, fixed_cost=100_000.0)},
        "customer wear $0.15/kWh": {"part_refit": replace(CENTRAL, deg_per_kwh=0.15)},
    }
    for sc, (ref, opt) in policies.items():
        obl0 = SCENARIOS[sc]
        for label, pol0 in (("B reference", ref), ("C optimised", opt)):
            fit0 = ps.fit_rule(pol0, obl0, CENTRAL)
            for name, t in tests.items():
                pol, obl, part, fit = pol0, obl0, CENTRAL, fit0
                if "policy" in t:
                    pol = t["policy"](pol0)
                    fit = ps.fit_rule(pol, obl, part)
                if "policy_fixed" in t:
                    pol = t["policy_fixed"](pol0)
                if "obl" in t:
                    obl = t["obl"](obl0)
                if "part_refit" in t:
                    part = t["part_refit"]
                    fit = ps.fit_rule(pol, obl, part)
                if "part_curve" in t:
                    part = t["part_curve"]
                    fit = ps.fit_rule(pol, obl, part)
                if "part" in t:
                    part = t["part"]
                if "rule" in t:
                    fit = replace(fit0, rule=t["rule"](fit0.rule))
                for period in ("in-sample", "test"):
                    e = ps.evaluate_policy(pol, market_obligations=obl, participation_scenario=part, period=period,
                                           fit=fit, forecast_data=t.get("fc"), prices=t.get("prices"))
                    cov = min(e.peak_days_covered.values()) if e.peak_days_covered else np.nan
                    fail = (e.zenith_profit < 0) or (e.customer_benefit < 0 and e.enrolled > 0) \
                        or (obl.capacity != "none" and period == "in-sample" and cov < 3)
                    rows.append({"scenario": sc, "case": label, "stress": name, "period": period,
                                 "enrolled": e.enrolled, "activations": e.activations, "energy_calls": e.energy_calls,
                                 "ircr_calls": e.ircr_calls, "zenith_profit": e.zenith_profit,
                                 "customer_benefit": e.customer_benefit, "peak_days_covered": cov, "fails": bool(fail)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------ leave one season out
def leave_one_season_out(rules: pd.DataFrame, part: ps.Participation = CENTRAL) -> pd.DataFrame:
    """The capacity stream has no Hot Season in the test, so (reported only) the whole choice - call rule per policy,
    then policy - is redone on two Hot Seasons and the chosen policy is scored on the third. Holding out 2025-26 is
    chronological (chosen on 2023-24 and 2024-25 only; its peak-day tolerance also uses only those); holding out
    2023-24 or 2024-25 is not (a later season is in the choice). The reference keeps its terms, its rule chosen the
    same way. Energy calls are left in (none passes in any capacity scenario)."""
    rows = []
    for sc in ("S2b", "S3"):
        r0 = rules[rules["scenario"] == sc]
        for held in ps.SEASONS:
            train = [y for y in ps.SEASONS if y != held]
            r = r0.copy()
            r["zen"] = r["energy_zen"] + r[[f"season{y}_zen" for y in train]].mean(axis=1) - r["annual_zen"]
            r["zen_risk"] = r["energy_zen"] + r[[f"season{y}_zen" for y in train]].min(axis=1) - r["annual_zen"]
            r["cust"] = r["energy_cust"] + r[[f"season{y}_cust" for y in train]].mean(axis=1) + r["annual_fee"]
            c = choose(r, part)
            c["held_zen"] = c["energy_zen"] + c[f"season{held}_zen"] - c["annual_zen"]
            c["held_cust"] = c["energy_cust"] + c[f"season{held}_cust"] + c["annual_fee"]
            c["held_profit"] = c["enrolled"] * c["held_zen"]
            pick = c[c["feasible"]].sort_values("fleet_profit", ascending=False)
            ref = c[(c["pay"] == 0.70) & (c["comp"] == "energy") & (c["cap"] == 30) & (c["event_h"] == 2.0)
                    & (c["standby_h"] == 2.0) & (c["reserve"] == 0.2)]
            for label, x in (("C chosen without the held-out season", pick.head(1)), ("B reference", ref)):
                if x.empty:
                    continue
                x = x.iloc[0]
                rows.append({"scenario": sc, "held_out": f"{held}-{(held + 1) % 100:02d}",
                             "chronological": held == max(ps.SEASONS), "case": label,
                             "pay": x["pay"], "comp": x["comp"], "cap": x["cap"], "event_h": x["event_h"],
                             "standby_h": x["standby_h"], "reserve": x["reserve"], "ircr_q": x["ircr_q"],
                             "enrolled": x["enrolled"], "expected_profit_from_training": x["fleet_profit"],
                             "held_out_profit": x["held_profit"], "held_out_customer_benefit": x["held_cust"],
                             f"covered_held_out": x[f"covered{held}"], "feasible_on_training": bool(x["feasible"])})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------- diagnostics, sensitivities
def energy_only_diagnostic() -> pd.DataFrame:
    """S1 in detail (reported only): what energy calls earn per enrolled customer at each payment and event length
    (energy payment, 60 activations, 1-h standby, 20% reserve) when the 09:00 rule is forced to call whenever its
    expected margin is at least 0 (theta = 0), the same with the spike warnings tripled, and with perfect foresight."""
    fc = ps.load_forecasts()
    fc3 = replace(fc, p_spike=np.minimum(fc.p_spike * 3, 1.0))
    obl = SCENARIOS["S1"]
    rows = []
    for pay, eh in product(GRID["pay"], GRID["event_h"]):
        pol = make_policy(pay, "energy", 60, eh, 1.0, 0.2)
        fit = ps.fit_rule(pol, obl, CENTRAL)
        for label, rule, f in (("09:00 rule, theta 0", ps.CallRule("optimised", 0.0), None),
                               ("09:00 rule, theta 0, spike warnings x3", ps.CallRule("optimised", 0.0), fc3),
                               ("perfect foresight", ps.CallRule("perfect"), None)):
            for period in ("in-sample", "test"):
                e = ps.evaluate_policy(pol, market_obligations=obl, rule=rule, period=period, fit=fit, forecast_data=f)
                per = lambda x: x / e.enrolled if e.enrolled else 0.0  # noqa: E731
                rows.append({"pay": pay, "event_h": eh, "rule": label, "period": period, "calls": e.activations,
                             "wholesale_per_customer": per(e.revenue["wholesale"]),
                             "payments_per_customer": per(e.compensation["pay_energy"] + e.compensation["offset"]),
                             "call_margin_per_customer": per(e.revenue["wholesale"] - e.compensation["pay_energy"]
                                                             - e.compensation["offset"] - e.costs["ops_event"]),
                             "customer_benefit": e.customer_benefit})
    return pd.DataFrame(rows)


def breakeven_share(opt: ps.Policy) -> pd.DataFrame:
    """S3: expected fleet profit of the optimised policy at each share of the IRCR value Synergy might pay Zenith."""
    rows = []
    for share in np.round(np.arange(0.0, 1.01, 0.05), 2):
        prof, ok, fit = fleet_of(opt, replace(SCENARIOS["S3"], capacity_share=float(share)))
        rows.append({"share": share, "fleet_profit": prof, "feasible": ok, "zen_per_customer": fit.zen})
    return pd.DataFrame(rows)


def sensitivities(policies: dict) -> pd.DataFrame:
    """C's expected fleet profit (rule refitted) under single changes (reported only)."""
    cases = {
        "base": ({}, {}, {}),
        "capacity price $360,700/MW-yr (2027-28) for every season": ({"rcp": 360_700.0}, {}, {}),
        "IRCR scaling ratio 1.57 (the factory's)": ({"rcp_ratio": 1.57}, {}, {}),
        "response availability 0.95": ({}, {"availability": 0.95}, {}),
        "battery 10 kWh usable": ({}, {}, {"usable_kwh": 10.0}),
        "household load 25 kWh/day": ({}, {}, {"load_kwh_day": 25.0}),
        "customer disutility $3/event": ({}, {"hassle_per_event": 3.0}, {}),
    }
    rows = []
    for sc, (_, opt) in policies.items():
        for name, (o, pa, hh) in cases.items():
            port = ps.Portfolio(household=replace(HH, **hh))
            prof, ok, fit = fleet_of(opt, replace(SCENARIOS[sc], **o), replace(CENTRAL, **pa), port)
            rows.append({"scenario": sc, "case": name, "fleet_profit": prof, "feasible": ok, "zen_per_customer": fit.zen,
                         "customer_benefit": fit.cust, "rule_theta": fit.rule.theta, "rule_q": fit.rule.ircr_q})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------------- charts
INK, INK2, MUTED, GRID_C, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1", "#fcfcfb"
SERIES = {"S1": "#2a78d6", "S2a": "#eb6834", "S2b": "#1baf7a", "S3": "#eda100"}
LABEL = {"S1": "S1 energy only", "S2a": "S2a retailer, current IRCR", "S2b": "S2b retailer, 2027 IRCR",
         "S3": "S3 Synergy TPA (50% share)"}


def _axes(ax, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=11, fontweight="bold")
    ax.set_xlabel(xlabel, color=INK2, fontsize=9)
    ax.set_ylabel(ylabel, color=INK2, fontsize=9)
    ax.grid(True, color=GRID_C, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID_C)
    ax.tick_params(colors=INK2, labelsize=8)


def _money(x: float) -> str:
    a = abs(x)
    s = f"${a / 1e6:.2f}M" if a >= 1e6 else f"${a / 1e3:.0f}k" if a >= 1e3 else f"${a:.0f}"
    return ("−" if x < 0 else "") + s


def charts(table: pd.DataFrame, bt: pd.DataFrame, policies: dict, cap_curve: pd.DataFrame, usage: pd.DataFrame,
           zen: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    plt.rcParams.update({"font.family": "DejaVu Sans", "figure.facecolor": SURFACE})
    money_fmt = FuncFormatter(lambda v, _: _money(v))

    def best_by(col: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Best expected fleet profit among policies that leave customers no worse off, and whether that policy also
        passes the downside test."""
        t = table[(table["cust"] >= 0) & (table["violations"] == 0)].sort_values("fleet_profit")
        best = t.groupby(["scenario", col]).tail(1)
        return (best.pivot(index=col, columns="scenario", values="fleet_profit"),
                best.pivot(index=col, columns="scenario", values="feasible"))

    # 1 and 2: profit vs payment and vs cap (filled marker: passes the downside test; hollow: fails it)
    for col, fname, title, xlabel in (("pay", "chart_profit_vs_payment.png", "Zenith profit vs activation payment",
                                       "Payment per kWh of event export ($/kWh; event and fixed+variable forms at the same rate)"),
                                      ("cap", "chart_profit_vs_cap.png", "Zenith profit vs annual activation cap",
                                       "Activations per contract year")):
        d, ok = best_by(col)
        fig, ax = plt.subplots(figsize=(8, 4.6))
        _axes(ax, title, xlabel, "Best expected fleet profit, $/yr\n(customers no worse off; building data, central participation)")
        for sc in SCENARIOS:
            if sc in d:
                y = d[sc].reindex(sorted(table[col].unique()))
                good = ok[sc].reindex(y.index).fillna(False).astype(bool).to_numpy()
                ax.plot(y.index, y.values, color=SERIES[sc], lw=2, label=LABEL[sc])
                ax.scatter(y.index[good], y.values[good], s=40, color=SERIES[sc], edgecolor=SURFACE, linewidth=1.5, zorder=4)
                ax.scatter(y.index[~good], y.values[~good], s=40, facecolor=SURFACE, edgecolor=SERIES[sc], linewidth=1.5,
                           zorder=4)
        ax.scatter([], [], s=40, color=INK2, label="passes the downside test")
        ax.scatter([], [], s=40, facecolor=SURFACE, edgecolor=INK2, label="fails it (a season loses money)")
        ax.axhline(0, color=INK2, lw=0.8)
        if col == "pay":
            ax.axvline(0.70, color=MUTED, lw=1)
            ax.text(0.705, ax.get_ylim()[1], "Synergy 70c", color=INK2, fontsize=8, va="top")
        else:
            ax.axvline(30, color=MUTED, lw=1)
            ax.text(30.5, ax.get_ylim()[1], "Synergy 30", color=INK2, fontsize=8, va="top")
        ax.yaxis.set_major_formatter(money_fmt)
        ax.legend(frameon=False, fontsize=8, labelcolor=INK)
        fig.tight_layout()
        fig.savefig(OUT / fname, dpi=150)
        plt.close(fig)

    # 3: customer benefit vs Zenith profit (one scenario per panel; every grid policy, feasible ones highlighted)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.6), sharey=False)
    for ax, sc in zip(axes, ("S2b", "S3")):
        t = table[table["scenario"] == sc]
        _axes(ax, LABEL[sc], "Expected customer net benefit, $ per customer per year", "Expected fleet profit, $/yr")
        ax.scatter(t["cust"], t["fleet_profit"], s=6, color=GRID_C, label="every grid policy", rasterized=True)
        f = t[t["feasible"]]
        ax.scatter(f["cust"], f["fleet_profit"], s=8, color=SERIES[sc], alpha=0.6, label="feasible")
        ref, opt = policies[sc]
        for pol, marker, lab in ((ref, "s", "B reference"), (opt, "D", "C optimised")):
            r = bt[(bt["scenario"] == sc) & (bt["case"] == ("B reference" if lab.startswith("B") else "C optimised"))
                   & (bt["period"] == "in-sample")].iloc[0]
            prof = r["enrolled"] * r["fit_zen"]
            ax.scatter([r["fit_cust"]], [prof], s=70, marker=marker, color=INK, edgecolor=SURFACE, linewidth=2, zorder=5)
            ax.annotate(lab, (r["fit_cust"], prof), xytext=(6, 6), textcoords="offset points", fontsize=8, color=INK)
        ax.axvline(0, color=INK2, lw=0.8)
        ax.axhline(0, color=INK2, lw=0.8)
        ax.yaxis.set_major_formatter(money_fmt)
        ax.legend(frameon=False, fontsize=8, loc="lower right", labelcolor=INK)
    fig.suptitle("Customer benefit vs Zenith profit: every grid policy (building data, central participation)",
                 x=0.01, ha="left", color=INK, fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "chart_customer_vs_zenith.png", dpi=150)
    plt.close(fig)

    # 4: activation usage through contract year 2025-26, and the value of one more activation (two panels)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4))
    ax = axes[0]
    _axes(ax, "Activations used, contract year 2025-26", "Date", "Cumulative activations")
    for (sc, case), g in usage.groupby(["scenario", "case"]):
        style = "-" if case.startswith("C") else (0, (4, 2))
        ax.step(g["date"], g["cumulative"], where="post", color=SERIES[sc], lw=2, linestyle=style, label=f"{sc} {case}")
    ax.legend(frameon=False, fontsize=7, labelcolor=INK)
    ax.tick_params(axis="x", rotation=30)
    ax = axes[1]
    _axes(ax, "Opportunity cost: value of one more activation", "Activation cap (other terms of C held)",
          "Change in expected fleet profit per extra activation, $")
    for sc, g in cap_curve.groupby("scenario"):
        ax.plot(g["cap"], g["marginal"], color=SERIES[sc], lw=2, marker="o", ms=4, label=LABEL[sc],
                markeredgecolor=SURFACE, markeredgewidth=1.5)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.yaxis.set_major_formatter(money_fmt)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(OUT / "chart_activation_usage.png", dpi=150)
    plt.close(fig)

    # 5: revenue waterfall, contract year 2025-26: Zenith-only (A), then the fleet's items for B and for C
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8), sharey=True)
    a_total = float(zen.loc["FY2025-26", "total"])
    for ax, sc in zip(axes, ("S2b", "S3")):
        _axes(ax, LABEL[sc], "", "Zenith $ in contract year 2025-26")
        steps = [("A Zenith-only", a_total, "total")]
        for case in ("B reference", "C optimised"):
            r = bt[(bt["scenario"] == sc) & (bt["case"] == case) & (bt["period"] == "in-sample")].iloc[0]
            items = [("wholesale", r["revenue_wholesale"]), ("capacity", r["revenue_capacity"]),
                     ("payments", -(r["compensation_pay_energy"] + r["compensation_pay_event"] + r["compensation_annual_fee"])),
                     ("offset credits", -r["compensation_offset"]),
                     ("operations", -(r["costs_ops_event"] + r["costs_ops_customer"] + r["costs_fixed"]))]
            tag = case.split()[0]
            steps += [(f"{tag}: {k}", v, "delta") for k, v in items]
            steps.append((f"A + {tag}", a_total + r["zenith_profit"], "total"))
        x, level = 0, 0.0
        for name, v, kind in steps:
            if kind == "total":
                ax.bar(x, v, width=0.6, color=INK2)
                ax.text(x, v, _money(v), ha="center", va="bottom", fontsize=7, color=INK)
                level = v if name.startswith("A Zenith") else a_total
            else:
                bottom = level if v >= 0 else level + v
                ax.bar(x, abs(v), bottom=bottom, width=0.6, color=SERIES[sc] if v >= 0 else "#e34948")
                level += v
            x += 1
        ax.set_xticks(range(len(steps)))
        ax.set_xticklabels([s[0] for s in steps], rotation=60, ha="right", fontsize=7)
        ax.yaxis.set_major_formatter(money_fmt)
    fig.suptitle("Revenue waterfall, contract year 2025-26: Zenith-only (A), then + reference (B) or + optimised (C)",
                 x=0.01, ha="left", color=INK, fontsize=12, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "chart_revenue_waterfall.png", dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------------------------------------- main
def cap_curve_of(policies: dict) -> pd.DataFrame:
    """Expected fleet profit of C's terms at every cap from 4 to 60, and the change per extra activation."""
    rows = []
    for sc, (_, opt) in policies.items():
        obl = SCENARIOS[sc]
        prev = None
        for cap in range(4, 61, 2):
            prof, ok, fit = fleet_of(replace(opt, max_events=cap), obl)
            rows.append({"scenario": sc, "cap": cap, "fleet_profit": prof, "feasible": ok,
                         "marginal": np.nan if prev is None else (prof - prev) / 2})
            prev = prof
    return pd.DataFrame(rows)


def usage_of(policies: dict) -> pd.DataFrame:
    m = ps.load_market()
    years = ps.contract_years(m)
    rows = []
    for sc, (ref, opt) in policies.items():
        obl = SCENARIOS[sc]
        for case, pol in (("B reference", ref), ("C optimised", opt)):
            fit = ps.fit_rule(pol, obl, CENTRAL)
            key = (pol.event_h, pol.standby_h, pol.reserve_soc, HH, tuple(pol.window), str(ps.LAST_DAY.date()))
            fam = ps.family(*key)
            starts = np.flatnonzero(fam.starts + 2 * pol.event_h <= 2 * (pol.window[1] - 8))
            scores = ps.energy_scores(key, pol, obl, ps.load_forecasts(), m, starts)
            calls = ps.select_calls(years[2025], fam, pol, fit.rule, ps.load_forecasts(), scores,
                                    obl.capacity != "none", obl.energy_to_zenith, m)
            days = m.days[years[2025]]
            used = pd.Series(0, index=days)
            used.loc[m.days[calls.day]] = 1
            rows.append(pd.DataFrame({"scenario": sc, "case": case, "date": days, "cumulative": used.cumsum().values,
                                      "cap": pol.max_events}))
    return pd.concat(rows, ignore_index=True)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    quick = "--quick" in argv
    grid = QUICK if quick else GRID
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rules = pd.read_parquet(SWEEP) if "--reuse" in argv else sweep(grid)
    if not quick and "--reuse" not in argv:
        rules.to_parquet(SWEEP)
    print(f"phase 1: {len(rules):,} rule outcomes in {time.time() - t0:.0f}s")
    table = results_table(rules)
    if not quick:
        cols = KEYS + ["theta", "ircr_q", "enrolled", "zen", "zen_risk", "cust", "cust_worst", "fleet_profit", "fleet_risk",
                       "violations", "feasible", "attractive", "rank", "energy_zen", "season2023_zen", "season2024_zen",
                       "season2025_zen", "covered2023", "covered2024", "covered2025", "fleet_profit_pessimistic",
                       "feasible_pessimistic", "fleet_profit_optimistic", "feasible_optimistic"]
        table[[c for c in cols if c in table]].round(4).to_csv(OUT / "policy_results.csv", index=False)
    # phase 2 and 3
    policies, refinements = {}, []
    for sc in SCENARIOS:
        w = winner(table, sc)
        if w is None:
            print(f"{sc}: no feasible policy")
            continue
        opt, info = refine(w, sc)
        info["launch"] = info["refined_profit"] > 0
        refinements.append(info)
        policies[sc] = (ps.SYNERGY_LIKE, opt)
        print(f"{sc}: {asdict(opt)}\n    {info}")
    pd.DataFrame(refinements).to_csv(OUT / "policy_refinement.csv", index=False)
    bt = backtest(policies)
    bt.to_csv(OUT / "policy_backtest.csv", index=False)
    ex = exactness(policies)
    ex.to_csv(OUT / "policy_exactness.csv", index=False)
    st = stress(policies)
    st.to_csv(OUT / "policy_stress.csv", index=False)
    zen = ps.zenith_only()
    zen.to_csv(OUT / "policy_zenith_only.csv")
    cc = cap_curve_of(policies)
    cc.to_csv(OUT / "policy_cap_curve.csv", index=False)
    us = usage_of(policies)
    charts(table, bt, policies, cc, us, zen)
    energy_only_diagnostic().to_csv(OUT / "policy_energy_only.csv", index=False)
    if "S3" in policies:
        breakeven_share(policies["S3"][1]).to_csv(OUT / "policy_s3_share.csv", index=False)
    sensitivities(policies).to_csv(OUT / "policy_sensitivity.csv", index=False)
    leave_one_season_out(rules).to_csv(OUT / "policy_loso.csv", index=False)
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
