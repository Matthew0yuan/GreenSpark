"""Q2B backtest: run the dispatcher through every trading day, carrying the state of charge over.

Strategies, same battery and constraints (FCESS always co-optimised; with and without the capacity
obligation):
  perfect                    plan on the actual prices: the most a once-a-day plan could earn
  yesterday                  plan on yesterday's prices
  yesterday+offer            ... plus the standing spike offer
  forecast                   plan on our forecast (vpp.forecast)
  forecast+offer             ... plus the standing spike offer
  forecast+offer+reserve     ... plus keep R MWh stored in intervals with spike risk >= threshold
The reserve setting is picked on the validation period with the obligation on, and kept only if it
beats forecast+offer there with a 95% bootstrap interval above zero (otherwise the simpler strategy
stays); the test period only reports.

Run:  .venv/bin/python -m vpp.backtest
"""

import numpy as np
import pandas as pd

from vpp import dispatch as dp
from vpp.data import PROCESSED, ROOT

REPORTS = ROOT / "reports"
PERIODS = {"validation": ("2025-04-01", "2025-09-30"), "test": ("2025-10-01", "2026-10-15")}
H, HALF = pd.Timedelta(hours=8), pd.Timedelta("30min")
RESERVE_GRID = [(threshold, mwh) for threshold in (0.05, 0.10, 0.20) for mwh in (0.5, 1.0)]


def strategies() -> dict[str, tuple[str, dp.Strategy]]:
    out = {
        "perfect": ("actual", dp.Strategy("perfect")),
        "yesterday": ("yesterday", dp.Strategy("yesterday")),
        "yesterday+offer": ("yesterday", dp.Strategy("yesterday+offer", offer=True)),
        "forecast": ("forecast", dp.Strategy("forecast")),
        "forecast+offer": ("forecast", dp.Strategy("forecast+offer", offer=True)),
    }
    for threshold, mwh in RESERVE_GRID:
        name = f"forecast+offer+reserve {mwh:.1f}MWh@{threshold:.0%}"
        out[name] = ("forecast", dp.Strategy(name, offer=True, risk_threshold=threshold, reserve_mwh=mwh))
    return out


def load_days() -> list[dict]:
    """Inputs of every trading day in the backtest with complete data."""
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    p5all = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=["energy"] + dp.RAISE)
    p5 = p5all["energy"]
    esroi = pd.read_parquet(PROCESSED / "esroi.parquet").set_index("trading_date")
    fc = pd.read_parquet(PROCESSED / "forecast_dispatch.parquet").set_index("ts")
    risk = pd.read_parquet(PROCESSED / "spike_risk.parquet").set_index("ts")["p_spike"]
    planning = lambda x: np.maximum(x, 0) + dp.OFFER_EPS  # noqa: E731  FCESS headroom offered at $0
    days = []
    for day in pd.date_range(PERIODS["validation"][0], PERIODS["test"][1]):
        ts = pd.date_range(day + H, periods=dp.N, freq="30min")
        a, f = df30.reindex(ts), fc.reindex(ts)
        ts5 = pd.date_range(day + H, periods=dp.N * dp.STEPS, freq="5min")
        price5 = p5.reindex(ts5).to_numpy()
        raise5 = {k: p5all[k].reindex(ts5).to_numpy() for k in dp.RAISE}
        cols = ["energy", "yday_energy"] + dp.FCESS + [f"yday_{k}" for k in dp.FCESS]
        if (a[["energy"] + dp.FCESS].isna().any().any() or f[cols].isna().any().any() or np.isnan(price5).any()
                or any(np.isnan(v).any() for v in raise5.values()) or day not in esroi.index):
            continue
        w = esroi.loc[day]
        act_f = {k: a[k].to_numpy() for k in dp.FCESS}
        days.append({
            "day": day, "rtp": a["energy"].to_numpy(), "act_f": act_f, "p5": price5, "p5_f": raise5,
            "window": (int((w["start_ts"] - day - H) / HALF), int((w["end_ts"] - day - H) / HALF)),
            "risk": risk.reindex(ts).fillna(0.0).to_numpy(),
            "prices": {
                "actual": (a["energy"].to_numpy(), act_f),
                "forecast": (f["energy"].to_numpy(), {k: planning(f[k].to_numpy()) for k in dp.FCESS}),
                "yesterday": (f["yday_energy"].to_numpy(), {k: planning(f[f"yday_{k}"].to_numpy()) for k in dp.FCESS}),
            },
        })
    return days


def run(days: list[dict], bat: dp.Battery, obligation: bool) -> tuple[pd.DataFrame, dict]:
    rows, logs = [], {}
    for name, (source, strat) in strategies().items():
        soc = 0.0
        for d in days:
            pe, pf = d["prices"][source]
            summary, log = dp.run_day(bat, strat, pe, pf, d["rtp"], d["act_f"], d["p5"],
                                      d["window"] if obligation else None, d["risk"], soc, d["p5_f"])
            soc = summary["soc_end"]
            rows.append({"day": d["day"], "strategy": name, "obligation": obligation, **summary})
            if name.startswith("forecast+offer"):
                logs[name, d["day"]] = log
    return pd.DataFrame(rows), logs


