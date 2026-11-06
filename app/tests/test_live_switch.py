"""The switch between the replay and live data (app.backend.main, app.live.run.setup), with the engines, jobs and downloads
stubbed: what must hold whichever way a switch ends. Nothing changes before the new engines are ready, a failure leaves the
server as it was, and a first build that ended early starts again. The real thing is run by hand: it downloads from AEMO.

  .venv/bin/python -m pytest app/tests/test_live_switch.py -q
"""

import asyncio
import datetime as dt
import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("GS_DB", str(Path(tempfile.mkdtemp()) / "test.db"))  # before the API module opens its database


class FakeSchedule:
    def __init__(self, on_prices=None, on_build=None):
        self.stop, self.started = type("E", (), {"set": lambda s: setattr(s, "was_set", True), "was_set": False})(), False
        self.status = {"build": {"state": "idle"}}

    def start(self):
        self.started = True


@pytest.fixture
def api(tmp_path, monkeypatch):
    from app.backend import main as m
    for k in ("GS_LIVE", "GS_AUTH", "GS_DATA"):
        monkeypatch.setenv(k, "x")  # so that monkeypatch restores it, however the test leaves it
        monkeypatch.delenv(k)
    monkeypatch.setattr(m.live_run, "OFF", tmp_path / "live" / "off")
    monkeypatch.setattr(m.live_run, "STATE", tmp_path / "live" / "state.json")
    monkeypatch.setattr(m.live_run.fetch, "LIVE", tmp_path / "live")
    monkeypatch.setattr(m.live_run, "Schedule", FakeSchedule)
    monkeypatch.setattr(m.live_fetch, "DATA", tmp_path / "live" / "data")
    monkeypatch.setattr(m, "MODE", {"live": False, "switching": False, "error": None, "setup": None})
    monkeypatch.setattr(m, "SCHEDULE", None)
    monkeypatch.setattr(m.jobs, "RUNNING", {})
    monkeypatch.setitem(m.engines.RELOAD, "state", None)
    calls = {}

    def reload(on_done=None, data="same"):
        calls.update(on_done=on_done, data=data)

    monkeypatch.setattr(m.engines, "reload", reload)
    m.calls = calls
    return m


def post(m, on):
    return asyncio.run(m.live_switch(m.LiveBody(on=on)))


def built_today(m):
    day = m.live_run.fetch.trading_day(m.live_run.fetch.now())
    m.live_run.fetch.LIVE.mkdir(parents=True, exist_ok=True)
    m.live_run.STATE.write_text(json.dumps({"live_day": str(day), "complete_through": str(day - dt.timedelta(days=1))}))


def test_on_changes_nothing_until_the_new_engines_are_ready(api):
    built_today(api)
    api.live_run.mark_off()
    post(api, True)
    assert api.MODE["switching"] and not api.MODE["live"]
    assert "GS_DATA" not in os.environ and api.live_run.OFF.exists() and api.SCHEDULE is None
    assert api.calls["data"] == str(api.live_fetch.DATA)  # the new engines get it directly
    api.calls["on_done"](None)
    assert api.MODE["live"] and not api.MODE["switching"]
    assert os.environ["GS_DATA"] == str(api.live_fetch.DATA) and not api.live_run.OFF.exists() and api.SCHEDULE.started


def test_a_failed_switch_leaves_the_server_as_it_was(api):
    built_today(api)
    api.live_run.mark_off()
    post(api, True)
    api.calls["on_done"]("storage: it broke")
    assert not api.MODE["live"] and not api.MODE["switching"] and "Nothing has changed" in api.MODE["error"]
    assert "GS_DATA" not in os.environ and api.live_run.OFF.exists() and api.SCHEDULE is None
    assert "Could not switch" in api._data_mode()["error"]


def test_off_goes_back_and_stays_off(api):
    built_today(api)
    post(api, True)
    api.calls["on_done"](None)
    schedule = api.SCHEDULE
    post(api, False)
    assert api.MODE["live"] and os.environ.get("GS_DATA") and api.calls["data"] is None  # still live until they are ready
    api.calls["on_done"](None)
    assert not api.MODE["live"] and "GS_DATA" not in os.environ and api.live_run.OFF.exists()
    assert schedule.stop.was_set and api.SCHEDULE is None


def test_data_behind_today_go_up_to_date_first_and_a_crash_starts_on_the_replay(api, monkeypatch):
    started = {}

    def start(store, kind, params, on_done=None):
        # when the job starts the marker is already there, so a build that dies leaves the server on the replay
        started.update(kind=kind, on_done=on_done, marked=api.live_run.OFF.exists())
        return 7

    monkeypatch.setattr(api.jobs, "start", start)
    monkeypatch.setattr(api.STORE, "job", lambda jid: {"id": jid, "state": "running", "progress": 0.1, "message": "copying"})
    d = post(api, True)
    assert started["kind"] == "live-setup" and started["marked"] and d["setup"]["id"] == 7 and not api.MODE["live"]
    assert "on_done" not in api.calls  # no switch until it has finished well
    started["on_done"]()
    assert api.calls["data"] == str(api.live_fetch.DATA)


