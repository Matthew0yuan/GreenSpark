"""Q2B backtest: run the dispatcher through every trading day, carrying the state of charge over.

LEGACY is the 1 MW / 2 MWh study of 2026-10-16, kept unchanged as a regression set
(python -m vpp.backtest --legacy). Every strategy plans once at 08:15 and co-optimises FCESS.
Scenario "main" has all the real constraints: battery wear at WEAR $/MWh discharged, an example site
sharing the grid connection (vpp.site) and the capacity obligation. Each other scenario changes one
of those, to show what it costs.

Strategies:
  perfect                 plan on the actual prices: the most a once-a-day plan could earn
  yesterday               plan on yesterday's prices
  forecast                plan on our forecast (vpp.forecast)
  forecast+offer          ... plus the standing spike offer at 1.2x the best forecast price later
  forecast+offer(value)   ... with the offer priced instead by re-planning the rest of the day with and
                          without the sale, so it includes the FCESS the energy would back
The forecast-based strategies add HURDLE $/MWh to the wear in every decision (vpp.dispatch). With
forecast errors, trades that only just pay on the forecast lose on the actual prices, so they mostly
keep their charge for price spikes and the capacity obligation. HURDLE was chosen on the validation
months of the old split from 0-150 $/MWh (2026-10-16): the monthly choice below earns most at 75, flat
from 50 to 150. The 10 MW study re-checks it on the days before the cutoff (q2b_main).
Which of forecast / forecast+offer / forecast+offer(value) to run is chosen month by month: each
month uses the one that earned most over the previous LOOKBACK days, so the choice only ever uses the
past and follows the market when it changes (FCESS was rich in 2025 and nearly gone in 2026). A
one-off choice on the validation period picked strategies for a market that no longer exists. (The
risk reserve tested on 2026-10-16 added nothing significant and is left out: reports/findings.md,
section 11.)

Run:  .venv/bin/python -m vpp.backtest --legacy
"""

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from vpp import config as cf
from vpp import contracts as ct
from vpp import dispatch as dp
from vpp import site as st
from vpp import site_bill as sb
from vpp.data import PROCESSED, ROOT
from vpp.evaluate import CUTOFF, period_of

REPORTS = ROOT / "reports"
FIRST_DAY, LAST_DAY = "2025-04-01", "2026-10-15"  # validation before vpp.evaluate.CUTOFF, test from it
PERIODS = ("validation", "test")
H, HALF = pd.Timedelta(hours=8), pd.Timedelta("30min")
WEAR = 30.0  # A$/MWh discharged, central assumption
HURDLE = 75.0  # A$/MWh margin for forecast error in forecast-based decisions, never settled
SITE = st.Site()
# name -> (price source, how it decides, the risk preferences it fixes). Perfect foresight and yesterday's
# prices carry no margin for forecast error; perfect foresight also knows the site's load.
STRATEGIES = {
    "perfect": ("actual", dp.Strategy("perfect"), {"hurdle_per_mwh": 0.0, "load_margin": 0.0}),
    "yesterday": ("yesterday", dp.Strategy("yesterday"), {"hurdle_per_mwh": 0.0}),
    "forecast": ("forecast", dp.Strategy("forecast"), {}),
    "forecast+offer": ("forecast", dp.Strategy("forecast+offer", offer=True), {}),
    "forecast+offer(value)": ("forecast", dp.Strategy("forecast+offer(value)", offer=True, offer_rule="value"), {}),
}
CORE = ["perfect", "forecast", "forecast+offer", "forecast+offer(value)"]
CANDIDATES = ["forecast", "forecast+offer", "forecast+offer(value)"]
LOOKBACK = pd.Timedelta(days=90)  # fixed, not tuned
# the spike reserve's window, 16:00-20:00: before the cutoff 44% of the $ above $150 fell in it, the most of any
# 4 hours (reports/q2b_plan.md, section 9, R)
RESERVE_WINDOW = (16, 24)
ASSET = cf.Asset(wear_per_mwh=WEAR)
PREFS = cf.RiskPrefs(hurdle_per_mwh=HURDLE)  # the client's risk preferences in every scenario unless it says otherwise


@dataclass(frozen=True)
class Scenario:
    """One backtest setting: the battery as run (asset), the site behind its connection (None: no site)
    and, for the legacy study, whether it holds the capacity obligation at its credited MW (battery
    only). route: the 10 MW study's connection and commitments (vpp.contracts.Route); None for the
    legacy study. prefs: the client's risk preferences."""
    name: str
    asset: cf.Asset
    site: st.Site | None
    obligation: bool
    strategies: tuple[str, ...]
    route: ct.Route | None = None
    prefs: cf.RiskPrefs = PREFS


LEGACY = {s.name: s for s in [
    Scenario("main", ASSET, SITE, True, tuple(STRATEGIES)),
    Scenario("no site", ASSET, None, True, tuple(CORE)),
    Scenario("import 1.5 MW", ASSET, st.Site(import_limit_mw=1.5), True, tuple(CORE)),
    Scenario("import 2.0 MW", ASSET, st.Site(import_limit_mw=2.0), True, tuple(CORE)),
    Scenario("no obligation", ASSET, SITE, False, tuple(CORE)),
    Scenario("wear $0", cf.Asset(wear_per_mwh=0.0), SITE, True, tuple(CORE)),
    Scenario("wear $15", cf.Asset(wear_per_mwh=15.0), SITE, True, tuple(CORE)),
    Scenario("wear $50", cf.Asset(wear_per_mwh=50.0), SITE, True, tuple(CORE)),
]}


