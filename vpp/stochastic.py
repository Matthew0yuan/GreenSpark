"""Step B of reports/q2b_plan.md, section 9: the 09:00 plan and its re-plans as a two-stage stochastic plan over
the price scenarios (vpp.scenarios), against the dispatcher as it is. Settings and the pass test were written
into the plan before the full run.

Every strategy is the value offer rule on the 10 MW main route m1 (CMD 9 MW, dynamic IRCR flags) with the
08:00 bridge plan; they differ in the planner (vpp.dispatch.Strategy):
  value  the dispatcher as it is: re-planned only when information arrives (09:00 forecast, offer sales)
  roll   the same, also re-planned every hour from the actual state of charge, nudged towards the latest prices
  mean   roll, planning on the scenarios' mean instead of the point forecast
  sp     roll, with the two-stage stochastic plan: one schedule until the next re-plan, one per scenario after
  dro    sp at the worst scenario weights the ambiguity allows (vpp.config.RiskPrefs.ambiguity)
roll isolates re-planning every hour; mean, the scenarios' price level; sp, planning on their spread; dro,
guarding against their probabilities being off. The offer rule's own what-if plans stay on the point forecast.

Pass (validation, before vpp.evaluate.CUTOFF): sp or dro earns more than value per day, the 95% interval of the
daily difference above 0 (a 7-day moving-block bootstrap; a day's money is its ledger plus its month's bill
saving per day). The test is printed once, for every strategy.

Run:  python -m vpp.stochastic [--smoke]
"""

import sys

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp import dispatch as dp
from vpp import scenarios as sn
from vpp import site_bill as sb
from vpp.data import PROCESSED

REPORTS = bt.REPORTS
PLANNERS = ("roll", "mean", "sp", "dro")
NAMES = {"value": bt.VALUE, **{p: f"{bt.VALUE}|plan={p}" for p in PLANNERS}}
CMD = 9.0       # MW, the CMD chosen on validation (q2b_main)
BLOCK = 7       # days per bootstrap block


def attach(days: list[dict]) -> int:
    """Give each backtest day its scenarios (sn.OUT) as run_day's scen; returns the days without any. Slots
    before the issue hold the 08:00 bridge prices (plans from the issue on never use them); FCESS as planning
    prices, like the forecast's."""
    x = pd.read_parquet(sn.OUT).sort_values(["day", "member", "slot"])
    by_day = {d: g for d, g in x.groupby("day")}
    missing = 0
    for d in days:
        g = by_day.get(d["day"])
        if g is None:
            d["scen"], missing = None, missing + 1
            continue
        n_s = g["member"].nunique()
        head_e, head_f = d["prices"]["bridge"][0][:sn.FIRST], d["prices"]["bridge"][1]
        d["scen"] = {"slot": sn.FIRST, "spike": sn.SPIKE,
                     "energy": np.c_[np.tile(head_e, (n_s, 1)), g["energy"].to_numpy().reshape(n_s, -1)],
                     "fcess": {k: np.c_[np.tile(head_f[k][:sn.FIRST], (n_s, 1)),
                                        np.maximum(g[k].to_numpy().reshape(n_s, -1), 0.0) + dp.OFFER_EPS] for k in dp.FCESS}}
    return missing


