"""Q2B backtest: run the dispatcher through every trading day, carrying the state of charge over.

Every strategy plans once at 08:15 and co-optimises FCESS. Scenario "main" has all the real
constraints: battery wear at WEAR $/MWh discharged, an example site sharing the grid connection
(vpp.site) and the capacity obligation. Each other scenario changes one of those, to show what it
costs.

Strategies:
  perfect                 plan on the actual prices: the most a once-a-day plan could earn
  yesterday               plan on yesterday's prices
  forecast                plan on our forecast (vpp.forecast)
  forecast+offer          ... plus the standing spike offer at 1.2x the best forecast price later
  forecast+offer(value)   ... with the offer priced instead by re-planning the rest of the day with and
                          without the sale, so it includes the FCESS the energy would back
Which of forecast / forecast+offer / forecast+offer(value) to run is chosen month by month: each
month uses the one that earned most over the previous LOOKBACK days, so the choice only ever uses the
past and follows the market when it changes (FCESS was rich in 2025 and nearly gone in 2026). A
one-off choice on the validation period picked strategies for a market that no longer exists. (The
risk reserve tested on 2026-10-16 added nothing significant and is left out: reports/findings.md,
section 11.)

Run:  .venv/bin/python -m vpp.backtest
"""

import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from vpp import dispatch as dp
from vpp import site as st
from vpp.data import PROCESSED, ROOT

REPORTS = ROOT / "reports"
PERIODS = {"validation": ("2025-04-01", "2025-09-30"), "test": ("2025-10-01", "2026-10-15")}
H, HALF = pd.Timedelta(hours=8), pd.Timedelta("30min")
WEAR = 30.0  # A$/MWh discharged, central assumption
SITE = st.Site()
STRATEGIES = {
    "perfect": ("actual", dp.Strategy("perfect")),
    "yesterday": ("yesterday", dp.Strategy("yesterday")),
    "forecast": ("forecast", dp.Strategy("forecast")),
    "forecast+offer": ("forecast", dp.Strategy("forecast+offer", offer=True)),
    "forecast+offer(value)": ("forecast", dp.Strategy("forecast+offer(value)", offer=True, offer_rule="value")),
}
CORE = ["perfect", "forecast", "forecast+offer", "forecast+offer(value)"]
CANDIDATES = ["forecast", "forecast+offer", "forecast+offer(value)"]
LOOKBACK = pd.Timedelta(days=90)  # fixed, not tuned
BAT = dp.Battery(degradation_per_mwh=WEAR)
SCENARIOS = {  # name: (battery, site or None, with the capacity obligation, strategies)
    "main": (BAT, SITE, True, list(STRATEGIES)),
    "no site": (BAT, None, True, CORE),
    "import 1.5 MW": (BAT, st.Site(import_limit_mw=1.5), True, CORE),
    "import 2.0 MW": (BAT, st.Site(import_limit_mw=2.0), True, CORE),
    "no obligation": (BAT, SITE, False, CORE),
    "wear $0": (dp.Battery(degradation_per_mwh=0.0), SITE, True, CORE),
    "wear $15": (dp.Battery(degradation_per_mwh=15.0), SITE, True, CORE),
    "wear $50": (dp.Battery(degradation_per_mwh=50.0), SITE, True, CORE),
}