def load_days(issue: str = "0815") -> list[dict]:
    """Inputs of every trading day in the backtest with complete data. issue "0900": the strict 09:00
    forecast (vpp.forecast.OUT_0900), whose energy part starts at slot 2, plus what the 10 MW study
    needs: the 08:00 bridge prices, the 7 h capacity window and each slot's calendar month."""
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    p5all = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=["energy"] + dp.FCESS)
    esroi = pd.read_parquet(PROCESSED / "esroi.parquet").set_index("trading_date")
    strict = issue == "0900"
    fc = pd.read_parquet(PROCESSED / ("forecast_dispatch_0900.parquet" if strict else "forecast_dispatch.parquet")).set_index("ts")
    risk = pd.read_parquet(PROCESSED / ("spike_risk_0900.parquet" if strict else "spike_risk.parquet")).set_index("ts")["p_spike"]
    first, last = pd.Timestamp(FIRST_DAY) + H, pd.Timestamp(LAST_DAY) + H + pd.Timedelta(days=1)
    ts_all = pd.date_range(first, last, freq="30min", inclusive="left")
    load_fc, load_act = (pd.Series(x, index=ts_all) for x in st.example_load(ts_all))
    planning = lambda x: np.maximum(x, 0) + dp.OFFER_EPS  # noqa: E731  FCESS headroom offered at $0
    days = []
    for day in pd.date_range(FIRST_DAY, LAST_DAY):
        ts = pd.date_range(day + H, periods=dp.N, freq="30min")
        a, f = df30.reindex(ts), fc.reindex(ts)
        ts5 = pd.date_range(day + H, periods=dp.N * dp.STEPS, freq="5min")
        price5 = p5all["energy"].reindex(ts5).to_numpy()
        fcess5 = {k: p5all[k].reindex(ts5).to_numpy() for k in dp.FCESS}
        cols = ["yday_energy"] + dp.FCESS + [f"yday_{k}" for k in dp.FCESS] + ([] if strict else ["energy"])
        if (a[["energy"] + dp.FCESS].isna().any().any() or f[cols].isna().any().any() or np.isnan(price5).any()
                or any(np.isnan(v).any() for v in fcess5.values()) or day not in esroi.index
                or (strict and f["energy"].iloc[2:].isna().any())):
            continue
        w = esroi.loc[day]
        act_f = {k: a[k].to_numpy() for k in dp.FCESS}
        d = {
            "day": day, "rtp": a["energy"].to_numpy(), "act_f": act_f, "p5": price5, "p5_f": fcess5,
            "window": (int((w["start_ts"] - day - H) / HALF), int((w["end_ts"] - day - H) / HALF)),
            "risk": risk.reindex(ts).fillna(0.0).to_numpy(),
            "load_fc": load_fc.reindex(ts).to_numpy(), "load_act": load_act.reindex(ts).to_numpy(),
            "prices": {
                "actual": (a["energy"].to_numpy(), act_f),
                "forecast": (f["energy"].to_numpy(), {k: planning(f[k].to_numpy()) for k in dp.FCESS}),
                "yesterday": (f["yday_energy"].to_numpy(), {k: planning(f[f"yday_{k}"].to_numpy()) for k in dp.FCESS}),
            },
        }
        if strict:
            yday = f["yday_energy"].to_numpy()
            d["fc0900"] = np.r_[yday[:2], f["energy"].to_numpy()[2:]]
            d["prices"]["bridge"] = (yday, d["prices"]["forecast"][1])  # known at 08:00: yesterday + 28-day FCESS
            d["window7"] = ct.window_7h(day)
            d["slot_month"] = ts.strftime("%Y-%m").to_numpy()
        days.append(d)
    return days


_DAYS: list[dict] = []
_AUX: dict = {}


def _init_worker(days: list[dict], aux: dict | None = None) -> None:
    global _DAYS, _AUX
    _DAYS, _AUX = days, aux or {}


def strategy(name: str, prefs: cf.RiskPrefs = PREFS) -> tuple[str, dp.Strategy, cf.RiskPrefs]:
    """(price source, strategy, risk preferences) of a strategy name in a scenario whose risk preferences
    are `prefs`; "<name>|h=<hurdle>" sets a different hurdle, "<name>|plan=<planner>" a different planner
    (vpp.dispatch.Strategy; the scenario planners need the day's scenarios, vpp.stochastic), and a last
    "|res=<MWh>" keeps that much for spikes every day (see _route_days). Flags "|vt" (stored energy at the end of
    each plan valued by the storage-value table) and "|bid" (layered offers from it; vpp.storage_value) may
    come anywhere after the name."""
    parts = name.split("|")
    flags = {f for f in parts[1:] if f in ("vt", "bid")}
    core = "|".join(f for f in parts if f not in flags)
    base, _, planner = core.partition("|res=")[0].partition("|plan=")
    base, _, hurdle = base.partition("|h=")
    source, strat, fixed = STRATEGIES[base]
    prefs = replace(prefs, **fixed)
    if planner or "|res=" in name or flags:
        strat = replace(strat, name=name, planner=planner or strat.planner)
    if "vt" in flags:
        strat = replace(strat, terminal="table")
    if "bid" in flags:
        strat = replace(strat, offer_rule="table")
    if hurdle:
        return source, replace(strat, name=name), replace(prefs, hurdle_per_mwh=float(hurdle))
    return source, strat, prefs


