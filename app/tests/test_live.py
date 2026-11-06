"""Live data (app/live). The parser tests always run; the rest need the live copy and run in their own session, because
vpp reads its data folder when it is imported:

  GS_DATA=var/live/data .venv/Scripts/python.exe -m pytest app/tests/test_live.py -q
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.live import today

ROOT = Path(__file__).resolve().parents[2]
LIVE_DATA = ROOT / "var" / "live" / "data"
ON = bool(os.environ.get("GS_DATA")) and Path(os.environ["GS_DATA"]).resolve() == LIVE_DATA.resolve()
live_only = pytest.mark.skipif(not ON, reason="needs GS_DATA=var/live/data (python -m app.live.run --init)")


# ---------------------------------------------------------------------------------------------------- 5-minute prices
def _head(prices_after_second_interval: bool = False) -> bytes:
    pr = '"prices":{"energy":140.31,"contingencyLower":0.0,"regulationRaise":1.5,"regulationLower":0.0,"rocof":0.0,"contingencyRaise":2.25}'
    first = '{"dispatchInterval":"2026-10-29T22:50:00+08:00","schedule":[{"facilitySchedule":[{"quantity":1.0}]}]'
    second = '{"dispatchInterval":"2026-10-29T22:55:00+08:00"'
    body = (first + "," + second + "," + pr) if prices_after_second_interval else (first[:-1] + "," + pr + "}," + second)
    return ('{"data":{"primaryDispatchInterval":"2026-10-29T22:50:00+08:00","dispatchDataIssueID":"20261029225106794_4638",'
            '"solutionData":[' + body).encode()


def test_dispatch_prices_parse():
    got = today.parse(_head())
    assert got["interval"] == "2026-10-29 22:50:00" and got["energy"] == 140.31
    assert (got["reg_raise"], got["cont_raise"], got["cont_lower"]) == (1.5, 2.25, 0.0)
    assert got["issue_id"].startswith("20261029225106794")


def test_dispatch_prices_not_in_head_yet():
    assert today.parse(_head()[:200]) is None  # the head stops before the prices: fetch more


def test_dispatch_prices_layout_change_is_refused():
    with pytest.raises(ValueError):  # the first prices object belongs to a later interval: never taken as the primary's
        today.parse(_head(prices_after_second_interval=True))


# ---------------------------------------------------------------------------------------------------- the live copy
@pytest.fixture(scope="module")
def storage():
    from app.engine.storage import Storage
    return Storage()


@live_only
def test_inputs_of_days_already_built_are_kept():
    """The live builds keep every row the research data had (AEMO's later revisions do not move old days)."""
    for name, key in (("forecast_dispatch_0900.parquet", ["issue_date", "ts"]), ("spike_risk_0900.parquet", ["issue_date", "ts"]),
                      ("wem_30min.parquet", None)):
        old, new = pd.read_parquet(ROOT / "data" / "processed" / name), pd.read_parquet(LIVE_DATA / "processed" / name)
        if key is None:
            pd.testing.assert_frame_equal(new.loc[old.index, old.columns], old)
        else:
            m = new.merge(old, on=key, suffixes=("", "_old"))
            assert len(m) == len(old)
            for c in old.columns.difference(key):
                if pd.api.types.is_numeric_dtype(old[c]):
                    assert np.array_equal(m[c].to_numpy(float), m[f"{c}_old"].to_numpy(float), equal_nan=True), (name, c)


@live_only
def test_one_day_equals_load_days(storage):
    from app.engine import live as lv
    for D in (storage.days[-2]["day"] if storage.live_info.get("ready") else storage.days[-1]["day"], pd.Timestamp("2026-01-20")):
        d, _ = lv.day_input(storage.inputs, D)
        ref = storage.days[storage.index[D]]
        assert set(d) == set(ref)
        for k, v in ref.items():
            if k == "prices":
                for src, (e, f) in v.items():
                    assert np.array_equal(e, d[k][src][0], equal_nan=True)
                    assert all(np.array_equal(f[q], d[k][src][1][q], equal_nan=True) for q in f)
            elif isinstance(v, dict):
                assert all(np.array_equal(v[q], d[k][q], equal_nan=True) for q in v)
            elif isinstance(v, np.ndarray):
                assert np.array_equal(v, d[k], equal_nan=v.dtype.kind == "f"), k
            else:
                assert v == d[k], k


@live_only
@pytest.mark.parametrize("connection", ["factory", "standalone"])
def test_start_state_carries_on_from_saved(storage, connection):
    first = storage.saved_last[connection] + pd.Timedelta(days=1)
    nxt = first + pd.Timedelta(days=1)
    g = storage.saved[connection][storage.dispatcher(connection, "e5")["name"]]
    assert storage.start_mwh(first, connection) == float(g["soc_end"].iloc[-1])
    assert storage.start_mwh(nxt, connection) == storage.replay(first, connection)["summary"]["soc_end"]


@live_only
def test_wear_by_halfhour_adds_up(storage):
    for D in ("2026-01-20", "2026-02-02", str(storage.saved_last["factory"].date())):
        for c in ("factory", "standalone"):
            r = storage.replay(D, c)
            assert abs(storage.wear_by_halfhour(r).sum() - r["summary"]["wear"]) < 1e-9


@live_only
def test_day_in_progress(storage):
    info = storage.live_info
    if info.get("complete") or not info.get("ready"):
        pytest.skip(f"no trading day in progress with its 09:00 plan: {info}")
    D = info["day"]
    k = info["known_halfhours"]
    o = storage.outcome(D)
    assert o["status"] == "live" and o["live"]["known_halfhours"] == k
    assert all(v is None for v in o["price_actual"][k:]) and all(v is not None for v in o["price_actual"][:k])
    assert all(v is None for v in o["soc_mwh"][k + 1:])
    r = storage.replay(D)
    log = r["log"]
    energy = float((log["rtp"].to_numpy()[:k] * log["energy_mwh"].to_numpy()[:k]).sum())
    assert abs(o["money_aud"]["energy"] - energy) < 1e-6
    assert o["explain"]["unaccounted"] == [] and o["explain"]["points"][k]["why"] == "not over yet"
    p = storage.plan(D)
    assert p["live"]["prices_to"] == info["as_of"] and p["state"] == "PASS"
    assert next(c for c in p["checks"] if c["id"] == "info_timing")["status"] == "pass"


@live_only
def test_plan_of_day_in_progress_does_not_move_with_later_prices(storage):
    """The 09:00 plan is the same with only 08:00-08:55 published as with every price so far."""
    from app.engine import live as lv
    info = storage.live_info
    if info.get("complete") or not info.get("ready"):
        pytest.skip("no trading day in progress")
    D = pd.Timestamp(info["day"])
    known = lv.today_prices(D)
    early, _ = lv.day_input(storage.inputs, D, known.iloc[:lv.READY_5MIN])
    full = storage.days[storage.index[D]]
    a = storage.record_0900(storage.replay(D)["records"])
    storage.days[storage.index[D]] = early
    storage.cache.clear()
    try:
        b = storage.record_0900(storage.replay(D)["records"])
    finally:
        storage.days[storage.index[D]] = full
        storage.cache.clear()
    for f in ("net", "soc", "reg_raise", "cont_raise", "reg_lower", "cont_lower"):
        assert np.array_equal(np.asarray(a[f], float), np.asarray(b[f], float)), f


@live_only
@pytest.mark.parametrize("connection", ["factory", "standalone"])
def test_span_across_the_saved_end_and_to_today(storage, connection):
    """Days after the saved backtest match their outcome pages, have no network bill yet, and the day in progress stops at
    the last half-hour over: the replay runs the rest on the forecast, which must not appear as what happened."""
    if storage.live_info.get("complete", True):
        pytest.skip("no day in progress")
    end = storage.days[-1]["day"]
    s = storage.span(end, 21, connection)
    rows = {r["day"]: r for r in s["rows"]}
    assert s["end"] == str(end.date()) and s["days"] == 21 and s["network_to"] == ("2026-10-22" if connection == "factory" else None)
    today = rows[s["end"]]
    known = int(storage.live_info["known_halfhours"])
    h = today["halfhours"]
    assert not today["complete"] and s["live"]["as_of"] == storage.live_info["as_of"]
    for f in ("price_actual", "battery_mw"):
        assert all(v is not None for v in h[f][:known]) and all(v is None for v in h[f][known:]), f
    assert all(v is not None for v in h["stored_mwh"][:known + 1]) and all(v is None for v in h["stored_mwh"][known + 1:])
    assert all(v is not None for v in h["price_forecast"][2:])  # the 09:00 forecast of the whole day was known at 09:00
    o = storage.outcome(end, connection)
    assert [v for v in h["price_actual"] if v is not None] == pytest.approx([v for v in o["price_actual"] if v is not None], abs=0.006)
    assert today["money_aud"]["net"] == pytest.approx(o["money_aud"]["net"], abs=0.01)
    assert today["discharged_mwh"] == pytest.approx(o["discharged_mwh"], abs=0.001)
    # a complete live day and a saved day. Not 2026-10-22: the saved backtest ended there with no next day, and on live data
    # its day page replays it with one (factory: A$39 and 0.47 MWh apart); the live days carry on from what was saved
    for day in ("2026-10-23", "2026-10-21"):
        o = storage.outcome(day, connection)
        r = rows[day]
        assert r["complete"] and r["halfhours"]["price_actual"] == pytest.approx(o["price_actual"], abs=0.006)
        assert r["halfhours"]["stored_mwh"] == pytest.approx(o["soc_mwh"], abs=0.001)
        assert r["money_aud"]["net"] == pytest.approx(o["money_aud"]["net"], abs=0.01)
    assert rows["2026-10-23"]["money_aud"]["network"] is None
    assert (rows["2026-10-22"]["money_aud"]["network"] is not None) == (connection == "factory")
    # the stored energy carries on across the saved backtest's end
    assert rows["2026-10-23"]["halfhours"]["stored_mwh"][0] == pytest.approx(rows["2026-10-22"]["halfhours"]["stored_mwh"][48], abs=0.001)


@live_only
def test_span_of_live_days_survives_the_replay_cache(storage):
    """The replays are limited to CACHE_DAYS; a long span must not depend on them still being held."""
    end = storage.days[-1]["day"]
    first = storage.span(end, 365)
    before = end - pd.Timedelta(days=1)  # today's own replay starts from the day before's
    for k in [k for k in storage.cache if k[0] < before]:
        del storage.cache[k]
    keep = storage.replay
    storage.replay = lambda *a, **k: pytest.fail("a complete live day was replayed") if pd.Timestamp(a[0]) < before else keep(*a, **k)
    try:
        again = storage.span(end, 365)
    finally:
        storage.replay = keep
    assert again["rows"] == first["rows"]