def load_days() -> list[dict]:
    """Inputs of every trading day in the backtest with complete data."""
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    p5all = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=["energy"] + dp.FCESS)
    esroi = pd.read_parquet(PROCESSED / "esroi.parquet").set_index("trading_date")
    fc = pd.read_parquet(PROCESSED / "forecast_dispatch.parquet").set_index("ts")
    risk = pd.read_parquet(PROCESSED / "spike_risk.parquet").set_index("ts")["p_spike"]
    first, last = pd.Timestamp(PERIODS["validation"][0]) + H, pd.Timestamp(PERIODS["test"][1]) + H + pd.Timedelta(days=1)
    ts_all = pd.date_range(first, last, freq="30min", inclusive="left")
    load_fc, load_act = (pd.Series(x, index=ts_all) for x in st.example_load(ts_all))
    planning = lambda x: np.maximum(x, 0) + dp.OFFER_EPS  # noqa: E731  FCESS headroom offered at $0
    days = []
    for day in pd.date_range(PERIODS["validation"][0], PERIODS["test"][1]):
        ts = pd.date_range(day + H, periods=dp.N, freq="30min")
        a, f = df30.reindex(ts), fc.reindex(ts)
        ts5 = pd.date_range(day + H, periods=dp.N * dp.STEPS, freq="5min")
        price5 = p5all["energy"].reindex(ts5).to_numpy()
        fcess5 = {k: p5all[k].reindex(ts5).to_numpy() for k in dp.FCESS}
        cols = ["energy", "yday_energy"] + dp.FCESS + [f"yday_{k}" for k in dp.FCESS]
        if (a[["energy"] + dp.FCESS].isna().any().any() or f[cols].isna().any().any() or np.isnan(price5).any()
                or any(np.isnan(v).any() for v in fcess5.values()) or day not in esroi.index):
            continue
        w = esroi.loc[day]
        act_f = {k: a[k].to_numpy() for k in dp.FCESS}
        days.append({
            "day": day, "rtp": a["energy"].to_numpy(), "act_f": act_f, "p5": price5, "p5_f": fcess5,
            "window": (int((w["start_ts"] - day - H) / HALF), int((w["end_ts"] - day - H) / HALF)),
            "risk": risk.reindex(ts).fillna(0.0).to_numpy(),
            "load_fc": load_fc.reindex(ts).to_numpy(), "load_act": load_act.reindex(ts).to_numpy(),
            "prices": {
                "actual": (a["energy"].to_numpy(), act_f),
                "forecast": (f["energy"].to_numpy(), {k: planning(f[k].to_numpy()) for k in dp.FCESS}),
                "yesterday": (f["yday_energy"].to_numpy(), {k: planning(f[f"yday_{k}"].to_numpy()) for k in dp.FCESS}),
            },
        })
    return days


_DAYS: list[dict] = []


def _init_worker(days: list[dict]) -> None:
    global _DAYS
    _DAYS = days


def _run(task: tuple[str, str, bool]):
    """One strategy in one scenario through every day; perfect foresight also knows the actual load."""
    scenario, name, keep_logs = task
    bat, site, obligation, _ = SCENARIOS[scenario]
    source, strat = STRATEGIES[name]
    rows, logs, soc = [], {}, 0.0
    for d in _DAYS:
        pe, pf = d["prices"][source]
        load_fc = None if site is None else d["load_act" if source == "actual" else "load_fc"]
        summary, log = dp.run_day(bat, strat, pe, pf, d["rtp"], d["act_f"], d["p5"], d["window"] if obligation else None,
                                  d["risk"], soc, d["p5_f"], site, load_fc, None if site is None else d["load_act"],
                                  0.0 if source == "actual" else None)
        soc = summary["soc_end"]
        rows.append({"scenario": scenario, "strategy": name, "day": d["day"], **summary})
        if keep_logs:
            logs[d["day"]] = log
    return scenario, name, rows, logs


def period_of(day: pd.Series) -> np.ndarray:
    return np.where(day >= pd.Timestamp(PERIODS["test"][0]), "test", "validation")


def bootstrap_ci(diff: np.ndarray, n: int = 4000, seed: int = 0) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = [diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(n)]
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def bootstrap(diff: np.ndarray) -> str:
    lo, hi = bootstrap_ci(diff)
    return f"{diff.mean():+.1f} [{lo:+.1f}, {hi:+.1f}]"


def by_day(daily: pd.DataFrame, scenario: str, period: str) -> pd.DataFrame:
    x = daily[(daily["scenario"] == scenario) & (daily["period"] == period)]
    return x.pivot(index="day", columns="strategy", values="total")


