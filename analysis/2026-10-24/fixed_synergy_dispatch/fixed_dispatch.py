"""Customer battery calls under the fixed Synergy-like contract (PLAN.md, written before this code).

Compares, for the same 1,000 enrolled homes, the same payments (70c/kWh of event net export, standby offset at the
retail tariff), the same 30 calls per contract period and the same billing:
  none       no customer calls (the Zenith-only baseline; Zenith's own battery is v2 e5, unchanged)
  reference  the existing fixed rule (vpp.policy.select_calls): call every flagged Hot Season day, first come first
             served, 2-h event centred on 18:30; its q and theta chosen on the training seasons
  paced      the new rule: a state-dependent threshold paced by calls left and days left (PLAN.md section 5)
  perfect    hindsight reference (actual peak intervals and prices; greedy, not a proven optimum)
under three ledgers (L1 current rule, L2 net-export assumption, L3 50% share assumption). Reported results are settled
on an exact interval-by-interval run of the battery through each whole contract period (calls interact through the
state of charge); the precomputed independent-call values are used only to train the rules.

Production code (vpp/) is imported read-only.

Run:  .venv/Scripts/python.exe analysis/2026-10-24/fixed_synergy_dispatch/fixed_dispatch.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from vpp import contracts as ct  # noqa: E402
from vpp import site_bill as sb  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402
from vpp.evaluate import CUTOFF, TRAIN_END  # noqa: E402
from vpp.policy import policy_simulator as ps  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
LAST = str(ps.LAST_DAY.date())
N = ps.N
DT = ps.DT

# ------------------------------------------------------------------------------------------- fixed inputs (PLAN 1)
HH = ps.Household()                 # 13.5 kWh usable, 5 kW, 6.6 kWp, 18 kWh/day [assumed archetype]
HOMES = 1000                        # enrolled homes, fixed [assumed]
RESPONSE = 0.9                      # share of enrolled batteries that respond [assumed]
RESERVE = 0.20                      # customer reserve [assumed, exogenous]
STANDBY_H = 2.0                     # experiment's standby (contract: "generally, approximately 2 hours")
MAX_EVENT_H = 4.0                   # contract: Maximum Activation Event Duration
CAP = 30                            # calls per contract 12-month period [contract]
PAY = 0.70                          # $/kWh of event net export, capped at capacity [contract]
OFFSET = ps.RETAIL                  # $/kWh of extra standby import [contract: the customer's retail tariff]
OPS_EVENT = 0.05                    # Zenith $ per responding home per call [assumed]
PLATFORM = 25.0                     # Zenith $ per enrolled home per year, reported separately [assumed]
WINDOW = (11.0, 22.0)               # experiment: events start from 11:00 and end by 22:00
REF_WINDOW = (15.0, 21.0)           # the existing reference rule's window
LENGTHS = (1.5, 2.0, 3.0)           # event lengths the new rule may choose (h)
KAPPAS = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
SEASONS = ps.SEASONS                # Hot Seasons 2023-24 .. 2025-26
PERIOD_START = "07-01"              # experiment: each contract 12-month period starts 1 July (not a Synergy rule)


@dataclass(frozen=True)
class Ledger:
    name: str
    energy: bool                    # the wholesale value of the change in net export reaches Zenith
    capacity: str                   # "floor": IRCR with net export as 0; "net": export counts negative
    share: float                    # share of the IRCR value Zenith receives


LEDGERS = {
    "L1": Ledger("L1 current rule: export counts 0 (assumes Zenith is the retailer)", True, "floor", 1.0),
    "L2": Ledger("L2 net-export assumption (2027 Peak IRCR; portfolio floor assumed not binding)", True, "net", 1.0),
    "L3": Ledger("L3 share assumption: Synergy keeps energy, passes 50% of the IRCR value", False, "net", 0.5),
}


def obligations(led: Ledger) -> ps.Obligations:
    return ps.Obligations(led.name, energy_to_zenith=led.energy, capacity=led.capacity, capacity_share=led.share,
                          ops_per_customer=0.0, ops_per_event=OPS_EVENT)


PART = ps.Participation(availability=RESPONSE)


def policy(event_h: float, window=WINDOW) -> ps.Policy:
    return ps.Policy(name=f"synergy-like {event_h}h", pay_per_kwh=PAY, max_events=CAP, event_h=event_h,
                     standby_h=STANDBY_H, max_control_h=STANDBY_H + MAX_EVENT_H, reserve_soc=RESERVE, notice_h=0.0,
                     window=window, offset_rate=OFFSET)


def fam_key(event_h: float, window=WINDOW) -> tuple:
    return (event_h, STANDBY_H, RESERVE, HH, tuple(window), LAST)


# ----------------------------------------------------------------------------------------------- 09:00 information
@lru_cache(maxsize=1)
def peak_inputs() -> dict:
    """Daily inputs of the capacity calls, all public by 09:00 on the day (PLAN 5.2a, 5.2c):
    f0 same-day forecast of the day's highest energy requirement (AEMO 08:00 pre-dispatch), f1 the day-before one,
    actual daily highest requirement (known once the trading day ends at 08:00 the next morning), and the forecast
    peak slot in 16:00-21:00 from the same 08:00 run."""
    m = ps.load_market()
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["req_energy_mw"])
    actual = sb.daily_max(df30["req_energy_mw"]).reindex(m.days)
    f0 = sb.peak_day_predictor(0).reindex(m.days)
    f1 = sb.peak_day_predictor(1).reindex(m.days)
    t = pd.read_parquet(PROCESSED / "spike_table.parquet",
                        columns=["days_ahead", "target_date", "target_time", "pd_req", "src_pd_available_at", "issue_time"])
    t = t[t["days_ahead"] == 0]
    slot = ((t["target_time"] - t["target_date"] - pd.Timedelta(hours=8)) / pd.Timedelta(minutes=30)).astype(int)
    t = t.assign(slot=slot)
    eve = t[(t["slot"] >= 16) & (t["slot"] < 26)].dropna(subset=["pd_req"])
    peak_slot = eve.loc[eve.groupby("target_date")["pd_req"].idxmax()].set_index("target_date")["slot"].reindex(m.days)
    late = (t["src_pd_available_at"] > t["issue_time"]).sum()
    return {"f0": f0.to_numpy(), "f1": f1.to_numpy(), "actual": actual.to_numpy(), "peak_slot": peak_slot.to_numpy(),
            "late_rows": int(late)}


def season_days(m, y: int) -> np.ndarray:
    return np.flatnonzero(ps.season_of(m) == y)


def margin_series(y: int, f0: np.ndarray, f1: np.ndarray, actual: np.ndarray, season: np.ndarray) -> np.ndarray:
    """m_d = f_d - K_d for the Hot Season starting in December of y (NaN elsewhere). K_d: the 4th-highest actual daily
    maximum of the season's completed days (those before d); with fewer than 4, the previous season's 4th-highest (a
    prior), or no threshold (+inf margin) when there is no previous season in the data."""
    out = np.full(len(f0), np.nan)
    days = np.flatnonzero(season == y)
    prev = np.flatnonzero(season == y - 1)
    pa = actual[prev][~np.isnan(actual[prev])]
    prior = np.sort(pa)[-4] if len(pa) >= 4 else -np.inf
    for n, d in enumerate(days):
        known = actual[days[:n]]
        known = known[~np.isnan(known)]
        k = np.sort(known)[-4] if len(known) >= 4 else prior
        f = f0[d] if not np.isnan(f0[d]) else f1[d]
        out[d] = f - k
    return out


@lru_cache(maxsize=8)
def margins(y: int) -> np.ndarray:
    m = ps.load_market()
    x = peak_inputs()
    return margin_series(y, x["f0"], x["f1"], x["actual"], ps.season_of(m))


@lru_cache(maxsize=64)
def tolerance(seasons: tuple, q: float) -> float:
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["req_energy_mw"])
    actual = sb.daily_max(df30["req_energy_mw"])
    return sb.forecast_tolerance(sb.peak_day_predictor(0), actual, list(seasons), q)


@lru_cache(maxsize=256)
def causal_flags(q: float, season: int, tol_seasons: tuple) -> np.ndarray:
    """The v2 peak-day flags (same day or day before) of one season, with the tolerance from tol_seasons only."""
    m = ps.load_market()
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["req_energy_mw"])
    actual = sb.daily_max(df30["req_energy_mw"])
    p0, p1 = sb.peak_day_predictor(0), sb.peak_day_predictor(1)
    f0 = sb.dynamic_flags(p0, actual, sb.forecast_tolerance(p0, actual, list(tol_seasons), q), 1)
    f1 = sb.dynamic_flags(p1, actual, sb.forecast_tolerance(p1, actual, list(tol_seasons), q), 2)
    days = [d for d in f0.union(f1) if sb.hot_season(pd.DatetimeIndex([d]))[0] == season]
    return m.days.isin(days)


def tol_seasons_for(season: int, train: tuple) -> tuple:
    """Seasons whose forecast errors set a season's flag tolerance: the training seasons for an evaluated season; the
    other training seasons (or itself, when alone) for a training season."""
    if season not in train:
        return train
    others = tuple(s for s in train if s != season)
    return others or (season,)


# ------------------------------------------------------------------------------------------------- contract periods
def periods(m) -> dict:
    """Contract 12-month period (starting 1 July, the experiment's choice) -> day indices."""
    return ps.contract_years(m)


# ---------------------------------------------------------------------------------------------------- the rules
@dataclass(frozen=True)
class PacedRule:
    ircr: bool = True
    kappa: float = 1.0
    event_h: float = 2.0
    anchor: str = "fixed"           # fixed (centred on 18:30) | forecast (centred on the forecast peak slot)
    theta_e: float = np.inf         # energy calls: 09:00 expected margin ($/responding home) at least this


@dataclass(frozen=True)
class RefRule:
    q: float = 0.9
    theta_e: float = np.inf


@dataclass
class Plan:
    """Chosen calls: day index, first event slot of the day (0..47), event length (h), kind."""
    day: np.ndarray
    start: np.ndarray
    event_h: np.ndarray
    kind: np.ndarray

    @staticmethod
    def empty() -> "Plan":
        return Plan(np.array([], int), np.array([], int), np.array([], float), np.array([], object))


def ircr_start(event_h: float, anchor: str, peak_slot) -> int:
    n_ev = int(round(2 * event_h))
    if anchor == "forecast" and not np.isnan(peak_slot):
        s = int(peak_slot) - (n_ev - 1) // 2
    else:
        s = ps.ircr_start(event_h)
    lo, hi = int(2 * (WINDOW[0] - 8)), int(2 * (WINDOW[1] - 8)) - n_ev
    return int(min(max(s, lo), hi))


class Pacer:
    """The paced threshold of PLAN 5.2b, from the training seasons' margins aligned by days to the season's end."""

    def __init__(self, train: tuple):
        m = ps.load_market()
        self.by_season = []
        for y in train:
            days = season_days(m, y)
            mg = margins(y)[days]
            to_end = (days[-1] - days)
            self.by_season.append((to_end, mg))
        self.n_train = len(train)
        self.floor = -tolerance(tuple(train), 0.9)

    def threshold(self, days_to_end: int, calls_left: int, kappa: float) -> float:
        pool = np.concatenate([mg[(te <= days_to_end) & ~np.isnan(mg)] for te, mg in self.by_season])
        k = int(np.ceil(kappa * calls_left * self.n_train))
        if k <= 0:
            return np.inf
        if k > len(pool):
            return self.floor
        tau = np.sort(pool)[::-1][k - 1]
        return max(tau, self.floor)

    def expected_left(self, days_to_end: int) -> float:
        """Average number of days per training season, from here to the end, with a margin at or above the floor."""
        return float(np.mean([((te <= days_to_end) & (mg >= self.floor)).sum() for te, mg in self.by_season]))


def select_paced(period_days: np.ndarray, rule: PacedRule, led: Ledger, pacer: Pacer, fc) -> Plan:
    m = ps.load_market()
    x = peak_inputs()
    season = ps.season_of(m)
    left = CAP
    out_d, out_s, out_h, out_k = [], [], [], []
    energy_on = led.energy and np.isfinite(rule.theta_e)
    if energy_on:
        key = fam_key(rule.event_h)
        fam = ps.family(*key)
        sc = ps.energy_scores(key, policy(rule.event_h), obligations(led), fc, m, np.arange(len(fam.starts)))
    hs = [d for d in period_days if season[d] >= 0]
    season_end = max(hs) if hs else None
    mg = {y: margins(y) for y in set(season[hs])} if hs else {}
    for d in period_days:
        if left <= 0:
            break
        y = season[d]
        if rule.ircr and y >= 0 and not np.isnan(mg[y][d]):
            tau = pacer.threshold(int(season_end - d), left, rule.kappa)
            if mg[y][d] >= tau:
                out_d.append(d), out_s.append(ircr_start(rule.event_h, rule.anchor, x["peak_slot"][d]))
                out_h.append(rule.event_h), out_k.append("ircr")
                left -= 1
                continue
        if energy_on and sc.best[d] >= rule.theta_e:
            need = 0.0
            if rule.ircr:
                need = min(left, pacer.expected_left(int(season_end - d)) if season_end is not None and d <= season_end else 0)
            if left - 1 >= need:
                out_d.append(d), out_s.append(int(fam.starts[sc.start[d]])), out_h.append(rule.event_h)
                out_k.append("energy")
                left -= 1
    return Plan(np.array(out_d, int), np.array(out_s, int), np.array(out_h, float), np.array(out_k, object))


def select_reference(period_days: np.ndarray, rule: RefRule, led: Ledger, train: tuple, fc) -> Plan:
    """The existing rule (vpp.policy.select_calls), with flags whose tolerance uses only the training seasons."""
    m = ps.load_market()
    season = ps.season_of(m)
    pol = policy(2.0, REF_WINDOW)
    key = fam_key(2.0, REF_WINDOW)
    fam = ps.family(*key)
    ys = sorted({int(y) for y in season[period_days] if y >= 0})
    flags = np.zeros(m.n_days, bool)
    for y in ys:
        flags |= causal_flags(rule.q, y, tol_seasons_for(y, train))
    mean_flags = float(np.mean([causal_flags(rule.q, s, tol_seasons_for(s, train)).sum() for s in train]))
    fcq = replace(fc, flags={rule.q: flags}, mean_flags={rule.q: mean_flags})
    obl = obligations(led)
    sc = ps.energy_scores(key, pol, obl, fcq, m, np.arange(len(fam.starts)))
    cr = ps.CallRule("optimised", rule.theta_e, rule.q, True)
    calls = ps.select_calls(period_days, fam, pol, cr, fcq, sc, led.capacity != "none", led.energy, m)
    return Plan(calls.day, fam.starts[calls.start].astype(int), np.full(len(calls.day), 2.0), calls.kind)


def select_perfect(period_days: np.ndarray, led: Ledger, event_h: float) -> Plan:
    """Hindsight: the season's peak days, each with the start that best covers its own peak intervals, then the days of
    highest realised energy margin above 0 (energy to Zenith only), within the cap. Greedy, not a proven optimum."""
    m = ps.load_market()
    key = fam_key(event_h)
    fam = ps.family(*key)
    peaks = ps.peak_intervals()
    season = ps.season_of(m)
    out_d, out_s, out_k = [], [], []
    ys = sorted({int(y) for y in season[period_days] if y >= 0})
    for y in ys:
        idx, slots = peaks[y]
        for d in sorted(set(idx.tolist())):
            sl = slots[idx == d]
            best, best_s = -np.inf, None
            for j, s in enumerate(fam.starts):
                net = fam.net_peak[j, d, sl - ps.PEAK[0]]
                cost = PAY * fam.paid_kwh[j, d] + OFFSET * fam.standby_kwh[j, d]
                val = -net.max() * 1000 - cost  # the worst covered interval drives the median; cost as a tie-break
                if val > best:
                    best, best_s = val, int(s)
            out_d.append(d), out_s.append(best_s), out_k.append("ircr")
    if led.energy:
        cost = PAY * fam.paid_kwh + OFFSET * fam.standby_kwh + OPS_EVENT
        margin = fam.w_actual - cost
        j = margin.argmax(axis=0)
        mg = margin[j, np.arange(m.n_days)]
        cand = [d for d in period_days[np.argsort(-mg[period_days])] if mg[d] > 0 and d not in out_d]
        for d in cand[:max(0, CAP - len(out_d))]:
            out_d.append(d), out_s.append(int(fam.starts[j[d]])), out_k.append("energy")
    o = np.argsort(out_d)
    return Plan(np.array(out_d, int)[o], np.array(out_s, int)[o], np.full(len(out_d), event_h), np.array(out_k, object)[o])


# ------------------------------------------------------------------------------- fast (independent-call) accounts
def fast_zenith(plan: Plan, period_days: np.ndarray, led: Ledger) -> float:
    """Zenith's call-dependent net per enrolled home over a period, from the precomputed independent calls (training
    only). All calls of a plan share one event length here (rules use one length)."""
    if len(plan.day) == 0:
        return 0.0
    m = ps.load_market()
    eh = float(plan.event_h[0])
    fam = ps.family(*fam_key(eh))  # every start a rule can choose is in the experiment window's family
    j = np.array([fam.index(s) for s in plan.start])
    calls = ps.Calls(plan.day, j, plan.kind)
    season = ps.season_of(m)
    ys = sorted({int(y) for y in season[period_days] if y >= 0})
    peaks = ps.peak_intervals() if led.capacity != "none" else None
    acc = ps.account(calls, fam, policy(eh), obligations(led), PART, m, ys[0] if ys else None, peaks)
    return float(acc["zenith_calls"])


# ----------------------------------------------------------------------------------- exact sequential settlement
@dataclass
class Run:
    soc: np.ndarray
    imp: np.ndarray
    exp: np.ndarray
    dis: np.ndarray
    ch: np.ndarray
    gch: np.ndarray
    modes: np.ndarray
    a: int
    z: int


def run_period(plan: Plan, period_days: np.ndarray) -> Run:
    """Run one battery through every interval of the period (to 15:00 after its last day) with the plan's calls, from
    the baseline's state; vpp.policy.step is the physics."""
    b = ps.baseline(HH)
    a = int(period_days[0]) * N
    z = min((int(period_days[-1]) + 1) * N + 14, len(b.pv))
    modes = np.zeros(z - a, int)
    rates = {}
    n_sb = int(round(2 * STANDBY_H))
    for d, s, eh in zip(plan.day, plan.start, plan.event_h):
        i = int(d) * N + int(s) - a
        n_ev = int(round(2 * eh))
        modes[i - n_sb:i] = ps.STANDBY
        modes[i:i + n_ev] = ps.EVENT
        rates[i] = n_ev
    reserve = RESERVE * HH.usable_kwh
    soc = np.empty(z - a + 1)
    imp, exp, dis, ch, gch = (np.empty(z - a) for _ in range(5))
    s = np.array([b.soc[a]])
    soc[0] = s[0]
    rate = np.array([0.0])
    for t in range(z - a):
        mode = modes[t]
        if mode == ps.EVENT and t in rates:
            rate = np.array([min(HH.power_kw, max(s[0] - reserve, 0.0) * HH.eta / (rates[t] * DT))])
        s, im, ex, di, c, g = ps.step(s, b.pv[a + t:a + t + 1], b.load[a + t:a + t + 1], np.array([mode]), rate, HH, reserve)
        soc[t + 1], imp[t], exp[t], dis[t], ch[t], gch[t] = s[0], im[0], ex[0], di[0], c[0], g[0]
    return Run(soc, imp, exp, dis, ch, gch, modes, a, z)


def settle(plan: Plan, period_days: np.ndarray, led: Ledger, run: Run | None = None) -> dict:
    """Per enrolled home (x RESPONSE for the responding share) and for the fixed fleet: three-party accounts from the
    exact run, the IRCR settled on the whole season's 12 Peak SWIS intervals, and the hard checks."""
    m = ps.load_market()
    b = ps.baseline(HH)
    run = run or run_period(plan, period_days)
    a, z = run.a, run.z
    d_imp, d_exp, d_dis = run.imp - b.imp[a:z], run.exp - b.exp[a:z], run.dis - b.dis[a:z]
    d_net = (d_exp - d_imp) * DT
    gap = run.soc[-1] - b.soc[z]
    p_eve = m.slot_price[14:26].mean()
    wholesale = (d_net * m.rtp[a:z]).sum() / 1000 + gap * HH.eta * p_eve / 1000
    n_sb = int(round(2 * STANDBY_H))
    billed, standby = [], []
    for d, s, eh in zip(plan.day, plan.start, plan.event_h):
        i = int(d) * N + int(s) - a
        n_ev = int(round(2 * eh))
        billed.append(min((run.exp[i:i + n_ev] * DT).sum(), HH.usable_kwh))
        standby.append(min((np.maximum(d_imp[i - n_sb:i], 0) * DT).sum(), HH.usable_kwh))
    billed, standby = np.array(billed), np.array(standby)
    slots = np.arange(a, z) % N
    feed = ps.feed_in(slots)
    bill = ps.RETAIL * (d_imp * DT).sum() - (d_exp * DT * feed).sum() - gap * HH.eta * ps.RETAIL
    wear = PART.deg_per_kwh * max((d_dis * DT).sum(), 0.0)
    hassle = PART.hassle_per_event * len(plan.day)
    pay = PAY * billed.sum()
    offset = OFFSET * standby.sum()
    ops = OPS_EVENT * len(plan.day)
    # IRCR on the season inside the period
    season = ps.season_of(m)
    ys = sorted({int(y) for y in season[period_days] if y >= 0})
    cap_full, kw, covered = 0.0, 0.0, 0
    if ys and led.capacity != "none":
        y = ys[0]
        idx, sl = ps.peak_intervals()[y]
        flat = idx * N + sl
        base = b.imp[flat] - b.exp[flat]
        withc = run.imp[flat - a] - run.exp[flat - a]
        covered = int((withc < base - 0.1).sum())
        if led.capacity == "floor":
            base, withc = np.maximum(base, 0), np.maximum(withc, 0)
        kw = float(np.median(base) - np.median(withc))
        cap_full = kw / 1000 * ct.RCP[y + 1]
    r = RESPONSE
    zen = r * ((wholesale if led.energy else 0.0) + cap_full * led.share - pay - offset - ops)
    cust = r * (pay + offset - bill - wear - hassle)
    retailer = r * (bill + (0.0 if led.energy else wholesale) + cap_full * (1 - led.share))
    # hard checks
    bal = np.abs(b.pv[a:z] + run.dis + run.imp - b.load[a:z] - run.ch - run.exp).max()
    reserve = RESERVE * HH.usable_kwh
    ev = run.modes == ps.EVENT
    soc_after = run.soc[1:]
    soc_before = run.soc[:-1]
    reserve_breach = int((ev & (soc_after < np.minimum(soc_before, reserve) - 1e-6)).sum())
    days_in = pd.Series(plan.day)
    per_period = len(plan.day)
    lengths_ok = bool((plan.event_h <= MAX_EVENT_H + 1e-9).all()) if len(plan.day) else True
    viol = {
        "energy_balance": int(bal > 1e-6),
        "soc_bounds": int(((run.soc < -1e-9) | (run.soc > HH.usable_kwh + 1e-9)).sum()),
        "reserve": reserve_breach,
        "power": int(((run.dis > HH.power_kw + 1e-9) | (run.ch > HH.power_kw + 1e-9)).sum()),
        "grid_charge_outside_standby": int(((run.gch > 1e-9) & (run.modes != ps.STANDBY)).sum()),
        "cap": int(per_period > CAP),
        "per_day": int(days_in.duplicated().sum()),
        "event_length": int(not lengths_ok),
        "standby_before_0900": int(sum(int(s) - n_sb < ps.DECISION_SLOT for s in plan.start)),
    }
    return {
        "calls": len(plan.day), "ircr_calls": int((plan.kind == "ircr").sum()), "energy_calls": int((plan.kind == "energy").sum()),
        "billed_kwh": r * billed.sum(), "standby_kwh": r * standby.sum(), "net_export_change_kwh": r * d_net.sum(),
        "wholesale": r * wholesale, "ircr_value_full": r * cap_full, "ircr_kw_per_home": kw, "peak_intervals_reduced": covered,
        "pay": r * pay, "offset": r * offset, "ops": r * ops,
        "zenith": zen, "customer": cust, "retailer": retailer, "customer_bill": r * bill, "customer_wear": r * wear,
        "customer_hassle": r * hassle,
        "violations": sum(viol.values()), **{f"v_{k}": v for k, v in viol.items()},
    }


# ----------------------------------------------------------------------------------------------- training
def paced_grid(led: Ledger, energy_ok: bool) -> list[PacedRule]:
    grid = [PacedRule(ircr=False, theta_e=th) for th in ((0.0, np.inf) if energy_ok and led.energy else (np.inf,))]
    for k in KAPPAS:
        for eh in LENGTHS:
            for anchor in ("fixed", "forecast"):
                for th in ((0.0, np.inf) if energy_ok and led.energy else (np.inf,)):
                    grid.append(PacedRule(True, k, eh, anchor, th))
    return grid


def train_paced(led: Ledger, train: tuple, fc) -> tuple[PacedRule, pd.DataFrame]:
    m = ps.load_market()
    per = periods(m)
    energy_ok = any(fc.has_fc[per[y]].any() for y in train)
    rows = []
    for rule in paced_grid(led, energy_ok):
        tot, n = 0.0, 0
        for y in train:
            pacer = Pacer(tuple(s for s in train if s != y) or (y,))
            plan = select_paced(per[y], rule, led, pacer, fc)
            tot += fast_zenith(plan, per[y], led)
            n += len(plan.day)
        rows.append({**rule.__dict__, "train_zenith": tot, "train_calls": n})
    df = pd.DataFrame(rows)
    best = df.sort_values(["train_zenith", "train_calls"], ascending=[False, True]).iloc[0]
    rule = PacedRule(bool(best["ircr"]), float(best["kappa"]), float(best["event_h"]), str(best["anchor"]), float(best["theta_e"]))
    return rule, df


def train_reference(led: Ledger, train: tuple, fc) -> tuple[RefRule, pd.DataFrame]:
    m = ps.load_market()
    per = periods(m)
    energy_ok = any(fc.has_fc[per[y]].any() for y in train)
    rows = []
    for q in ps.IRCR_QUANTILES:
        for th in ((0.0, 0.5, 1.0, 2.0, 4.0, np.inf) if energy_ok and led.energy else (np.inf,)):
            rule = RefRule(q, th)
            tot, n = 0.0, 0
            for y in train:
                plan = select_reference(per[y], rule, led, train, fc)
                tot += fast_zenith(plan, per[y], led)
                n += len(plan.day)
            rows.append({"q": q, "theta_e": th, "train_zenith": tot, "train_calls": n})
    df = pd.DataFrame(rows)
    best = df.sort_values(["train_zenith", "train_calls"], ascending=[False, True]).iloc[0]
    return RefRule(float(best["q"]), float(best["theta_e"])), df


# ---------------------------------------------------------------------------------------------------- reports
def evaluate(led_key: str, train: tuple, eval_periods: list[int], fc, label: str) -> tuple[list[dict], dict, dict]:
    m = ps.load_market()
    per = periods(m)
    led = LEDGERS[led_key]
    paced, _ = train_paced(led, train, fc)
    ref, _ = train_reference(led, train, fc)
    pacer = Pacer(train)
    rows, plans = [], {}
    for y in eval_periods:
        days = per[y]
        if y == 2026:
            days = days[m.days[days] >= CUTOFF]
        cand = {"none": Plan.empty(), "reference": select_reference(days, ref, led, train, fc),
                "paced": select_paced(days, paced, led, pacer, fc)}
        best_p, best_v = None, -np.inf
        for eh in LENGTHS:
            p = select_perfect(days, led, eh)
            v = settle(p, days, led)["zenith"]
            if v > best_v:
                best_p, best_v = p, v
        cand["perfect"] = best_p
        for name, plan in cand.items():
            s = settle(plan, days, led) if len(plan.day) else settle(plan, days, led, run=None)
            fast = fast_zenith(plan, days, led) if len(plan.day) else 0.0
            rows.append({"evaluation": label, "ledger": led_key, "train_seasons": "+".join(f"{t}-{(t + 1) % 100:02d}" for t in train),
                         "period": f"{y}-07-01..{m.days[days[-1]].date()}", "policy": name,
                         "rule": str(ref) if name == "reference" else str(paced) if name == "paced" else "",
                         **s, "zenith_independent_calls": fast, "approx_gap": s["zenith"] - fast,
                         "zenith_fleet": HOMES * s["zenith"], "customer_fleet": HOMES * s["customer"],
                         "zenith_after_platform_fleet": HOMES * (s["zenith"] - PLATFORM * len(days) / 365) if name != "none" else 0.0})
            plans[(label, led_key, y, name)] = plan
    choice = {"evaluation": label, "ledger": led_key, "train": "+".join(map(str, train)), "paced": str(paced),
              "reference": str(ref)}
    return rows, plans, choice


def main() -> None:
    OUT.mkdir(exist_ok=True)
    m = ps.load_market()
    fc = ps.load_forecasts()
    x = peak_inputs()
    print("pre-dispatch rows published after 09:00:", x["late_rows"])
    rows, all_plans, choices = [], {}, []
    # historical forward development evaluation (these seasons have been looked at before: not strictly unseen)
    for led_key in LEDGERS:
        for train, ev in (((2023,), 2024), ((2023, 2024), 2025)):
            r, p, c = evaluate(led_key, train, [ev], fc, "forward development")
            rows += r
            all_plans.update(p)
            choices.append(c)
        # final rules trained on all three Hot Seasons (data to 2026-06-30); in-sample periods, then the test
        r, p, c = evaluate(led_key, SEASONS, [2023, 2024, 2025, 2026], fc, "final rule")
        rows += r
        all_plans.update(p)
        choices.append(c)
        print(led_key, "done", flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "comparison.csv", index=False)
    pd.DataFrame(choices).to_csv(OUT / "rule_choices.csv", index=False)
    cal = []
    for (label, led_key, y, name), plan in all_plans.items():
        for d, s, eh, k in zip(plan.day, plan.start, plan.event_h, plan.kind):
            cal.append({"evaluation": label, "ledger": led_key, "period_start_year": y, "policy": name,
                        "day": m.days[d].date(), "event_start": f"{(8 + s // 2) % 24:02d}:{30 * (s % 2):02d}",
                        "event_h": eh, "kind": k})
    pd.DataFrame(cal).to_csv(OUT / "calls.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    cols = ["evaluation", "ledger", "period", "policy", "calls", "ircr_calls", "energy_calls", "zenith", "customer",
            "pay", "offset", "wholesale", "ircr_value_full", "ircr_kw_per_home", "peak_intervals_reduced", "violations", "approx_gap"]
    print(res[cols].round(2).to_string())
    zen = ps.zenith_only()
    zen.to_csv(OUT / "zenith_only_e5.csv")
    print(zen.round(0))


if __name__ == "__main__":
    main()
