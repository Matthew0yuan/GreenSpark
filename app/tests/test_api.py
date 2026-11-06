"""The HTTP API end to end, with the real engine workers and a throwaway database.

  .venv/bin/python -m pytest app/tests -q          (Windows: .venv/Scripts/python.exe)
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
    os.environ["GS_CONFIGS"] = tempfile.mkdtemp()  # no battery and CMD of the user's own
    os.environ["GS_LIVE"] = "0"  # the replay (to 2026-10-22); live data have their own tests (test_live.py)
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
    m = client.get("/api/v1/meta").json()["storage"]
    assert m["defaults"] == {"connection": "factory", "dispatcher": "e5"} and m["dispatcher"]["short"] == "e5"
    assert m["days"] == 570 and m["verdicts"]["factory"]["passed"] and not m["verdicts"]["standalone"]["passed"]


def test_market_data_cannot_be_switched_here(client):
    d = client.get("/api/v1/meta").json()["data_mode"]
    assert d["locked"] and not d["live"] and not d["switching"] and d["setup"] is None
    r = client.post("/api/v1/live", json={"on": True})
    assert r.status_code == 409 and "GS_LIVE" in r.json()["detail"]


def test_login(client, monkeypatch):
    """GS_AUTH (the demo on a public address, deploy/): every page and API route asks for it; unset, nothing changes."""
    import base64
    monkeypatch.setenv("GS_AUTH", "demo:s3cret")
    basic = lambda s: {"Authorization": "Basic " + base64.b64encode(s.encode()).decode()}  # noqa: E731
    for path in ("/", "/api/v1/meta", f"/api/v1/storage/days/{DAY}/plan", "/docs"):
        r = client.get(path)
        assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic"), path
    for bad in (basic("demo:wrong"), basic("demo:s3cret2"), {"Authorization": "Basic !!"}, {"Authorization": "Bearer x"}):
        assert client.get("/api/v1/meta", headers=bad).status_code == 401
    assert client.get("/api/v1/meta", headers=basic("demo:s3cret")).status_code == 200
    monkeypatch.delenv("GS_AUTH")
    assert client.get("/api/v1/meta").status_code == 200


def test_plan_then_outcome(client):
    p = client.get(f"/api/v1/storage/days/{DAY}/plan")
    assert p.status_code == 200 and p.json()["basis"] == "plan" and "money_aud" not in p.text
    o = client.get(f"/api/v1/storage/days/{DAY}/outcome").json()
    assert o["basis"] == "outcome" and abs(o["replay_check"]["replay_minus_saved_total_aud"]) < 1e-6


def test_blocked_and_bad_input(client):
    assert client.get(f"/api/v1/storage/days/{DAY}/plan?connection=standalone&export_mw=3").json()["state"] == "BLOCKED"
    assert client.get(f"/api/v1/storage/days/{DAY}/plan?import_mw=6").json()["state"] == "BLOCKED"
    assert client.get("/api/v1/storage/days/2024-01-01/plan").status_code == 404
    assert client.get(f"/api/v1/storage/days/{DAY}/plan?fcess=bogus").status_code == 422
    assert client.get(f"/api/v1/storage/days/{DAY}/plan?connection=roof").status_code == 422
    assert client.get("/api/v1/storage/evidence?dispatcher=v9").status_code == 422


def test_modes(client):
    for c, d in (("standalone", "v1"), ("factory", "v1")):
        p = client.get(f"/api/v1/storage/days/{DAY}/plan?connection={c}&dispatcher={d}").json()
        assert p["connection"] == c and p["dispatcher"]["short"] == d and not p["unvalidated"]
        assert (p["site"] is not None) == (c == "factory")
        e = client.get(f"/api/v1/storage/evidence?connection={c}&dispatcher={d}").json()
        assert e["adopted"] == d and e["connection"] == c


def test_fcess_off_recomputes(client):
    a = client.get(f"/api/v1/storage/days/{DAY}/plan").json()
    b = client.get(f"/api/v1/storage/days/{DAY}/plan?fcess=").json()
    assert b["unvalidated"] and b["settings"]["fcess"] == []
    assert b["plan"]["value_at_forecast_aud"]["fcess"] == 0 and a["plan"]["value_at_forecast_aud"]["fcess"] > 0


def test_battery_and_cmd_are_refused_without_a_backtest(client):
    """Nothing is computed from another configuration's backtest; the tested values are no change, however given."""
    for q in ("energy_mwh=35", "cmd_mw=7", "energy_mwh=35&cmd_mw=7", "connection=standalone&energy_mwh=35"):
        r = client.get(f"/api/v1/storage/days/{DAY}/plan?{q}")
        assert r.status_code == 409 and "Settings" in r.json()["detail"], q
        assert client.get(f"/api/v1/storage/days/{DAY}/outcome?{q}").status_code == 409, q
    assert client.get("/api/v1/storage/evidence?cmd_mw=7").status_code == 409
    state = client.get("/api/v1/storage/config?energy_mwh=35&cmd_mw=7").json()
    assert not state["ready"] and state["periods"] is None and "35 MWh" in state["reason"]
    assert client.post("/api/v1/jobs/storage-backtest", json={"energy_mwh": 35}).status_code == 409
    for q in ("energy_mwh=9", "energy_mwh=81", "energy_mwh=35.5", "cmd_mw=4.5", "cmd_mw=12.5", "cmd_mw=7.3"):
        assert client.get(f"/api/v1/storage/days/{DAY}/plan?{q}").status_code == 422, q
    assert client.get(f"/api/v1/storage/days/{DAY}/plan?start_mwh=41").status_code == 422  # more than the battery holds
    base = client.get(f"/api/v1/storage/days/{DAY}/plan").json()
    same = client.get(f"/api/v1/storage/days/{DAY}/plan?energy_mwh=40&cmd_mw=9").json()
    assert same["plan"] == base["plan"] and not same["unvalidated"]
    own = client.get(f"/api/v1/storage/days/{DAY}/plan?connection=standalone&cmd_mw=7").json()  # no CMD on its own connection
    assert own["connection"] == "standalone" and not own["unvalidated"]
    assert client.post("/api/v1/jobs/storage-config", json={"energy_mwh": 40, "cmd_mw": 9}).json() == {"ready": True, "id": None}


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="process groups")
def test_cancelled_build_leaves_no_process_and_nothing_usable(client):
    from app.backend import jobs
    body = {"energy_mwh": 33, "cmd_mw": 8}
    r = client.post("/api/v1/jobs/storage-config", json=body).json()
    assert not r["ready"] and r["id"] is not None
    assert client.post("/api/v1/jobs/storage-config", json=body).json()["id"] == r["id"]  # not a second one
    assert client.post("/api/v1/jobs/storage-config", json={"energy_mwh": 34}).status_code == 409  # one at a time
    group = jobs.RUNNING[r["id"]].pid
    time.sleep(8)  # into the value table's process pool
    assert client.delete(f"/api/v1/jobs/{r['id']}").json()["cancelled"]
    t = time.time()
    while time.time() - t < 20:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            break
        time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.killpg(group, 0)
    assert client.get(f"/api/v1/jobs/{r['id']}").json()["state"] == "cancelled"
    assert not client.get("/api/v1/storage/config?energy_mwh=33&cmd_mw=8").json()["ready"]
    for table in Path(__file__).resolve().parents[2].glob("data/processed/v2_value_median_33mwh_*"):
        table.unlink()  # the value table, if it was finished before the cancel: nothing else removes it


