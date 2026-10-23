"""v2 checks (reports/v2_plan.md).

  --parity [--full]   with every v2 feature off (config.V1) a v2 run is v1's forecast+offer(value), result for
                      result: day by day in one process on 30 days of 2026-01 (summaries and logs exactly equal,
                      with decision records off and on), and with --full also all days through the process pool
                      (daily rows, logs, float32 meters, ledger and bills). It guards the copied loop against drift.
  --runs [s2,s3]      the saved runs (reports/v2_<step>_*, data/processed/v2_<step>_*):
                      plans (every recorded plan): stored energy within the battery, power and FCESS headroom within
                        the battery's power (no MW given twice), FCESS raise backed by stored energy, the planned meter
                        within the connection, the compliance floor kept;
                      execution (logs, daily rows): v2's I1 (meter within the connection), I3 (stored energy within
                        the battery), I6 (energy revenue = sum of RTP x MWh), I7 (discharge within the budget),
                        I9 (information used only once public);
                      offers: every layered offer non-increasing in stored energy and protected energy never below
                        its threshold (random days and states);
                      accounts: every number in the plain-Chinese account of 40 recorded plans comes from a computed
                        fact (vpp.v2.explain.check_numbers).
  --timing            the 09:00 choice among candidates (vpp.v2.candidates) is unchanged when the day's actual prices
                      and load after 09:00 are changed (positive control: the start state changes the plays)
  --same              runs that share a configuration agree: rel+ladder in steps s3, r2 and r3 day for day

Run:  python -m vpp.v2.checks --parity [--full]
      python -m vpp.v2.checks --runs s2,s3,r2,r3 --timing --same
"""

import sys
from dataclasses import replace

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import dispatch as dp
from vpp import site as st
from vpp import site_bill as sb
from vpp import storage_value as sv
from vpp.data import PROCESSED
from vpp.v2 import backtest as vb
from vpp.v2 import bids as bd
from vpp.v2 import explain as ex
from vpp.v2 import harness as hs
from vpp.v2 import outputs as op
from vpp.v2 import value as vl
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


TOL = 1e-6


def plans(step: str) -> bool:
    """Every recorded plan of a run, checked against the battery, the connection and its floors."""
    recs = pd.read_parquet(PROCESSED / f"v2_{step}_records.parquet")
    asset, site = bt.Q2B_ASSET, st.FACTORY
    bat, tau = asset.battery, asset.limits.fcess_sustain_h
    P, E, eta = bat.power_mw, bat.energy_mwh, bat.eta
    bad = {k: 0 for k in ("stored", "power", "headroom", "backing", "meter", "floor")}
    for r in recs.itertuples():
        net, soc = np.asarray(r.net, float), np.asarray(r.soc, float)
        rr, cr, rl, cl = (np.asarray(getattr(r, k), float) for k in dp.FCESS)
        prev = np.r_[r.stored, soc[:-1]]
        load = np.asarray(r.load, float)
        bad["stored"] += int(((soc < -TOL) | (soc > E + TOL)).any())
        bad["power"] += int((np.abs(net) > P + TOL).any())
        bad["headroom"] += int(((net + rr + cr > P + TOL) | (-net + rl + cl > P + TOL)).any())
        bad["backing"] += int(((rr + cr) * tau / eta > np.minimum(prev, soc) + TOL).any())
        bad["meter"] += int(((load - net > site.import_limit_mw + TOL) | (net - load > site.export_limit_mw + TOL)).any())
        if r.hard is not None and not (r.shortfall > TOL):
            bad["floor"] += int((soc < np.asarray(r.hard, float) - TOL).any())
    ok = True
    for k, n in bad.items():
        ok &= _result(f"{step} plans: {k}", n == 0, f"{n} of {len(recs)} plans")
    return ok


