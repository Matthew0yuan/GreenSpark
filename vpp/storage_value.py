"""Step 1 of reports/q2b_plan.md, section 10: what a MWh of stored energy is worth, by stochastic dynamic
programming (the constrained MDP layer), as the plan's end-of-day value ("vt") and for layered offers ("vt+bid").
Settings and the pass test were written into the plan before the full run.

One table per calendar month, from the WINDOW days of 30-min prices (RTP) before the month, never after TRAIN_END
(the months from the cutoff on all use the table cut at TRAIN_END):
  state     day of the week, half-hour t of the trading day (0..48; 48 is the next day's 0), stored energy s
            (0..E MWh on a GRID, interpolated between) and price state b: which of the BIN_Q quantile bins of
            that slot's past prices the half-hour's price falls in
  decision  how much to charge or discharge in the half-hour (STEP MWh of stored energy), knowing its price
            state, as a limit order does: the offer is in, the price decides
  money     the bin's mean price on the energy bought or sold, less wear + hurdle per MWh sold, as the plan's
            decisions are charged (so keeping energy and selling it are charged alike)
  limits    the battery's power and energy; charging keeps the meter within the CMD and the import limit at the
            factory's expected load for the month (no margin); where that load is above the CMD the battery must
            discharge the excess, each MWh short costing SHORT_PENALTY (the RT7 excess charge per MW-month;
            steering only). Left out: FCESS, IRCR and the cycle limit.
  next      the next half-hour's price state, from transitions counted between consecutive half-hours (07:30 to
            the next 08:00 too), plus one pseudo-count spread over the next slot's bins by their frequency
Solved by relative value iteration over the week until no marginal value moves more than TOL $/MWh (at most
MAX_WEEKS weeks); marginal values are then made non-increasing in stored energy (pool-adjacent violators).

Uses: "vt" values the energy stored at the end of each plan (08:00 the next day) by the expected curve at that
time (vpp.dispatch.plan's concave terminal value); "vt+bid" also replaces the value offer rule by layered offers
(DayBids): each 5 minutes the MWh in segment k sells if the price reaches max(k_min, alpha x its marginal value /
eta + wear + hurdle), the marginal value being the table's at the end of the half-hour given the price state of
the latest 5-min price seen.

Pass (validation, before vpp.evaluate.CUTOFF; a day's money is its ledger plus its month's bill saving per day;
7-day block bootstrap): vt or vt+bid earns more than value, 95% interval above 0 (P1), and its mean is at least
that of keeping 20 MWh for spikes every day (R=20, P2). The test is printed once, for every strategy.

Run:  python -m vpp.storage_value [--smoke] [--rebuild]
"""

import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

from vpp import aemo_correction as ac
from vpp import backtest as bt
from vpp import contracts as ct
from vpp import dispatch as dp
from vpp import site as st
from vpp import site_bill as sb
from vpp import stochastic as sto
from vpp.data import PROCESSED
from vpp.evaluate import CUTOFF, TRAIN_END

REPORTS = bt.REPORTS
OUT = PROCESSED / "storage_value.npz"
BIN_Q = (0.0, 0.25, 0.5, 0.7, 0.85, 0.93, 0.97, 0.99, 1.0)  # price-state bins, quantiles of each slot's prices [fixed]
B = len(BIN_Q) - 1
WINDOW = pd.Timedelta(days=365)  # [fixed]
GRID = 1.0                        # MWh between stored-energy points [fixed]
STEP = 0.25                       # MWh of stored energy between decisions [fixed]
CMD = 9.0                         # MW, m1's CMD (chosen on validation, q2b_main)
SHORT_PENALTY = sb.RT7(sb.cmd_kva(CMD)).enuc_per_mw()  # $ per MWh the battery cannot shave above the CMD (steering)
TOL, MAX_WEEKS = 0.01, 60
MONTHS = pd.period_range("2025-04", "2026-10", freq="M")
STRATS = {"value": bt.VALUE, "vt": f"{bt.VALUE}|vt", "vt+bid": f"{bt.VALUE}|vt|bid", "R=20": f"{bt.VALUE}|res=20"}
CANDIDATES = ("vt", "vt+bid")
REF = "R=20"


