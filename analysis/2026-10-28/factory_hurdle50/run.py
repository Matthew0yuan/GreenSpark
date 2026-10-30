"""PLAN.md: e5 and v1 on the factory route m1 with the plans' hurdle at 75 (the default) and 50 (set by the user on
2026-10-28), every trading day; money as vpp.v2.backtest (ledger + the month's bill saving per day). Reported by
period; the pass rule is PLAN.md's, on the in-sample days only.

  .venv/bin/python analysis/2026-10-28/factory_hurdle50/run.py
"""

import sys
import time
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import contracts as ct  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp import site_bill as sb  # noqa: E402
from vpp import stochastic as sto  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2.config import V1  # noqa: E402

HURDLES = (75.0, 50.0)  # A$/MWh: the plans' hurdle, default and the user's
UNSEEN = pd.Timestamp("2026-10-16")
PERIODS = ("in-sample 2025-04-01..2026-06-30", "test seen 2026-07-01..2026-10-15", "test unseen 2026-10-16..2026-10-22")
E5 = replace(vb.E5, record=False)
TOL = 1e-6


def period(day) -> np.ndarray:
    d = pd.to_datetime(pd.Series(day))
    return np.where(d < bt.CUTOFF, PERIODS[0], np.where(d < UNSEEN, PERIODS[1], PERIODS[2]))


def label(cfg, h: float) -> str:
    return f"{'e5' if cfg.name == E5.name else 'v1'} h{h:g}"


def main() -> None:
    t0 = time.time()
    scs = {h: bt._sc(f"m1 h{h:g}", ct.Route("m1", cmd_mw=vb.CMD, ircr="dynamic"), (bt.VALUE,),
                     prefs=replace(bt.PREFS, hurdle_per_mwh=h)) for h in HURDLES}
    assert all(s is not None for s in scs.values()), bt.REFUSED
    days = bt.load_days("0900")
    aux = bt.q2b_aux()
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    tasks = [(scs[h], cfg, True) for h in HURDLES for cfg in (E5, V1)]
    daily, logs, meters, _ = vb.run(tasks, days, aux)
    print(f"m1, {len(days)} days x {len(tasks)} runs in {time.time() - t0:.0f} s")

    money, market, ircr_rows, ledgers = [], [], [], []
    for h, sc in scs.items():
        d = daily[daily["scenario"] == sc.name]
        led = bt.q2b_ledger(d, [sc])
        bills, ircr = bt.q2b_bills({k: v for k, v in meters.items() if k[0] == sc.name}, days, [sc], cmd_nb)
        names = {E5.name: f"e5 h{h:g}", V1.name: f"v1 h{h:g}"}
        money.append(sto.daily_money(led, bills).rename(columns=names))
        market.append(led.pivot_table(index="day", columns="strategy", values="ledger_total").rename(columns=names))
        ircr_rows.append(ircr.assign(config=ircr["strategy"].map(names)))
        ledgers.append(led.assign(config=led["strategy"].map(names)))
    money, market = pd.concat(money, axis=1), pd.concat(market, axis=1)
    ledger = pd.concat(ledgers, ignore_index=True)
    ledger.to_csv(HERE / "daily.csv", index=False)
    money.to_csv(HERE / "money.csv")
    ircr = pd.concat(ircr_rows, ignore_index=True)
    ircr.to_csv(HERE / "ircr.csv", index=False)

    # checks: the e5 at 75 reproduces reports/v2_test_daily.csv; energy balance; power and stored-energy limits
    saved = pd.read_csv(ROOT / "reports" / "v2_test_daily.csv", parse_dates=["day"])
    saved = saved[saved["strategy"] == E5.name].set_index("day")["ledger_total"]
    mine = ledger[ledger["config"] == "e5 h75"].set_index("day")["ledger_total"]
    common = mine.index.intersection(saved.index)
    print(f"e5 h75 ledger against reports/v2_test_daily.csv: {len(common)} days, largest |difference| "
          f"{(mine[common] - saved[common]).abs().max():.2e} A$")
    # behind the factory meter, import and export include the load, so the check uses the battery's own half-hours:
    # energy_mwh is the net of its 5-min steps, regulation included (vpp.dispatch: energy[t] += up - down). Charging
    # and discharging inside one half-hour lose energy the net cannot show, so stored energy may only fall short of
    # what the net gives, never exceed it
    eta = bt.Q2B_ASSET.battery.eta
    log = pd.concat([x.assign(day=d, scenario=s, strategy=n) for (s, n), lg in logs.items() for d, x in lg.items()],
                    ignore_index=True).sort_values(["scenario", "strategy", "day", "slot"], ignore_index=True)
    log.to_parquet(HERE / "log.parquet")
    prev = np.where(log["slot"] == 0, log["soc_start"], log["soc"].shift(1))
    e = log["energy_mwh"].to_numpy()
    gap = log["soc"].to_numpy() - prev - np.where(e < 0, -e * eta, -e / eta)
    worst = float(gap.max())
    print(f"stored energy against the battery's half-hours: most created {worst:.2e} MWh (must be <= 0), most lost "
          f"inside a half-hour {-gap.min():.3f} MWh")
    P, E = bt.Q2B_ASSET.battery.power_mw, bt.Q2B_ASSET.battery.energy_mwh
    over_p = int((np.abs(log["energy_mwh"]) / dp.DT > P + TOL).sum())
    soc_bad = int(((log["soc"] < -TOL) | (log["soc"] > E + TOL)).sum())
    print(f"energy balance: {'PASS' if worst < TOL else 'FAIL'}; half-hours over "
          f"{P:g} MW: {over_p} of {len(log)}; stored energy outside 0-{E:g} MWh: {soc_bad}")

    pairs = [("e5 h50", "e5 h75"), ("v1 h50", "v1 h75"), ("e5 h50", "v1 h50"), ("e5 h75", "v1 h75")]
    per = pd.Series(period(money.index), index=money.index)
    rows = []
    for p in PERIODS:
        mp, kp = money[per == p], market[per == p]
        for a, b in pairs:
            diff = mp[a] - mp[b]
            lo, hi = vb.month_ci(diff) if diff.index.to_period("M").nunique() > 1 else (np.nan, np.nan)
            rows.append({"period": p, "config": a, "base": b, "days": len(mp), "per_day": mp[a].mean(),
                         "base_per_day": mp[b].mean(), "gain": diff.mean(), "lo95_month": lo, "hi95_month": hi,
                         "market_gain": (kp[a] - kp[b]).mean(), "bill_gain": diff.mean() - (kp[a] - kp[b]).mean(),
                         "days_better": int((diff > 0).sum())})
    table = pd.DataFrame(rows)
    table.to_csv(HERE / "result.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print(table.round(1).to_string(index=False))
        print(ircr[["config", "ircr_mw", "ircr_no_battery_mw", "saving_per_year", "peak_days_covered"]].round(2).to_string(index=False))
    ins = table[(table["period"] == PERIODS[0]) & (table["config"] == "e5 h50") & (table["base"] == "e5 h75")].iloc[0]
    verdict = "50 better" if ins["lo95_month"] > 0 else "50 worse" if ins["hi95_month"] < 0 else "cannot tell"
    print(f"PLAN.md rule (in-sample, e5 h50 - e5 h75, month-block 95%): {ins['gain']:.1f} "
          f"[{ins['lo95_month']:.1f}, {ins['hi95_month']:.1f}] -> {verdict}")


if __name__ == "__main__":
    main()