def _route_days(sc: Scenario, name: str, decide=None):
    """One strategy through every day on a 10 MW route, yielding (day, terms, summary, log): the day's
    contract terms and caps (vpp.contracts), the 08:00 bridge plan and the 09:00 forecast, the stored
    energy and the month-to-date peak (for the CMD cap) carried from day to day, and on the eve of a
    day flagged as a possible system peak the energy its 17:00-20:00 hours need, kept overnight. Under a
    fixed retail contract the meter's energy is priced at RETAIL_FIXED, there is no standing offer to
    sell at the wholesale price, and net energy export earns nothing. The spike reserve (vpp.dispatch.Reserve,
    in RESERVE_WINDOW from 09:00): a name ending "|res=<MWh>" keeps that much every day; decide(d, kw), if
    given, returns each day's Reserve or None from the day and run_day's keyword arguments."""
    route, asset, site = sc.route, sc.asset, sc.site
    bat = asset.battery
    source, strat, prefs = strategy(name, sc.prefs)
    res_mwh = float(name.partition("|res=")[2] or 0.0)
    perfect = source == "actual"
    fixed = route.retail == "fixed"
    if fixed:
        strat = replace(strat, offer=False)
    events = {c.name: ct.call_days(c, _AUX["demand"]) for c in route.contracts}
    peaks = _AUX["ircr_peaks"] if route.ircr_hindsight else {}
    same_day, day_before = set(), set(peaks)  # possible peak days flagged at 09:00 on the day / the day before
    if route.ircr == "dynamic":
        same_day, day_before = set(_AUX["flags0"]), set(_AUX["flags1"])
    elif route.ircr is not None and not route.ircr_hindsight:
        same_day = set(sb.flag_days(route.ircr[0], _AUX["predictor0"]))
        day_before = set(sb.flag_days(route.ircr[1], _AUX["predictor1"]))
    by_date = {d["day"]: d for d in _DAYS}
    window = np.zeros(dp.N, bool)
    window[sb.IRCR_SLOTS[0]:sb.IRCR_SLOTS[1]] = True
    enuc = sb.RT7(route.cmd_mw / sb.PF * 1000).enuc_per_mw() if route.cmd_mw else 0.0
    ref = (19, 27) if route.window == "esroi" else ct.window_7h(ct.HOT_DAY)  # credits are certified once, on a hot weekday
    credit = ct.credits(bat, site, ref, route.rcm, sc.prefs.load_margin) if route.rcm else 0.0
    soc, mtd = 0.0, {}
    for d in _DAYS:
        day = d["day"]
        forecast = None
        if perfect or route.issue == "0815":
            pe, pf = d["prices"][source]
        else:
            (pe, pf), forecast = d["prices"]["bridge"], d["fc0900"]
        rtp, p5 = d["rtp"], d["p5"]
        if fixed:
            pe, forecast = np.full(dp.N, ct.RETAIL_FIXED), None
            rtp, p5 = pe, np.full(dp.N * dp.STEPS, ct.RETAIL_FIXED)
        load_fc = load_act = None
        if site is not None:
            load_fc = d["load_act" if perfect else "load_fc"] * site.load_scale
            load_act = d["load_act"] * site.load_scale
        cmd_cap = None if route.cmd_mw is None else np.array([max(route.cmd_mw, mtd.get(m, 0.0)) for m in d["slot_month"]])
        ircr_cap, ircr_at, reserve = None, 0 if day in day_before else 2, 0.0
        if site is not None and (day in same_day or day in day_before):
            ircr_cap = np.full(dp.N, np.inf)
            ircr_cap[peaks[day] if day in peaks else window] = 0.0
        tomorrow = by_date.get(day + pd.Timedelta(days=1))
        if site is not None and tomorrow is not None and tomorrow["day"] in day_before:
            # hold overnight what tomorrow's flagged hours need: the factory's planned load then
            need = tomorrow["load_fc"] * site.load_scale * (1 + sc.prefs.load_margin)
            slots = peaks[tomorrow["day"]] if tomorrow["day"] in peaks else np.flatnonzero(window)
            reserve = min(bat.energy_mwh, float(np.minimum(bat.power_mw, need[slots]).sum()) * dp.DT / bat.eta)
        carry = 0.0
        if tomorrow is not None:  # tomorrow's standing compliance obligations are known today
            plan_next = None if site is None else tomorrow["load_fc"] * site.load_scale * (1 + sc.prefs.load_margin)
            standing = ct.day_terms(route, tomorrow, bat, site, None, None, 0, None, credit, {}, enuc, 0.0,
                                    sc.prefs.capacity_floor)
            today = ct.day_terms(route, d, bat, site, None, None, 0, None, credit, {}, enuc, 0.0, sc.prefs.capacity_floor)
            plan_today = None if site is None else d["load_fc"] * site.load_scale * (1 + sc.prefs.load_margin)
            carry = ct.carry_in(standing, bat, site, plan_next, today, plan_today)
        terms = ct.day_terms(route, d, bat, site, cmd_cap, ircr_cap, ircr_at, forecast, credit, events, enuc, reserve,
                             sc.prefs.capacity_floor, carry, _AUX["ircr_value_mw"])
        kw = dict(asset=asset, strat=strat, prefs=prefs, fc_e=pe, fc_f=pf, rtp=rtp, act_f=d["act_f"], p5=p5,
                  obligations=None, risk=d["risk"], s_start=soc, p5_f=d["p5_f"], site=site, load_fc=load_fc,
                  load_act=load_act, terms=terms, export_energy=not fixed, reg_util=route.reg_util,
                  scen=None if strat.planner == "point" else d.get("scen"))
        if decide is not None:
            kw["reserve"] = decide(d, kw)
        elif res_mwh > 0:
            kw["reserve"] = dp.Reserve(res_mwh, *RESERVE_WINDOW)
        if strat.terminal == "table":  # vpp.storage_value's tables, in the shared inputs
            kw["terminal"] = _AUX["storage"].terminal(day)
        if strat.offer and strat.offer_rule == "table":
            kw["bids"] = _AUX["storage"].bids(day)
        summary, log = dp.run_day(**kw)
        soc = summary["soc_end"]
        for m, x in zip(d["slot_month"], log["meter_mw"].to_numpy()):
            mtd[m] = max(mtd.get(m, 0.0), x)
        yield day, terms, {"credit_mw": credit, **summary}, log


def _run_route(sc: Scenario, name: str, keep_logs: bool):
    rows, logs, meters = [], {}, []
    for day, _, summary, log in _route_days(sc, name):
        rows.append({"scenario": sc.name, "strategy": name, "day": day, **summary})
        meters.append(log["meter_mw"].to_numpy().astype(np.float32))
        if keep_logs:
            logs[day] = log
    return sc.name, name, rows, logs, np.vstack(meters)


def explain_route_day(sc: Scenario, name: str, day: pd.Timestamp, days: list[dict], aux: dict, money: dict | None = None) -> list[str]:
    """Plain-language account of one day of a 10 MW route (replays the route up to that day);
    money: ledger_* items settled outside the dispatcher (capacity, contracts, bill) for that day."""
    _init_worker(days, aux)
    _, strat, prefs = strategy(name, sc.prefs)
    for d, terms, summary, log in _route_days(sc, name):
        if d == day:
            return dp.explain(log, {**summary, **(money or {})}, sc.asset, strat, prefs, None, sc.site, terms)
    raise KeyError(day)


