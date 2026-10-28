"""Long engine runs, each in its own process (app.backend.jobs): a dispatcher on a connection through every trading day
with changed settings, compared with the saved run of the default settings, by period. Behind the factory meter the
money of a day is its ledger plus the month's network bill saving per day, as in the saved runs."""

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import site_bill as sb
from vpp.v2 import harness as hs

from .common import (DEFAULT_CONNECTION, DEFAULT_DISPATCHER, PERIODS, STRATEGY, check_mode, num, period, period_key,
                     saved_rel)
from .evidence import saved_runs
from .storage import config, scenario


def storage_backtest(params: dict, progress) -> dict:
    """progress(share done, message) is called after each day."""
    connection = params.get("connection") or DEFAULT_CONNECTION
    dispatcher = params.get("dispatcher") or DEFAULT_DISPATCHER
    check_mode(connection, dispatcher)
    fcess = tuple(params.get("fcess") or ())
    sc, cfg = scenario(connection, fcess), config(dispatcher)
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
    saved = saved_runs(connection)[1][STRATEGY[connection, dispatcher]]
    keys = pd.Series([period_key(d) for d in run.index], index=run.index)
    out = []
    for k in PERIODS:
        g = run[keys == k]
        s = saved.reindex(g.index)
        out.append({"period": period(k, len(g)), "per_day_aud": num(g["money"].mean(), 1),
                    "default_per_day_aud": num(s.mean(), 1),
                    "difference_aud": num((g["money"] - s).mean(), 1),
                    "fcess_per_day_aud": num(g["fcess"].mean(), 1)})
    return {"settings": {"connection": connection, "dispatcher": dispatcher, "fcess": list(fcess)}, "periods": out,
            "unvalidated": True, "source": saved_rel(connection),
            "note": "settings changed in the app: the same dispatcher and value table, not chosen or tested beforehand"}