def decreasing(y: np.ndarray) -> np.ndarray:
    """The closest non-increasing sequence to y (least squares; pool-adjacent violators)."""
    vals, wts = [], []
    for v in y:
        vals.append(float(v))
        wts.append(1)
        while len(vals) > 1 and vals[-2] < vals[-1]:
            v2, w2 = vals.pop(), wts.pop()
            vals[-1] = (vals[-1] * wts[-1] + v2 * w2) / (wts[-1] + w2)
            wts[-1] += w2
    return np.repeat(vals, wts)


def price_states(prices: pd.DataFrame, month: pd.Period, level: pd.Series | None = None) -> dict:
    """Bins, bin means, transitions and the 08:00 bin frequencies from the WINDOW days before the month (at most
    TRAIN_END). prices: (trading day x slot 0..47) 30-min prices. level: a price level per trading day (vpp.v2.value);
    the states are then of each half-hour's price less its day's level, and the bin means are relative to it."""
    end = min(month.start_time, CUTOFF)
    x = prices[(prices.index >= end - WINDOW) & (prices.index < end) & (prices.index <= TRAIN_END)].dropna()
    if level is not None:
        x = x.sub(level.reindex(x.index), axis=0).dropna()
    X, days = x.to_numpy(), x.index
    edges = np.quantile(X, BIN_Q, axis=0).T  # (48, B + 1)
    bins = np.stack([np.searchsorted(edges[t, 1:-1], X[:, t], side="right") for t in range(dp.N)], axis=1)
    means = np.array([[X[bins[:, t] == b, t].mean() if (bins[:, t] == b).any() else edges[t, b:b + 2].mean()
                       for b in range(B)] for t in range(dp.N)])
    freq = np.stack([np.bincount(bins[:, t], minlength=B) / len(X) for t in range(dp.N)])
    counts = np.zeros((dp.N, B, B))
    for t in range(dp.N - 1):
        np.add.at(counts[t], (bins[:, t], bins[:, t + 1]), 1)
    nxt = np.flatnonzero((days[1:] - days[:-1]) == pd.Timedelta(days=1))  # day i followed by day i + 1
    np.add.at(counts[dp.N - 1], (bins[nxt, dp.N - 1], bins[nxt + 1, 0]), 1)
    trans = np.empty_like(counts)
    for t in range(dp.N):
        follow = freq[(t + 1) % dp.N]
        trans[t] = (counts[t] + follow) / (counts[t].sum(axis=1, keepdims=True) + 1.0)
    return {"edges": edges, "means": means, "trans": trans, "pi0": freq[0], "days": len(X)}


def load_profiles(month: pd.Period, site: st.Site = st.FACTORY) -> np.ndarray:
    """(day of the week x slot) the factory's expected load (MW) in the month."""
    out = np.zeros((7, dp.N))
    for k in range(7):
        day = month.start_time + pd.Timedelta(days=k)
        ts = pd.date_range(day + bt.H, periods=dp.N, freq="30min")
        out[day.dayofweek] = st.example_load(ts)[0] * site.load_scale
    return out


