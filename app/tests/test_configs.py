"""A battery and CMD of the user's own (app/engine/configs.py), against what they could silently get wrong: a saved
backtest written differently from the tested ones' run.py, a replay that starts from another configuration's state, a value
table or start state of the tested battery, and a backtest used after its code, data or table changed. The whole 570 days
at the range's corners are checked by hand (2026-11-05); here the first 30 days of a backtest, about 3 min in all.

  .venv/bin/python -m pytest app/tests/test_configs.py -q
"""

import json
import multiprocessing as mp
import os
import shutil
from pathlib import Path

import pandas as pd
import pytest

from app.engine import configs as cg
from app.engine.common import SAVED_DIR, STRATEGY

SETUP = {"energy_mwh": 38.0, "cmd_mw": 8.5}  # not a value anyone would have built for real: its table is removed after
DAYS = 30
DAY = "2025-04-20"
MODES = (("factory", "e5"), ("factory", "v1"))


def test_of():
    assert cg.of("factory") == cg.TESTED and cg.of("factory", 40, 9.0) == cg.TESTED and cg.TESTED.tested
    assert cg.of("factory", 35, 9) == cg.Config(35.0, None) and cg.of("factory", 40, 7.5) == cg.Config(None, 7.5)
    assert cg.of("standalone", 40, 7) == cg.TESTED and cg.of("standalone", 35, 7) == cg.Config(35.0, None)
    assert (cg.Config(35.0, None).energy, cg.Config(35.0, None).cmd) == (35.0, 9.0)
    for bad in ((9, None), (81, None), (35.5, None), (None, 4.5), (None, 12.5), (None, 7.3)):
        with pytest.raises(ValueError):
            cg.of("factory", *bad)
    assert cg.key("factory", cg.Config(35.0, None)) == "factory_35mwh_cmd9" and cg.key("standalone", cg.Config(35.0, None)) == "standalone_35mwh"


def test_a_backtest_is_usable_only_as_built(tmp_path, monkeypatch):
    """Each way a backtest goes out of date is refused, so these checks can fail: no manifest, other code or data, no table."""
    monkeypatch.setenv("GS_CONFIGS", str(tmp_path))
    setup = cg.of("factory", 35, 7)
    with pytest.raises(cg.NotBuilt, match="No backtest"):
        cg.require("factory", setup)
    assert cg.require("factory", cg.TESTED) is None
    table = tmp_path / "table.npz"
    table.write_text("x")
    folder = cg.folder("factory", setup)
    folder.mkdir(parents=True)
    m = {"fingerprint": cg.fingerprint(), "table": str(table)}
    (folder / "manifest.json").write_text(json.dumps(m))
    assert cg.require("factory", setup) == m
    (folder / "manifest.json").write_text(json.dumps({**m, "fingerprint": "another"}))
    with pytest.raises(cg.NotBuilt, match="out of date"):
        cg.require("factory", setup)
    (folder / "manifest.json").write_text(json.dumps(m))
    monkeypatch.setenv("GS_DATA", str(tmp_path))  # the live data have their own backtest
    assert cg.folder("factory", setup) != folder
    with pytest.raises(cg.NotBuilt, match="No backtest"):
        cg.require("factory", setup)
    monkeypatch.delenv("GS_DATA")
    table.unlink()
    with pytest.raises(cg.NotBuilt, match="value table"):
        cg.require("factory", setup)


def test_prune_keeps_the_newest_with_their_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("GS_CONFIGS", str(tmp_path))
    tables = []
    for i, e in enumerate((20, 21, 22)):
        f = cg.folder("factory", cg.of("factory", e))
        f.mkdir(parents=True)
        t = tmp_path / f"t{e}.npz"
        t.write_text("x")
        t.with_suffix(".json").write_text("x")
        tables.append(t)
        m = f / "manifest.json"
        m.write_text(json.dumps({"table": str(t)}))
        os.utime(m, (1000 + i, 1000 + i))
    cg.prune(keep=2)
    assert not cg.folder("factory", cg.of("factory", 20)).exists() and not tables[0].exists() and not tables[0].with_suffix(".json").exists()
    assert cg.folder("factory", cg.of("factory", 22)).exists() and tables[2].exists()