def execution(step: str) -> bool:
    """v2's I1, I3, I6, I7 and I9 on a run's logs and daily rows."""
    log = pd.read_parquet(PROCESSED / f"v2_{step}_log.parquet")
    daily = pd.read_csv(bt.REPORTS / f"v2_{step}_daily.csv", parse_dates=["day"])
    E = bt.Q2B_ASSET.battery.energy_mwh
    short = daily.set_index(["strategy", "day"])["site_shortfall_mw"]
    lk = log.set_index(["strategy", "day"]).index
    over = (log["meter_mw"].to_numpy() > st.FACTORY.import_limit_mw + TOL) & ~(short.reindex(lk).to_numpy() > 0)
    ok = _result(f"{step} I1 import within the connection or recorded", not over.any(), f"{int(over.sum())} of {len(log)} intervals")
    ok &= _result(f"{step} I1 export within the connection", not (-log["meter_mw"] > st.FACTORY.export_limit_mw + TOL).any())
    ok &= _result(f"{step} I3 stored energy within 0-{E:g} MWh", bool(((log["soc"] > -TOL) & (log["soc"] < E + TOL)).all()))
    rev = (log["rtp"] * log["energy_mwh"]).groupby([log["strategy"], log["day"]]).sum()
    got = daily.set_index(["strategy", "day"])["energy"].reindex(rev.index)
    ok &= _result(f"{step} I6 energy revenue = sum(RTP x MWh)", bool(np.allclose(rev, got, rtol=1e-9, atol=1e-6)), f"{len(rev)} days")
    budget = E * bt.Q2B_ASSET.limits.cycles_per_day + daily["budget_overrun_mwh"]
    ok &= _result(f"{step} I7 discharge within the budget", bool((daily["discharged_mwh"] <= budget + TOL).all()))
    ok &= _result(f"{step} I9 information used only once public", int(daily["timing_errors"].sum()) == 0)
    return ok


def offers(n: int = 3000, seed: int = 2) -> bool:
    """Layered offers on random days and states: non-increasing in stored energy; protected energy never below."""
    z = vl.load(vl.build())
    rng = np.random.default_rng(seed)
    eta, dw = bt.Q2B_ASSET.battery.eta, dp.decision_wear(bt.Q2B_ASSET, bt.PREFS)
    rising = below = 0
    for _ in range(n):
        i, t = int(rng.integers(0, len(z["level"]))), int(rng.integers(2, dp.N))
        protect = np.zeros(dp.N)
        protect[t] = float(rng.uniform(0, 40))
        cost = (None, 30.0, 55.0, 80.0)[int(rng.integers(0, 4))]  # round 4, B2: the offers' own cost per MWh sold
        b = bd.LadderBids(z["edges"][i], z["marg"][i], float(z["level"][i]), protect, float(rng.uniform(500, 3000)),
                          cost=cost)
        seen = float(rng.uniform(-50, 3000))
        thr = b.thresholds(t, seen, eta, 1.2, dw, 150.0)
        rising += int((np.diff(thr) > 1e-9).any())
        k = np.arange(len(thr)) < protect[t] - 1e-9
        # the protected value alone is a lower bound (offer values are float32: relative tolerance)
        below += int((thr[k] < (1.2 * b.value / eta + (dw if cost is None else cost)) * (1 - 1e-6)).any())
    ok = _result("offers non-increasing in stored energy", rising == 0, f"{rising} of {n}")
    return ok & _result("protected energy offered at least at its value", below == 0, f"{below} of {n}")


def buys(n: int = 3000, seed: int = 4, h: float = 50.0) -> bool:
    """The offers' buy side (round 4, B3) on random days and states: buy limits non-increasing in stored energy and
    below each segment's sell threshold; the economic part never fills a segment whose limit is below the price; the
    plan's charging up to the protected level is never dropped for the price; never more than the battery's power."""
    z = vl.load(vl.build("median", 40.0, hurdle=h))
    rng = np.random.default_rng(seed)
    bat = bt.Q2B_ASSET.battery
    eta, E, P, cost = bat.eta, bat.energy_mwh, bat.power_mw, bt.Q2B_ASSET.wear_per_mwh + h
    bad = {"rising": 0, "above sell": 0, "economic": 0, "must": 0, "power": 0}
    for _ in range(n):
        i, t = int(rng.integers(0, len(z["level"]))), int(rng.integers(0, dp.N))
        protect = rng.uniform(0, 40, dp.N) * (rng.random() < 0.5)
        b = bd.LadderBids(z["edges"][i], z["marg"][i], float(z["level"][i]), protect, float(rng.uniform(500, 3000)),
                          cost=cost, buy_margin=h)
        seen, price = float(rng.uniform(-50, 500)), float(rng.uniform(-100, 400))
        s = float(rng.uniform(0, E))
        pw = 0.0 if rng.random() < 0.3 else -float(rng.uniform(0, P))
        lim = b.buy_limits(t, seen, eta, 1.2)
        thr = b.thresholds(t, seen, eta, 1.2, dp.decision_wear(bt.Q2B_ASSET, bt.PREFS), 150.0)
        bad["rising"] += int((np.diff(lim) > 1e-6).any())
        bad["above sell"] += int((lim >= thr).any())
        c = -b.buy(t, s, pw, price, seen, eta, 1.2, E, P)
        bad["power"] += int(c > P + 1e-9)
        need = max(0.0, protect[min(t + 1, dp.N - 1)] - s) / (eta * dp.DT5)
        must = min(-pw, need) if pw < 0 else 0.0
        bad["must"] += int(c < min(must, P) - 1e-9)
        if c > must + 1e-9:  # the part bought for its value: every segment it fills is worth the price
            top = min(E, s + eta * c * dp.DT5)
            ks = np.arange(int(np.floor(s / sv.GRID + 1e-9)), int(np.ceil(top / sv.GRID - 1e-9)))
            bad["economic"] += int((lim[np.clip(ks, 0, len(lim) - 1)] < price - 1e-9).any())
    ok = True
    for k, v in bad.items():
        ok &= _result(f"buy side: {k}", v == 0, f"{v} of {n}")
    return ok


