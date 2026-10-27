"""Long engine runs, each in its own process (app.backend.jobs): the adopted dispatcher through every trading day with
changed settings, compared with the saved run of the default settings, by period."""

from dataclasses import replace

import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs
from vpp.v2.config import V1

from .common import PERIODS, STANDALONE, num, period, period_key


def storage_backtest(params: dict, progress) -> dict:
    """progress(share done, message) is called after each day."""
    adopted = (STANDALONE / "adopted.txt").read_text(encoding="utf-8").strip()
    cfg = replace(vb.E5 if adopted == vb.E5.name else V1, record=False)
    fcess = tuple(params.get("fcess") or ())
    sc = bt._sc("sa", ct.Route("sa", metering="standalone", fcess=fcess), (bt.VALUE,))
    if sc is None:
        raise ValueError(f"route refused: {bt.REFUSED}")
    days = bt.load_days("0900")
    bt._init_worker(days, bt.q2b_aux())
    progress(0.0, "data loaded")
    rows = []
    for n, (day, _, summary, _, _) in enumerate(hs.route_days(sc, cfg)):
        rows.append({"day": day, "total": summary["total"], "energy": summary["energy"], "fcess": summary["fcess"],
                     "wear": summary["wear"]})
        progress((n + 1) / len(days), str(day.date()))
    run = pd.DataFrame(rows).set_index("day")
    saved = pd.read_csv(STANDALONE / "daily.csv", parse_dates=["day"])
    saved = saved[saved["strategy"] == adopted].set_index("day")
    keys = pd.Series([period_key(d) for d in run.index], index=run.index)
    out = []
    for k in PERIODS:
        g = run[keys == k]
        s = saved.loc[g.index]
        out.append({"period": period(k, len(g)), "per_day_aud": num(g["total"].mean(), 1),
                    "default_per_day_aud": num(s["total"].mean(), 1),
                    "difference_aud": num((g["total"] - s["total"]).mean(), 1),
                    "fcess_per_day_aud": num(g["fcess"].mean(), 1)})
    return {"settings": {"fcess": list(fcess)}, "periods": out, "unvalidated": True,
            "note": "settings changed in the app: the same dispatcher and value table, not chosen or tested beforehand"}
