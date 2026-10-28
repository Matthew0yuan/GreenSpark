"""The app's saved backtests at hurdle 15 A$/MWh (the app's hurdle for part of 2026-10-28, then 50: app_hurdle50)
instead of the research default 75: the same runs as analysis/2026-10-27/standalone_v2 (run.py: v1 and e5; run_refs.py:
the 09:00 plan alone and prices known ahead) on route sa, every trading day. Nothing is chosen on these numbers: the
dispatcher stays v1 (adopted at hurdle 75 by the 10-27 PLAN.md); the comparison with 75 is reported only.

  .venv/bin/python analysis/2026-10-28/app_hurdle15/run.py
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
from vpp import contracts as ct  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402

OLD = HERE.parents[1] / "2026-10-27" / "standalone_v2"  # the same runs at hurdle 75
UNSEEN = pd.Timestamp("2026-10-16")
PERIODS = ("in-sample 2025-04-01..2026-06-30", "test seen 2026-07-01..2026-10-15", "test unseen 2026-10-16..2026-10-22")
E5, V1 = vb.E5.name, bt.VALUE
TOL = 1e-6
HURDLE = 15.0  # A$/MWh


def period(day: pd.Series) -> pd.Series:
    d = pd.to_datetime(day)
    return pd.Series(np.where(d < bt.CUTOFF, PERIODS[0], np.where(d < UNSEEN, PERIODS[1], PERIODS[2])), index=day.index)


def balance(daily: pd.DataFrame) -> float:
    """Largest |stored energy change - (in x eta - out / eta)| over every day, MWh."""
    eta = bt.Q2B_ASSET.battery.eta
    worst = 0.0
    for _, g in daily.sort_values("day").groupby("strategy"):
        start = np.concatenate([[0.0], g["soc_end"].to_numpy()[:-1]])
        flows = (g["import_mwh"] + g["reg_in_mwh"]) * eta - (g["export_mwh"] + g["reg_out_mwh"]) / eta
        worst = max(worst, float(np.abs(g["soc_end"].to_numpy() - start - flows.to_numpy()).max()))
    return worst


def main() -> None:
    t0 = time.time()
    prefs = replace(bt.PREFS, hurdle_per_mwh=HURDLE)
    sc = bt._sc("sa", ct.Route("sa", metering="standalone"), (V1,), prefs=prefs)
    refs = bt._sc("sa", ct.Route("sa", metering="standalone"), ("forecast", "perfect"), prefs=prefs)
    assert sc is not None and refs is not None, bt.REFUSED
    days = bt.load_days("0900")
    tasks = [(sc, replace(vb.E5, record=False), True), (sc, V1, True), (refs, "forecast", False), (refs, "perfect", False)]
    out, logs, _, _ = vb.run(tasks, days, bt.q2b_aux())
    print(f"hurdle {HURDLE:g} A$/MWh: {len(days)} days x {len(tasks)} runs in {time.time() - t0:.0f} s")
    out["period"] = period(out["day"])
    is_ref = out["strategy"].isin(["forecast", "perfect"])
    daily, ref_daily = out[~is_ref], out[is_ref]
    daily.to_csv(HERE / "daily.csv", index=False)
    ref_daily.to_csv(HERE / "refs_daily.csv", index=False)
    log = pd.concat([x.assign(day=d, strategy=name) for (_, name), lg in logs.items() for d, x in lg.items()],
                    ignore_index=True)
    log.to_parquet(HERE / "log.parquet")
    (HERE / "adopted.txt").write_text(V1 + "\n", encoding="utf-8")

    err = balance(daily)
    print(f"energy balance: largest error {err:.2e} MWh -> {'PASS' if err < TOL else 'FAIL'}")
    P, E = bt.Q2B_ASSET.battery.power_mw, bt.Q2B_ASSET.battery.energy_mwh
    over_p = int((np.abs(log["energy_mwh"]) / dp.DT > P + TOL).sum())
    soc_bad = int(((log["soc"] < -TOL) | (log["soc"] > E + TOL)).sum())
    print(f"half-hour mean output within {P:g} MW: {over_p} of {len(log)} outside -> {'PASS' if over_p == 0 else 'FAIL'}; "
          f"stored energy within 0-{E:g} MWh: {soc_bad} outside -> {'PASS' if soc_bad == 0 else 'FAIL'}")

    # hurdle 15 against the 10-27 runs at 75, same strategy and day, A$ per day
    old = pd.concat([pd.read_csv(OLD / "daily.csv"), pd.read_csv(OLD / "refs_daily.csv")], ignore_index=True)
    new = pd.concat([daily, ref_daily], ignore_index=True)
    cols = ["energy", "fcess", "wear", "total", "discharged_mwh"]
    old["day"], new["day"] = pd.to_datetime(old["day"]), pd.to_datetime(new["day"])
    m = new.merge(old[["strategy", "day", *cols]], on=["strategy", "day"], suffixes=("", "_75"))
    assert len(m) == len(new), "a day or strategy missing from the hurdle-75 runs"
    rows = []
    for (p, s), g in m.groupby(["period", "strategy"]):
        diff = g.set_index("day")["total"] - g.set_index("day")["total_75"]
        lo, hi = vb.month_ci(diff) if diff.index.to_period("M").nunique() > 1 else (np.nan, np.nan)
        rows.append({"period": p, "strategy": s, "days": len(g), **{f"{c}_15": g[c].mean() for c in cols},
                     "total_75": g["total_75"].mean(), "discharged_mwh_75": g["discharged_mwh_75"].mean(),
                     "h15_minus_h75": diff.mean(), "lo95_month": lo, "hi95_month": hi, "days_15_better": int((diff > 0).sum())})
    comp = pd.DataFrame(rows)
    comp.to_csv(HERE / "compare_75.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(comp.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