def states(n: int = 200, seed: int = 5) -> bool:
    """Values by market state (round 4, B4): non-increasing in stored energy and not below 0; the states a day uses
    are public by then: changing the calls of the days after tomorrow leaves today's and tomorrow's states as they
    were (positive control: changing tomorrow's own call changes tomorrow's state on some days)."""
    from vpp.v2 import risk as rk
    z = np.load(vl.build("median", 40.0, hurdle=vb.B2_WIN.hurdle, state=True))
    cs, ms = z["curve_state"], z["marg_state"]
    ok = _result("state values non-increasing and >= 0",
                 bool((np.diff(cs, axis=2) <= 1e-6).all() and (np.diff(ms, axis=4) <= 1e-6).all() and (cs >= 0).all()
                      and (ms >= 0).all()), f"{len(cs)} days")
    risk = rk.day_risk(rk.history(days_ahead=1))
    flags = rk.recent_flags(risk)
    rng = np.random.default_rng(seed)
    days = pd.DatetimeIndex(z["day"].astype(str))
    moved = changed = 0
    for d in rng.choice(days[:-2], n, replace=False):
        d = pd.Timestamp(d)
        later = risk.copy()
        later[later.index >= d + pd.Timedelta(days=2)] = rng.uniform(0, 1, int((later.index >= d + pd.Timedelta(days=2)).sum()))
        f2 = rk.recent_flags(later)
        same = all(np.array_equal(np.nan_to_num(flags.get(x, np.nan), nan=-1), np.nan_to_num(f2.get(x, np.nan), nan=-1))
                   for x in (d, d + pd.Timedelta(days=1)))
        changed += not same
        own = risk.copy()
        if d + pd.Timedelta(days=1) in own.index:
            own[d + pd.Timedelta(days=1)] = 1.0 - own[d + pd.Timedelta(days=1)]
            moved += flags.get(d + pd.Timedelta(days=1), np.nan) != rk.recent_flags(own).get(d + pd.Timedelta(days=1), np.nan)
    ok &= _result("states used only once called: the days after tomorrow change nothing", changed == 0, f"{changed} of {n}")
    return ok & _result("positive control: tomorrow's own call moves tomorrow's state", moved > 0, f"{moved} of {n}")


def accounts(step: str, n: int = 40, seed: int = 3) -> bool:
    """The plain-Chinese account of recorded plans: every number comes from a computed fact."""
    recs = pd.read_parquet(PROCESSED / f"v2_{step}_records.parquet")
    pick = recs.sample(min(n, len(recs)), random_state=seed)
    asset = bt.Q2B_ASSET
    bad = []
    for _, r in pick.iterrows():
        snap = op.Snapshot(r.to_dict(), asset.battery, bt.PREFS.load_margin, asset.limits.fcess_sustain_h,
                           asset.wear_per_mwh, ladder=step != "s2")
        lines, facts = ex.explain(snap)
        bad += ex.check_numbers(lines, facts)
    return _result(f"{step} accounts: every number computed", not bad, f"{len(pick)} plans" + (f"; {bad[:2]}" if bad else ""))


