"""Long engine runs, each in its own process (app.backend.jobs runs python -m app.engine.jobs <kind> <params>): a dispatcher
on a connection through every trading day with changed settings, compared with the saved run of the default settings, by
period; and a battery and CMD's own value table and saved backtest (configs.py). Behind the factory meter the money of a
day is its ledger plus the month's network bill saving per day, as in the saved runs."""

import json
import multiprocessing as mp
import os
import shutil
import sys
import threading
import time
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import dispatch as dp
from vpp import site_bill as sb
from vpp import stochastic as sto
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs
from vpp.v2 import value as vl
from vpp.v2.config import V1

from . import configs as cg
from .common import (DEFAULT_CONNECTION, DEFAULT_DISPATCHER, DISPATCHERS, HURDLE, PERFECT, PLAN_ONLY, SAVED_PERIODS, STRATEGY,
                     check_mode, num, period, period_key, saved_rel)
from .evidence import read_runs, saved_runs
from .storage import SERVICES, config, scenario

REFS = (PLAN_ONLY, PERFECT)
TOL = 1e-6


def _periods(money: pd.Series, fcess: pd.Series, connection: str, dispatcher: str) -> list:
    """Each evaluation period's money per day against the saved run of the tested settings."""
    saved = saved_runs(connection)[1][STRATEGY[connection, dispatcher]]
    keys = pd.Series([period_key(d) for d in money.index], index=money.index)
    out = []
    for k in SAVED_PERIODS:
        g = money[keys == k]
        s = saved.reindex(g.index)
        out.append({"period": period(k, len(g)), "per_day_aud": num(g.mean(), 1), "default_per_day_aud": num(s.mean(), 1),
                    "difference_aud": num((g - s).mean(), 1), "fcess_per_day_aud": num(fcess.reindex(g.index).mean(), 1)})
    return out


def storage_backtest(params: dict, progress) -> dict:
    """progress(share done, message) is called after each day."""
    connection = params.get("connection") or DEFAULT_CONNECTION
    dispatcher = params.get("dispatcher") or DEFAULT_DISPATCHER
    check_mode(connection, dispatcher)
    setup = cg.of(connection, params.get("energy_mwh"), params.get("cmd_mw"))
    cg.require(connection, setup)
    fcess = tuple(params.get("fcess") or ())
    sc, cfg = scenario(connection, fcess, setup), config(dispatcher)
    days = bt.load_days("0900")
    bt._init_worker(days, bt.q2b_aux())
    progress(0.0, "data loaded")
    rows, meters = [], []
    for n, (day, _, summary, log, _) in enumerate(hs.route_days(sc, cfg)):
        rows.append({"day": day, "total": summary["total"], "energy": summary["energy"], "fcess": summary["fcess"],
                     "wear": summary["wear"]})
        if sc.site is not None:
            meters.append(log["meter_mw"].to_numpy())
        progress((n + 1) / len(days), str(day.date()))
    run = pd.DataFrame(rows).set_index("day")
    run["money"] = run["total"]
    if sc.site is not None:  # the month's network bill saving per day, against the factory without a battery
        cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
        bills, _ = bt.q2b_bills({(sc.name, cfg.name): np.concatenate(meters).astype(np.float32)}, days, [sc], cmd_nb)
        per_day = pd.Series((bills["saving"] / bills["days"]).to_numpy(), index=bills["month"].astype(str))
        run["money"] += per_day.reindex(run.index.strftime("%Y-%m")).to_numpy()
    return {"settings": {"connection": connection, "dispatcher": dispatcher, "fcess": list(fcess),
                         "energy_mwh": setup.energy_mwh, "cmd_mw": setup.cmd_mw},
            "periods": _periods(run["money"], run["fcess"], connection, dispatcher),
            "unvalidated": True, "source": saved_rel(connection),
            "note": "settings changed in the app: the same dispatcher and value table, not chosen or tested beforehand"}