def _run(task: tuple[Scenario, str, bool]):
    """One strategy in one scenario through every day; perfect foresight also knows the actual load."""
    sc, name, keep_logs = task
    if sc.route is not None:
        return _run_route(sc, name, keep_logs)
    asset, site = sc.asset, sc.site
    source, strat, prefs = strategy(name, sc.prefs)
    rows, logs, soc = [], {}, 0.0
    for d in _DAYS:
        pe, pf = d["prices"][source]
        load_fc = load_act = None
        if site is not None:
            load_fc = d["load_act" if source == "actual" else "load_fc"] * site.load_scale
            load_act = d["load_act"] * site.load_scale
        obligations = [ct.capacity_obligation(asset.battery, d["window"])] if sc.obligation else None
        summary, log = dp.run_day(asset, strat, prefs, pe, pf, d["rtp"], d["act_f"], d["p5"], obligations, d["risk"], soc,
                                  d["p5_f"], site, load_fc, load_act)
        soc = summary["soc_end"]
        rows.append({"scenario": sc.name, "strategy": name, "day": d["day"], **summary})
        if keep_logs:
            logs[d["day"]] = log
    return sc.name, name, rows, logs, None


def run_tasks(tasks: list[tuple[Scenario, str, bool]], days: list[dict], aux: dict | None = None):
    """Run (scenario, strategy, keep logs) tasks in parallel. Returns the daily rows, logs and the meter
    import per slot (days x 48, 10 MW routes only), both by (scenario, strategy)."""
    tasks = sorted(tasks, key=lambda t: "value" not in t[1])  # slowest first keeps the pool busy
    daily, logs, meters = [], {}, {}
    workers = min(len(tasks), max(1, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(days, aux)) as pool:
        for sc, name, rows, lg, meter in pool.map(_run, tasks):
            daily.extend(rows)
            if lg:
                logs[sc, name] = lg
            if meter is not None:
                meters[sc, name] = meter
    daily = pd.DataFrame(daily)
    daily["period"] = period_of(daily["day"])
    return daily, logs, meters


def legacy_tasks() -> list[tuple[Scenario, str, bool]]:
    return [(sc, name, sc.name == "main" and name.startswith("forecast+offer")) for sc in LEGACY.values() for name in sc.strategies]


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


def legacy_main(days: list[dict]) -> None:
    """The 1 MW / 2 MWh study's tables and printout (reports/q2b_backtest_*.csv)."""
    daily, by_task, _ = run_tasks(legacy_tasks(), days)
    logs = {name: lg for (_, name), lg in by_task.items()}
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
    print(sc.pivot_table(index="scenario", columns=["period", "strategy"], values="total").loc[list(LEGACY)].round(1).to_string())
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
    cap = np.clip(np.minimum(ASSET.battery.power_mw, SITE.import_limit_mw - fl["site_load_plan"]), 0.0, None)
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
    main_sc = LEGACY["main"]
    _, strat, prefs = strategy(pick, main_sc.prefs)
    print("\n".join(dp.explain(logs[pick][example], summ, main_sc.asset, strat, prefs,
                               [ct.capacity_obligation(main_sc.asset.battery, d["window"])], SITE)))


# ---------------------------------------------------------------------------- the 10 MW study (2026-10-19)
# A 10 MW / 40 MWh battery behind the factory's meter (vpp.site.FACTORY), issued at 09:00 with an 08:00
# bridge plan, on routes that differ in what it commits to (vpp.contracts.Route). Daily money is the
# market (energy + FCESS - wear) plus capacity credits less refunds plus contract availability; the
# factory's bill items are settled per month (RT7 network demand) and per Hot Season (IRCR).

Q2B_ASSET = cf.Asset(cf.Battery(power_mw=10.0, energy_mwh=40.0), wear_per_mwh=WEAR)
VALUE = "forecast+offer(value)"
Q2B_STRATEGIES = ("perfect", "forecast", VALUE)
HURDLES = (0.0, 25.0, 50.0, 100.0, 150.0)
CMD_GRID = (11.0, 10.0, 9.0, 8.0, 7.0)
REFUND_FACTOR = ct.REFUND_FACTOR
SC_CHOICE_MONTHS = ("2025-12", "2026-01", "2026-02", "2026-03")  # the backtest's Hot Season, before the cutoff
IRCR_SEASON = 2025                    # the 2025-26 Hot Season sets the factory's 2026-27 IRCR
EXPORT_3MW = replace(st.FACTORY, export_limit_mw=3.0)


def q2b_aux() -> dict:
    """Inputs the 10 MW workers share: system demand (contract call days), AEMO's forecast peak
    requirement (IRCR flags), the actual 12 peak intervals of IRCR_SEASON (the hindsight bound), and the
    dynamic IRCR rule's flags and value.

    The dynamic rule (vpp.site_bill.dynamic_flags) uses only Hot Seasons before IRCR_SEASON for its
    tolerance. Value of a flagged day: covering at least 3 of the 4 peak days (9 of the 12 intervals)
    brings the median, and so the IRCR, to 0, so every flagged day is needed; each is valued as an equal
    share of the year's capacity price per MW, RCP / the flags a season takes (past seasons' average),
    on the day's largest import in its peak hours. A proxy, settled against the official structure
    after the season (q2b_bills)."""
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["demand_mw", "req_energy_mw"])
    demand = df30["demand_mw"]
    peaks = {}
    for t in sb.ircr_intervals(demand, IRCR_SEASON):
        day = (t - H).normalize()
        peaks.setdefault(day, []).append(int((t - day - H) / HALF))
    p0, p1 = sb.peak_day_predictor(0), sb.peak_day_predictor(1)
    actual = sb.daily_max(df30["req_energy_mw"])  # the predictor's own quantity
    past = [y for y in sorted(set(sb.hot_season(actual.index))) if 0 <= y < IRCR_SEASON]
    tol0, tol1 = sb.forecast_tolerance(p0, actual, past), sb.forecast_tolerance(p1, actual, past)
    flags0, flags1 = sb.dynamic_flags(p0, actual, tol0, 1), sb.dynamic_flags(p1, actual, tol1, 2)
    flagged = flags0.union(flags1)
    per_season = [int((sb.hot_season(flagged) == y).sum()) for y in past]
    value = ct.RCP[IRCR_SEASON + 1] * sb.IRCR_RATIO / max(1.0, float(np.mean(per_season)))
    return {"demand": demand, "predictor0": p0, "predictor1": p1, "ircr_peaks": peaks, "flags0": flags0, "flags1": flags1,
            "ircr_value_mw": value, "ircr_tolerance": (tol0, tol1), "ircr_flags_past": per_season}