def chooser_timing(n_days: int = 3) -> bool:
    """The 09:00 choice (vpp.v2.candidates) uses nothing after 09:00: with the day's actual prices and load after
    09:00 changed, every candidate's plays and the choice stay the same (positive control: the start state)."""
    import copy
    from concurrent.futures import ProcessPoolExecutor

    from vpp import reserve as rs
    from vpp.v2 import candidates as cd
    days, _, aux = bt.q2b_inputs(smoke=True)
    analogs, ok, moved = rs.Analogs(), True, 0
    with ProcessPoolExecutor() as pool:
        for k in (3, 11, 19)[:n_days]:
            pair = days[k:k + 2]
            out = []
            for variant in ("as is", "changed", "start"):
                sub = copy.deepcopy(pair)
                if variant == "changed":
                    d = sub[0]
                    d["rtp"] = np.r_[d["rtp"][:2], d["rtp"][2:] * 3 + 100]
                    d["p5"] = np.r_[d["p5"][:12], d["p5"][12:] * 3 + 100]
                    d["act_f"] = {f: np.r_[v[:2], v[2:] + 50] for f, v in d["act_f"].items()}
                    d["p5_f"] = {f: np.r_[v[:12], v[12:] + 50] for f, v in d["p5_f"].items()}
                    d["load_act"] = np.r_[d["load_act"][:2], d["load_act"][2:] * 1.1]
                bt._init_worker(sub, aux)
                ch = cd.Chooser(pool, analogs, 0.0)
                list(hs.route_days(vb.m1(), cd.BASE, soc0=20.0 if variant == "start" else 10.0, until=sub[0]["day"],
                                   decide=ch))
                r = ch.rows[0]
                out.append((r["chosen"], [r[f"expected_{o[0]}"] for o in cd.OPTIONS]))
            same = out[0][0] == out[1][0] and np.allclose(out[0][1], out[1][1], rtol=0, atol=1e-9)
            moved += not np.allclose(out[0][1], out[2][1])
            ok &= _result(f"09:00 choice on {pair[0]['day'].date()} unchanged by the actuals after 09:00", same,
                          f"chosen {out[0][0]} / {out[1][0]}")
    return ok & _result("positive control: a different start state changes the plays", moved > 0, f"{moved} of {n_days}")


def terminal_timing(tomorrows=("2026-01-21", "2026-02-02", "2026-03-06")) -> bool:
    """t5 (round 5, section 12.3): with tomorrow's IRCR flag (public at 09:00 today) changed, the fixed configuration's
    08:00 plan and its 08:00-09:00 steps stay the same, while the plans' end value from 09:00 moves (positive control);
    the base, before the fix, shows the leak: its 08:00 plan moves."""
    days, aux = bt.load_days("0900"), bt.q2b_aux()
    by = {d["day"]: d for d in days}
    ok, moved_end, leak = True, 0, 0
    for tom in map(pd.Timestamp, tomorrows):
        pair = [by[tom - pd.Timedelta(days=1)], by[tom]]
        runs = {}
        for cfg in (vb.T5, vb.R4_WIN):
            for flagged in (True, False):
                a = dict(aux, flags1=aux["flags1"] if flagged else aux["flags1"].drop(tom))
                bt._init_worker(pair, a)
                (_, _, _, log, recs), = list(hs.route_days(vb.m1(), replace(cfg, record=True), soc0=10.0, until=pair[0]["day"]))
                runs[cfg.name, flagged] = (log, {r["kind"]: r for r in recs if r["kind"] in ("morning", "reveal")})
        (la, ra), (lb, rb) = runs[vb.T5.name, True], runs[vb.T5.name, False]
        same = (np.array_equal(ra["morning"]["net"], rb["morning"]["net"])
                and np.array_equal(ra["morning"]["terminal"], rb["morning"]["terminal"]) and _same(la.iloc[:2], lb.iloc[:2]))
        moved_end += not np.array_equal(ra["reveal"]["terminal"], rb["reveal"]["terminal"])
        base_a, base_b = runs[vb.R4_WIN.name, True][1]["morning"], runs[vb.R4_WIN.name, False][1]["morning"]
        leak += not np.array_equal(base_a["terminal"], base_b["terminal"])
        ok &= _result(f"t5: {pair[0]['day'].date()} 08:00 plan and 08:00-09:00 unchanged by tomorrow's IRCR flag", same)
    ok &= _result("t5 positive control: the end value from 09:00 moves with the flag", moved_end > 0, f"{moved_end} of {len(tomorrows)}")
    return ok & _result("the base's 08:00 end value moved with the flag (the leak t5 removes)", leak > 0, f"{leak} of {len(tomorrows)}")


