"""PLAN.md comparison: the same 10 MW / 40 MWh standalone storage, Scheduled vs Non-Scheduled, every day.

  .venv/Scripts/python.exe analysis/2026-10-26/registration_switch/run.py
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

STRATS = ("perfect", "forecast", bt.VALUE)
UNSEEN = pd.Timestamp("2026-10-16")
PERIODS = ("样本内 2025-04-01 至 2026-06-30", "测试期（看过）2026-07-01 至 2026-10-15", "测试期（没看过）2026-10-16 至 2026-10-22")


def scenarios() -> list:
    a = ct.Route("A", metering="standalone")
    c = replace(a, registration="non_scheduled", facilities=3, fcess=())
    return [bt._sc("A Scheduled + FCESS", a, STRATS), bt._sc("B Scheduled, no FCESS", replace(a, fcess=()), STRATS),
            bt._sc("C Non-Scheduled, lag 1", c, STRATS), bt._sc("D Non-Scheduled, lag 2", replace(c, gate_lag=2), STRATS)]


def period(day: pd.Series) -> pd.Series:
    d = pd.to_datetime(day)
    return pd.Series(np.where(d < bt.CUTOFF, PERIODS[0], np.where(d < UNSEEN, PERIODS[1], PERIODS[2])), index=day.index)


def balance(daily: pd.DataFrame) -> float:
    """PLAN.md check 4: largest |stored energy change - (charged x eta - discharged / eta)| over every day, MWh."""
    eta = bt.Q2B_ASSET.battery.eta
    worst = 0.0
    for _, g in daily.sort_values("day").groupby(["scenario", "strategy"]):
        start = np.concatenate([[0.0], g["soc_end"].to_numpy()[:-1]])
        flows = (g["import_mwh"] + g["reg_in_mwh"]) * eta - (g["export_mwh"] + g["reg_out_mwh"]) / eta
        worst = max(worst, float(np.abs(g["soc_end"].to_numpy() - start - flows.to_numpy()).max()))
    return worst


def main() -> None:
    scs = scenarios()
    assert all(s is not None for s in scs), bt.REFUSED
    days = bt.load_days("0900")
    tasks = [(sc, name, False) for sc in scs for name in sc.strategies]
    daily, _, _ = bt.run_tasks(tasks, days, bt.q2b_aux())
    daily["period"] = period(daily["day"])
    daily.to_csv(HERE / "daily.csv", index=False)
    err = balance(daily)
    print(f"check 4 - energy balance: largest error {err:.2e} MWh -> {'PASS' if err < 1e-6 else 'FAIL'}")
    same = daily.pivot_table(index=["day"], columns=["scenario", "strategy"], values="total")
    plan_only = same.xs("forecast", axis=1, level="strategy")[[s.name for s in scs[1:]]]
    print(f"consistency: plan-only identical in B, C, D: {bool(np.allclose(plan_only.to_numpy(), plan_only.iloc[:, [0]].to_numpy()))}")

    summ = (daily.groupby(["period", "scenario", "strategy"])
            .agg(days=("day", "count"), energy=("energy", "mean"), fcess=("fcess", "mean"), wear=("wear", "mean"),
                 total=("total", "mean"), discharged_mwh=("discharged_mwh", "mean"))
            .reset_index())
    perfect = summ[summ["strategy"] == "perfect"].set_index(["period", "scenario"])["total"]
    summ["share_of_perfect"] = summ["total"] / perfect.reindex(pd.MultiIndex.from_frame(summ[["period", "scenario"]])).to_numpy()
    summ.to_csv(HERE / "summary.csv", index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(summ.round(1).to_string(index=False))

    rows = []
    v = daily[daily["strategy"] == bt.VALUE].pivot_table(index=["period", "day"], columns="scenario", values="total")
    names = {s.name[0]: s.name for s in scs}
    for p in PERIODS:
        g = v.loc[p]
        for label, x, y in (("A - B: FCESS", "A", "B"), ("B - C: Non-Scheduled, lag 1", "B", "C"),
                            ("B - D: Non-Scheduled, lag 2", "B", "D"), ("C - D: lag 1 vs 2", "C", "D")):
            diff = (g[names[x]] - g[names[y]]).to_numpy()
            lo, hi = bt.bootstrap_ci(diff) if len(diff) > 1 else (np.nan, np.nan)
            rows.append({"period": p, "days": len(diff), "comparison": label, "mean": diff.mean(), "lo95": lo, "hi95": hi})
    diffs = pd.DataFrame(rows)
    diffs.to_csv(HERE / "diffs.csv", index=False)
    print(diffs.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