def period_of(day: pd.Series) -> pd.Series:
    return np.where(day >= pd.Timestamp(PERIODS["test"][0]), "test", "validation")


def bootstrap_ci(diff: np.ndarray, n: int = 4000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(n)]
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def bootstrap(diff: np.ndarray) -> str:
    lo, hi = bootstrap_ci(diff)
    return f"{diff.mean():+.1f} [{lo:+.1f}, {hi:+.1f}]"


def main() -> None:
    bat = dp.Battery()
    days = load_days()
    daily, logs = [], {}
    for obligation in (True, False):
        r, lg = run(days, bat, obligation)
        daily.append(r)
        if obligation:
            logs = lg
    daily = pd.concat(daily, ignore_index=True)
    daily["period"] = period_of(daily["day"])
    REPORTS.mkdir(exist_ok=True)
    daily.to_csv(REPORTS / "q2b_backtest_daily.csv", index=False)

    summary = daily.groupby(["obligation", "period", "strategy"], sort=False).agg(
        days=("total", "size"), total=("total", "mean"), energy=("energy", "mean"), fcess=("fcess", "mean"),
        triggers_per_day=("triggers", "mean"), discharged_mwh=("discharged_mwh", "mean"),
        max_obligation_shortfall_mwh=("obligation_shortfall_mwh", "max"))
    perfect = summary.xs("perfect", level="strategy")["total"]
    summary["capture"] = summary["total"] / perfect.reindex(summary.index.droplevel("strategy")).to_numpy()
    summary.to_csv(REPORTS / "q2b_backtest_summary.csv")

    # pick the reserve setting on validation (obligation on); keep it only if clearly better than none
    val = summary.loc[(True, "validation")]["total"]
    candidates = [s for s in val.index if s.startswith("forecast+offer")]
    best = max(candidates, key=lambda s: val[s])
    w = daily[daily.obligation & (daily.period == "validation")].pivot(index="day", columns="strategy", values="total")
    lo, _ = bootstrap_ci((w[best] - w["forecast+offer"]).to_numpy())
    final = best if best != "forecast+offer" and lo > 0 else "forecast+offer"
    print(f"{len(days)} days. Best on validation (obligation on): {best}, gain over forecast+offer "
          f"{bootstrap((w[best] - w['forecast+offer']).to_numpy())} -> final strategy: {final}")
    print("validation $/day:", {s: round(val[s], 1) for s in candidates})

    for obligation in (True, False):
        print(f"\n=== capacity obligation {'ON' if obligation else 'OFF'}")
        show = ["perfect", "yesterday", "yesterday+offer", "forecast", "forecast+offer", best]
        t = summary.loc[obligation].reset_index().pivot(index="strategy", columns="period", values=["total", "capture"])
        print(t.loc[list(dict.fromkeys(show))].round(3).to_string())
        for period in PERIODS:
            w = daily[(daily.obligation == obligation) & (daily.period == period)].pivot(index="day", columns="strategy", values="total")
            print(f"  {period}: offer gain on forecast {bootstrap((w['forecast+offer'] - w['forecast']).to_numpy())} | "
                  f"offer gain on yesterday {bootstrap((w['yesterday+offer'] - w['yesterday']).to_numpy())} | "
                  f"best reserve gain {bootstrap((w[best] - w['forecast+offer']).to_numpy())} | "
                  f"final - yesterday {bootstrap((w[final] - w['yesterday']).to_numpy())} | "
                  f"final - forecast {bootstrap((w[final] - w['forecast']).to_numpy())}")

    q = daily[daily.obligation].assign(q=lambda d: d["day"].dt.to_period("Q"))
    qt = q.pivot_table(index="q", columns="strategy", values="total")[list(dict.fromkeys(["perfect", "yesterday", "forecast", "forecast+offer", final]))]
    qt["final_capture"] = qt[final] / qt["perfect"]
    print("\n=== by quarter, obligation ON ($/day)")
    print(qt.round(2).to_string())

    final_logs = pd.concat([lg.assign(day=day) for (name, day), lg in logs.items() if name == final], ignore_index=True)
    final_logs.to_parquet(PROCESSED / "q2b_dispatch_log.parquet")
    test = daily[(daily.obligation) & (daily.period == "test")].pivot(index="day", columns="strategy", values="total")
    example = (test[final] - test["forecast"]).idxmax()
    d = next(x for x in days if x["day"] == example)
    strat = strategies()[final][1]
    summ = daily[(daily.obligation) & (daily.strategy == final) & (daily.day == example)].iloc[0].to_dict()
    print(f"\n=== example explanation: {example.date()} ({final}, obligation on)")
    print("\n".join(dp.explain(logs[final, example], summ, bat, strat, d["window"])))


if __name__ == "__main__":
    main()