def adaptive(daily: pd.DataFrame, scenario: str = "main") -> pd.DataFrame:
    """Each month, the candidate that earned most over the previous LOOKBACK days (past days only).

    Uses each candidate's own run, so the state of charge at a switch is approximate.
    """
    m = daily[daily["scenario"] == scenario].pivot(index="day", columns="strategy", values="total")
    first = (m.index.min() + LOOKBACK).to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
    out = []
    for month in pd.date_range(first, m.index.max(), freq="MS"):
        past = m[(m.index < month) & (m.index >= month - LOOKBACK)]
        pick = past[CANDIDATES].mean().idxmax()
        cur = m[(m.index >= month) & (m.index < month + pd.offsets.MonthBegin(1))]
        out.append(cur.assign(pick=pick, adaptive=cur[pick]))
    return pd.concat(out)


def main() -> None:
    days = load_days()
    tasks = [(sc, name, sc == "main" and name.startswith("forecast+offer")) for sc, spec in SCENARIOS.items() for name in spec[3]]
    tasks.sort(key=lambda t: "value" not in t[1])  # slowest first keeps the pool busy
    daily, logs = [], {}
    workers = min(len(tasks), max(1, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(days,)) as pool:
        for _, name, rows, lg in pool.map(_run, tasks):
            daily.extend(rows)
            if lg:
                logs[name] = lg
    daily = pd.DataFrame(daily)
    daily["period"] = period_of(daily["day"])
    REPORTS.mkdir(exist_ok=True)
    daily.to_csv(REPORTS / "q2b_backtest_daily.csv", index=False)

    summary = daily.groupby(["scenario", "period", "strategy"], sort=False).agg(
        days=("total", "size"), total=("total", "mean"), energy=("energy", "mean"), fcess=("fcess", "mean"),
        wear=("wear", "mean"), triggers_per_day=("triggers", "mean"), discharged_mwh=("discharged_mwh", "mean"),
        max_obligation_shortfall_mwh=("obligation_shortfall_mwh", "max"), max_site_shortfall_mw=("site_shortfall_mw", "max"))
    perfect = summary.xs("perfect", level="strategy")["total"]
    summary["capture"] = summary["total"] / perfect.reindex(summary.index.droplevel("strategy")).to_numpy()
    summary.to_csv(REPORTS / "q2b_backtest_summary.csv")

    # which strategy to run: chosen month by month from the previous LOOKBACK days (main scenario)
    ad = adaptive(daily)
    ad["period"] = period_of(pd.Series(ad.index))
    ad.reset_index().to_csv(REPORTS / "q2b_adaptive_daily.csv", index=False)
    print(f"{len(days)} days. Strategy used each month = best of the previous {LOOKBACK.days} days (main scenario):")
    print(ad.groupby(ad.index.to_period("M"))["pick"].first().to_string())
    for period, g in ad.groupby("period"):
        print(f"  {period} ({len(g)} days): adaptive {g['adaptive'].mean():.1f} | "
              + " | ".join(f"{c} {g[c].mean():.1f}" for c in CANDIDATES + ["perfect"])
              + f"\n    adaptive - forecast {bootstrap((g['adaptive'] - g['forecast']).to_numpy())} | adaptive - "
              f"forecast+offer(value) {bootstrap((g['adaptive'] - g['forecast+offer(value)']).to_numpy())}")
    current = ad["pick"].iloc[-1]

    print("\n=== main scenario: wear $%.0f/MWh, example site, capacity obligation ($/day)" % WEAR)
    m = summary.loc["main"].reset_index()
    print(m.pivot(index="strategy", columns="period", values=["total", "energy", "fcess", "wear", "capture"])
          .loc[list(STRATEGIES)].round(2).to_string())
    for period in PERIODS:
        w = by_day(daily, "main", period)
        print(f"  {period}: offer(forecast) - forecast {bootstrap((w['forecast+offer'] - w['forecast']).to_numpy())} | "
              f"offer(value) - forecast {bootstrap((w['forecast+offer(value)'] - w['forecast']).to_numpy())} | "
              f"offer(value) - offer(forecast) {bootstrap((w['forecast+offer(value)'] - w['forecast+offer']).to_numpy())} | "
              f"forecast - yesterday {bootstrap((w['forecast'] - w['yesterday']).to_numpy())}")

    print("\n=== scenarios ($/day; each changes one thing from main)")
    sc = summary.reset_index()
    sc = sc[sc["strategy"].isin(["perfect", "forecast", "forecast+offer(value)"])]
    print(sc.pivot_table(index="scenario", columns=["period", "strategy"], values="total").loc[list(SCENARIOS)].round(1).to_string())
    print("max obligation shortfall (MWh):", summary.groupby("scenario")["max_obligation_shortfall_mwh"].max().round(4).to_dict())
    print("max site import shortfall (MW):", summary.groupby("scenario")["max_site_shortfall_mw"].max().round(4).to_dict())

    q = daily[daily["scenario"] == "main"].assign(q=lambda d: d["day"].dt.to_period("Q"))
    qt = q.pivot_table(index="q", columns="strategy", values="total")[list(STRATEGIES)]
    qt["adaptive"] = ad["adaptive"].groupby(ad.index.to_period("Q")).mean()
    qt["adaptive_capture"] = qt["adaptive"] / qt["perfect"]
    print("\n=== by quarter, main scenario ($/day)")
    print(qt.round(2).to_string())

    lg = pd.concat([x.assign(day=d, strategy=name) for name in logs for d, x in logs[name].items()], ignore_index=True)
    lg["period"] = period_of(lg["day"])
    cand = lg[(lg["strategy"] == "forecast+offer(value)") & np.isfinite(lg["offer_price"])]
    alpha = STRATEGIES["forecast+offer(value)"][1].alpha
    print("\n=== value-priced offer: offer prices where the price passed $150 with energy to sell")
    for period, g in cand.groupby("period"):
        print(f"  {period}: {len(g)} intervals, median offer ${g['offer_price'].median():.0f}/MWh, FCESS part "
              f"median ${alpha * g['offer_fcess'].median():.1f}, mean ${alpha * g['offer_fcess'].mean():.1f}, "
              f"90th pct ${alpha * g['offer_fcess'].quantile(0.9):.1f}/MWh")
    fl = lg[lg["strategy"] == "forecast+offer(value)"]
    ts = fl["day"] + H + fl["slot"] * HALF
    cap = np.clip(np.minimum(1.0, SITE.import_limit_mw - fl["site_load_plan"]), 0.0, None)
    business = (ts.dt.dayofweek < 5) & (ts.dt.hour >= 8) & (ts.dt.hour < 17)
    print(f"\n=== site: mean load {fl['site_load'].mean():.2f} MW (max {fl['site_load'].max():.2f}); most the battery may "
          f"charge: weekday 08-17 {cap[business].mean():.2f} MW, other hours {cap[~business].mean():.2f} MW")
    lg.to_parquet(PROCESSED / "q2b_dispatch_log.parquet")

    test = ad[ad["period"] == "test"]
    example = (test["adaptive"] - test["forecast"]).idxmax()
    pick = test.loc[example, "pick"]
    d = next(x for x in days if x["day"] == example)
    summ = daily[(daily["scenario"] == "main") & (daily["strategy"] == pick) & (daily["day"] == example)].iloc[0].to_dict()
    print(f"\n=== currently chosen: {current}. Example explanation: {example.date()} ({pick}, main scenario)")
    print("\n".join(dp.explain(logs[pick][example], summ, SCENARIOS["main"][0], STRATEGIES[pick][1], d["window"], SITE)))


if __name__ == "__main__":
    main()
