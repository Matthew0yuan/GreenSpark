"""Step 0 runner (analysis/2026-10-28/spikev3/PLAN.md section 2 and change log 1): e5 configurations through every
day, each task with at most one input replaced by its perfect-foresight version (vpp.spikev3.oracle). As
vpp.v2.backtest.run, without editing vpp.v2: the price oracles swap the worker's day inputs (vpp.backtest._DAYS), the
table oracles put a value table in vpp.v2.harness.VALUE_INPUTS under the key the harness looks up. Every task runs
in a fresh worker (max_tasks_per_child=1) that starts with an empty table cache and ends asserting the cache holds
only the table this task was meant to use.

The workers record, for PROBE days, what the dispatcher was handed (the 09:00 energy forecast revealed to the plans,
the plans' FCESS prices from 08:00, the market states), to check the injections (PLAN.md section 2.5, check 3).
"""

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp.spikev3 import oracle as orc
from vpp.v2 import harness as hs
from vpp.v2 import value as vl

_SPEC: dict = {}      # labels, partners by seed, c, probe_days: set in every worker


@dataclass(frozen=True)
class Task:
    sc: object                    # vpp.backtest.Scenario
    cfg: object                   # vpp.v2.config.V2Config; its name labels the run
    oracle: str | None = None     # None, "A", "A_ctrl", "B", "B_ctrl", "B2", "C_e", "C_ef"
    table: str | None = None      # a value table to use in place of the cached one (O-A, its controls, R5, R0')
    seed: int = 0                 # the controls' pairing
    keep_logs: bool = True


def table_key(sc, cfg) -> tuple:
    """(harness.VALUE_INPUTS key, table parameters, hurdle) as vpp.v2.harness.value_inputs computes them."""
    s = hs.scenario(sc, cfg)
    params = hs.table_params(s.asset, s.site, s.route.cmd_mw)
    hurdle = bt.HURDLE if cfg.hurdle is None else cfg.hurdle
    return (cfg.level_stat, hurdle, cfg.state, cfg.regime, params.key()), params, hurdle


def _init(days: list[dict], aux: dict, spec: dict) -> None:
    bt._init_worker(days, aux)
    _SPEC.clear()
    _SPEC.update(spec)


def _days_for(task: Task, base: list[dict]) -> list[dict]:
    if task.oracle not in orc.PRICE_ORACLES:
        return base
    by = {d["day"]: d for d in base}
    part = _SPEC["partners"][task.seed]
    return [orc.inject(d, task.oracle, _SPEC["c"], by[part[d["day"]]] if task.oracle == "B_ctrl" else None)
            for d in base]


def _table_for(task: Task):
    if task.table is None:
        return None
    key, params, hurdle = table_key(task.sc, task.cfg)
    inputs = vl.load(task.table)
    vl.check_table(Path(task.table), inputs, params, task.cfg.level_stat, hurdle, task.cfg.state, task.cfg.regime)
    if task.oracle == "A":
        same = orc.with_states(inputs, _SPEC["labels"])
        for k in ("state_today", "state_tomorrow"):  # the table was built on the labels: its states are them already
            if not np.array_equal(same[k], inputs[k], equal_nan=True):
                raise AssertionError(f"O-A table's {k} differs from the labels")
    elif task.oracle == "A_ctrl":
        inputs = orc.with_states(inputs, _SPEC["labels"], _SPEC["partners"][task.seed])
    return key, inputs


def _task(task: Task) -> tuple:
    hs.VALUE_INPUTS.clear()
    table = _table_for(task)
    key = table_key(task.sc, task.cfg)[0]
    if table is not None:
        hs.VALUE_INPUTS[table[0]] = table[1]
    bt._DAYS = _days_for(task, bt._DAYS)
    probe_days = set(_SPEC.get("probe_days", ()))
    probes = []

    def on_day(day, kw, summary, log):
        if day not in probe_days:
            return
        rv = [r for r in kw["terms"].reveals if r.label == "forecast issued"]
        rec = {"run": task.cfg.name, "day": day,
               "fc_reveal": np.asarray(rv[0].energy, float).copy() if rv else None,
               "fc_f": {k: np.asarray(v, float).copy() for k, v in kw["fc_f"].items()}}
        inp = hs.VALUE_INPUTS.get(key)
        if task.cfg.state and inp is not None and "state_today" in inp:  # the harness reads states only with state
            i = inp["index"][pd.Timestamp(day).normalize()]
            rec.update(state_today=float(inp["state_today"][i]), state_tomorrow=float(inp["state_tomorrow"][i]))
        probes.append(rec)

    rows, logs, meters = [], {}, []
    for day, _, summary, log, _ in hs.route_days(task.sc, task.cfg, on_day=on_day):
        rows.append({"scenario": task.sc.name, "strategy": task.cfg.name, "day": day, **summary})
        meters.append(log["meter_mw"].to_numpy().astype(np.float32))
        if task.keep_logs:
            logs[day] = log
    cached = list(hs.VALUE_INPUTS)
    if cached != [key]:
        raise AssertionError(f"{task.cfg.name}: value-table cache holds {cached}, expected only {key}")
    if table is not None and hs.VALUE_INPUTS[key] is not table[1]:
        raise AssertionError(f"{task.cfg.name}: the preloaded table was replaced during the run")
    return task.sc.name, task.cfg.name, rows, logs, np.vstack(meters), probes


def run(tasks: list[Task], days: list[dict], aux: dict, spec: dict, workers: int | None = None):
    """Run the tasks in parallel, each in its own worker. Returns the daily rows (with period), the logs and float32
    meters by (scenario, run name), and the probe records."""
    names = [t.cfg.name for t in tasks]
    if len(set(names)) != len(names):
        raise ValueError(f"run names must differ: {names}")
    workers = workers or min(len(tasks), max(1, (os.cpu_count() or 2) - 1))
    daily, logs, meters, probes = [], {}, {}, []
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(days, aux, spec),
                             max_tasks_per_child=1) as pool:
        for sc, name, rows, lg, meter, pr_ in pool.map(_task, tasks):
            daily.extend(rows)
            if lg:
                logs[sc, name] = lg
            meters[sc, name] = meter
            probes.extend(pr_)
    daily = pd.DataFrame(daily)
    daily["period"] = bt.period_of(daily["day"])
    return daily, logs, meters, probes