def _span(client, end, days, **kw):
    r = client.get("/api/v1/storage/span", params={"end": end, "days": days, **kw})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("connection,dispatcher", [("factory", "e5"), ("factory", "v1"), ("standalone", "e5"), ("standalone", "v1")])
def test_span_shows_what_the_day_page_shows(client, connection, dispatcher):
    """Each half-hour of several days against the same day's outcome and plan pages, which replay the day."""
    mode = {"connection": connection, "dispatcher": dispatcher}
    s = _span(client, "2026-01-22", 5, **mode)
    assert s["resolution"] == "halfhour" and [r["day"] for r in s["rows"]] == [f"2026-01-{d}" for d in range(18, 23)]
    for r in s["rows"][::2]:
        o = client.get(f"/api/v1/storage/days/{r['day']}/outcome", params=mode).json()
        p = client.get(f"/api/v1/storage/days/{r['day']}/plan", params=mode).json()
        h = r["halfhours"]
        assert h["price_actual"] == pytest.approx(o["price_actual"], abs=0.006)
        assert h["battery_mw"] == pytest.approx(o["battery_mw"], abs=0.001)
        assert h["stored_mwh"] == pytest.approx(o["soc_mwh"], abs=0.001)
        assert h["price_forecast"][2:] == pytest.approx(p["price"]["forecast_p50"][2:], abs=0.006)
        assert h["price_p10"] == p["price"]["forecast_p10"] and h["price_p90"] == p["price"]["forecast_p90"]
        for f, got in (("energy", o["money_aud"]["energy"]), ("fcess", o["money_aud"]["fcess"]), ("wear", o["money_aud"]["wear"]), ("net", o["money_aud"]["net"])):
            assert r["money_aud"][f] == pytest.approx(got, abs=0.01), f
        assert r["discharged_mwh"] == pytest.approx(o["discharged_mwh"], abs=0.001)
        if connection == "factory":
            assert h["meter_mw"] == pytest.approx(o["site"]["meter_mw"], abs=0.001)
            assert h["load_mw"] == pytest.approx(o["site"]["load_mw"], abs=0.001)
            assert h["cap_mw"] == pytest.approx(o["site"]["cap_mw"], abs=0.001)
            assert r["money_aud"]["network"] == pytest.approx(o["money_aud"]["network"], abs=0.01)
        else:
            assert h["meter_mw"] is None and r["site"] is None and r["money_aud"]["network"] is None