@pytest.mark.parametrize("busy", ("switching", "job", "morning", "reload", "gs_live", "gs_auth"))
def test_refused(api, monkeypatch, busy):
    built_today(api)
    if busy == "switching":
        api.MODE["switching"] = True
    elif busy == "job":
        api.jobs.RUNNING[1] = object()
    elif busy == "morning":
        s = FakeSchedule()
        s.status["build"]["state"] = "running"
        monkeypatch.setattr(api, "SCHEDULE", s)
    elif busy == "reload":
        api.engines.RELOAD["state"] = "starting"
    elif busy == "gs_live":
        monkeypatch.setenv("GS_LIVE", "0")
    else:
        monkeypatch.setenv("GS_AUTH", "")  # the demo's compose file sets it even when it is empty
    with pytest.raises(HTTPException) as e:
        post(api, True)
    assert e.value.status_code == 409 and "on_done" not in api.calls and not api.MODE["live"]


def test_no_job_starts_during_a_switch(api):
    api.MODE["switching"] = True
    with pytest.raises(HTTPException) as e:
        api._no_job_during_a_switch()
    assert e.value.status_code == 409


@pytest.fixture
def run(tmp_path, monkeypatch):
    from app.live import run as r
    monkeypatch.setattr(r, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(r.fetch, "DATA", tmp_path / "data")
    monkeypatch.setattr(r.fetch, "predispatch", lambda through: None)
    monkeypatch.setattr(r.fetch, "weather", lambda: None)
    monkeypatch.setattr(r.today, "update", lambda day: {})
    r.copied = []
    monkeypatch.setattr(r, "init", lambda: r.copied.append(sorted(p.name for p in r.fetch.DATA.glob("*"))))
    return r


def test_a_first_build_that_ended_early_starts_again(run, monkeypatch):
    run.fetch.DATA.mkdir()
    (run.fetch.DATA / "half_built.parquet").write_text("x")

    def morning(day, say):
        run.STATE.write_text(json.dumps({"live_day": str(day)}))
        return {"ok": True, "detail": "built"}

    monkeypatch.setattr(run, "morning", morning)
    assert run.setup()["detail"] == "built"
    assert run.copied == [[]]  # the half-built file was gone before the copy


def test_built_data_are_not_thrown_away(run, monkeypatch):
    run.fetch.DATA.mkdir()
    (run.fetch.DATA / "rows.parquet").write_text("x")
    run.STATE.write_text(json.dumps({"live_day": "2026-11-05"}))
    monkeypatch.setattr(run, "morning", lambda day, say: {"ok": False, "wait": True, "detail": "08:00 run not listed yet"})
    assert run.setup()["detail"] == "08:00 run not listed yet"  # usable: yesterday's, and the schedule carries on
    assert (run.fetch.DATA / "rows.parquet").exists()


def test_first_build_before_todays_files_builds_the_days_already_over(run, monkeypatch):
    monkeypatch.setattr(run, "morning", lambda day, say: {"ok": False, "wait": True, "detail": "not listed"})

    def build():
        run.STATE.write_text(json.dumps({"live_day": "2026-11-05"}))
        return True, "built"

    monkeypatch.setattr(run, "build", build)
    assert run.setup()["detail"] == "built"


@pytest.mark.parametrize("r", ({"ok": False, "detail": "the build broke"}, {"ok": False, "wait": True, "detail": "no file"}))
def test_setup_fails_without_usable_data(run, monkeypatch, r):
    monkeypatch.setattr(run, "morning", lambda day, say: r)
    monkeypatch.setattr(run, "build", lambda: (False, "the build broke"))
    with pytest.raises(RuntimeError, match="could not be built"):  # the Settings page shows this line
        run.setup()


def test_a_dropped_connection_is_tried_again(run, monkeypatch):
    import urllib.error
    monkeypatch.setattr(run, "SETUP_WAIT_S", 0)
    tries = []

    def morning(day, say):
        tries.append(day)
        if len(tries) == 1:  # as it happened on 2026-11-06, downloading the weather
            raise urllib.error.URLError(ConnectionResetError(54, "Connection reset by peer"))
        run.STATE.write_text(json.dumps({"live_day": str(day)}))
        return {"ok": True, "detail": "built"}

    monkeypatch.setattr(run, "morning", morning)
    assert run.setup()["detail"] == "built" and len(tries) == 2


def test_a_network_that_stays_down_fails_the_setup(run, monkeypatch):
    import urllib.error
    monkeypatch.setattr(run, "SETUP_WAIT_S", 0)
    tries = []

    def morning(day, say):
        tries.append(day)
        raise urllib.error.URLError("down")

    monkeypatch.setattr(run, "morning", morning)
    with pytest.raises(RuntimeError, match="Could not download.*connection"):
        run.setup()
    assert len(tries) == run.SETUP_TRIES and not run.initialised()


def test_current(run, monkeypatch):
    now = dt.datetime(2026, 11, 6, 10, 0)
    monkeypatch.setattr(run.fetch, "now", lambda: now)
    assert not run.current()  # no state
    run.STATE.write_text(json.dumps({"live_day": "2026-11-05", "complete_through": "2026-11-04"}))
    assert not run.current()  # yesterday's
    run.STATE.write_text(json.dumps({"live_day": "2026-11-06", "complete_through": "2026-11-05"}))
    assert run.current()


def test_missing_offer_ceiling_says_what_to_do():
    from vpp.v2 import offers as of
    assert of.ceiling("2026-11-30") == 1300
    for f in (of.ceiling, of.fcess_ceiling):
        with pytest.raises(ValueError, match="2030-12.*ERA"):
            f("2030-12-01")