@pytest.mark.parametrize("connection", ("factory", "standalone"))
def test_the_new_backtest_is_the_saved_one_at_the_tested_values(connection, tmp_path):
    """What storage_config writes equals what run.py wrote for the tested battery and CMD, day for day: a difference in the
    runs, their order or their settings would show here."""
    from app.engine.jobs import write_runs
    from vpp import backtest as bt
    days = bt.load_days("0900")[:DAYS]
    count = mp.Value("i", 0)
    checks = write_runs(connection, cg.TESTED, tmp_path / "x", days, count)
    assert count.value == DAYS * (2 if connection == "factory" else 1)  # the progress bar's days
    assert checks["energy_balance_error_mwh"] < 1e-6 and not checks["over_power"] and not checks["soc_outside"]
    for name in ("daily.csv", "refs_daily.csv"):
        mine = pd.read_csv(tmp_path / "x" / name, parse_dates=["day"])
        saved = pd.read_csv(SAVED_DIR[connection] / name, parse_dates=["day"])
        # the last day looks ahead to a next day this short run lacks
        m = mine[mine["day"] < days[-1]["day"]].merge(saved, on=["strategy", "day"], suffixes=("", "_saved"))
        assert len(m) == (DAYS - 1) * mine["strategy"].nunique()
        for c in ("total", "soc_end", "energy", "fcess", "wear"):
            assert (m[c] - m[c + "_saved"]).abs().max() < 1e-6, (name, c)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A real value table and the first days' backtest of SETUP, in a folder of its own."""
    from app.engine.jobs import _table, write_runs
    from app.engine.storage import Storage, scenario
    from vpp import backtest as bt
    before = os.environ.get("GS_CONFIGS")
    os.environ["GS_CONFIGS"] = str(tmp_path_factory.mktemp("configs"))
    setup = cg.of("factory", **SETUP)
    table = _table(scenario("factory", setup=setup))
    checks = write_runs("factory", setup, cg.folder("factory", setup), bt.load_days("0900")[:DAYS])
    (cg.folder("factory", setup) / "manifest.json").write_text(json.dumps(
        {"fingerprint": cg.fingerprint(), "table": str(table), "built_at": "t0", "checks": checks}))
    yield Storage(), setup
    Path(table).unlink(missing_ok=True)
    Path(table).with_suffix(".json").unlink(missing_ok=True)
    shutil.rmtree(cg.root(), ignore_errors=True)
    if before is None:
        del os.environ["GS_CONFIGS"]
    else:
        os.environ["GS_CONFIGS"] = before


@pytest.mark.parametrize("mode", MODES)
def test_replay_equals_its_own_backtest(built, mode):
    """The replay starts from this configuration's saved state and month's peak: from the tested ones' it would differ."""
    storage, _ = built
    o = storage.outcome(DAY, *mode, **SETUP)
    assert abs(o["replay_check"]["replay_minus_saved_total_aud"]) < 1e-6
    assert abs(o["replay_check"]["replay_minus_saved_soc_end_mwh"]) < 1e-9
    assert o["unvalidated"] and o["money_aud"]["network"] is not None
    assert max(o["soc_mwh"]) <= SETUP["energy_mwh"] + 1e-6


def test_plan_uses_this_battery_and_cmd(built):
    storage, setup = built
    p = storage.plan(DAY, **SETUP)
    assert p["asset"]["energy_mwh"] == 38 and p["asset"]["site"]["cmd_mw"] == 8.5 and p["site"]["cmd_mw"] == 8.5
    assert p["unvalidated"] and p["settings"]["energy_mwh"] == 38
    assert max(p["plan"]["soc_mwh"]) <= 38 + 1e-6
    labels = {c["id"]: c["label"] for c in p["checks"]}
    assert "38" in labels["soc_max"] and "8.5" in labels["cmd"]
    # the 09:00 check replays the day with later prices x 3: the same battery's plan, and it must not move
    assert next(c for c in p["checks"] if c["id"] == "info_timing")["status"] == "pass"
    keys = [k for k in storage.cache if k[0] == pd.Timestamp(DAY) and k[5]]  # the perturbed replays
    assert keys and all(k[-1] == setup for k in keys)
    assert storage.replay(DAY, perturb=True, setup=setup)["bat"].energy_mwh == 38


def test_configurations_do_not_mix(built):
    """The tested battery's day, a custom day, the tested again: each as it is alone. Saved money compares across them."""
    storage, _ = built
    alone = storage.outcome(DAY)
    own = storage.outcome(DAY, **SETUP)
    again = storage.outcome(DAY)
    assert alone["money_aud"] == again["money_aud"] and alone["soc_mwh"] == again["soc_mwh"]
    assert own["money_aud"] != alone["money_aud"]
    assert abs(again["replay_check"]["replay_minus_saved_total_aud"]) < 1e-6
    assert storage.plan(DAY)["asset"]["energy_mwh"] == 40 and not storage.plan(DAY)["unvalidated"]