def factory_load(days: list[dict], site: st.Site = st.FACTORY) -> pd.Series:
    """The factory's actual load (MW) per 30-min interval of the backtest days."""
    ts = pd.DatetimeIndex(np.concatenate([pd.date_range(d["day"] + H, periods=dp.N, freq="30min") for d in days]))
    return pd.Series(np.concatenate([d["load_act"] for d in days]) * site.load_scale, index=ts)


REFUSED: dict[str, str] = {}  # scenario -> why its configuration is not allowed or cannot be had
WARNED: dict[str, list] = {}  # scenario -> what it commits to that the battery cannot always cover


def _sc(name: str, route: ct.Route, strategies=(VALUE,), site: st.Site | None = st.FACTORY,
        prefs: cf.RiskPrefs = PREFS) -> Scenario | None:
    """The scenario, or None (recorded in REFUSED) if Route.check refuses its configuration. Without an
    export service the connection exports nothing (RT7 is import-only)."""
    route = replace(route, name=name)
    site = None if route.metering == "standalone" else site
    if site is not None and not route.export_service:
        site = replace(site, export_limit_mw=0.0)
    try:
        warnings = route.check(Q2B_ASSET.battery, site, prefs.load_margin)
    except ValueError as e:
        REFUSED[name] = str(e).split(": ", 1)[1]
        return None
    if warnings:
        WARNED[name] = warnings
    return Scenario(name, Q2B_ASSET, site, False, tuple(strategies), route, prefs)


def q2b_scenarios(stage: int, cmd_nb: float, cmd: float | None = None, hurdle: float = HURDLE) -> list[Scenario]:
    """Stage 1: the CMD level, the hurdle and the IRCR flag rule, on the market-only route m0. Stage 2:
    every commitment on m1 = m0 + the chosen CMD + the IRCR rule (or standalone, for gross credits)."""
    fixed = (sb.IRCR_THRESHOLD_MW, sb.IRCR_DAY_AHEAD_MW)
    if stage == 1:
        m0 = ct.Route("m0", cmd_mw=cmd_nb)
        scs = ([_sc("m0", m0, Q2B_STRATEGIES + tuple(f"{VALUE}|h={h:g}" for h in HURDLES)),
                _sc("m0 no CMD cap", ct.Route("x"))]
               + [_sc(f"CMD {c:g} MW", ct.Route("x", cmd_mw=c)) for c in CMD_GRID]
               + [_sc("m0 IRCR dynamic", replace(m0, ircr="dynamic")), _sc("m0 IRCR fixed", replace(m0, ircr=fixed)),
                  _sc("m0 IRCR wide", replace(m0, ircr=sb.IRCR_WIDE)),
                  _sc("m0 IRCR hindsight", replace(m0, ircr=(0.0, 0.0), ircr_hindsight=True))])
        return [x for x in scs if x is not None]
    v = VALUE if hurdle == HURDLE else f"{VALUE}|h={hurdle:g}"
    f = "forecast" if hurdle == HURDLE else f"forecast|h={hurdle:g}"
    m1 = ct.Route("m1", cmd_mw=cmd, ircr="dynamic")
    scs = [
        _sc("m1", m1, ("perfect", f, v)),
        _sc("m1 no FCESS", replace(m1, fcess=()), (v,)),
        _sc("m1 retail fixed", replace(m1, retail="fixed"), (v,)),
        _sc("m1 no export service", replace(m1, export_service=False), (v,)),
        _sc("m1 export 3 MW", m1, (v,), EXPORT_3MW),
        _sc("m1 regulation not used", replace(m1, reg_util=(0.0, 0.0)), (v,)),
        _sc("m1 regulation used more", replace(m1, reg_util=(0.18, 0.24)), (v,)),
        _sc("m1 IRCR fixed", replace(m1, ircr=fixed), (v,)),
        _sc("m1 IRCR wide", replace(m1, ircr=sb.IRCR_WIDE), (v,)),
        _sc("m1 IRCR hindsight", replace(m1, ircr=(0.0, 0.0), ircr_hindsight=True), (v,)),
        # a facility with capacity credits and a load must not run its battery to reduce IRCR (ESM 7.10.6B)
        _sc("RCM netted 4h", replace(m1, rcm="netted", ircr=None), (v,)),
        _sc("RCM netted 4h, IRCR targeted", replace(m1, rcm="netted"), (v,)),
        _sc("RCM netted 4h, margin", replace(m1, rcm="netted_margin", ircr=None), (v,)),
        _sc("RCM netted 4h, refund test", replace(m1, rcm="netted", ircr=None), (v,),
            prefs=replace(PREFS, capacity_floor="refund_test")),
        _sc("standalone market", ct.Route("x", metering="standalone"), (v,)),
        _sc("RCM gross 4h standalone", ct.Route("x", metering="standalone", rcm="gross"), (v,)),
        _sc("RCM gross 7h standalone", ct.Route("x", metering="standalone", rcm="gross", window="7h"), (v,)),
        _sc("SCM 5 MW (registered)", replace(m1, contracts=(ct.scm(5.0),)), (v,)),
        _sc("SCM 5 MW (registered), no export service", replace(m1, contracts=(ct.scm(5.0),), export_service=False), (v,)),
        _sc("SCM 5 MW (unregistered form)", replace(m1, contracts=(ct.scm_unregistered(5.0),)), (v,)),
        _sc("SCM AO 5 MW", replace(m1, contracts=(ct.scm_ao(5.0),)), (v,)),
        _sc("NCESS AEMO 1.5 MW", replace(m1, contracts=(ct.ncess_aemo(1.5),)), (v,)),
        _sc("NCESS Western Power 3 MW, calls 2 h", replace(m1, contracts=(ct.ncess_wp(3.0, 2.0, 10),)), (v,)),
        _sc("PPA backup 4 MWh", replace(m1, backup_mwh=4.0), (v,)),
        _sc("PPA backup 8 MWh", replace(m1, backup_mwh=8.0), (v,)),
    ]
    return [x for x in scs if x is not None]