def test_span_year_adds_up_to_the_saved_money(client):
    """A year's profit against the saved money table (what Evidence averages), and each day's figures against its half-hours."""
    import pandas as pd
    s = _span(client, "2026-10-22", 365)
    assert s["resolution"] == "day" and s["days"] == 365 and s["start"] == "2025-10-23" and s["network_to"] == "2026-10-22"
    assert all(r["halfhours"] is None for r in s["rows"])
    money = pd.read_csv(Path(__file__).resolve().parents[2] / "analysis/2026-10-28/app_factory_h50/money.csv", index_col=0, parse_dates=True)["rw50+state t5+roll"]
    want = money["2025-10-23":"2026-10-22"]
    assert s["totals"]["net"] == pytest.approx(want.sum(), abs=2.0) and s["totals"]["per_day"] == pytest.approx(want.mean(), abs=0.01)
    assert [r["money_aud"]["net"] for r in s["rows"]] == pytest.approx(want.tolist(), abs=0.01)
    week = _span(client, "2026-01-22", 7)["rows"]
    days = {r["day"]: r for r in s["rows"]}
    for r in week:
        h, y = r["halfhours"], days[r["day"]]
        assert y["price"]["actual_hi"] == pytest.approx(max(h["price_actual"]), abs=0.01)
        assert y["price"]["actual_mean"] == pytest.approx(sum(h["price_actual"][2:]) / 46, abs=0.01)
        assert y["stored_mwh"]["end"] == pytest.approx(h["stored_mwh"][48], abs=0.001) and y["site"]["peak_mw"] == pytest.approx(max(h["meter_mw"]), abs=0.001)
        assert y["charged_mwh"] == pytest.approx(sum(-min(v, 0) for v in h["battery_mw"]) / 2, abs=0.01)


def test_span_limits(client):
    get = lambda **q: client.get("/api/v1/storage/span", params=q)  # noqa: E731
    assert get(end=DAY, days=1).status_code == 422 and get(end=DAY, days=367).status_code == 422 and get(end=DAY, days="x").status_code == 422
    assert get(end="2026-10-23", days=7).status_code == 404 and get(end="not a day", days=7).status_code == 422
    assert get(end=DAY, days=7, connection="moon").status_code == 422
    assert get(end=DAY, days=7, energy_mwh=35).status_code == 409 and get(end=DAY, days=7, cmd_mw=99).status_code == 422
    s = _span(client, "2025-04-05", 365)  # the data start here: fewer days, and the same half-hour or day figures as asked
    assert s["days"] == 5 and s["asked_days"] == 365 and s["start"] == "2025-04-01" and s["resolution"] == "day"
    assert _span(client, "2026-10-22", 366)["days"] == 366


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
