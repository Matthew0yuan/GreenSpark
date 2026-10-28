"""The engine adapter against the saved backtests, for each connection (behind the factory meter, standalone) and
dispatcher (e5, v1): a replayed day equals the saved ledger, the 09:00 plan holds no actual price or load after 09:00,
the checks block an over-limit connection, the evidence follows the selection rule; and the customer-battery accounts
reproduce the policy study.

  .venv/bin/python -m pytest app/tests -q          (Windows: .venv/Scripts/python.exe)
"""

import json
import math

import pandas as pd
import pytest

from app.engine.common import STRATEGY
from app.engine.evidence import saved_runs

DAYS = ("2026-01-20", "2026-09-09")  # in-sample Hot Season, test (reviewed)
MODES = (("factory", "e5"), ("factory", "v1"), ("standalone", "e5"), ("standalone", "v1"))


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


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("day", DAYS)
def test_outcome_equals_saved_ledger(storage, day, mode):
    out = storage.outcome(day, *mode)
    assert abs(out["replay_check"]["replay_minus_saved_total_aud"]) < 1e-6
    assert abs(out["replay_check"]["replay_minus_saved_soc_end_mwh"]) < 1e-9
    money = saved_runs(mode[0])[1][STRATEGY[mode]]  # behind the meter: the ledger + the month's bill saving per day
    assert math.isclose(out["money_aud"]["net"], money.loc[pd.Timestamp(day)], abs_tol=1e-4)
    assert (out["site"] is not None) == (mode[0] == "factory")
    assert out["baseline_same_day"] is not None and finite_json(out)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("day", DAYS)
def test_plan_has_no_actuals_after_0900(storage, day, mode):
    p = storage.plan(day, *mode)
    assert p["basis"] == "plan" and finite_json(p)
    flat = json.dumps(p)
    for k in ("price_actual", "money_aud", "offer_cleared_mw", "hindsight", '"load_mw"', "cap_excess"):
        assert k not in flat
    assert all(v is None for v in p["price"]["actual_before_0900"][2:])
    assert len(p["plan"]["battery_mw"]) == 48 and len(p["plan"]["soc_mwh"]) == 49
    if mode[0] == "factory":  # from 09:00 the meter is the forecast load less the plan, never the metered load
        s = p["site"]
        assert len(s["meter_mw"]) == 48 and len(s["load_fc_mw"]) == 48
        for k in range(2, 48):
            assert math.isclose(s["meter_mw"][k], s["load_fc_mw"][k] - p["plan"]["battery_mw"][k], abs_tol=1e-5)
    else:
        assert p["site"] is None
    ids = {c["id"]: c["status"] for c in p["checks"]}
    assert ids["info_timing"] == "pass" and ids["power"] == "pass" and ids["connection_export"] == "pass"
    assert ids["contractual"] == "not_checked"
    assert p["state"] == "PASS"


@pytest.mark.parametrize("connection, limit", (("standalone", {"export_mw": 3.0}), ("factory", {"import_mw": 6.0})))
def test_connection_limit_blocks(storage, connection, limit):
    # standalone: 3 MW export is below the plan's discharge; behind the meter: 6 MW import is below the factory's load
    p = storage.plan(DAYS[0], connection, "e5", **limit)
    assert p["state"] == "BLOCKED" and p["unvalidated"]
    assert p["blocked_halfhours"] and min(p["blocked_halfhours"]) >= 2


@pytest.mark.parametrize("mode", MODES)
def test_evidence(storage, mode):
    from app.engine.evidence import evidence
    e = evidence(*mode)
    assert e["adopted"] == mode[1] and e["connection"] == mode[0] and len(e["periods"]) == 3
    ins = e["periods"][0]
    assert ins["period"]["days"] == 456
    # the selection rule at the app's hurdle: e5 passes behind the factory meter, not on its own connection
    assert e["verdict"]["passed"] == (mode[0] == "factory")
    assert all(x["over_power"] == 0 and x["soc_outside"] == 0 for x in e["limits"])
    assert (e["site"] is not None) == (mode[0] == "factory")
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
