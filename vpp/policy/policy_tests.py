"""Physical, accounting and contract tests for the activation-policy simulator (reports/policy/POLICY_MODEL.md
section 7). Plain functions named test_*, so pytest finds them too; without pytest:

Run:  python -m vpp.policy.policy_tests
"""

import sys
import traceback
from dataclasses import replace

import numpy as np
import pandas as pd

from vpp import site_bill as sb
from vpp.data import PROCESSED
from vpp.evaluate import CUTOFF, TRAIN_END
from vpp.policy import policy_simulator as ps

HH = ps.Household()
S1 = ps.Obligations()
S2B = ps.Obligations("S2b", capacity="net")
S3 = ps.Obligations("S3", energy_to_zenith=False, capacity="net", capacity_share=0.5)
TOL = 1e-6


def _fam(policy: ps.Policy = ps.SYNERGY_LIKE) -> ps.Family:
    return ps.family(policy.event_h, policy.standby_h, policy.reserve_soc, HH, tuple(policy.window), str(ps.LAST_DAY.date()))


# ------------------------------------------------------------------------------------------------------- physics
def test_step_energy_balance_and_limits():
    """Every interval: PV + discharge + import = load + charge + export; power and storage within limits; the event
    never takes the battery below the customer's reserve."""
    rng = np.random.default_rng(1)
    n = 20_000
    soc = rng.uniform(0, HH.usable_kwh, n)
    pv, load = rng.uniform(0, 6, n), rng.uniform(0, 6, n)
    mode = rng.integers(0, 3, n)
    rate = rng.uniform(0, 6, n)
    reserve = 0.3 * HH.usable_kwh
    new, imp, exp, dis, ch, gch = ps.step(soc, pv, load, mode, rate, HH, reserve)
    assert np.allclose(pv + dis + imp, load + ch + exp, atol=TOL)
    assert (imp >= -TOL).all() and (exp >= -TOL).all() and ((imp < TOL) | (exp < TOL)).all()
    assert (dis <= HH.power_kw + TOL).all() and (ch <= HH.power_kw + TOL).all()
    assert (new >= -TOL).all() and (new <= HH.usable_kwh + TOL).all()
    ev = mode == ps.EVENT
    assert (new[ev] >= np.minimum(soc[ev], reserve) - TOL).all()
    assert np.allclose(new, soc + (ch * HH.eta - dis / HH.eta) * ps.DT, atol=TOL)
    assert (gch[mode != ps.STANDBY] == 0).all()                      # the grid charges the battery only in standby
    assert (dis[mode == ps.STANDBY] == 0).all()                      # standby holds the charge


def test_baseline_self_consumption():
    """Without calls the battery never charges from the grid, stays within its limits, exports only the PV surplus
    it cannot store in the interval and imports only the load it cannot serve."""
    b = ps.baseline(HH)
    assert (b.soc >= -TOL).all() and (b.soc <= HH.usable_kwh + TOL).all()
    soc = b.soc[:-1]
    surplus = b.pv - b.load
    can_store = np.minimum(HH.power_kw, (HH.usable_kwh - soc) / (HH.eta * ps.DT))
    can_serve = np.minimum(HH.power_kw, np.maximum(soc - HH.own_reserve * HH.usable_kwh, 0) * HH.eta / ps.DT)
    assert np.allclose(b.exp, np.maximum(surplus - can_store, 0) * (surplus > 0), atol=1e-6)
    assert np.allclose(b.imp, np.maximum(-surplus - can_serve, 0) * (surplus < 0), atol=1e-6)


def test_family_physics_no_violations():
    """No activation of any design breaks a physical or contract limit."""
    for eh, sbh, r in [(1.0, 1.0, 0.2), (2.0, 2.0, 0.2), (4.0, 2.0, 0.6), (3.0, 1.0, 0.4)]:
        f = ps.family(eh, sbh, r, HH, (15.0, 21.0), str(ps.LAST_DAY.date()))
        assert f.violations.sum() == 0, (eh, sbh, r)
        assert (f.paid_kwh <= HH.usable_kwh + TOL).all() and (f.standby_kwh <= HH.usable_kwh + TOL).all()
        assert (f.delivered_kwh <= HH.usable_kwh * (1 - r) * HH.eta + TOL).all()
        # the gap at 15:00 next day can be either sign: a short event at a high reserve discharges less than standby
        # filled from the grid, and the customer keeps the difference
        assert (np.abs(f.gap_kwh) <= HH.usable_kwh + TOL).all()


