"""Checks for fixed_dispatch.py (PLAN.md section 7). Plain test_* functions (pytest finds them); without pytest:

Run:  .venv/Scripts/python.exe analysis/2026-10-24/fixed_synergy_dispatch/test_fixed_dispatch.py
"""

import sys
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixed_dispatch as fd  # noqa: E402
from fixed_dispatch import ps  # noqa: E402


def _period(y: int) -> np.ndarray:
    return fd.periods(ps.load_market())[y]


def test_predispatch_public_by_0900():
    """Every same-day pre-dispatch row the rules use was published at or before its 09:00 issue time."""
    assert fd.peak_inputs()["late_rows"] == 0


def test_margins_do_not_look_ahead():
    """A day's margin is unchanged when that day's and later days' actual maxima change."""
    m = ps.load_market()
    x = fd.peak_inputs()
    season = ps.season_of(m)
    for y in fd.SEASONS:
        days = fd.season_days(m, y)
        base = fd.margin_series(y, x["f0"], x["f1"], x["actual"], season)
        for d0 in days[[0, 10, 60, 110]]:
            shocked = x["actual"].copy()
            shocked[d0:] *= 1.5
            again = fd.margin_series(y, x["f0"], x["f1"], shocked, season)
            assert np.isclose(base[d0], again[d0], equal_nan=True), (y, d0)


def test_plans_ignore_actual_prices():
    """Shocking actual prices does not change any decision of the paced or reference rule."""
    m = ps.load_market()
    fc = ps.load_forecasts()
    led = fd.LEDGERS["L2"]
    train = (2023, 2024)
    rule = fd.PacedRule(True, 1.0, 2.0, "forecast", 0.0)
    days = _period(2025)
    pacer = fd.Pacer(train)
    a = fd.select_paced(days, rule, led, pacer, fc)
    r = fd.select_reference(days, fd.RefRule(0.75, 0.0), led, train, fc)
    saved = m.rtp.copy()
    try:
        m.rtp[:] = saved * np.random.default_rng(3).uniform(0, 3, len(saved))
        b = fd.select_paced(days, rule, led, pacer, fc)
        r2 = fd.select_reference(days, fd.RefRule(0.75, 0.0), led, train, fc)
    finally:
        m.rtp[:] = saved
    assert np.array_equal(a.day, b.day) and np.array_equal(a.start, b.start)
    assert np.array_equal(r.day, r2.day) and np.array_equal(r.start, r2.start)


def test_training_data_end_by_cutoff():
    m = ps.load_market()
    for y in fd.SEASONS:
        assert m.days[_period(y)[-1]] <= fd.TRAIN_END


def test_single_call_sequential_equals_independent():
    """One isolated call: the exact run's wholesale value and billed export equal the precomputed call."""
    m = ps.load_market()
    days = _period(2025)
    d = int(np.flatnonzero(m.days == np.datetime64("2026-01-21"))[0])
    fam = ps.family(*fd.fam_key(2.0))
    s = ps.ircr_start(2.0)
    plan = fd.Plan(np.array([d]), np.array([s]), np.array([2.0]), np.array(["ircr"], object))
    out = fd.settle(plan, days, fd.LEDGERS["L2"])
    j = fam.index(s)
    assert abs(out["wholesale"] - fd.RESPONSE * fam.w_actual[j, d]) < 1e-6
    assert abs(out["billed_kwh"] - fd.RESPONSE * fam.paid_kwh[j, d]) < 1e-6
    assert out["violations"] == 0


def test_dense_calls_pass_hard_checks():
    """Thirty calls on consecutive days (state of charge carried from call to call) break no hard limit."""
    m = ps.load_market()
    days = _period(2025)
    start = int(np.flatnonzero(m.days == np.datetime64("2025-12-01"))[0])
    for eh in fd.LENGTHS:
        plan = fd.Plan(np.arange(start, start + 30), np.full(30, ps.ircr_start(eh)), np.full(30, eh),
                       np.array(["ircr"] * 30, object))
        out = fd.settle(plan, days, fd.LEDGERS["L1"])
        assert out["violations"] == 0, (eh, {k: v for k, v in out.items() if k.startswith("v_")})
        assert out["calls"] == 30


def test_parties_reconcile():
    """Zenith + customer + retailer = wholesale + full IRCR value - wear - hassle - operations (transfers cancel)."""
    m = ps.load_market()
    days = _period(2025)
    start = int(np.flatnonzero(m.days == np.datetime64("2026-01-15"))[0])
    plan = fd.Plan(np.arange(start, start + 10), np.full(10, ps.ircr_start(2.0)), np.full(10, 2.0),
                   np.array(["ircr"] * 10, object))
    for led in fd.LEDGERS.values():
        o = fd.settle(plan, days, led)
        system = o["wholesale"] + o["ircr_value_full"] - o["customer_wear"] - o["customer_hassle"] - o["ops"]
        assert abs(o["zenith"] + o["customer"] + o["retailer"] - system) < 1e-6, led.name


def test_rules_respect_cap_and_one_call_a_day():
    fc = ps.load_forecasts()
    for key, led in fd.LEDGERS.items():
        for y in (2024, 2025):
            days = _period(y)
            train = tuple(s for s in fd.SEASONS if s < y)
            for plan in (fd.select_paced(days, fd.PacedRule(True, 3.0, 1.5, "forecast", 0.0), led, fd.Pacer(train), fc),
                         fd.select_reference(days, fd.RefRule(0.9, 0.0), led, train, fc),
                         fd.select_perfect(days, led, 2.0)):
                assert len(plan.day) <= fd.CAP and len(set(plan.day.tolist())) == len(plan.day), (key, y)
                assert (plan.event_h <= fd.MAX_EVENT_H).all()


def main() -> int:
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}\n{traceback.format_exc()}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
