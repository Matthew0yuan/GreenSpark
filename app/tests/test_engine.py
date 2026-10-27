"""The engine adapter against the saved backtests: a replayed day equals the saved ledger, the 09:00 plan holds no
actual price after 09:00, the checks block an over-limit connection, and the customer-battery accounts reproduce the
policy study.

  .venv/Scripts/python.exe -m pytest app/tests -q
"""

import json
import math

import pandas as pd
import pytest

from app.engine.common import STANDALONE

DAYS = ("2026-01-20", "2026-09-09")  # in-sample Hot Season, test (reviewed)


@pytest.fixture(scope="module")
def storage():
    from app.engine.storage import Storage
    return Storage()


@pytest.fixture(scope="module")
def fleet():
    from app.engine.fleet import Fleet
    return Fleet()


def finite_json(x) -> bool:
    json.dumps(x, allow_nan=False)
    return True


@pytest.mark.parametrize("day", DAYS)
def test_outcome_equals_saved_ledger(storage, day):
    out = storage.outcome(day)
    assert abs(out["replay_check"]["replay_minus_saved_total_aud"]) < 1e-6
    assert abs(out["replay_check"]["replay_minus_saved_soc_end_mwh"]) < 1e-9
    daily = pd.read_csv(STANDALONE / "daily.csv")
    row = daily[(daily["day"] == day) & (daily["strategy"] == storage.adopted)].iloc[0]
    assert math.isclose(out["money_aud"]["net"], row["total"], abs_tol=1e-4)
    assert out["baseline_same_day"] is not None and finite_json(out)


@pytest.mark.parametrize("day", DAYS)
def test_plan_has_no_actuals_after_0900(storage, day):
    p = storage.plan(day)
    assert p["basis"] == "plan" and finite_json(p)
    flat = json.dumps(p)
    for k in ("price_actual", "money_aud", "offer_cleared_mw", "hindsight"):
        assert k not in flat
    assert all(v is None for v in p["price"]["actual_before_0900"][2:])
    assert len(p["plan"]["battery_mw"]) == 48 and len(p["plan"]["soc_mwh"]) == 49
    ids = {c["id"]: c["status"] for c in p["checks"]}
    assert ids["info_timing"] == "pass" and ids["power"] == "pass" and ids["connection_export"] == "pass"
    assert ids["contractual"] == "not_checked"
    assert p["state"] == "PASS"


def test_export_limit_blocks(storage):
    p = storage.plan(DAYS[0], export_mw=3.0)
    assert p["state"] == "BLOCKED" and p["unvalidated"]
    assert p["blocked_halfhours"] and min(p["blocked_halfhours"]) >= 2


def test_evidence(storage):
    from app.engine.evidence import evidence
    e = evidence()
    assert e["adopted"] == "v1" and len(e["periods"]) == 3
    ins = e["periods"][0]
    assert ins["period"]["days"] == 456 and ins["candidate_minus_adopted"]["lo95"] < 0
    assert all(x["over_power"] == 0 and x["soc_outside"] == 0 for x in e["limits"])
    assert finite_json(e)


def test_fleet_reproduces_study(fleet):
    saved = pd.read_csv("reports/policy/policy_backtest.csv")
    for oid, case in (("ref", "B reference"), ("custom", "C optimised")):
        v = fleet.value(oid, 2025, {})
        s = saved[(saved["scenario"] == "S2b") & (saved["case"] == case) & (saved["period"] == "in-sample")].iloc[0]
        assert v["calls"] == int(s["activations"])
        assert math.isclose(v["parties_aud"]["zenith"], s["zenith_profit"], abs_tol=0.01)
        assert v["peak_days_covered"] == int(s["covered_2025"])


def test_operator_call_changes_count_and_money(fleet):
    day = "2026-01-06"
    base = fleet.call_day("custom", day, {})
    flip = {"call": not base["decision"]["call"], "start": "17:30"}
    mine = fleet.call_day("custom", day, {day: flip})
    assert mine["decision"]["source"] == "operator" and mine["decision"]["call"] == flip["call"]
    assert mine["calls"]["used_with_today"] - mine["calls"]["used_before"] == int(flip["call"])
    a, b = fleet.value("custom", 2025, {}), fleet.value("custom", 2025, {day: flip})
    assert a["calls"] != b["calls"] and a["parties_aud"]["zenith"] != b["parties_aud"]["zenith"]
    assert "hindsight" not in mine


def test_fleet_season_hides_future(fleet):
    s = fleet.season_days("custom", {}, through="2026-01-06")
    later = [d for d in s["days"] if d["day"] > "2026-01-06"]
    assert later and all(d["actual_peak_mw"] is None and d["official_peak_day"] is None for d in later)
    assert not any(d["called"] or d["flagged"] or d["forecast_mw"] is not None for d in later)