def test_independent_event_equals_sequential_run():
    """One isolated call: the precomputed outcome equals an exact run of the battery through every interval."""
    m, b, fam = ps.load_market(), ps.baseline(HH), _fam()
    p = ps.SYNERGY_LIKE
    d = int(np.flatnonzero(m.days == pd.Timestamp("2026-01-21"))[0])
    j = fam.index(ps.ircr_start(p.event_h))
    s, n_sb, n_ev = fam.starts[j], int(2 * p.standby_h), int(2 * p.event_h)
    a, z = (d - 1) * ps.N, (d + 2) * ps.N
    modes = np.zeros(z - a, int)
    o = d * ps.N - a
    modes[o + s - n_sb:o + s] = ps.STANDBY
    modes[o + s:o + s + n_ev] = ps.EVENT
    run = ps.simulate(b.pv[a:z], b.load[a:z], HH, modes, {o + s: n_ev}, p.reserve_soc * HH.usable_kwh, soc0=b.soc[a])
    k = slice(o + s - n_sb, o + ps.HORIZON)
    d_net = ((run.exp - run.imp) - (b.exp[a:z] - b.imp[a:z]))[k] * ps.DT
    assert np.allclose(d_net, fam.d_net[j, d, s - n_sb:], atol=TOL)
    w = (d_net * m.rtp[a:z][k]).sum() / 1000 + (run.soc[o + ps.HORIZON] - b.soc[d * ps.N + ps.HORIZON]) * HH.eta \
        * m.slot_price[14:26].mean() / 1000
    assert abs(w - fam.w_actual[j, d]) < TOL
    paid = min((run.exp[o + s:o + s + n_ev] * ps.DT).sum(), HH.usable_kwh)
    assert abs(paid - fam.paid_kwh[j, d]) < TOL


def test_reprice_reproduces_settlement():
    m, fam = ps.load_market(), _fam()
    assert np.allclose(ps.reprice(fam, m.rtp, m).w_actual, fam.w_actual, atol=TOL)


# ------------------------------------------------------------------------------------------------------ accounting
def test_parties_reconcile():
    """Zenith + customers + retailer = the system's net value (wholesale + capacity - wear - hassle - operations):
    payments, offset credits, fees and bills are transfers and cancel."""
    for obl in (S1, S2B, S3):
        for period in ("in-sample", "test"):
            e = ps.evaluate_policy(ps.SYNERGY_LIKE, market_obligations=obl, period=period)
            p = e.parties
            assert abs(p["zenith"] + p["customers"] + p["retailer"] - p["system"]) < 1e-3 * max(1, abs(p["system"])), (obl.name, period, p)


def test_profit_equals_items():
    """Zenith's profit = revenue - compensation - costs, item by item; payments = rate x kWh."""
    e = ps.evaluate_policy(ps.SYNERGY_LIKE, market_obligations=S2B)
    items = sum(e.revenue.values()) - sum(e.compensation.values()) - sum(e.costs.values())
    assert abs(items - e.zenith_profit) < 1e-3 * max(1, abs(e.zenith_profit))
    assert abs(e.compensation["pay_energy"] - ps.SYNERGY_LIKE.pay_per_kwh * e.energy_kwh) < 1e-3 * max(1, e.energy_kwh)
    cust = sum(e.customer_items.values())
    assert abs(cust - e.customer_benefit) < 1e-6


def test_units_wholesale():
    """$ = kWh x ($/MWh) / 1000 on a hand-made activation."""
    m, fam = ps.load_market(), _fam()
    j, d = 3, 900
    hand = (fam.d_net[j, d] * m.rtp[d * ps.N + np.arange(ps.HORIZON)]).sum() / 1000 \
        + fam.gap_kwh[j, d] * HH.eta * m.slot_price[14:26].mean() / 1000
    assert abs(hand - fam.w_actual[j, d]) < TOL


def test_ircr_value_rules():
    """The IRCR value is 0 without calls; with the 'floor' rule the reduction is at most the baseline import (here 0,
    the battery already covers the home at the peak); with 'net' exports count."""
    m, fam, peaks = ps.load_market(), _fam(), ps.peak_intervals()
    none = ps.Calls(np.array([], int), np.array([], int), np.array([], object))
    assert ps.ircr_value(none, fam, S2B, m, 2025, peaks) == 0.0
    j = fam.index(ps.ircr_start(2.0))
    days = np.array(sorted(set(peaks[2025][0])))
    calls = ps.Calls(days, np.full(len(days), j), np.array(["ircr"] * len(days), object))
    b = ps.baseline(HH)
    base = b.imp[peaks[2025][0] * ps.N + peaks[2025][1]] - b.exp[peaks[2025][0] * ps.N + peaks[2025][1]]
    floor = ps.ircr_value(calls, fam, replace(S2B, capacity="floor"), m, 2025, peaks)
    assert floor <= np.median(np.maximum(base, 0)) / 1000 * ps.ct.RCP[2026] + TOL
    assert ps.ircr_value(calls, fam, S2B, m, 2025, peaks) > 0