def regime_values(months=("2025-07", "2026-01"), level: float = 90.0, tol: float = 1e-6) -> bool:
    """c5 (round 5, section 12.4): the values by regime are non-increasing in stored energy and >= 0; with the same
    transitions for both regimes they are B4's values by state (within tol); the split's windows end before each month
    and not after TRAIN_END."""
    from vpp.evaluate import CUTOFF, TRAIN_END
    h = vb.B2_WIN.hurdle
    z = np.load(vl.build("median", 40.0, hurdle=h, state=True, regime=True))
    mr = z["marg_regime"]
    ok = _result("c5 values by regime non-increasing and >= 0",
                 bool((np.diff(mr, axis=4) <= 1e-6).all() and (mr >= 0).all()), f"{len(mr)} days")
    table = vl.prices()
    lv = vl.levels(table)
    flags = vl.regimes()
    prefs = replace(bt.PREFS, hurdle_per_mwh=h)
    E = bt.Q2B_ASSET.battery.energy_mwh
    worst = 0.0
    for m in map(pd.Period, months):
        ps = sv.price_states(table, m, level=lv)
        rtrans = vl.regime_trans(table, m, lv, flags, ps)
        at = {**ps, "means": level + ps["means"]}
        V, _ = sv.solve(at, sv.load_profiles(m), prefs=prefs, short_penalty=0.0)
        acts = sv.actions(at, sv.load_profiles(m), bt.Q2B_ASSET, prefs, short_penalty=0.0)
        _, marg_s = vl._by_state(V, ps, acts, rtrans, E)
        split = vl.regime_split(table, m, lv, flags, ps, rtrans)
        chance = vl.spike_chance(split["pool_rel"], ps["means"], level)
        for v in (0, 1):
            same = {**split, "trans": np.stack([np.stack([rtrans[v], rtrans[v]])] * 3)}
            mr_same = vl._by_regime(V, acts, same, chance, E)[v]
            worst = max(worst, float(np.abs(mr_same[..., 0, :] - marg_s[v]).max()), float(np.abs(mr_same[..., 1, :] - marg_s[v]).max()))
    ok &= _result(f"c5 with one set of transitions for both regimes = B4's values (within {tol:g})", worst <= tol,
                  f"largest difference {worst:.2g} over {len(months)} months x 2 states x 7 weekdays")
    bad = 0
    for m in sv.MONTHS:
        end = min(m.start_time, CUTOFF)
        used = table.index[(table.index >= end - sv.WINDOW) & (table.index < end) & (table.index <= TRAIN_END)]
        bad += int(not (used.max() < m.start_time and used.max() <= TRAIN_END))
    return ok & _result("c5 split windows before each month and not after the training end", bad == 0, f"{bad} of {len(sv.MONTHS)}")


def regime_timing(days=("2025-12-10", "2026-01-21", "2026-02-02"), slots=(14, 24)) -> bool:
    """c5: the regime at slot t comes from half-hours settled before t only. Changing the settled prices from slot t on
    (5-min prices kept) leaves every decision up to slot t as it was; changing the one settled just before t to $1,000
    (positive control) changes the offers from slot t on on some day."""
    import copy
    all_days, aux = bt.load_days("0900"), bt.q2b_aux()
    by = {d["day"]: d for d in all_days}
    ok, moved = True, 0
    for day in map(pd.Timestamp, days):
        for t in slots:
            out = {}
            for variant in ("as is", "later", "before"):
                pair = copy.deepcopy([by[day], by[day + pd.Timedelta(days=1)]])
                if variant == "later":
                    pair[0]["rtp"] = np.r_[pair[0]["rtp"][:t], pair[0]["rtp"][t:] + 300.0]
                elif variant == "before":
                    pair[0]["rtp"] = np.r_[pair[0]["rtp"][:t - 1], 1000.0, pair[0]["rtp"][t:]]
                bt._init_worker(pair, aux)
                (_, _, _, log, _), = list(hs.route_days(vb.m1(), replace(vb.C5, record=False), soc0=20.0, until=day))
                out[variant] = log.drop(columns="rtp")
            ok &= _result(f"c5: {day.date()} up to slot {t} unchanged by settled prices from slot {t} on",
                          _same(out["as is"].iloc[:t + 1], out["later"].iloc[:t + 1]))
            moved += not out["as is"].iloc[t:].equals(out["before"].iloc[t:])
    return ok & _result("c5 positive control: a spike settled just before t moves the offers from t on", moved > 0,
                        f"{moved} of {len(days) * len(slots)}")


