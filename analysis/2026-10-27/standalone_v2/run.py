"""PLAN.md comparison: e5 vs v1 on the standalone Scheduled storage network (route sa), every trading day.

  .venv/Scripts/python.exe analysis/2026-10-27/standalone_v2/run.py
"""

import sys
import time
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
from vpp.v2 import backtest as vb  # noqa: E402

UNSEEN = pd.Timestamp("2026-10-16")
PERIODS = ("in-sample 2025-04-01..2026-06-30", "test seen 2026-07-01..2026-10-15", "test unseen 2026-10-16..2026-10-22")
E5, V1 = vb.E5.name, bt.VALUE
TOL = 1e-6


def period(day: pd.Series) -> pd.Series:
    d = pd.to_datetime(day)
    return pd.Series(np.where(d < bt.CUTOFF, PERIODS[0], np.where(d < UNSEEN, PERIODS[1], PERIODS[2])), index=day.index)


def balance(daily: pd.DataFrame) -> float:
    """Check 3: largest |stored energy change - (in x eta - out / eta)| over every day, MWh."""
    eta = bt.Q2B_ASSET.battery.eta
    worst = 0.0
    for _, g in daily.sort_values("day").groupby("strategy"):
        start = np.concatenate([[0.0], g["soc_end"].to_numpy()[:-1]])
        flows = (g["import_mwh"] + g["reg_in_mwh"]) * eta - (g["export_mwh"] + g["reg_out_mwh"]) / eta
        worst = max(worst, float(np.abs(g["soc_end"].to_numpy() - start - flows.to_numpy()).max()))
    return worst


def main() -> None:
    t0 = time.time()
    sc = vb.sa()
    assert sc is not None, bt.REFUSED
    days = bt.load_days("0900")
    daily, logs, _, _ = vb.run([(sc, replace(vb.E5, record=False), True), (sc, V1, True)], days, bt.q2b_aux())
    print(f"{len(days)} days x 2 dispatchers in {time.time() - t0:.0f} s")
    daily["period"] = period(daily["day"])
    daily.to_csv(HERE / "daily.csv", index=False)
    log = pd.concat([x.assign(day=d, strategy=name) for (_, name), lg in logs.items() for d, x in lg.items()],
                    ignore_index=True)
    log.to_parquet(HERE / "log.parquet")

    err = balance(daily)
    print(f"check 3 - energy balance: largest error {err:.2e} MWh -> {'PASS' if err < TOL else 'FAIL'}")
    P, E = bt.Q2B_ASSET.battery.power_mw, bt.Q2B_ASSET.battery.energy_mwh
    over_p = int((np.abs(log["energy_mwh"]) / dp.DT > P + TOL).sum())
    soc_bad = int(((log["soc"] < -TOL) | (log["soc"] > E + TOL)).sum())
    print(f"check 5 - half-hour mean output within {P:g} MW: {over_p} of {len(log)} -> {'PASS' if over_p == 0 else 'FAIL'}; "
          f"stored energy within 0-{E:g} MWh: {soc_bad} outside -> {'PASS' if soc_bad == 0 else 'FAIL'}")

    summ = (daily.groupby(["period", "strategy"])
            .agg(days=("day", "count"), energy=("energy", "mean"), fcess=("fcess", "mean"), wear=("wear", "mean"),
                 total=("total", "mean"), discharged_mwh=("discharged_mwh", "mean"))
            .reset_index())
    summ.to_csv(HERE / "summary.csv", index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(summ.round(1).to_string(index=False))

    v = daily.pivot_table(index="day", columns="strategy", values="total")
    v.index = pd.to_datetime(v.index)
    diff = v[E5] - v[V1]
    rows = []
    for p in PERIODS:
        g = diff[period(pd.Series(diff.index, index=diff.index)) == p]
        lo, hi = vb.month_ci(g) if g.index.to_period("M").nunique() > 1 else (np.nan, np.nan)
        rows.append({"period": p, "days": len(g), "e5_minus_v1": g.mean(), "lo95_month": lo, "hi95_month": hi,
                     "days_e5_better": int((g > 0).sum())})
    diffs = pd.DataFrame(rows)
    diffs.to_csv(HERE / "diffs.csv", index=False)
    print(diffs.round(1).to_string(index=False))
    ins = diffs.iloc[0]
    adopt = E5 if ins["lo95_month"] > 0 else V1
    print(f"pass rule (in-sample month-block 95% lower bound > 0): {ins['lo95_month']:.1f} -> adopt {adopt}")
    (HERE / "adopted.txt").write_text(adopt + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