# -------------------------------------------------------------------------------------------- contract and timing
def test_activation_limits_every_contract_year():
    """Calls never exceed the cap in any contract year, at most one a day, inside the event window, standby after
    the 09:00 decision plus notice, standby + event within the control limit; for every rule kind."""
    m, fc = ps.load_market(), ps.load_forecasts()
    for cap in (3, 10, 30):
        p = replace(ps.SYNERGY_LIKE, max_events=cap, pay_per_kwh=0.2)
        for obl in (S1, S2B):
            fit = ps.fit_rule(p, obl, ps.Participation())
            for rule in (None, ps.CallRule("simple"), ps.CallRule("perfect"), ps.CallRule("optimised", 0.0, 0.5, False)):
                for period in ("in-sample", "test", "FY2023", "FY2024"):
                    e = ps.evaluate_policy(p, market_obligations=obl, rule=rule, period=period, fit=fit)
                    assert e.activations <= cap and e.remaining_allowance >= 0, (cap, obl.name, rule, period)
                    assert sum(e.violations.values()) == 0, (cap, obl.name, rule, period, e.violations)


def test_decisions_use_only_0900_information():
    """Changing actual prices from 09:00 on does not change any call (the settlement changes, the decisions do not)."""
    m = ps.load_market()
    rng = np.random.default_rng(2)
    shocked = m.rtp * rng.uniform(0.0, 3.0, len(m.rtp))
    p = replace(ps.SYNERGY_LIKE, pay_per_kwh=0.2, max_events=60)
    fit = ps.fit_rule(p, S1, ps.Participation())
    fit = replace(fit, rule=replace(fit.rule, theta=-1e9))   # call on every day the rule can, to exercise decisions
    for period in ("in-sample", "test"):
        a = ps.evaluate_policy(p, market_obligations=S1, period=period, fit=fit)
        b = ps.evaluate_policy(p, market_obligations=S1, period=period, fit=fit, prices=shocked)
        assert a.activations == b.activations and a.energy_calls == b.energy_calls
        assert a.energy_kwh == b.energy_kwh
        assert a.revenue["wholesale"] != b.revenue["wholesale"]


def test_peak_day_flags_do_not_look_ahead():
    """A day's peak-day flag (lag 1: public at 09:00 on the day) is unchanged when that day's and later days' actual
    maxima are changed."""
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["req_energy_mw"])
    actual = sb.daily_max(df30["req_energy_mw"])
    p0 = sb.peak_day_predictor(0)
    base = sb.dynamic_flags(p0, actual, 50.0, 1)
    for day in pd.to_datetime(["2024-01-10", "2025-02-01", "2026-01-21"]):
        shocked = actual.copy()
        shocked[shocked.index >= day] *= 1.5
        again = sb.dynamic_flags(p0, shocked, 50.0, 1)
        assert (day in base) == (day in again), day


def test_fit_uses_only_building_data():
    """The expected profiles behind the 09:00 scores average days up to 2026-06-30 only; the contract years the rule
    is fitted on end by then."""
    m, fam = ps.load_market(), _fam()
    a = ps.expected_profiles(fam, m)
    test_days = m.days > TRAIN_END
    fam2 = replace(fam, d_net=fam.d_net.copy(), paid_kwh=fam.paid_kwh.copy(), standby_kwh=fam.standby_kwh.copy())
    fam2.d_net[:, test_days] += 99.0
    fam2.paid_kwh[:, test_days] += 99.0
    fam2.standby_kwh[:, test_days] += 99.0
    b = ps.expected_profiles(fam2, m)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))
    years = ps.contract_years(m)
    assert m.days[years[2025][-1]] == TRAIN_END and m.days[years[2026][0]] == CUTOFF


def test_participation_curve():
    part = ps.Participation()
    b = np.linspace(-50, 500, 100)
    s = np.array([part.share(x) for x in b])
    assert (s[b < part.min_benefit] == 0).all()
    assert (np.diff(s[b >= part.min_benefit]) >= 0).all() and s.max() <= part.max_share


def test_reference_policy_matches_verified_terms():
    """The Synergy-like reference uses the verified Battery Rewards terms (V3, 6 May 2026)."""
    p = ps.SYNERGY_LIKE
    assert p.pay_per_kwh == 0.70 and p.max_events == 30 and p.event_h <= 4.0 and p.standby_h + p.event_h <= 6.0
    assert p.offset_rate == ps.RETAIL and ps.Participation().debs_on_event_exports


def main() -> int:
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:  # noqa: BLE001  report every failure, then exit non-zero
            failed += 1
            print(f"FAIL  {name}\n{traceback.format_exc()}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