def solve(ps: dict, load: np.ndarray, asset=bt.Q2B_ASSET, prefs=bt.PREFS, site: st.Site = st.FACTORY,
          short_penalty: float = SHORT_PENALTY) -> tuple[np.ndarray, int]:
    """V[day of the week, slot 0..48, stored-energy point, price state] (relative values) and the weeks it took.
    short_penalty: $ per MWh the battery cannot shave above the CMD (0: shaving left to the caller, vpp.v2)."""
    bat = asset.battery
    E, P, eta = bat.energy_mwh, bat.power_mw, bat.eta
    grid = np.arange(0.0, E + 1e-9, GRID)
    dw = asset.wear_per_mwh + prefs.hurdle_per_mwh
    V = np.zeros((7, dp.N + 1, len(grid), B))
    acts = {}
    for dow in range(7):
        for t in range(dp.N):
            room = max(0.0, min(site.import_limit_mw, CMD) - load[dow, t])
            cmax, dmax = eta * min(P, room) * dp.DT, P * dp.DT / eta
            a = np.unique(np.r_[np.arange(-dmax, cmax, STEP), -dmax, 0.0, cmax])
            deliver, buy = np.clip(-a, 0.0, None) * eta, np.clip(a, 0.0, None) / eta
            short = np.clip(max(0.0, load[dow, t] - CMD) * dp.DT - deliver, 0.0, None)
            x = grid[:, None] + a[None, :]
            acts[dow, t] = (x, (x >= -1e-9) & (x <= E + 1e-9),
                            ps["means"][t][:, None] * (deliver - buy)[None, :] - dw * deliver - short_penalty * short)
    prev = None
    for week in range(1, MAX_WEEKS + 1):
        V[6, dp.N] = V[0, 0]
        for dow in reversed(range(7)):
            if dow < 6:
                V[dow, dp.N] = V[dow + 1, 0]
            for t in reversed(range(dp.N)):
                ev = V[dow, t + 1] @ ps["trans"][t].T  # (points, b): expected next value given this state b
                x, ok, reward = acts[dow, t]
                xc = np.clip(x, 0.0, E)
                for b in range(B):
                    tot = np.where(ok, reward[b][None, :] + np.interp(xc, grid, ev[:, b]), -np.inf)
                    V[dow, t, :, b] = tot.max(axis=1)
        V -= V[0, 0].mean()
        marg = np.diff(V, axis=2)
        if prev is not None and np.abs(marg - prev).max() < TOL:
            return V, week
        prev = marg
    return V, MAX_WEEKS


@dataclass
class DayBids:
    """One day's layered offers: edges (48, B + 1) the month's price-state bins; marg (48, B, segments) $ per MWh of
    stored energy in each GRID segment at the end of slot t, given slot t's price state."""
    edges: np.ndarray
    marg: np.ndarray

    def sell(self, t: int, s: float, base: float, sellable: float, price: float, seen: float, eta: float,
             alpha: float, dw: float, k_min: float) -> tuple[float, float]:
        """(MW to discharge this 5-min step, the threshold of the last MWh sold - or of the next, if none):
        beyond the plan's base MW, the MWh whose threshold max(k_min, alpha x value / eta + dw) the price
        reaches, from the top of the stored energy down; at most sellable MW."""
        b = int(np.clip(np.searchsorted(self.edges[t, 1:-1], seen, side="right"), 0, B - 1))
        thr = np.maximum(k_min, alpha * self.marg[t, b] / eta + dw)  # non-increasing in the segment
        x_plan = s - base * dp.DT5 / eta
        x_end = max(0.0, s - sellable * dp.DT5 / eta)
        ok = np.flatnonzero(thr <= price)
        if not len(ok) or x_plan <= x_end + 1e-12:
            return base, float(thr[min(len(thr) - 1, max(0, int(np.ceil(x_plan / GRID - 1e-9)) - 1))])
        x_star = min(x_plan, max(x_end, ok[0] * GRID))  # sell down to the lowest segment the price clears
        target = max(base, min(sellable, (s - x_star) * eta / dp.DT5))
        return target, float(thr[min(len(thr) - 1, int(np.floor(x_star / GRID + 1e-9)))])


@dataclass
class Tables:
    """Every month's table, as the dispatcher uses it."""
    months: tuple
    curves: np.ndarray  # (months, day of the week, segments): $ per MWh at the end of a trading day starting that weekday
    edges: np.ndarray   # (months, 48, B + 1)
    marg: np.ndarray    # (months, day of the week, 48, B, segments)

    def _m(self, day: pd.Timestamp) -> int:
        return int(np.clip(np.searchsorted(self.months, day.strftime("%Y-%m"), side="right") - 1, 0, len(self.months) - 1))

    def terminal(self, day: pd.Timestamp) -> np.ndarray:
        return self.curves[self._m(day), day.dayofweek]

    def bids(self, day: pd.Timestamp) -> DayBids:
        m = self._m(day)
        return DayBids(self.edges[m], self.marg[m, day.dayofweek])


