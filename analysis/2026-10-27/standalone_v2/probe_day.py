"""Probe (no result is chosen on it): one standalone e5 day with the 09:00 offer collector, to see what run_day's
steps carry without a site and whether vpp.v2.offers.Collector runs on it.

  .venv/Scripts/python.exe analysis/2026-10-27/standalone_v2/probe_day.py
"""

import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import harness as hs  # noqa: E402
from vpp.v2 import offers as of  # noqa: E402

DAY = pd.Timestamp("2026-01-20")


def main() -> None:
    t = time.time()
    days = bt.load_days("0900")
    aux = bt.q2b_aux()
    print(f"loaded in {time.time() - t:.1f} s")
    i = next(k for k, d in enumerate(days) if d["day"] == DAY)
    bt._init_worker(days[i:i + 2], aux)
    sc = vb.sa()
    cfg = replace(vb.E5, record=True)
    bat = hs.scenario(sc, cfg).asset.battery
    col = of.Collector(bat)
    first = {}

    def on_step(d, x):
        if not first:
            first.update({k: (type(v).__name__, v if np.isscalar(v) or v is None else "...") for k, v in x.items()})
        x = dict(x)
        if x["load_act"] is None:
            x["load_act"] = 0.0
        col(d, x)

    def on_day(d, kw, summary, log):
        P = bat.power_mw
        kw2 = {**kw, "load_fc": np.zeros(dp.N), "site": SimpleNamespace(export_limit_mw=P, import_limit_mw=P)}
        col.on_day(d, kw2, summary, log)

    t = time.time()
    _, _, summary, log, recs = next(hs.route_days(sc, cfg, 0.0, {}, until=DAY, on_day=on_day, on_step=on_step))
    print(f"day in {time.time() - t:.1f} s; total {summary['total']:.2f}; records {len(recs)}; kinds "
          f"{sorted({(r['t0'], r['kind']) for r in recs})[:4]}")
    print("step keys:", {k: v for k, v in first.items()})
    fwd = col.forward.get(DAY, {})
    print(f"09:00 offers: {len(fwd)} DIs")
    req = of.request(DAY, fwd, "x", "x")
    print("rule errors:", of.rule_errors(req, bat.power_mw)[:5])


if __name__ == "__main__":
    main()