def _balance(connection: str, sc, daily: pd.DataFrame, log: pd.DataFrame) -> float:
    """Largest |stored energy change - flows| over every day, MWh, with this battery's round trip: exact from the day's
    5-min sums, as each saved folder's run.py checks it. Behind the meter the battery discharged self + export and charged
    import - the factory's load + self."""
    eta = sc.asset.battery.eta
    d = daily[~daily["strategy"].isin(REFS)]
    if connection == "factory":
        by = log.groupby(["strategy", "day"])
        d = d.set_index(["strategy", "day"])
        load = (by["site_load"].sum() * dp.DT).reindex(d.index)
        flows = ((d["import_mwh"] - load + d["self_mwh"]) * eta - (d["self_mwh"] + d["export_mwh"]) / eta
                 + d["reg_in_mwh"] * eta - d["reg_out_mwh"] / eta)
        return float((d["soc_end"] - by["soc_start"].first().reindex(d.index) - flows).abs().max())
    worst = 0.0
    for _, g in d.sort_values("day").groupby("strategy"):
        start = np.concatenate([[0.0], g["soc_end"].to_numpy()[:-1]])
        flows = (g["import_mwh"] + g["reg_in_mwh"]) * eta - (g["export_mwh"] + g["reg_out_mwh"]) / eta
        worst = max(worst, float(np.abs(g["soc_end"].to_numpy() - start - flows.to_numpy()).max()))
    return worst


def write_runs(connection: str, setup: cg.Config, folder: Path, days: list | None = None, count=None) -> dict:
    """The saved backtest of this connection, battery and CMD in folder, in the files of
    analysis/2026-10-28/app_factory_h50/run.py and app_hurdle50/run.py: e5, v1, and as references the 09:00 plan alone and
    prices known ahead. Returns its checks; RuntimeError if the energy balance or a limit of this battery fails. count:
    vpp.v2.backtest.run's, of the days the v2 runs have finished (v1 too behind the factory meter: there it is one)."""
    days = bt.load_days("0900") if days is None else days
    sc = scenario(connection, SERVICES, setup)
    refs = replace(sc, strategies=REFS)
    factory = connection == "factory"
    tasks = [(sc, replace(vb.E5, record=False), True), (sc, V1 if factory else bt.VALUE, True),
             *((refs, r, False) for r in REFS)]
    daily, logs, meters, _ = vb.run(tasks, days, bt.q2b_aux(), count)
    log = pd.concat([x.assign(day=d, strategy=name) for (_, name), lg in logs.items() for d, x in lg.items()],
                    ignore_index=True)
    folder.mkdir(parents=True)
    if factory:
        ledger = bt.q2b_ledger(daily, [sc])
        cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
        bills, ircr = bt.q2b_bills(meters, days, [sc], cmd_nb)
        bills.to_csv(folder / "bills.csv", index=False)
        ircr.to_csv(folder / "ircr.csv", index=False)
        sto.daily_money(ledger, bills).to_csv(folder / "money.csv")
    rows = ledger if factory else daily
    is_ref = rows["strategy"].isin(REFS)
    rows[~is_ref].to_csv(folder / "daily.csv", index=False)
    rows[is_ref].to_csv(folder / "refs_daily.csv", index=False)
    log.to_parquet(folder / "log.parquet")

    bat = sc.asset.battery
    checks = {"energy_balance_error_mwh": _balance(connection, sc, daily, log), "halfhours": len(log),
              "over_power": int((np.abs(log["energy_mwh"]) / dp.DT > bat.power_mw + TOL).sum()),
              "soc_outside": int(((log["soc"] < -TOL) | (log["soc"] > bat.energy_mwh + TOL)).sum())}
    if checks["energy_balance_error_mwh"] >= TOL or checks["over_power"] or checks["soc_outside"]:
        raise RuntimeError(f"the backtest of {bat.energy_mwh:g} MWh failed its checks: {checks}")
    return checks


def _table(sc) -> Path:
    """Solve the value table this scenario's e5 reads, from the same parameters it looks it up by."""
    p = hs.table_params(sc.asset, sc.site, sc.route.cmd_mw)
    e5 = vb.E5
    return vl.build(e5.level_stat, p.energy_mwh, True, bt.HURDLE if e5.hurdle is None else e5.hurdle, state=e5.state,
                    regime=e5.regime, **{k: v for k, v in p.kwargs().items() if k != "energy_mwh"})