def test_refused_before_anything_runs(built):
    storage, _ = built
    with pytest.raises(ValueError, match="more than the battery"):
        storage.plan(DAY, start_mwh=39, **SETUP)
    assert storage.plan(DAY, start_mwh=38, **SETUP)["start_state"]["stored_mwh"] == 38
    with pytest.raises(ValueError, match="ends on"):
        storage.plan("2026-01-20", **SETUP)  # after this short backtest's last day


def test_a_new_build_replaces_what_is_held(built):
    """Built again, the old replays and the old value table in memory go: rebuilt results never meet old arrays."""
    from vpp.v2 import harness as hs
    storage, setup = built
    storage.outcome(DAY, **SETUP)
    assert any(not k[-1].tested for k in storage.cache)
    assert len(hs.VALUE_INPUTS) >= 1
    held = {k for k in hs.VALUE_INPUTS if k[-1].energy_mwh == 38}
    assert held
    m = cg.folder("factory", setup) / "manifest.json"
    m.write_text(json.dumps({**json.loads(m.read_text()), "built_at": "t1"}))
    storage.runs("factory", setup)
    assert not any(not k[-1].tested for k in storage.cache)
    assert not any(k[-1].energy_mwh == 38 for k in hs.VALUE_INPUTS)


def test_the_replay_check_can_fail(built):
    """A planted error: the saved state of the day before moved by 1 MWh. The check must show it, and on the next read
    of the true file it must be 0 again."""
    storage, setup = built
    folder = cg.folder("factory", setup)
    daily = folder / "daily.csv"
    true = daily.read_text()
    m = folder / "manifest.json"

    def reread(stamp):  # as a new build would: the engine reads the folder again
        m.write_text(json.dumps({**json.loads(m.read_text()), "built_at": stamp}))

    d = pd.read_csv(daily, parse_dates=["day"])
    before = (d["day"] == pd.Timestamp(DAY) - pd.Timedelta(days=1)) & (d["strategy"] == STRATEGY["factory", "e5"])
    assert before.sum() == 1
    d.loc[before, "soc_end"] = d.loc[before, "soc_end"] + (1.0 if d.loc[before, "soc_end"].iloc[0] < 37 else -1.0)
    d.to_csv(daily, index=False)
    reread("planted")
    check = storage.outcome(DAY, **SETUP)["replay_check"]
    assert abs(check["replay_minus_saved_soc_end_mwh"]) + abs(check["replay_minus_saved_total_aud"]) > 1e-3
    daily.write_text(true)
    reread("true")
    assert abs(storage.outcome(DAY, **SETUP)["replay_check"]["replay_minus_saved_total_aud"]) < 1e-6


def test_the_checks_at_build_can_fail(built):
    """A planted error in a day's stored energy shows in the energy balance; a table of another battery is refused."""
    from app.engine.jobs import _balance
    from app.engine.storage import scenario
    from vpp.v2 import harness as hs
    from vpp.v2 import value as vl
    _, setup = built
    folder, sc = cg.folder("factory", setup), scenario("factory", setup=setup)
    daily, log = pd.read_csv(folder / "daily.csv", parse_dates=["day"]), pd.read_parquet(folder / "log.parquet")
    log["day"] = pd.to_datetime(log["day"])
    assert _balance("factory", sc, daily, log) < 1e-6
    daily.loc[10, "soc_end"] += 0.01
    assert _balance("factory", sc, daily, log) > 0.009
    table = Path(json.loads((folder / "manifest.json").read_text())["table"])
    inputs = vl.load(table)
    params = hs.table_params(sc.asset, sc.site, sc.route.cmd_mw)
    vl.check_table(table, inputs, params, "median", 50.0, True)  # as read by the dispatcher
    for other in (scenario("factory", setup=cg.of("factory", 39, 8.5)), scenario("factory", setup=cg.of("factory", 38, 9))):
        with pytest.raises(ValueError, match="was solved for"):
            vl.check_table(table, inputs, hs.table_params(other.asset, other.site, other.route.cmd_mw), "median", 50.0, True)


def test_evidence_of_this_battery(built):
    from app.engine.evidence import evidence, verdict
    storage, setup = built
    e = evidence("factory", "e5", **SETUP)
    assert e["asset"]["energy_mwh"] == 38 and e["asset"]["cmd_mw"] == 8.5 and e["unvalidated"]
    assert e["limits"][0]["soc_outside"] == 0 and e["limits"][0]["over_power"] == 0
    assert "var/configs/" in e["source"]
    # its own runs, not the tested ones'
    money = storage.runs("factory", setup).money[STRATEGY["factory", "e5"]]
    assert e["periods"][0]["per_day_aud"]["e5"] == round(money.mean(), 1)
    assert verdict(storage.tested["factory"].money, "factory")["source"].startswith("analysis/")