def build(rebuild: bool = False) -> tuple[Tables, pd.DataFrame]:
    """Every month's table (cached in OUT) and a summary per month."""
    summary_file = REPORTS / "storage_value_tables.csv"
    if OUT.exists() and summary_file.exists() and not rebuild:
        z = np.load(OUT, allow_pickle=False)
        return Tables(tuple(str(m) for m in z["months"]), z["curves"], z["edges"], z["marg"]), pd.read_csv(summary_file)
    prices = ac._by_day_slot(pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["energy"])["energy"])
    curves, edges, margs, rows = [], [], [], []
    for month in MONTHS:
        ps = price_states(prices, month)
        V, weeks = solve(ps, load_profiles(month))
        ev0 = V[:, dp.N] @ ps["pi0"]  # (dow, points): expected value at the next 08:00
        raw = np.diff(ev0, axis=1) / GRID
        curve = np.clip(np.array([decreasing(r) for r in raw]), 0.0, None)
        ev = np.einsum("dtpc,tbc->dtbp", V[:, 1:], ps["trans"])  # (dow, t, b, points): end-of-slot value given b
        m_raw = np.diff(ev, axis=3) / GRID
        marg = np.clip(np.apply_along_axis(decreasing, 3, m_raw), 0.0, None)
        curves.append(curve)
        edges.append(ps["edges"])
        margs.append(marg)
        rows.append({"month": str(month), "price_days": ps["days"], "weeks": weeks,
                     "pav_shift_max": float(np.abs(marg - m_raw).max()), "pav_shift_mean": float(np.abs(marg - m_raw).mean()),
                     **{f"wkday_{s}": float(curve[2, s]) for s in (0, 5, 10, 20, 30, 39)},
                     **{f"sat_{s}": float(curve[5, s]) for s in (0, 5, 10, 20, 30, 39)}})
        print(f"{month}: {ps['days']} days, {weeks} weeks; 08:00 value after a Wednesday, $/MWh at 0/10/20/30/39 MWh: "
              + " / ".join(f"{curve[2, s]:.0f}" for s in (0, 10, 20, 30, 39)))
    summary = pd.DataFrame(rows)
    np.savez_compressed(OUT, months=np.array([str(m) for m in MONTHS]), curves=np.array(curves), edges=np.array(edges),
                        marg=np.array(margs))
    summary.to_csv(summary_file, index=False)
    return Tables(tuple(str(m) for m in MONTHS), np.array(curves), np.array(edges), np.array(margs)), summary


def diagnostics(logs: dict) -> pd.DataFrame:
    """Per strategy and period: mean stored energy, stored at 08:00 (start) and 16:00, energy charged 20:00-08:00."""
    rows = []
    for (_, name), by_day in logs.items():
        for day, lg in by_day.items():
            rows.append({"strategy": name, "period": bt.period_of([day])[0], "soc_mean": lg["soc"].mean(),
                         "soc_0800": lg["soc_start"].iloc[0], "soc_1600": lg["soc"].iloc[15],
                         "night_charge_mwh": -lg["energy_mwh"].iloc[24:].clip(upper=0).sum(),
                         "offer_mwh": lg["offer_mwh"].sum()})
    return pd.DataFrame(rows).groupby(["period", "strategy"]).mean()


