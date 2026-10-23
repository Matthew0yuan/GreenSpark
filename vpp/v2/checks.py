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
        ok &= same_base()
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
