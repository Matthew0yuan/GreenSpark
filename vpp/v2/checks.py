"""v2 checks (reports/v2_plan.md).

  --parity [--full]   with every v2 feature off (config.V1) a v2 run is v1's forecast+offer(value), result for
                      result: day by day in one process on 30 days of 2026-01 (summaries and logs exactly equal,
                      with decision records off and on), and with --full also all days through the process pool
                      (daily rows, logs, float32 meters, ledger and bills). It guards the copied loop against drift.

Run:  python -m vpp.v2.checks --parity [--full]
"""

import sys
from dataclasses import replace

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import site_bill as sb
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs
from vpp.v2.config import V1


def _result(name: str, ok: bool, note: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{': ' + note if note else ''}")
    return ok


def _same(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    try:
        pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_exact=True)
        return True
    except AssertionError as e:
        print(str(e)[:400])
        return False


def parity_in_process(days: list[dict], aux: dict) -> bool:
    bt._init_worker(days, aux)
    sc = vb.m1()
    v1 = list(bt._route_days(sc, bt.VALUE))
    ok = True
    for cfg in (V1, replace(V1, record=True)):
        v2 = list(hs.route_days(sc, cfg))
        same_days = [a[0] for a in v1] == [b[0] for b in v2]
        rows = _same(pd.DataFrame([a[2] for a in v1]), pd.DataFrame([b[2] for b in v2]))
        logs = _same(pd.concat([a[3] for a in v1]), pd.concat([b[3] for b in v2]))
        recs = sum(len(b[4]) for b in v2)
        ok &= _result(f"parity in one process, {len(days)} days, records {'on' if cfg.record else 'off'}",
                      same_days and rows and logs, f"summaries {rows}, logs {logs}, {recs} plans recorded")
    return ok


def parity_full(days: list[dict], aux: dict) -> bool:
    sc = vb.m1()
    rec = replace(V1, name="v1 recorded", record=True)
    daily, logs, meters, records = vb.run([(sc, bt.VALUE, True), (sc, V1, True), (sc, rec, True)], days, aux)
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    ledger = bt.q2b_ledger(daily, [sc])
    bills, _ = bt.q2b_bills(meters, days, [sc], cmd_nb)
    ok = True
    for name in (V1.name, rec.name):
        def part(frame, n):
            return frame[frame["strategy"] == n].drop(columns="strategy").reset_index(drop=True)
        lv1 = pd.concat([x.assign(day=d) for d, x in logs[sc.name, bt.VALUE].items()])
        lv2 = pd.concat([x.assign(day=d) for d, x in logs[sc.name, name].items()])
        checks = {"daily": _same(part(daily, bt.VALUE), part(daily, name)), "logs": _same(lv1, lv2),
                  "meters": np.array_equal(meters[sc.name, bt.VALUE], meters[sc.name, name]),
                  "ledger": _same(part(ledger, bt.VALUE), part(ledger, name)),
                  "bills": _same(part(bills, bt.VALUE), part(bills, name))}
        ok &= _result(f"parity through the pool, {len(days)} days, {name}", all(checks.values()),
                      ", ".join(f"{k} {v}" for k, v in checks.items()))
    _result("decision records returned from the workers", len(records) > 0, f"{len(records)} plans")
    return ok


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    ok = True
    if "--parity" in argv:
        days, _, aux = bt.q2b_inputs(smoke=True)
        ok &= parity_in_process(days, aux)
        if "--full" in argv:
            ok &= parity_full(bt.load_days("0900"), aux)
    print("ALL CHECKS PASS" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
