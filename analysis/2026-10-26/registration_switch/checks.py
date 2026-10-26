"""PLAN.md checks 2 (no peeking) and 3 (what the switch allows).

  .venv/Scripts/python.exe analysis/2026-10-26/registration_switch/checks.py
"""

import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = str(HERE.parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import contracts as ct  # noqa: E402
from vpp import dispatch as dp  # noqa: E402

BAT = bt.Q2B_ASSET.battery
STANDALONE = ct.Route("A", metering="standalone")
NON_SCHEDULED = replace(STANDALONE, name="C", registration="non_scheduled", facilities=3, fcess=())
TRIALS = sorted([*range(36, 288, 12), *range(41, 288, 12)])  # the first and the last 5 minutes of a half hour, every hour from 11:00


def refused(route: ct.Route) -> str:
    try:
        route.check(BAT, None)
    except ValueError as e:
        return str(e).split(": ", 1)[1]
    return ""


def check_rules() -> bool:
    cases = [  # (route, should it be refused)
        (STANDALONE, False),
        (replace(STANDALONE, fcess=()), False),
        (NON_SCHEDULED, False),
        (replace(NON_SCHEDULED, facilities=1), True),   # 20 MW of system size
        (replace(NON_SCHEDULED, facilities=2), True),   # 10 MW: not below 10
        (replace(NON_SCHEDULED, fcess=tuple(dp.FCESS)), True),
        (replace(NON_SCHEDULED, contracts=(ct.scm(5.0),)), True),
        (replace(NON_SCHEDULED, rcm="gross"), True),
        (replace(NON_SCHEDULED, gate_lag=0), True),
        (replace(NON_SCHEDULED, registration="semi"), True),
    ]
    ok = True
    for route, should in cases:
        why = refused(route)
        size = ct.system_size_mw(BAT, None, None, max(1, route.facilities))
        good = bool(why) == should
        ok &= good
        print(f"  {'ok ' if good else 'BAD'} {route.registration:13s} facilities={route.facilities} size={size:5.2f} MW "
              f"fcess={len(route.fcess)} rcm={route.rcm} contracts={len(route.contracts)} gate_lag={route.gate_lag}: "
              f"{'refused - ' + why if why else 'allowed'}")
    print(f"  assumptions (C): {NON_SCHEDULED.assumptions()}")
    return ok


def day_kw(route: ct.Route, day: dict, aux: dict) -> dict:
    """run_day's arguments for one day of a route, as the backtest makes them."""
    sc = bt._sc(route.name, route, (bt.VALUE,))
    got = {}

    def grab(d, kw):
        got.update(kw)
        return None

    bt._init_worker([day], aux)
    for _ in bt._route_days(sc, bt.VALUE, decide=grab):
        pass
    got.pop("reserve", None)
    return got


def steps(kw: dict, p5: np.ndarray) -> list[float]:
    rec = []
    dp.run_day(**{**kw, "p5": p5}, on_step=lambda x: rec.append(x["pw"]))
    return rec


def check_no_peeking() -> bool:
    days = bt.load_days("0900")
    aux = bt.q2b_aux()
    test = [d for d in days if d["day"] >= pd.Timestamp("2026-07-01")]
    ok = True
    routes = ((NON_SCHEDULED, True), (replace(NON_SCHEDULED, name="D", gate_lag=2), True),
              (replace(STANDALONE, fcess=()), False))
    for d in sorted(test, key=lambda d: -float(np.max(d["p5"])))[:3]:  # the three test days with the highest 5-min price
        for route, must_hold in routes:
            kw = day_kw(route, d, aux)
            base = steps(kw, np.asarray(d["p5"], float))
            lag = max(1, route.gate_lag if route.registration != "scheduled" else 1)
            changed = 0
            for k in TRIALS:  # one step at a time
                p5 = np.asarray(d["p5"], float).copy()
                p5[k] = 10_000.0
                pert = steps(kw, p5)
                # a price published at step k can first be acted on at step k + lag
                changed += int(not np.allclose(pert[k:k + lag], base[k:k + lag]))
                ok &= np.allclose(pert[:k], base[:k])
            if must_hold:
                ok &= changed == 0
            print(f"  {d['day'].date()} {route.name} ({route.registration}, lag {lag}): steps k to k+{lag - 1} changed when "
                  f"step k's price was raised to $10,000 in {changed} of {len(TRIALS)} trials "
                  f"({'must be 0' if must_hold else 'a Scheduled offer may clear on it'})")
    return ok


if __name__ == "__main__":
    print("check 3 - what the switch allows:")
    r = check_rules()
    print("check 2 - a Non-Scheduled step never uses its own price:")
    n = check_no_peeking()
    print(f"check 3 {'PASS' if r else 'FAIL'}; check 2 {'PASS' if n else 'FAIL'}")