def compare(money: pd.DataFrame, ledger: pd.DataFrame, bills: pd.DataFrame) -> pd.DataFrame:
    """Per period and strategy: $/day, against value and against R=20 (block-bootstrap 95% intervals), the market
    part (ledger) against value, and the bill's months better / worse than value's."""
    rows = []
    period = pd.Series(bt.period_of(money.index), index=money.index)
    led = ledger.pivot_table(index="day", columns="strategy", values="ledger_total")
    bm = bills.pivot_table(index="month", columns="strategy", values="saving")
    for p in bt.PERIODS:
        mp, lp = money[period == p], led[period.reindex(led.index) == p]
        bp = bm[bt.period_of(bm.index.to_timestamp()) == p]
        for label, name in STRATS.items():
            if name not in mp or not len(mp):
                continue
            row = {"period": p, "strategy": label, "days": len(mp), "per_day": mp[name].mean()}
            for ref, col in (("value", STRATS["value"]), (REF, STRATS[REF])):
                if label == ref or col not in mp:
                    continue
                diff = (mp[name] - mp[col]).to_numpy()
                row[f"vs_{ref}"], (row[f"vs_{ref}_lo"], row[f"vs_{ref}_hi"]) = diff.mean(), sto.block_ci(diff)
            if label != "value":
                diff = (lp[name] - lp[STRATS["value"]]).to_numpy()
                row["market_vs_value"], (row["market_lo"], row["market_hi"]) = diff.mean(), sto.block_ci(diff)
                d = bp[name] - bp[STRATS["value"]]
                row["bill_vs_value"] = (mp[name] - mp[STRATS["value"]]).mean() - row["market_vs_value"]
                row["bill_months_better"], row["bill_months_worse"] = int((d > 1.0).sum()), int((d < -1.0).sum())
            rows.append(row)
    return pd.DataFrame(rows).set_index(["period", "strategy"])


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    tables, _ = build("--rebuild" in argv)
    days = bt.load_days("0900")
    smoke = "--smoke" in argv
    if smoke:
        days = [d for d in days if d["day"].year == 2026 and d["day"].month == 1][:12]
    aux = bt.q2b_aux()
    aux["storage"] = tables
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    m1 = bt._sc("m1", ct.Route("m1", cmd_mw=CMD, ircr="dynamic"), tuple(STRATS.values()))
    daily, logs, meters = bt.run_tasks(bt.q2b_tasks([m1], keep=("m1",)), days, aux)
    ledger = bt.q2b_ledger(daily, [m1])
    bills, ircr = bt.q2b_bills(meters, days, [m1], cmd_nb)
    money = sto.daily_money(ledger, bills)
    table = compare(money, ledger, bills)
    diag = diagnostics(logs)
    tag = "_smoke" if smoke else ""
    ledger.to_csv(REPORTS / f"storage_value_daily{tag}.csv", index=False)
    bills.to_csv(REPORTS / f"storage_value_bills{tag}.csv", index=False)
    table.to_csv(REPORTS / f"storage_value_result{tag}.csv")
    diag.to_csv(REPORTS / f"storage_value_diag{tag}.csv")
    pd.set_option("display.width", 250)
    print(f"\n{len(days)} days; $/day on m1; 95% intervals by a {sto.BLOCK}-day block bootstrap\n{table.round(1).T.to_string()}\n")
    print(f"=== stored energy (MWh) and offers\n{diag.round(2).to_string()}\n")
    print(f"=== IRCR, {bt.IRCR_SEASON}-{bt.IRCR_SEASON - 1999} Hot Season ($/year)\n"
          f"{ircr[['strategy', 'ircr_mw', 'saving_per_year', 'peak_days_covered']].round(2).to_string(index=False)}\n")
    if "validation" in table.index.get_level_values(0):
        val = table.loc["validation"]
        passing = [c for c in CANDIDATES if c in val.index and val.loc[c, "vs_value_lo"] > 0 and val.loc[c, "vs_R=20"] >= 0]
        best = max(passing, key=lambda c: val.loc[c, "per_day"]) if passing else None
        print(f"P1 (vs value, lower bound > 0): {', '.join(c for c in CANDIDATES if c in val.index and val.loc[c, 'vs_value_lo'] > 0) or 'none'}; "
              f"P2 (mean >= R=20): {', '.join(c for c in CANDIDATES if c in val.index and val.loc[c, 'vs_R=20'] >= 0) or 'none'}; "
              f"pass: {best or 'none'}")


if __name__ == "__main__":
    main()