def full_bids(n: int = 3000, seed: int = 6, h: float = 50.0) -> bool:
    """f5 (round 5, section 12.6) on random days and states, with and without a floor: buy limits non-increasing in
    stored energy, below each segment's sell threshold, never above the cap, never below the floor (or the cap); the
    economic part never fills a segment whose limit is below the price; the plan's charging up to the protected level
    is never dropped for the price; never more than the battery's power. The floor of a day uses only the days before
    it (prices of the day and later changed: unchanged; positive control: the 28 days before changed)."""
    z = vl.load(vl.build("median", 40.0, hurdle=h))
    rng = np.random.default_rng(seed)
    bat = bt.Q2B_ASSET.battery
    eta, E, P, cost = bat.eta, bat.energy_mwh, bat.power_mw, bt.Q2B_ASSET.wear_per_mwh + h
    cap = (bd.OFFER_CAP - cost) * eta ** 2
    dw = dp.decision_wear(bt.Q2B_ASSET, bt.PREFS)
    bad = {k: 0 for k in ("rising", "above sell", "cap", "floor", "economic", "must", "power")}
    for _ in range(n):
        i, t = int(rng.integers(0, len(z["level"]))), int(rng.integers(0, dp.N))
        protect = rng.uniform(0, 40, dp.N) * (rng.random() < 0.5)
        floor = float(rng.uniform(0, 150)) if rng.random() < 0.5 else None
        b = bd.LadderBids(z["edges"][i], z["marg"][i], float(z["level"][i]), protect, float(rng.uniform(500, 3000)),
                          cost=cost, buy_margin=0.0, buy_alpha=1.0, buy_cap=cap,
                          value_floor=None if floor is None else floor / eta)
        seen, price = float(rng.uniform(-50, 500)), float(rng.uniform(-100, 400))
        s = float(rng.uniform(0, E))
        pw = 0.0 if rng.random() < 0.3 else -float(rng.uniform(0, P))
        lim = b.buy_limits(t, seen, eta, 1.2)
        thr = b.thresholds(t, seen, eta, 1.2, dw, 150.0)
        bad["rising"] += int((np.diff(lim) > 1e-6).any())
        bad["above sell"] += int((lim >= thr).any())
        bad["cap"] += int((lim > cap + 1e-9).any())
        bad["floor"] += int(floor is not None and (lim < min(floor, cap) - 1e-6).any())
        c = -b.buy(t, s, pw, price, seen, eta, 1.2, E, P)
        bad["power"] += int(c > P + 1e-9)
        need = max(0.0, protect[min(t + 1, dp.N - 1)] - s) / (eta * dp.DT5)
        must = min(-pw, need) if pw < 0 else 0.0
        bad["must"] += int(c < min(must, P) - 1e-9)
        if c > must + 1e-9:
            top = min(E, s + eta * c * dp.DT5)
            ks = np.arange(int(np.floor(s / sv.GRID + 1e-9)), int(np.ceil(top / sv.GRID - 1e-9)))
            bad["economic"] += int((lim[np.clip(ks, 0, len(lim) - 1)] < price - 1e-9).any())
    ok = True
    for k, v in bad.items():
        ok &= _result(f"f5 bids: {k}", v == 0, f"{v} of {n}")
    table = vl.prices()
    base = bd.charge_floor(table, 9.0, st.FACTORY.load_scale, bt.PREFS.load_margin)
    d = pd.Timestamp("2026-01-15")
    later, before = table.copy(), table.copy()
    later.loc[later.index >= d] += 500.0
    before.loc[(before.index >= d - pd.Timedelta(days=28)) & (before.index < d)] += 500.0
    same = base[d] == bd.charge_floor(later, 9.0, st.FACTORY.load_scale, bt.PREFS.load_margin)[d]
    moved = base[d] != bd.charge_floor(before, 9.0, st.FACTORY.load_scale, bt.PREFS.load_margin)[d]
    ok &= _result("f5 floor uses only the days before (the day and later changed)", bool(same), f"${base[d]:.1f} on {d.date()}")
    return ok & _result("f5 floor positive control: the 28 days before changed", bool(moved))


