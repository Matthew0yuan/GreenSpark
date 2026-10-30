"""PLAN.md checks 1, 2, 4, 5 for the standalone Scheduled storage network on v2.

  .venv/Scripts/python.exe analysis/2026-10-27/standalone_v2/checks.py before   # check 1, before the code change
  .venv/Scripts/python.exe analysis/2026-10-27/standalone_v2/checks.py after    # check 1, after it: bit for bit
  .venv/Scripts/python.exe analysis/2026-10-27/standalone_v2/checks.py table    # check 2
  .venv/Scripts/python.exe analysis/2026-10-27/standalone_v2/checks.py peek     # check 4
  .venv/Scripts/python.exe analysis/2026-10-27/standalone_v2/checks.py power    # check 5 (needs run.py's log)
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
from vpp import dispatch as dp  # noqa: E402
from vpp.evaluate import TRAIN_END  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import harness as hs  # noqa: E402

PEEK_DAYS = ("2026-01-20", "2025-08-12", "2026-09-09")  # in-sample Hot Season, in-sample other, test [fixed in PLAN.md]


def parity(tag: str) -> None:
    """Check 1: e5 on m1 (the factory route) over the smoke days."""
    days = vb.smoke_days(bt.load_days("0900"))
    daily, _, _, _ = vb.run([(vb.m1(), vb.E5, False)], days, bt.q2b_aux())
    out = HERE / f"parity_{tag}.csv"
    daily.drop(columns=["period"]).to_csv(out, index=False, float_format="%.17g")
    print(f"{tag}: {len(daily)} days -> {out.name}")
    if tag == "after":
        a, b = pd.read_csv(HERE / "parity_before.csv"), pd.read_csv(out)
        same = a.shape == b.shape and a.equals(b)
        num = a.select_dtypes("number").columns
        worst = float(np.nanmax(np.abs(a[num].to_numpy() - b[num].to_numpy()))) if a.shape == b.shape else np.inf
        print(f"check 1 - factory route unchanged: {'PASS' if same else 'FAIL'} (largest difference {worst:.3g})")


def table() -> None:
    """Check 2: the standalone table's curves and offer values non-increasing and >= 0; windows before each month."""
    from vpp import storage_value as sv
    from vpp.v2 import value as vl
    p = vl.path("median", hurdle=vb.E5.hurdle, state=True, **vl.standalone_params(bt.Q2B_ASSET).kwargs())
    z = vl.load(p)
    ok = True
    for k in ("curve", "marg", "curve_state", "marg_state"):
        x = z[k].astype(float)
        flat = bool((np.diff(x, axis=-1) <= 1e-6).all())
        pos = bool((x >= 0).all())
        print(f"{'PASS' if flat and pos else 'FAIL'}  {k}: non-increasing {flat}, >= 0 {pos}")
        ok &= flat and pos
    table = vl.prices()
    for month in sv.MONTHS:
        end = min(month.start_time, bt.CUTOFF)
        used = table.index[(table.index >= end - sv.WINDOW) & (table.index < end) & (table.index <= TRAIN_END)]
        ok &= bool(used.max() < month.start_time and used.max() <= TRAIN_END)
    rec = vl.recorded(p)
    print(f"{p.name}: {len(z['index'])} days, record {rec['params'] if rec else None}")
    print(f"check 2 - table: {'PASS' if ok else 'FAIL'}")


def _plan_0900(days: list[dict], day: str, aux: dict, cfg) -> dict:
    """The 09:00 plan (record t0 == 2) of cfg on the standalone route for one day, from 0 stored energy."""
    bt._init_worker(days, aux)
    for d, _, _, _, recs in hs.route_days(vb.sa(), replace(cfg, record=True), 0.0, {}, until=pd.Timestamp(day)):
        if d == pd.Timestamp(day):
            r = [x for x in recs if x["t0"] == 2]
            return r[0]
    raise KeyError(day)


def peek() -> None:
    """Check 4: actual prices after 09:00 x 3 leave the 09:00 plan unchanged."""
    all_days = bt.load_days("0900")
    aux = bt.q2b_aux()
    from vpp.v2.config import V1
    ok = True
    for cfg in (vb.E5, V1):
        for day in PEEK_DAYS:
            i = next(k for k, d in enumerate(all_days) if d["day"] == pd.Timestamp(day))
            base = _plan_0900([all_days[i]], day, aux, cfg)
            d = dict(all_days[i])
            rtp, p5 = d["rtp"].copy(), d["p5"].copy()
            rtp[2:] *= 3.0
            p5[2 * dp.STEPS:] *= 3.0
            d["rtp"], d["p5"] = rtp, p5
            hit = _plan_0900([d], day, aux, cfg)
            keys = [k for k in base if isinstance(base[k], np.ndarray) or isinstance(base[k], (int, float))]
            diff = [k for k in keys if not np.array_equal(np.asarray(base[k]), np.asarray(hit[k]), equal_nan=True)]
            print(f"{cfg.name} {day}: {len(keys)} fields compared; changed: {diff or 'none'}")
            ok &= not diff
    print(f"check 4 - no peeking: {'PASS' if ok else 'FAIL'}")


def power() -> None:
    """Check 5: every 5-minute step within the 10 MW both ways, energy plus regulation (run.py's log)."""
    log = pd.read_parquet(HERE / "log.parquet")
    P = bt.Q2B_ASSET.battery.power_mw
    print(f"log columns: {list(log.columns)}")
    out_mw = log["dis_mw"] + log.get("reg_raise_mw", 0) if "dis_mw" in log else None
    if out_mw is None:
        print("check 5: columns not found, see the list above")
        return
    in_mw = log["chg_mw"] + log.get("reg_lower_mw", 0)
    worst = max(float((out_mw - P).max()), float((in_mw - P).max()))
    print(f"check 5 - power cap: largest excess {worst:.3g} MW -> {'PASS' if worst <= 1e-6 else 'FAIL'}")


if __name__ == "__main__":
    {"before": lambda: parity("before"), "after": lambda: parity("after"), "table": table, "peek": peek,
     "power": power}[sys.argv[1]]()