def q2b_tasks(scenarios: list[Scenario], keep: tuple[str, ...] = ()) -> list[tuple[Scenario, str, bool]]:
    return [(sc, name, sc.name in keep) for sc in scenarios for name in sc.strategies]


def _wp_discount(calls_failed: int) -> float:
    """Western Power network support: share of the season's availability fee lost to non-complying calls."""
    return 0.0 if calls_failed <= 2 else min(0.9, 0.05 * (calls_failed - 2))


def q2b_ledger(daily: pd.DataFrame, scenarios: list[Scenario]) -> pd.DataFrame:
    """Daily money: market, capacity credits less refunds, contract availability, and their sum."""
    route = {s.name: s.route for s in scenarios}
    out = daily.copy()
    rcp = out["day"].map(ct.rcp)
    out["capacity"] = out["credit_mw"] * rcp / 365
    rate = np.array([ct.refund_rate(d, route[n].window) for d, n in zip(out["day"], out["scenario"])])
    out["refund"] = rate * out.get("capacity_short_mw_ti", 0.0)
    out["contract"] = 0.0
    for name, r in route.items():
        for spec in r.contracts:
            unit = "5min" if spec.form == "registered" else "ti"
            rows = (out["scenario"] == name) & (out.get(f"{spec.name}_window_{unit}", pd.Series(0, index=out.index)).fillna(0) > 0)
            x = out.loc[rows]
            pay = spec.rate * spec.mw * x[f"{spec.name}_available_{unit}"] / x[f"{spec.name}_window_{unit}"]
            if spec.activation_price:  # activation-only: paid on the energy delivered in events
                pay = pay + spec.activation_price * x[f"{spec.name}_event_mwh"]
            if spec.name == "ncess_wp":  # non-performance discount per season, on the season's fee
                season = np.where(x["day"].dt.month >= 10, x["day"].dt.year, x["day"].dt.year - 1)
                failed = (x[f"{spec.name}_min_delivery"] < spec.min_delivery - 1e-9).groupby([x["strategy"], season]).transform("sum")
                pay = spec.rate * spec.mw * (1 - failed.map(_wp_discount))
            out.loc[rows, "contract"] = out.loc[rows, "contract"] + pay
    out["ledger_total"] = out["total"] + out["capacity"] - out["refund"] + out["contract"]
    return out