def locked_offers(n: int = 1000, seed: int = 7, h: float = 50.0) -> bool:
    """g5 (round 5, section 12.7) on random days and states: with a lock of 3 steps, a step's offer depends on the
    stored energy at submission only (the stored energy after it changed, never below the least possible: the same
    offer; changed at submission: a different offer on some, the positive control); with the ceiling no segment is
    offered above $1,000."""
    z = vl.load(vl.build("median", 40.0, hurdle=h))
    rng = np.random.default_rng(seed)
    bat = bt.Q2B_ASSET.battery
    eta, P, cost = bat.eta, bat.power_mw, bt.Q2B_ASSET.wear_per_mwh + h
    dw, drain = dp.decision_wear(bt.Q2B_ASSET, bt.PREFS), bat.power_mw * dp.DT5 / bat.eta
    changed = moved = above = 0
    for _ in range(n):
        i, t, j = int(rng.integers(0, len(z["level"]))), int(rng.integers(1, dp.N)), int(rng.integers(0, dp.STEPS))
        i5 = t * dp.STEPS + j
        b0 = bd.LadderBids(z["edges"][i], z["marg"][i], float(z["level"][i]), np.zeros(dp.N), 0.0, cost=cost, lock=3,
                           lock_drain=drain, offer_cap=1000.0)
        seen, price = float(rng.uniform(0, 400)), float(rng.uniform(150, 1100))
        hist = rng.uniform(5.0, 40.0, i5 + 1)
        # physically possible: within the lock the battery cannot draw more than lock x drain from what it had
        hist[i5 - 2:] = np.maximum(hist[i5 - 2:], hist[i5 - 3] - 3 * drain)

        def offer(h_):
            b = replace(b0)
            for k in range(i5 + 1):
                b.observe(k, np.array([]), float(h_[k]))
            return b.sell(t, float(h_[i5]), 0.0, P, price, seen, eta, 1.2, dw, 150.0)

        first = offer(hist)
        later = hist.copy()
        low = max(0.0, hist[i5 - 3] - 3 * drain)
        later[i5 - 2:] = rng.uniform(low, 40.0, len(later[i5 - 2:]))
        changed += not np.allclose(first, offer(later), rtol=0, atol=1e-12)
        sub = hist.copy()
        sub[i5 - 3] = rng.uniform(5.0, 40.0)
        moved += not np.allclose(first, offer(sub), rtol=0, atol=1e-12)
        thr = b0.thresholds(t, seen, eta, 1.2, dw, 150.0)
        above += int(((thr > 1000.0) & np.isfinite(thr)).any())
    ok = _result("g5 offers depend on the stored energy at submission only", changed == 0, f"{changed} of {n}")
    ok &= _result("g5 positive control: the stored energy at submission moves the offer", moved > 0, f"{moved} of {n}")
    return ok & _result("g5 no segment offered above the ceiling", above == 0, f"{above} of {n}")


def buy_caps(step: str) -> bool:
    """Round 5 (section 12.6): in a run with a buy side, no interval where the battery bought beyond its plan ends with
    the meter above both its cap and the month's earlier highest (a buy must not raise the month's peak)."""
    log = pd.read_parquet(PROCESSED / f"v2_{step}_log.parquet")
    if "bid_mwh" not in log:
        return True
    ok = True
    for name, x in log.dropna(subset=["bid_mwh"]).groupby("strategy"):
        x = x.assign(day=pd.to_datetime(x["day"])).sort_values(["day", "slot"])
        n = 0
        for _, g in x.groupby(x["day"].dt.strftime("%Y-%m")):
            meter = g["meter_mw"].to_numpy()
            before = np.maximum.accumulate(np.r_[0.0, meter[:-1]])
            n += int(((g["bid_mwh"].to_numpy() > 1e-9) & (meter > np.maximum(g["cap_mw"].to_numpy(), before) + 1e-6)).sum())
        ok &= _result(f"{step} {name}: buying never raises the meter above its cap and the month's highest", n == 0,
                      f"{n} intervals")
    return ok