def _one_build_at_a_time():
    """A lock the system lets go of when the process ends, however it ends (none on Windows). Hold on to the result."""
    cg.root().mkdir(parents=True, exist_ok=True)
    lock = open(cg.root() / ".lock", "w")
    try:
        import fcntl
    except ImportError:
        return lock
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise RuntimeError("another battery is being backtested: try again when it has finished") from None
    return lock


def storage_config(params: dict, progress) -> dict:
    """A battery and CMD's value table and saved backtest. Its old manifest goes first and the new one arrives with the
    finished folder, so a run that dies leaves nothing usable, not a half-written table behind a valid manifest. The
    fingerprint is the start's: inputs that change during the run leave it out of date."""
    connection = params.get("connection") or DEFAULT_CONNECTION
    dispatcher = params.get("dispatcher") or DEFAULT_DISPATCHER
    check_mode(connection, dispatcher)
    setup = cg.of(connection, params.get("energy_mwh"), params.get("cmd_mw"))
    if setup.tested:
        raise ValueError("the tested battery and CMD have their saved backtests: nothing to build")
    lock = _one_build_at_a_time()
    stamp = cg.fingerprint()
    final = cg.folder(connection, setup)
    work = final.with_name(f".tmp-{final.name}")
    shutil.rmtree(final, ignore_errors=True)
    for old in cg.root().glob("*/.tmp-*"):
        shutil.rmtree(old, ignore_errors=True)
    progress(0.01, "solving the value table")
    table = _table(scenario(connection, SERVICES, setup))

    days = bt.load_days("0900")
    count, total = mp.Value("i", 0), len(days) * (2 if connection == "factory" else 1)
    done, t0 = threading.Event(), time.time()

    def report():
        while not done.wait(2.0):
            share = count.value / total
            left = (time.time() - t0) * (1 - share) / share if share > 0.03 else None
            progress(0.03 + 0.92 * share, f"backtesting {len(days)} days" + (f", about {max(1, round(left / 60))} min left" if left is not None else ""))

    threading.Thread(target=report, daemon=True).start()
    try:
        checks = write_runs(connection, setup, work, days, count)
    finally:
        done.set()
    progress(0.97, "checking")
    daily, money, _ = read_runs(work, connection)
    fcess = daily.pivot_table(index="day", columns="strategy", values="fcess")
    periods = {d: _periods(money[STRATEGY[connection, d]], fcess[STRATEGY[connection, d]], connection, d) for d in DISPATCHERS}
    (work / "manifest.json").write_text(json.dumps(
        {"connection": connection, "energy_mwh": setup.energy, "cmd_mw": setup.cmd if connection == "factory" else None,
         "fingerprint": stamp, "table": str(table), "checks": checks, "periods": periods,
         "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, allow_nan=False, indent=1), encoding="utf-8")
    os.replace(work, final)
    cg.prune()
    lock.close()
    return {"settings": {"connection": connection, "dispatcher": dispatcher, "energy_mwh": setup.energy_mwh,
                         "cmd_mw": setup.cmd_mw},
            "periods": periods[dispatcher], "unvalidated": True, "source": saved_rel(connection, setup), "checks": checks,
            "note": "the tested dispatcher and hurdle on another battery or CMD, not chosen or tested beforehand"}


def live_setup(params: dict, progress) -> dict:
    """The live data up to date (app.live.run.setup), for the switch in Settings."""
    from app.live import run as live_run
    return live_run.setup(progress)


KINDS = {"storage-backtest": storage_backtest, "storage-config": storage_config, "live-setup": live_setup}


def main(kind: str, params: str) -> None:
    """One line of JSON per message on stdout, which app.backend.jobs reads; anything vpp prints goes to stderr."""
    out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    lock = threading.Lock()

    def say(*msg):
        line = json.dumps(msg, allow_nan=False) + "\n"
        with lock:
            out.write(line)

    try:
        say("done", KINDS[kind](json.loads(params), lambda share, msg: say("progress", share, msg)))
    except Exception:  # noqa: BLE001 - reported on the job
        say("failed", traceback.format_exc(limit=4))


if __name__ == "__main__":
    main(*sys.argv[1:])
