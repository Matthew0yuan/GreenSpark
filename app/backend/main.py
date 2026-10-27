"""The GreenSpark VPP API (/api/v1) and the built frontend (app/frontend/dist) on one local port.

Every result is a historical replay of the trading days with data (2025-04-01 to 2026-10-22): no live telemetry, no
bidding, no device control. What was known at 09:00 (plan) and what happened (outcome) come from separate routes, so
the frontend never holds an actual price before the operator asks for it."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import engines, jobs
from .db import Store

DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
SERVICES = ("reg_raise", "cont_raise", "reg_lower", "cont_lower")
OFFERS = ("ref", "custom")
STORE = Store(Path(os.environ["GS_DB"]) if os.environ.get("GS_DB") else None)  # GS_DB: another database (tests)


class StrictJSON(JSONResponse):
    """NaN and inf are bugs here (a missing value is null with a reason), so encoding refuses them."""

    def render(self, content) -> bytes:
        return json.dumps(content, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@asynccontextmanager
async def lifespan(app: FastAPI):
    engines.start()
    yield
    engines.stop()


app = FastAPI(title="GreenSpark VPP", version="1", lifespan=lifespan, default_response_class=StrictJSON)


@app.exception_handler(KeyError)
async def not_found(_: Request, e: KeyError):
    return StrictJSON({"detail": str(e).strip("'\"")}, status_code=404)


@app.exception_handler(ValueError)
async def bad_value(_: Request, e: ValueError):
    return StrictJSON({"detail": str(e)}, status_code=422)


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


def _offer(offer: str) -> str:
    if offer not in OFFERS:
        raise HTTPException(422, f"offer must be one of {OFFERS}")
    return offer


# ---------------------------------------------------------------------------------------------------- meta
@app.get("/api/v1/meta")
async def meta():
    st = {k: {"state": e.state, "error": e.error} for k, e in engines.ENGINES.items()}
    out = {"api_version": "1", "engines": st, "decision_time": "09:00", "market_time": "AWST (UTC+08:00)",
           "data": "historical replay, trading days 2025-04-01 to 2026-10-22", "storage": None}
    if engines.ENGINES.get("storage") and engines.ENGINES["storage"].state == "ready":
        out["storage"] = await engines.ENGINES["storage"].call("meta")
    return out


# ---------------------------------------------------------------------------------------------------- storage
@app.get("/api/v1/storage/days/{day}/plan")
async def storage_plan(day: str, fcess: str | None = None, start_mwh: float | None = Query(None, ge=0, le=40),
                       export_mw: float | None = Query(None, gt=0, le=20), import_mw: float | None = Query(None, gt=0, le=20)):
    return await _engine("storage").call("plan", day=day, fcess=_fcess(fcess), start_mwh=start_mwh,
                                          export_mw=export_mw, import_mw=import_mw)


@app.get("/api/v1/storage/days/{day}/outcome")
async def storage_outcome(day: str, fcess: str | None = None, start_mwh: float | None = Query(None, ge=0, le=40)):
    return await _engine("storage").call("outcome", day=day, fcess=_fcess(fcess), start_mwh=start_mwh)


@app.get("/api/v1/storage/evidence")
async def storage_evidence():
    return await _engine("storage").call("evidence")


class BacktestBody(BaseModel):
    fcess: list[str] = Field(default_factory=lambda: list(SERVICES))


@app.post("/api/v1/jobs/storage-backtest")
async def storage_backtest(body: BacktestBody):
    return {"id": jobs.start(STORE, "storage-backtest", {"fcess": list(_fcess(",".join(body.fcess)))})}


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
