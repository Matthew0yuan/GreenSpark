"""The HTTP API end to end, with the real engine workers and a throwaway database.

  .venv/Scripts/python.exe -m pytest app/tests -q
"""

import os
import tempfile
import time
from pathlib import Path

import pytest

DAY, CALL_DAY = "2026-01-20", "2026-01-06"


@pytest.fixture(scope="module")
def client():
    os.environ["GS_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
    from fastapi.testclient import TestClient

    from app.backend.main import app
    with TestClient(app) as c:
        t = time.time()
        while time.time() - t < 240:
            st = c.get("/api/v1/meta").json()["engines"]
            if all(v["state"] == "ready" for v in st.values()):
                break
            assert not any(v["state"] == "failed" for v in st.values()), st
            time.sleep(1)
        yield c


def test_meta(client):
    m = client.get("/api/v1/meta").json()
    assert m["storage"]["dispatcher"]["short"] == "v1" and m["storage"]["days"] == 570


def test_plan_then_outcome(client):
    p = client.get(f"/api/v1/storage/days/{DAY}/plan")
    assert p.status_code == 200 and p.json()["basis"] == "plan" and "money_aud" not in p.text
    o = client.get(f"/api/v1/storage/days/{DAY}/outcome").json()
    assert o["basis"] == "outcome" and abs(o["replay_check"]["replay_minus_saved_total_aud"]) < 1e-6


def test_blocked_and_bad_input(client):
    assert client.get(f"/api/v1/storage/days/{DAY}/plan?export_mw=3").json()["state"] == "BLOCKED"
    assert client.get("/api/v1/storage/days/2024-01-01/plan").status_code == 404
    assert client.get(f"/api/v1/storage/days/{DAY}/plan?fcess=bogus").status_code == 422


def test_fcess_off_recomputes(client):
    a = client.get(f"/api/v1/storage/days/{DAY}/plan").json()
    b = client.get(f"/api/v1/storage/days/{DAY}/plan?fcess=").json()
    assert b["unvalidated"] and b["settings"]["fcess"] == []
    assert b["plan"]["value_at_forecast_aud"]["fcess"] == 0 and a["plan"]["value_at_forecast_aud"]["fcess"] > 0


def test_operator_decision_round_trip(client):
    base = client.get(f"/api/v1/fleet/days/{CALL_DAY}/call").json()
    flip = not base["decision"]["call"]
    r = client.put(f"/api/v1/fleet/days/{CALL_DAY}/decision", json={"offer": "custom", "call": flip, "start": "17:30"})
    assert r.status_code == 200 and r.json()["decision"]["source"] == "operator"
    again = client.get(f"/api/v1/fleet/days/{CALL_DAY}/call").json()
    assert again["decision"]["call"] == flip  # stored
    v = client.get("/api/v1/fleet/value").json()
    assert v["offers"]["custom"]["your_calls"] is not None
    undo = client.delete(f"/api/v1/fleet/days/{CALL_DAY}/decision?offer=custom").json()
    assert undo["decision"]["source"] == "model" and undo["decision"]["call"] == base["decision"]["call"]
    assert client.put(f"/api/v1/fleet/days/{CALL_DAY}/decision", json={"call": True, "start": "03:00"}).status_code == 422


def test_evaluate_terms(client):
    r = client.post("/api/v1/fleet/offers/evaluate", json={"offer": "ref", "pay_c_per_kwh": 50}).json()
    assert r["unvalidated"] and r["terms"]["pay_per_kwh"] == 0.5 and "zenith" in r["parties_aud"]


def test_spa_fallback(client):
    r = client.get("/api/v1/nothing-here")
    assert r.status_code == 404
