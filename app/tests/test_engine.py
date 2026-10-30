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


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("day", DAYS)
def test_plan_explains_each_decision(storage, day, mode):
    """Every half-hour and every run of one action has a plain-language reason, a charge, discharge or shave says why
    and not only what, and every number in the text was computed (app/engine/explain.py)."""
    p = storage.plan(day, *mode)
    e, act = p["explain"], p["plan"]["action"]
    assert e["unaccounted"] == [] and len(e["by_halfhour"]) == 48 and all(e["by_halfhour"]) and e["day"]
    segs = e["segments"]
    assert segs[0]["from"] == 2 and segs[-1]["to"] == 48 and all(a["to"] == b["from"] for a, b in zip(segs, segs[1:]))
    assert all(set(act[s["from"]:s["to"]]) == {s["action"]} and s["text"] for s in segs)
    for k in range(2, 48):
        if act[k] != "hold":
            body = e["by_halfhour"][k].split(" Grid services:")[0]
            assert body.count(". ") >= 1, body
    # the grid-service sentences price the standby at the plan's own FCESS prices: they add up to the plan's FCESS value
    from app.engine.storage import SERVICES
    import numpy as np
    rec = storage.record_0900(storage.replay(day, *mode)["records"])
    total = sum((np.clip(np.asarray(rec[k], float), 0, None) * np.asarray(rec["fcess_prices"][k], float)).sum() * 0.5
                for k in SERVICES)
    assert math.isclose(total, p["plan"]["value_at_forecast_aud"]["fcess"], abs_tol=0.01)


@pytest.mark.parametrize("mode", MODES)
def test_outcome_explains_each_half_hour(storage, mode):
    """What happened is told against the decision in effect at each half-hour's start: the MW the 5-minute steps
    followed (e5 re-plans every half-hour, v1 keeps the 09:00 plan), with its reasons; every number was computed."""
    out = storage.outcome(DAYS[0], *mode)
    assert out["explain"]["unaccounted"] == [] and len(out["explain"]["by_halfhour"]) == 48
    dec = out["explain"]["decisions"]
    assert dec[:2] == [None, None] and all(dec[2:])
    pw_plan = storage.replay(DAYS[0], *mode)["steps"].sort_values("step")["pw_plan"].to_numpy()[::6]
    assert all(abs(dec[k]["mw"] - pw_plan[k]) < 1e-3 for k in range(2, 48))
    if mode[1] == "e5":
        assert dec[3]["at"] == "09:30" and not dec[3]["is_0900"] and dec[3]["points"]
    else:
        assert dec[3]["is_0900"]
    assert all(p["say"] == "Actual" for p in out["explain"]["points"])


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


def test_explain_rounds_like_the_page():
    """Numbers in a reason read as the page's toLocaleString writes them: halves away from zero on the exact value."""
    from app.engine.explain import Facts
    f = Facts()
    assert f.aud("x", 35174.5) == "A$35,175" and f.aud("x", -2.5) == "−A$3"
    assert f("x", 0.125, "{:.2f}") == "0.13" and f("x", 8.745, "{:.2f}") == "8.74"  # 8.745 is stored as 8.74499…
    assert f.pct("x", 0.15) == "15%" and f("x", 2027, "{:d}") == "2027"


@pytest.mark.parametrize("day", ("2025-12-01", "2026-01-06", "2026-01-20", "2026-02-02", "2026-09-09"))
@pytest.mark.parametrize("offer", ("custom", "ref"))
def test_call_explains_decision(fleet, day, offer):
    """Season start, not flagged, flagged, an official peak day, outside the Hot Season: the reason opens with the
    decision, every number was computed, and hindsight is said only in the hindsight view."""
    for hind in (False, True):
        c = fleet.call_day(offer, day, {}, hindsight=hind)
        lines = c["explain"]["lines"]
        assert c["explain"]["unaccounted"] == []
        assert lines[0].startswith("Call " if c["decision"]["call"] else "No call")
        assert any("In hindsight" in x for x in lines) == (hind and c["hot_season"] is not None)
    mine = fleet.call_day(offer, "2026-01-06", {"2026-01-06": {"call": True, "start": "18:00"}})
    assert mine["explain"]["lines"][0].startswith("Your decision: call 18:00") and not mine["explain"]["unaccounted"]


def test_fleet_season_hides_future(fleet):
    s = fleet.season_days("custom", {}, through="2026-01-06")
    later = [d for d in s["days"] if d["day"] > "2026-01-06"]
    assert later and all(d["actual_peak_mw"] is None and d["official_peak_day"] is None for d in later)
    assert not any(d["called"] or d["flagged"] or d["forecast_mw"] is not None for d in later)