def block_ci(x, block: int = BLOCK, n: int = 4000, seed: int = 0) -> tuple[float, float]:
    """95% interval of the mean of a daily series, by a moving-block bootstrap."""
    x = np.asarray(x, float)
    m = len(x)
    starts = np.random.default_rng(seed).integers(0, m - block + 1, size=(n, -(-m // block)))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n, -1)[:, :m]
    lo, hi = np.percentile(x[idx].mean(axis=1), [2.5, 97.5])
    return float(lo), float(hi)


def daily_money(ledger: pd.DataFrame, bills: pd.DataFrame) -> pd.DataFrame:
    """(day x strategy) money: the daily ledger plus the day's month's bill saving per day."""
    led = ledger.pivot_table(index="day", columns="strategy", values="ledger_total")
    b = bills.assign(per_day=bills["saving"] / bills["days"]).pivot_table(index="month", columns="strategy", values="per_day")
    month = led.index.to_period("M")
    return led + b.reindex(month).set_axis(led.index).reindex(columns=led.columns)


def compare(money: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    """Per period and strategy: $/day, against value and against roll (block-bootstrap 95% intervals), the worst
    5% of days, and the 09:00 plan's expected money less what followed (its optimism)."""
    rows = []
    period = pd.Series(bt.period_of(money.index), index=money.index)
    for p in bt.PERIODS:
        mp = money[period == p]
        for label, name in NAMES.items():
            if name not in mp:
                continue
            x, diff, d_roll = mp[name], mp[name] - mp[bt.VALUE], mp[name] - mp[NAMES["roll"]]
            if not len(x):
                continue
            lo, hi = block_ci(diff.to_numpy()) if label != "value" else (0.0, 0.0)
            r_lo, r_hi = block_ci(d_roll.to_numpy()) if label in ("mean", "sp", "dro") else (np.nan, np.nan)
            led = ledger[(ledger["strategy"] == name) & (ledger["period"] == p)]
            worst = x.nsmallest(max(1, int(np.ceil(0.05 * len(x)))))
            rows.append({"period": p, "strategy": label, "days": len(x), "per_day": x.mean(), "vs_value": diff.mean(),
                         "vs_value_lo": lo, "vs_value_hi": hi, "vs_roll": d_roll.mean() if label in ("mean", "sp", "dro") else np.nan,
                         "vs_roll_lo": r_lo, "vs_roll_hi": r_hi, "worst_5pct_days": worst.mean(),
                         "plan_expected": led["plan_money"].mean(), "plan_got": led["plan_got"].mean(),
                         "optimism": (led["plan_money"] - led["plan_got"]).mean(),
                         "fallback_days": int(led["planner_fallback"].sum()), "plans_short": int(led["plans_short"].sum())})
    return pd.DataFrame(rows).set_index(["period", "strategy"])


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    days = bt.load_days("0900")
    if "--smoke" in argv:
        days = [d for d in days if d["day"].month in (1, 7)][:12]
    missing = attach(days)
    aux = bt.q2b_aux()
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    m1 = bt._sc("m1", ct.Route("m1", cmd_mw=CMD, ircr="dynamic"), tuple(NAMES.values()))
    daily, logs, meters = bt.run_tasks(bt.q2b_tasks([m1], keep=("m1",)), days, aux)
    ledger = bt.q2b_ledger(daily, [m1])
    bills, ircr = bt.q2b_bills(meters, days, [m1], cmd_nb)
    money = daily_money(ledger, bills)
    table = compare(money, ledger)
    tag = "_smoke" if "--smoke" in argv else ""
    ledger.to_csv(REPORTS / f"stochastic_daily{tag}.csv", index=False)
    pd.concat([lg.assign(day=d, strategy=st) for (_, st), by_day in logs.items() for d, lg in by_day.items()],
              ignore_index=True).to_parquet(PROCESSED / f"stochastic_log{tag}.parquet")
    table.to_csv(REPORTS / f"stochastic_result{tag}.csv")
    print(f"{len(days)} days, {missing} without scenarios (planned on the forecast)")
    print(f"=== $/day, m1, value offer rule; the 95% interval against value is a {BLOCK}-day block bootstrap\n"
          f"{table.round(1).to_string()}\n")
    print(f"=== IRCR, {bt.IRCR_SEASON}-{bt.IRCR_SEASON - 1999} Hot Season (validation only; $/year)\n"
          f"{ircr[['strategy', 'ircr_mw', 'saving_per_year', 'peak_days_covered']].round(2).to_string(index=False)}\n")
    val = table.loc["validation"]
    passing = [p for p in ("sp", "dro") if p in val.index and val.loc[p, "vs_value_lo"] > 0]
    print(f"pass (validation): {', '.join(passing) or 'none'}")


if __name__ == "__main__":
    main()
