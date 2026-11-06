"""The GreenSpark VPP API (/api/v1) and the built frontend (app/frontend/dist) on one local port.

Live mode (the switch in Settings, POST /api/v1/live; GS_LIVE=0 keeps it off): the engines read AEMO's public data as it
is published (app/live): every complete trading day, and the day in progress from its 09:00 plan on, its 5-minute prices
filling in every minute. Otherwise a replay of the saved days (2025-04-01 to 2026-10-22). Either way: no bidding, no
device control. What was known at 09:00 (plan) and what happened (outcome) come from separate
routes, so the frontend never holds an actual price before the operator asks for it."""

import base64
import binascii
import json
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.engine import configs
from app.live import fetch as live_fetch
from app.live import run as live_run

from . import engines, jobs
from .db import Store

DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
SERVICES = ("reg_raise", "cont_raise", "reg_lower", "cont_lower")
CONNECTIONS, DISPATCHERS = ("factory", "standalone"), ("e5", "v1")  # = app.engine.common (not imported: no vpp here)
OFFERS = ("ref", "custom")
STORE = Store(Path(os.environ["GS_DB"]) if os.environ.get("GS_DB") else None)  # GS_DB: another database (tests)


class StrictJSON(JSONResponse):
    """NaN and inf are bugs here (a missing value is null with a reason), so encoding refuses them."""

    def render(self, content) -> bytes:
        return json.dumps(content, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


MODE = {"live": False, "switching": False, "error": None, "setup": None}  # setup: the job bringing the live data up to date
SCHEDULE = None  # the live schedule, while on live data


def _refresh_today():
    e = engines.ENGINES.get("storage")
    if e is not None and e.state == "ready":
        e.submit("refresh_today")


def _locked() -> str | None:
    """Why this server's data cannot be switched: GS_LIVE=0 (the tests), or the demo on a public address, whose compose
    file always sets GS_AUTH (deploy/), where a visitor must not change what everyone sees."""
    if os.environ.get("GS_LIVE") == "0":
        return "this server was started with GS_LIVE=0"
    return "this is the public demo" if "GS_AUTH" in os.environ else None


def _start_schedule() -> None:
    global SCHEDULE
    SCHEDULE = live_run.Schedule(on_prices=_refresh_today, on_build=engines.reload)
    SCHEDULE.start()


def _switch(on: bool) -> None:
    """New engines on the other data, serving only when ready. Everything else changes after that, so a failure leaves
    the server as it was, and jobs and fingerprints (configs.py) never see a mode not in force."""
    global SCHEDULE
    MODE.update(switching=True, error=None)

    def done(error: str | None):
        global SCHEDULE
        if error:
            print(f"the market data did not switch: {error}", file=sys.stderr, flush=True)
            MODE.update(switching=False, error="Could not switch: the engines did not start on that data. Nothing has changed.")
            return
        if on:
            os.environ["GS_DATA"] = str(live_fetch.DATA)
            live_run.OFF.unlink(missing_ok=True)
            _start_schedule()
        else:
            os.environ.pop("GS_DATA", None)
            live_run.mark_off()
            if SCHEDULE:
                SCHEDULE.stop.set()
                SCHEDULE = None
        MODE.update(live=on, switching=False, setup=None)

    engines.reload(done, str(live_fetch.DATA) if on else None)


def _data_mode() -> dict:
    job = STORE.job(MODE["setup"]) if MODE["setup"] else None
    return {"live": MODE["live"], "switching": MODE["switching"], "error": MODE["error"], "locked": _locked(),
            "setup": None if job is None else {k: job[k] for k in ("id", "state", "progress", "message")}}


def _switch_refused() -> str | None:
    if _locked():
        return _locked()
    if MODE["switching"] or engines.RELOAD["state"] == "starting":
        return "the engines are restarting"
    if SCHEDULE and SCHEDULE.status["build"]["state"] == "running":
        return "this morning's rebuild is running"
    return "a job is running" if jobs.RUNNING else None


def _no_job_during_a_switch() -> None:
    """A job would run on the data folder it started on, and its result would not fit the mode it ended in."""
    if MODE["switching"] or jobs.running(STORE, "live-setup"):
        raise HTTPException(409, "the market data are being switched: try again when that has finished")


@asynccontextmanager
async def lifespan(app: FastAPI):
    STORE.end_unfinished_jobs()
    if os.environ.get("GS_LIVE", "1") != "0" and live_run.initialised() and not live_run.OFF.exists():
        # the engine processes read the live copy of every input
        os.environ["GS_DATA"] = str(live_fetch.DATA)
        MODE["live"] = True
    engines.start()
    if MODE["live"]:
        _start_schedule()
    yield
    if SCHEDULE:
        SCHEDULE.stop.set()
    jobs.stop(STORE)
    engines.stop()


app = FastAPI(title="GreenSpark VPP", version="1", lifespan=lifespan, default_response_class=StrictJSON)


def _authorised(header: str, want: str) -> bool:
    if not header.startswith("Basic "):
        return False
    try:
        got = base64.b64decode(header[6:], validate=True)
    except (binascii.Error, ValueError):
        return False
    return secrets.compare_digest(got, want.encode("utf-8"))


@app.middleware("http")
async def login(request: Request, call_next):
    """GS_AUTH="user:password" puts every page and API route behind a browser login (the demo on a public address,
    deploy/). Unset, as on this machine and in the tests, nothing changes."""
    want = os.environ.get("GS_AUTH")
    if want and not _authorised(request.headers.get("authorization", ""), want):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="GreenSpark", charset="UTF-8"'})
    return await call_next(request)


@app.exception_handler(KeyError)
async def not_found(_: Request, e: KeyError):
    return StrictJSON({"detail": str(e).strip("'\"")}, status_code=404)


@app.exception_handler(ValueError)
async def bad_value(_: Request, e: ValueError):
    return StrictJSON({"detail": str(e)}, status_code=422)


@app.exception_handler(configs.NotBuilt)
async def not_built(_: Request, e: configs.NotBuilt):
    return StrictJSON({"detail": str(e)}, status_code=409)


def _engine(name: str) -> engines.Engine:
    e = engines.ENGINES.get(name)
    if e is None or e.state == "failed":
        raise HTTPException(503, f"{name} engine unavailable: {getattr(e, 'error', 'not started')}")
    return e


def _fcess(fcess: str | None) -> tuple:
    if fcess is None:
        return SERVICES
    got = tuple(s for s in fcess.split(",") if s)
    bad = [s for s in got if s not in SERVICES]
    if bad:
        raise HTTPException(422, f"unknown FCESS service(s) {bad}; use {', '.join(SERVICES)}")
    return tuple(s for s in SERVICES if s in got)


def _mode(connection: str, dispatcher: str) -> dict:
    if connection not in CONNECTIONS:
        raise HTTPException(422, f"connection must be one of {CONNECTIONS}")
    if dispatcher not in DISPATCHERS:
        raise HTTPException(422, f"dispatcher must be one of {DISPATCHERS}")
    return {"connection": connection, "dispatcher": dispatcher}


def _setup(connection: str, dispatcher: str, energy_mwh: float | None, cmd_mw: float | None) -> dict:
    """The mode and the battery and CMD as the engine takes them; out of range is 422 (configs.of)."""
    mode = _mode(connection, dispatcher)
    configs.of(connection, energy_mwh, cmd_mw)
    return {**mode, "energy_mwh": energy_mwh, "cmd_mw": cmd_mw}


def _offer(offer: str) -> str:
    if offer not in OFFERS:
        raise HTTPException(422, f"offer must be one of {OFFERS}")
    return offer


# ---------------------------------------------------------------------------------------------------- meta
@app.get("/api/v1/meta")
async def meta():
    st = {k: {"state": e.state, "error": e.error} for k, e in engines.ENGINES.items()}
    out = {"api_version": "1", "engines": st, "decision_time": "09:00", "market_time": "AWST (UTC+08:00)",
           "data": "live AEMO data" if MODE["live"] else "historical replay, trading days 2025-04-01 to 2026-10-22",
           "data_mode": _data_mode(), "storage": None, "live": None}
    if engines.ENGINES.get("storage") and engines.ENGINES["storage"].state == "ready":
        out["storage"] = await engines.ENGINES["storage"].call("meta")
    if SCHEDULE:
        out["live"] = {**SCHEDULE.status, "reload": engines.RELOAD, "now": live_fetch.now().isoformat(timespec="seconds")}
    return out


class LiveBody(BaseModel):
    on: bool


@app.post("/api/v1/live")
async def live_switch(body: LiveBody):
    """Turn live data on or off. Data behind today's go up to date first, so that an old day is not shown as in progress:
    the first time this copies the research data and downloads what AEMO has published since. Until the switch is done
    the marker says off, so a run that dies leaves the server starting on the replay."""
    why = _switch_refused()
    if why:
        raise HTTPException(409, f"cannot switch now: {why}")
    if body.on != MODE["live"]:
        if not body.on or live_run.current():
            _switch(body.on)
        else:
            live_run.mark_off()
            MODE.update(error=None, setup=jobs.start(STORE, "live-setup", {}, on_done=lambda: _switch(True)))
    return _data_mode()


# ---------------------------------------------------------------------------------------------------- storage
@app.get("/api/v1/storage/days/{day}/plan")
async def storage_plan(day: str, connection: str = "factory", dispatcher: str = "e5", fcess: str | None = None,
                       start_mwh: float | None = Query(None, ge=0), export_mw: float | None = Query(None, gt=0, le=20),
                       import_mw: float | None = Query(None, gt=0, le=20), energy_mwh: float | None = None,
                       cmd_mw: float | None = None):
    return await _engine("storage").call("plan", day=day, **_setup(connection, dispatcher, energy_mwh, cmd_mw),
                                          fcess=_fcess(fcess), start_mwh=start_mwh, export_mw=export_mw, import_mw=import_mw)


@app.get("/api/v1/storage/days/{day}/outcome")
async def storage_outcome(day: str, connection: str = "factory", dispatcher: str = "e5", fcess: str | None = None,
                          start_mwh: float | None = Query(None, ge=0), energy_mwh: float | None = None,
                          cmd_mw: float | None = None):
    return await _engine("storage").call("outcome", day=day, **_setup(connection, dispatcher, energy_mwh, cmd_mw),
                                          fcess=_fcess(fcess), start_mwh=start_mwh)


@app.get("/api/v1/storage/evidence")
async def storage_evidence(connection: str = "factory", dispatcher: str = "e5", energy_mwh: float | None = None,
                           cmd_mw: float | None = None):
    return await _engine("storage").call("evidence", **_setup(connection, dispatcher, energy_mwh, cmd_mw))


@app.get("/api/v1/storage/config")
async def storage_config_state(connection: str = "factory", dispatcher: str = "e5", energy_mwh: float | None = None,
                               cmd_mw: float | None = None):
    """Whether this battery and CMD can be used yet (why not, if not) and what its backtest showed."""
    _mode(connection, dispatcher)
    setup = configs.of(connection, energy_mwh, cmd_mw)
    m = None if setup.tested else configs.manifest(connection, setup)
    why = configs.problem(connection, setup, m)
    return {"ready": why is None, "reason": why, "periods": None if why or m is None else m["periods"][dispatcher]}


class BacktestBody(BaseModel):
    connection: str = "factory"
    dispatcher: str = "e5"
    fcess: list[str] = Field(default_factory=lambda: list(SERVICES))
    energy_mwh: float | None = None
    cmd_mw: float | None = None


@app.post("/api/v1/jobs/storage-backtest")
async def storage_backtest(body: BacktestBody):
    _no_job_during_a_switch()
    params = _setup(body.connection, body.dispatcher, body.energy_mwh, body.cmd_mw)
    configs.require(body.connection, configs.of(body.connection, body.energy_mwh, body.cmd_mw))
    return {"id": jobs.start(STORE, "storage-backtest", {**params, "fcess": list(_fcess(",".join(body.fcess)))})}


class ConfigBody(BaseModel):
    connection: str = "factory"
    dispatcher: str = "e5"
    energy_mwh: float | None = None
    cmd_mw: float | None = None


@app.post("/api/v1/jobs/storage-config")
async def storage_config(body: ConfigBody):
    """Start the value table and backtest of a battery and CMD (a few minutes), unless it has them or is getting them."""
    params = _setup(body.connection, body.dispatcher, body.energy_mwh, body.cmd_mw)
    setup = configs.of(body.connection, body.energy_mwh, body.cmd_mw)
    if configs.problem(body.connection, setup) is None:
        return {"ready": True, "id": None}
    _no_job_during_a_switch()
    other = jobs.running(STORE, "storage-config")
    if other is not None:
        q = other["params"]
        if (q["connection"], configs.of(q["connection"], q["energy_mwh"], q["cmd_mw"])) == (body.connection, setup):
            return {"ready": False, "id": other["id"]}
        raise HTTPException(409, "another battery is being backtested: try again when it has finished")
    return {"ready": False, "id": jobs.start(STORE, "storage-config", params)}


@app.get("/api/v1/jobs/{jid}")
async def job(jid: int):
    j = STORE.job(jid)
    if j is None:
        raise HTTPException(404, f"no job {jid}")
    return j


@app.delete("/api/v1/jobs/{jid}")
async def job_cancel(jid: int):
    return {"cancelled": jobs.cancel(STORE, jid)}


# ---------------------------------------------------------------------------------------------------- fleet
@app.get("/api/v1/fleet/days/{day}/call")
async def fleet_call(day: str, offer: str = "custom", hindsight: bool = False):
    o = _offer(offer)
    return await _engine("fleet").call("call_day", oid=o, day=day, decisions=STORE.decisions(o), hindsight=hindsight)


class Decision(BaseModel):
    offer: str = "custom"
    call: bool
    start: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")


@app.put("/api/v1/fleet/days/{day}/decision")
async def fleet_decide(day: str, body: Decision):
    o = _offer(body.offer)
    before = await _engine("fleet").call("call_day", oid=o, day=day, decisions=STORE.decisions(o))
    if body.call and before["calls"]["used_before"] >= before["calls"]["cap"]:
        raise HTTPException(409, f"no calls left in contract year {before['contract_year']} "
                                 f"({before['calls']['cap']} used)")
    if body.call and body.start and body.start not in before["start_options"]:
        raise HTTPException(422, f"{body.start} is not an allowed start; use one of {before['start_options']}")
    STORE.set_decision(o, day, body.call, body.start if body.call else None)
    return await _engine("fleet").call("call_day", oid=o, day=day, decisions=STORE.decisions(o))


@app.delete("/api/v1/fleet/days/{day}/decision")
async def fleet_undo(day: str, offer: str = "custom"):
    o = _offer(offer)
    STORE.clear_decision(o, day)
    return await _engine("fleet").call("call_day", oid=o, day=day, decisions=STORE.decisions(o))


@app.delete("/api/v1/fleet/decisions")
async def fleet_reset(offer: str = "custom"):
    STORE.clear_all(_offer(offer))
    return {"cleared": offer}


@app.get("/api/v1/fleet/season")
async def fleet_season(offer: str = "custom", through: str | None = None, hindsight: bool = False):
    o = _offer(offer)
    return await _engine("fleet").call("season_days", oid=o, decisions=STORE.decisions(o), through=through,
                                       hindsight=hindsight)


@app.get("/api/v1/fleet/value")
async def fleet_value():
    return await _engine("fleet").call("summary", decisions_by_offer={o: STORE.decisions(o) for o in OFFERS})


class Terms(BaseModel):
    offer: str = "custom"
    pay_c_per_kwh: float | None = Field(None, ge=0, le=500)
    pay_aud_per_event: float | None = Field(None, ge=0, le=500)
    annual_fee_aud: float | None = Field(None, ge=0, le=5000)
    max_calls: int | None = Field(None, ge=1, le=120)


@app.post("/api/v1/fleet/offers/evaluate")
async def fleet_evaluate(body: Terms):
    o = _offer(body.offer)
    return await _engine("fleet").call("evaluate_terms", oid=o, pay_c_per_kwh=body.pay_c_per_kwh,
                                       pay_aud_per_event=body.pay_aud_per_event, annual_fee_aud=body.annual_fee_aud,
                                       max_calls=body.max_calls)


# ---------------------------------------------------------------------------------------------------- frontend
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "no such API route")
    f = DIST / path
    if path and f.is_file():
        return FileResponse(f)
    index = DIST / "index.html"
    if not index.exists():
        return StrictJSON({"detail": "frontend not built: cd app/frontend && npm run build"}, status_code=503)
    return FileResponse(index)