def same_steps(pairs=(("t5", "b4", "rw50+state"), ("c5", "t5", "rw50+state t5"), ("e5", "c5", "rw50+state t5"),
                      ("f5", "e5", "rw50+state t5+roll"), ("g5", "e5", "rw50+state t5+roll"),
                      ("f5x", "e5", "rw50+state t5+roll"), ("b3x", "b2", "rw50")), tol: float = 1e-9) -> bool:
    """Round 5: a step's base is the same run, day for day, as in the step that chose it: within tol, the drift between
    the environments the two runs were made in (round 5 runs in another session than round 4: up to 1.3e-11; in one
    environment the base is bit for bit the same before and after the round's code, v2_plan.md section 6)."""
    ok = True
    for step, earlier, name in pairs:
        p, q = bt.REPORTS / f"v2_{step}_daily.csv", bt.REPORTS / f"v2_{earlier}_daily.csv"
        if not (p.exists() and q.exists()):
            continue
        x, y = (pd.read_csv(f) for f in (q, p))
        x, y = (f[f["strategy"] == name].reset_index(drop=True) for f in (x, y))
        num = x.select_dtypes("number").columns.intersection(y.select_dtypes("number").columns)
        gap = float((x[num] - y[num]).abs().max().max()) if len(x) == len(y) and len(y) else np.inf
        ok &= _result(f"{name} in step {step} = in step {earlier} (within {tol:g})", gap <= tol,
                      f"{len(y)} days, {len(num)} columns, largest difference {gap:.2g}")
    return ok


def same_base() -> bool:
    """rel+ladder is the same run, day for day, in every later step (each later step's base): exactly equal to step
    r3's, and within 1e-9 of step s3's, whose F1 rerun (2026-10-22) differs by up to 6.4e-12, the environment drift
    of findings section 24.1."""
    ok = True

    def rel(step):
        p = bt.REPORTS / f"v2_{step}_daily.csv"
        if not p.exists():
            return None
        x = pd.read_csv(p)
        return x[x["strategy"] == vb.WIN.name].reset_index(drop=True)

    ref, s3 = rel("r3"), rel("s3")
    b2 = bt.REPORTS / "v2_b2_daily.csv"
    for later in ("b3", "b4"):  # their base is B2's winner, with the new feature off
        p = bt.REPORTS / f"v2_{later}_daily.csv"
        if b2.exists() and p.exists():
            x, y = (pd.read_csv(f) for f in (b2, p))
            x, y = (f[f["strategy"] == vb.B2_WIN.name].reset_index(drop=True) for f in (x, y))
            num = x.select_dtypes("number").columns.intersection(y.select_dtypes("number").columns)
            ok &= _result(f"{vb.B2_WIN.name} in step {later} = in step b2", x[num].equals(y[num]),
                          f"{len(y)} days, {len(num)} columns")
    for step in ("r2", "b2", "b3", "b4"):
        x = rel(step)
        if x is None or x.empty:  # a step without rel+ladder (b3, b4 run on later bases)
            continue
        num = ref.select_dtypes("number").columns.intersection(x.select_dtypes("number").columns)  # columns of both runs
        ok &= _result(f"rel+ladder in step {step} = in step r3", ref[num].equals(x[num]), f"{len(x)} days, {len(num)} columns")
        num = s3.select_dtypes("number").columns.intersection(x.select_dtypes("number").columns)
        gap = float((s3[num] - x[num]).abs().max().max())
        ok &= _result(f"rel+ladder in step {step} within 1e-9 of step s3", gap <= 1e-9, f"largest difference {gap:.2g}")
    return ok


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    ok = True
    if "--timing" in argv:
        ok &= chooser_timing()
    if "--same" in argv:
        ok &= same_base() & same_steps()
    if "--t5" in argv:
        ok &= terminal_timing()
    if "--c5" in argv:
        ok &= regime_values() & regime_timing()
    if "--f5" in argv:
        ok &= full_bids() & buy_caps("f5")
    if "--g5" in argv:
        ok &= locked_offers()
    if "--f5x" in argv:  # section 13: the buy side within the room; the meter check must now pass
        ok &= full_bids() & buy_caps("f5x") & buy_caps("b3x")
    if "--parity" in argv:
        days, _, aux = bt.q2b_inputs(smoke=True)
        ok &= parity_in_process(days, aux)
        if "--full" in argv:
            ok &= parity_full(bt.load_days("0900"), aux)
    if "--runs" in argv:
        for step in argv[argv.index("--runs") + 1].split(","):
            ok &= plans(step) & execution(step) & accounts(step)
        ok &= offers()
        runs = argv[argv.index("--runs") + 1].split(",")
        if "b3" in runs:
            ok &= buys()
        if "b4" in runs:
            ok &= states()
    print("ALL CHECKS PASS" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