def q2b_bills(meters: dict, days: list[dict], scenarios: list[Scenario], cmd_nb: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per (scenario, strategy): the factory's RT7 charges by calendar month and its 2025-26 IRCR, against
    the factory without a battery (CMD nominated at its own peak). Standalone routes leave the bill as is."""
    load = factory_load(days)
    ts = load.index
    nb_rt7 = sb.RT7(sb.cmd_kva(cmd_nb))
    nb_bill = sb.monthly_bills(load, nb_rt7)
    iv = sb.ircr_intervals(pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["demand_mw"])["demand_mw"], IRCR_SEASON)
    ircr_nb = sb.ircr_mw(load, iv)
    route = {s.name: s.route for s in scenarios}
    bills, ircr = [], []
    for (name, strat), m in meters.items():
        r = route[name]
        if r.metering == "standalone":
            continue
        meter = pd.Series(m.ravel().astype(float), index=ts)
        bill = sb.monthly_bills(meter, sb.RT7(sb.cmd_kva(r.cmd_mw if r.cmd_mw is not None else cmd_nb)))
        bills.append(bill.assign(scenario=name, strategy=strat, saving=nb_bill["total"] - bill["total"],
                                 saving_per_day=(nb_bill["total"] - bill["total"]) / bill["days"]).reset_index())
        mw = sb.ircr_mw(meter, iv)
        saving = (ircr_nb - mw) * ct.RCP[IRCR_SEASON + 1] * sb.IRCR_RATIO
        barred = bool(r.rcm)  # ESM 7.10.6B: a facility with capacity credits and a load must not reduce its IRCR
        ircr.append({"scenario": name, "strategy": strat, "ircr_mw": mw, "ircr_no_battery_mw": ircr_nb,
                     "saving_per_year": 0.0 if barred else saving, "saving_barred_7_10_6B": saving if barred else 0.0,
                     "intervals_covered": int((meter.reindex(iv) <= 1e-6).sum()),
                     "peak_days_covered": sb.peak_days_covered(meter, iv)})
    bills = pd.concat(bills, ignore_index=True)
    bills["period"] = period_of(bills["month"].dt.to_timestamp())
    return bills, pd.DataFrame(ircr)


def contract_record(ledger: pd.DataFrame) -> pd.DataFrame:
    """Per (scenario, strategy) and contract: called days, called days delivered below the contract's
    threshold (by Hot Season), and the worst share of unavailable service intervals in any 90 days."""
    rows = []
    for (sc, strat), g in ledger.groupby(["scenario", "strategy"]):
        for c in [c[:-len("_window_ti")] for c in g.columns if c.endswith("_window_ti")]:
            unit = "5min" if f"{c}_window_5min" in g and g[f"{c}_window_5min"].fillna(0).sum() > 0 else "ti"
            x = g[g[f"{c}_window_{unit}"].fillna(0) > 0].set_index("day").sort_index()
            if not len(x):
                continue
            threshold = {"ncess_wp": 0.95}.get(c, 0.9)
            called = x[x[f"{c}_event_ti"].fillna(0) > 0]
            failed = called[called[f"{c}_min_delivery"] < threshold - 1e-9]
            unavail = (x[f"{c}_window_{unit}"] - x[f"{c}_available_{unit}"]).rolling("90D").sum()
            share = (unavail / x[f"{c}_window_{unit}"].rolling("90D").sum()).max()
            seasons = sb.hot_season(failed.index)
            rows.append({"scenario": sc, "strategy": strat, "contract": c, "called_days": len(called),
                         "failed_days": len(failed), "most_failed_in_a_season": int(pd.Series(seasons).value_counts().max()) if len(failed) else 0,
                         "worst_90d_unavailable": float(share)})
    return pd.DataFrame(rows)


def q2b_summary(ledger: pd.DataFrame, bills: pd.DataFrame, ircr: pd.DataFrame) -> pd.DataFrame:
    """$/day by scenario, strategy and period: daily items, the month's bill saving per day, the IRCR saving
    (one Hot Season's result, counted only in the period that holds that season: the test has none);
    the worst 5% of days (mean of their daily ledger) and the worst day; compliance: planned shortfall (plans
    that could not meet every requirement, the largest in MWh), unplanned shortfall in real time (MWh),
    capacity shortfall and timing errors (information used before it was public; must be 0)."""
    cols = ["total", "energy", "fcess", "wear", "capacity", "refund", "contract", "ledger_total"]
    s = ledger.groupby(["scenario", "strategy", "period"])[cols].mean()
    s["days"] = ledger.groupby(["scenario", "strategy", "period"]).size()
    extra = ledger.groupby(["scenario", "strategy", "period"]).agg(
        max_import_mw=("max_import_mw", "max"), self_mwh=("self_mwh", "mean"), export_mwh=("export_mwh", "mean"),
        plans_short=("plans_short", "sum"), plan_shortfall_mwh=("plan_shortfall_mwh", "max"),
        unplanned_shortfall_mwh=("obligation_shortfall_mwh", "max"), timing_errors=("timing_errors", "sum"),
        worst_day=("ledger_total", "min"), site_short_mw=("site_shortfall_mw", "max"),
        budget_overrun_mwh=("budget_overrun_mwh", "sum"), refund_mw_ti=("capacity_short_mw_ti", "sum"))
    s = s.join(extra)
    s["worst_5pct_days"] = ledger.groupby(["scenario", "strategy", "period"])["ledger_total"].apply(
        lambda x: x.nsmallest(max(1, int(np.ceil(0.05 * len(x))))).mean())
    if len(bills):
        b = bills.groupby(["scenario", "strategy", "period"]).apply(lambda g: g["saving"].sum() / g["days"].sum())
        s["bill_saving"] = b
        s["enuc_months"] = bills[bills["enuc"] > 0].groupby(["scenario", "strategy", "period"]).size()
    if len(ircr):
        s = s.join(ircr.set_index(["scenario", "strategy"])["saving_per_year"].div(365).rename("ircr_saving"), on=["scenario", "strategy"])
        held = period_of(pd.DatetimeIndex([f"{IRCR_SEASON}-12-01", f"{IRCR_SEASON + 1}-03-31"]))
        s.loc[~np.isin(s.index.get_level_values("period"), held), "ircr_saving"] = 0.0
    s[["bill_saving", "ircr_saving", "enuc_months"]] = s.reindex(columns=["bill_saving", "ircr_saving", "enuc_months"]).fillna(0.0)
    s["value"] = s["ledger_total"] + s["bill_saving"] + s["ircr_saving"]
    return s


def _pick(ledger: pd.DataFrame, bills: pd.DataFrame, names: dict, months=None) -> pd.DataFrame:
    """Validation-period value per candidate (daily ledger + monthly bill saving per day), with the
    daily part's 95% interval against the first candidate."""
    val = ledger[ledger["period"] == "validation"] if months is None else ledger[ledger["day"].dt.strftime("%Y-%m").isin(months)]
    rows, base = [], None
    for label, (sc, strat) in names.items():
        x = val[(val["scenario"] == sc) & (val["strategy"] == strat)].set_index("day")["ledger_total"]
        b = bills[(bills["scenario"] == sc) & (bills["strategy"] == strat) & (bills["period"] == "validation")]
        bill = b["saving"].sum() / b["days"].sum() if len(b) else 0.0
        if base is None:
            base = x
        lo, hi = bootstrap_ci((x - base.reindex(x.index)).to_numpy())
        rows.append({"candidate": label, "daily": x.mean(), "bill_saving": bill, "value": x.mean() + bill,
                     "daily_vs_first": (x - base.reindex(x.index)).mean(), "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(rows).set_index("candidate")


def q2b_inputs(smoke: bool = False) -> tuple[list[dict], list, dict]:
    """Days, a representative task set and the shared inputs, for the regression harness."""
    days = load_days("0900")
    if smoke:
        days = [d for d in days if d["day"].month in (1, 2)][:30]
    cmd_nb = sb.cmd_kva(factory_load(days).max()) * sb.PF / 1000
    pick = ["m1", "RCM netted 4h", "SCM 5 MW (registered)", "NCESS Western Power 3 MW, calls 2 h", "PPA backup 4 MWh",
            "standalone market"]
    scs = [s for s in q2b_scenarios(2, cmd_nb, cmd=9.0) if s.name in pick]
    return days, q2b_tasks(scs, keep=tuple(pick)), q2b_aux()


def q2b_main(days: list[dict]) -> None:
    """Stage 1 (CMD level, hurdle, IRCR rule) and stage 2 (every commitment), chosen on validation only."""
    REPORTS.mkdir(exist_ok=True)
    aux = q2b_aux()
    cmd_nb = sb.cmd_kva(factory_load(days).max()) * sb.PF / 1000
    s1 = q2b_scenarios(1, cmd_nb)
    daily1, _, meters1 = run_tasks(q2b_tasks(s1), days, aux)
    led1 = q2b_ledger(daily1, s1)
    bills1, ircr1 = q2b_bills(meters1, days, s1, cmd_nb)
    cmd_pick = _pick(led1, bills1, {f"{cmd_nb:.2f} MW (no battery)": ("m0", VALUE),
                                     **{f"{c:g} MW": (f"CMD {c:g} MW", VALUE) for c in CMD_GRID}})
    hurdle_pick = _pick(led1, bills1, {f"{HURDLE:g}": ("m0", VALUE), **{f"{h:g}": ("m0", f"{VALUE}|h={h:g}") for h in HURDLES}})
    cmd = cmd_nb if cmd_pick["value"].idxmax().endswith("(no battery)") else float(cmd_pick["value"].idxmax().split()[0])
    best_h = float(hurdle_pick["value"].idxmax())
    # a different hurdle replaces 75 only if its daily gain on validation is significant
    hurdle = best_h if hurdle_pick.loc[f"{best_h:g}", "ci_lo"] > 0 else HURDLE
    print("=== market access assumed by the main route m1 (unverified):\n  " + "\n  ".join(ct.Route("m1").assumptions()) + "\n")
    print(f"=== stage 1 (validation): CMD choice\n{cmd_pick.round(1).to_string()}\n=> CMD {cmd:.2f} MW\n")
    print(f"=== hurdle ($/MWh)\n{hurdle_pick.round(1).to_string()}\n=> hurdle {hurdle:g}\n")

    s2 = q2b_scenarios(2, cmd_nb, cmd, hurdle)
    if REFUSED:
        print("=== configurations refused (not allowed, or not on offer):")
        print("\n".join(f"  {k}: {v}" for k, v in REFUSED.items()) + "\n")
    if WARNED:
        print("=== commitments the battery cannot always cover:")
        print("\n".join(f"  {k}: {'; '.join(v)}" for k, v in WARNED.items()) + "\n")
    logged = ("m1", "RCM netted 4h", "SCM 5 MW (registered)", "NCESS Western Power 3 MW, calls 2 h", "PPA backup 4 MWh")
    daily2, logs2, meters2 = run_tasks(q2b_tasks(s2, keep=logged), days, aux)
    scenarios = s1 + s2
    daily = pd.concat([daily1, daily2], ignore_index=True)
    ledger = q2b_ledger(daily, scenarios)
    bills, ircr = q2b_bills({**meters1, **meters2}, days, scenarios, cmd_nb)
    summary = q2b_summary(ledger, bills, ircr)
    record = contract_record(ledger)
    ledger.to_csv(REPORTS / "q2b_10mw_daily.csv", index=False)
    record.to_csv(REPORTS / "q2b_10mw_contracts.csv", index=False)
    bills.to_csv(REPORTS / "q2b_10mw_bills.csv", index=False)
    ircr.to_csv(REPORTS / "q2b_10mw_ircr.csv", index=False)
    summary.to_csv(REPORTS / "q2b_10mw_summary.csv")
    v = VALUE if hurdle == HURDLE else f"{VALUE}|h={hurdle:g}"
    sc_names = [s.name for s in s2 if s.name.startswith("SCM")]
    sc_pick = _pick(ledger, bills, {"m1 (no SCM)": ("m1", v), **{n: (n, v) for n in sc_names}}, SC_CHOICE_MONTHS)
    print(f"=== SCM size and hours, chosen on {', '.join(SC_CHOICE_MONTHS)} (daily ledger incl. availability)\n{sc_pick.round(1).to_string()}\n")
    pd.concat({"cmd": cmd_pick, "hurdle": hurdle_pick, "scm": sc_pick}, names=["choice"]).to_csv(REPORTS / "q2b_10mw_choices.csv")
    show = ["days", "total", "capacity", "refund", "contract", "ledger_total", "bill_saving", "ircr_saving", "value",
            "worst_5pct_days", "max_import_mw", "plans_short", "plan_shortfall_mwh", "unplanned_shortfall_mwh", "enuc_months"]
    print("=== $/day by route (value strategy unless noted)")
    print(f"(IRCR: the {IRCR_SEASON}-{IRCR_SEASON - 1999} Hot Season, before the cutoff; the test from {CUTOFF.date()} has no "
          "Hot Season and scores no IRCR)")
    print(summary[show].round(1).to_string())
    print("\n=== contract record (called days, failed days, worst 90-day unavailability)")
    print(record.round(3).to_string(index=False))
    print(f"\nIRCR dynamic rule: tolerance {aux['ircr_tolerance'][0]:.0f} / {aux['ircr_tolerance'][1]:.0f} MW (same day / day "
          f"before), past seasons' flags {aux['ircr_flags_past']}, value ${aux['ircr_value_mw']:,.0f} per MW of a flagged day")
    print("\n=== IRCR, 2025-26 Hot Season (sets the 2026-27 capacity cost)")
    print(ircr.round(2).to_string(index=False))
    lg = pd.concat([x.assign(day=d, scenario=sc, strategy=st_) for (sc, st_), by_day in logs2.items() for d, x in by_day.items()],
                   ignore_index=True)
    lg.to_parquet(PROCESSED / "q2b_10mw_log.parquet")
    lg = lg[(lg["scenario"] == "m1") & (lg["strategy"] == v)]
    if len(lg):
        df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
        req = df30.reindex(pd.DatetimeIndex(lg["day"] + H + lg["slot"] * HALF))
        share = {k: (lg[f"{k}_mw"].to_numpy() / req[f"req_{k}_mw"].replace(0, np.nan).to_numpy()) for k in dp.FCESS}
        print("\n=== FCESS enabled by the battery as a share of AEMO's requirement (m1, all intervals): median / 95th pct")
        print({k: (round(float(np.nanmedian(v_)), 3), round(float(np.nanpercentile(v_, 95)), 3)) for k, v_ in share.items()})
    example, day = next(s for s in s2 if s.name == "RCM netted 4h"), pd.Timestamp("2026-02-02")
    row = ledger[(ledger["scenario"] == example.name) & (ledger["strategy"] == v) & (ledger["day"] == day)].iloc[0]
    money = {"ledger_capacity_credits": row["capacity"], "ledger_refund": -row["refund"], "ledger_total": row["ledger_total"]}
    print(f"\n=== example: {example.name}, {day.date()} (the 2025-26 Hot Season's highest-demand day)")
    print("\n".join(explain_route_day(example, v, day, days, aux, money)))


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if "--legacy" in argv:
        legacy_main(load_days())
    else:
        q2b_main(load_days("0900"))


if __name__ == "__main__":
    main()
