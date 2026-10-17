"""Q2B dispatcher core for one battery behind a site's grid connection in the WEM: morning plan +
standing spike offer (+ optional risk reserve).

Every trading day:
1. Morning plan (08:15). A MILP schedules charging, discharging and FCESS enablement for the 48
   trading intervals on the forecast prices, within the battery's limits, a daily cycle budget, the
   site's grid connection (the site's own load shares its import/export limits), the capacity
   obligation (full when the ESR obligation window opens, then enough stored energy to deliver the
   credited MW until it closes) and optionally a risk reserve (keep `reserve_mwh` stored in intervals
   whose spike risk is high). Every MWh discharged costs `degradation_per_mwh` of battery wear, so
   the plan only cycles when the spread pays for losses and wear. Decisions (the plan and the offer
   price) also add the strategy's `hurdle_per_mwh`, a margin for forecast error that is never
   settled: a trade that only just pays on the forecast tends to lose on the actual prices.
2. Real time (every 5-min dispatch interval). Follow the plan, plus a standing offer to discharge
   more when the price reaches the offer price K_t:
     offer_rule "forecast": K_t = max(k_min, alpha x the highest forecast price later today).
     offer_rule "value":    the first time the price reaches k_min in an interval, re-plan the rest
                            of the day twice - keeping the energy, and selling it now - and offer at
                            the price that beats alpha x the value lost plus the wear of selling now.
                            The value lost covers everything the energy would still earn: later energy
                            sales, the FCESS it would back, the obligation.
   Energy and FCESS are co-optimised, so the extra discharge also has to beat the FCESS raise revenue
   its headroom earns in that interval. It never sells energy the capacity obligation still needs
   (given how fast the site's connection lets it recharge), and it respects the connection's limits
   with the site's actual load. After an interval with such a sale the rest of the day is re-planned
   from the actual state of charge, with the forecast nudged towards the price just seen.
3. Settlement. Energy at the 30-min Reference Trading Price on the energy actually delivered, FCESS
   at the 30-min clearing price on the average MW enabled, minus wear on the energy discharged. The
   site's load is metered behind the same connection and settled at the same prices, so it does not
   change what each battery MWh earns; it limits what the battery can do.

Energy still stored at the end of the trading day is valued at what it would cost to buy back (the
mean of the day's 8 cheapest planning prices / eta), so the battery holds its charge instead of
cycling when the spread does not pay for losses and wear; the next day starts where this one ended.
Plans assume the site may draw `load_margin` more than forecast, so the battery can still charge in
time for the capacity obligation when the site is busier than expected.

Assumptions: price-taker; RoCoF ($0) not offered; enabled FCESS must be deliverable for
`fcess_sustain_h` and its activation energy is ignored.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from vpp.site import Site

N, DT = 48, 0.5          # trading intervals per day, hours per trading interval
STEPS, DT5 = 6, 5 / 60   # dispatch intervals per trading interval, hours per dispatch interval
FCESS = ["reg_raise", "cont_raise", "reg_lower", "cont_lower"]
RAISE, LOWER = FCESS[:2], FCESS[2:]
OFFER_EPS = 0.01  # $/MW/h added to FCESS planning prices: spare headroom is worth offering at $0


@dataclass(frozen=True)
class Battery:
    power_mw: float = 1.0
    energy_mwh: float = 2.0
    round_trip: float = 0.85
    cycles_per_day: float = 1.0
    fcess_sustain_h: float = 0.25      # enabled FCESS must be deliverable for this long
    duration_req_h: float = 4.0        # ESR duration requirement behind capacity credits
    degradation_per_mwh: float = 30.0  # A$ of wear per MWh discharged: cells ~A$180k/MWh over ~6,000 cycles

    @property
    def eta(self) -> float:
        return self.round_trip ** 0.5

    @property
    def credited_mw(self) -> float:
        """Capacity credits: the power the battery can sustain for the duration requirement."""
        return self.energy_mwh * self.eta / self.duration_req_h


@dataclass(frozen=True)
class Strategy:
    name: str
    offer: bool = False                  # standing spike offer in real time
    offer_rule: str = "forecast"         # "forecast" or "value", see the module docstring
    k_min: float = 150.0                 # $/MWh, lowest offer price
    alpha: float = 1.2                   # safety margin on what selling early gives up
    risk_threshold: float | None = None  # keep reserve_mwh stored in intervals with spike risk >= this
    reserve_mwh: float = 0.0
    hurdle_per_mwh: float = 0.0          # $/MWh discharged added to the wear in decisions (plan, offer), not settled


def decision_wear(bat: Battery, strat: Strategy) -> float:
    """$ per MWh discharged that plans and offer prices charge themselves: wear plus the strategy's hurdle."""
    return bat.degradation_per_mwh + strat.hurdle_per_mwh


@lru_cache(maxsize=None)
def _structure(n: int, bat: Battery):
    """Constraint matrix of an n-interval plan, plus the rows whose bounds are set per call."""
    P, E, eta = bat.power_mw, bat.energy_mwh, bat.eta
    kr, kl = bat.fcess_sustain_h / eta, bat.fcess_sustain_h * eta
    C, D, S, U, RR, CR, RL, CL = (np.arange(n) + k * n for k in range(8))
    rows, cols, vals, lb, ub = [], [], [], [], []
    idx = {"export": [], "import": [], "raise_site": [], "lower_site": []}

    def add(c, v, lo, hi):
        rows.extend([len(lb)] * len(c)); cols.extend(c); vals.extend(v); lb.append(lo); ub.append(hi)
        return len(lb) - 1

    for t in range(n):
        # s_t = s_{t-1} + eta*c_t*DT - d_t*DT/eta
        c, v = [S[t], C[t], D[t]], [1, -eta * DT, DT / eta]
        r = add(c + [S[t - 1]], v + [-1], 0, 0) if t else add(c, v, 0, 0)
        if t == 0:
            idx["dyn0"] = r
        add([C[t], U[t]], [1, P], -np.inf, P)                            # charge only if u = 0
        add([D[t], U[t]], [1, -P], -np.inf, 0)                           # discharge only if u = 1
        add([RR[t], CR[t]], [1, 1], -np.inf, P)
        add([D[t], C[t], RR[t], CR[t]], [1, -1, 1, 1], -np.inf, P)       # raise headroom
        add([RL[t], CL[t]], [1, 1], -np.inf, P)
        add([C[t], D[t], RL[t], CL[t]], [1, -1, 1, 1], -np.inf, P)       # lower headroom
        add([S[t], RR[t], CR[t]], [1, -kr, -kr], 0, np.inf)              # energy behind raise
        add([S[t], RL[t], CL[t]], [1, kl, kl], -np.inf, E)               # room behind lower
        if t:
            add([S[t - 1], RR[t], CR[t]], [1, -kr, -kr], 0, np.inf)
            add([S[t - 1], RL[t], CL[t]], [1, kl, kl], -np.inf, E)
        else:
            idx["raise0"] = add([RR[t], CR[t]], [kr, kr], -np.inf, 0)
            idx["lower0"] = add([RL[t], CL[t]], [kl, kl], -np.inf, E)
        # the site's connection (bounds set in plan): export / import limits net of the site's load
        idx["export"].append(add([D[t], C[t]], [1, -1], -np.inf, np.inf))
        idx["import"].append(add([C[t], D[t]], [1, -1], -np.inf, np.inf))
        idx["raise_site"].append(add([D[t], C[t], RR[t], CR[t]], [1, -1, 1, 1], -np.inf, np.inf))
        idx["lower_site"].append(add([C[t], D[t], RL[t], CL[t]], [1, -1, 1, 1], -np.inf, np.inf))
    idx["budget"] = add(list(D), [DT] * n, 0, 0)
    A = coo_matrix((vals, (rows, cols)), shape=(len(lb), 8 * n)).tocsr()
    return A, np.array(lb, float), np.array(ub, float), {k: np.array(v) if isinstance(v, list) else v for k, v in idx.items()}


def plan(bat: Battery, price_e, price_f=None, s_init: float = 0.0, budget: float | None = None, floor=None,
         site: Site | None = None, load=None, terminal_value: float = 0.0, wear_per_mwh: float | None = None) -> dict:
    """Best schedule for the remaining n intervals on the given prices.

    price_e: $/MWh per interval; price_f: {product: $/MW/h} or None for energy only; budget: MWh the
    battery may still discharge today; floor: minimum stored MWh at the end of each interval; site,
    load: the connection limits and the site's load (MW) per interval; terminal_value: $ per MWh
    still stored at the end; wear_per_mwh: $ per MWh discharged (default: the battery's wear).
    Returns the schedule and its value at these prices (energy + FCESS - wear + stored energy left).
    """
    n = len(price_e)
    P, E = bat.power_mw, bat.energy_mwh
    wear = bat.degradation_per_mwh if wear_per_mwh is None else wear_per_mwh
    A, lb, ub, idx = _structure(n, bat)
    lb, ub = lb.copy(), ub.copy()
    lb[idx["dyn0"]] = ub[idx["dyn0"]] = s_init
    ub[idx["raise0"]], ub[idx["lower0"]] = s_init, E - s_init
    ub[idx["budget"]] = max(0.0, bat.cycles_per_day * E if budget is None else budget)
    if site is not None:
        load = np.asarray(load, float)
        ub[idx["export"]] = ub[idx["raise_site"]] = site.export_limit_mw + load
        ub[idx["import"]] = ub[idx["lower_site"]] = site.import_limit_mw - load
    lo = np.zeros(8 * n)
    hi = np.r_[np.full(2 * n, P), np.full(n, E), np.ones(n), np.full(4 * n, P if price_f is not None else 0.0)]
    if floor is not None:
        lo[2 * n:3 * n] = np.minimum(floor, E)
    pe = np.asarray(price_e, float)
    cost = np.zeros(8 * n)
    cost[:n], cost[n:2 * n] = pe * DT, (wear - pe) * DT
    if price_f is not None:
        for k, name in enumerate(FCESS):
            cost[(4 + k) * n:(5 + k) * n] = -np.asarray(price_f[name], float) * DT
    cost[3 * n - 1] = -terminal_value
    integrality = np.r_[np.zeros(3 * n), np.ones(n), np.zeros(4 * n)]
    res = milp(cost, constraints=LinearConstraint(A, lb, ub), bounds=Bounds(lo, hi), integrality=integrality)
    if res.status != 0:
        raise RuntimeError(f"plan infeasible ({res.message}); s_init={s_init:.3f}, budget={budget}")
    x = res.x
    c, d = x[:n], x[n:2 * n]
    out = {"net": d - c, "soc": x[2 * n:3 * n], **{name: x[(4 + k) * n:(5 + k) * n] for k, name in enumerate(FCESS)}}
    out["energy_value"] = float((pe * (d - c)).sum() * DT)
    out["fcess_value"] = float(sum((np.asarray(price_f[k], float) * out[k]).sum() for k in FCESS) * DT) if price_f is not None else 0.0
    out["wear"] = float(wear * d.sum() * DT)
    out["stored_value"] = float(terminal_value * out["soc"][-1])
    out["value"] = out["energy_value"] + out["fcess_value"] - out["wear"] + out["stored_value"]
    return out


def stored_energy_value(bat: Battery, price_e) -> float:
    """$ per MWh still stored at the end of the day: what buying it back would cost (cheapest 4 hours / eta)."""
    return max(0.0, float(np.sort(np.asarray(price_e, float))[:8].mean())) / bat.eta


def charge_room(bat: Battery, site: Site | None = None, load=None) -> np.ndarray:
    """Most stored MWh the battery can add in each interval: full power, within the site's import room."""
    rate = np.full(N, bat.power_mw)
    if site is not None:
        rate = np.clip(np.minimum(rate, site.import_limit_mw - np.asarray(load, float)), 0.0, None)
    return bat.eta * rate * DT


def obligation_need(bat: Battery, window: tuple[int, int], hours: float) -> float:
    """Stored MWh the capacity obligation requires `hours` after 08:00 (window = first, end slot)."""
    start, end = window[0] * DT, window[1] * DT
    return bat.credited_mw * (end - hours) / bat.eta if start <= hours <= end else 0.0


def keep_for_obligation(bat: Battery, window: tuple[int, int], hours: float, room=None) -> float:
    """Stored MWh that cannot be sold at `hours`: before the window, what could not be recharged in
    time (room = most MWh the battery can add per interval); inside it, what the obligation needs."""
    start = window[0] * DT
    if hours < start:
        if room is None:
            refill = bat.eta * bat.power_mw * (start - hours)
        else:
            t = int(hours // DT)
            refill = room[t] * ((t + 1) * DT - hours) / DT + room[t + 1:window[0]].sum()
        return max(0.0, bat.energy_mwh - refill)
    return obligation_need(bat, window, hours)


def plan_floor(bat: Battery, t0: int, s_init: float, window=None, risk=None, strat: Strategy | None = None,
               room=None) -> np.ndarray:
    """Minimum stored MWh at the end of intervals t0..N-1 from the obligation and the risk reserve,
    capped at what charging from s_init can reach."""
    t = np.arange(t0, N)
    floor = np.zeros(len(t))
    if window is not None:
        floor = np.maximum(floor, [obligation_need(bat, window, (k + 1) * DT) for k in t])
    if strat is not None and strat.risk_threshold is not None and risk is not None:
        risky_next = np.r_[np.asarray(risk)[t0 + 1:], 0.0] >= strat.risk_threshold
        floor = np.maximum(floor, np.where(risky_next, strat.reserve_mwh, 0.0))
    step = np.full(len(t), bat.eta * bat.power_mw * DT) if room is None else np.asarray(room)[t0:]
    return np.minimum(floor, np.minimum(s_init + np.cumsum(step), bat.energy_mwh))


def nowcast(fc_e, t: int, price_now: float, half_life: float = 1.0) -> np.ndarray:
    """Forecast for intervals t+1.. updated with the price just seen: today's forecast error at
    interval t fades with a half-life of `half_life` intervals (fixed, not tuned)."""
    fc_e = np.asarray(fc_e, float)
    k = np.arange(1, N - t)
    return fc_e[t + 1:] + (price_now - fc_e[t]) * 0.5 ** (k / half_life)


def nowcast_fcess(fc_f, t: int, now: dict | None) -> dict | None:
    """FCESS planning prices for intervals t+1.., nudged towards the 5-min prices just seen the same way."""
    if fc_f is None:
        return None
    if now is None:
        return {k: np.asarray(fc_f[k], float)[t + 1:] for k in FCESS}
    return {k: np.maximum(nowcast(fc_f[k], t, now[k]), OFFER_EPS) for k in FCESS}


def offer_prices(strat: Strategy, fc_e) -> np.ndarray:
    """"forecast" rule: sell early only if the price beats alpha x the best forecast price later today."""
    if not strat.offer:
        return np.full(N, np.inf)
    fc_e = np.asarray(fc_e, float)
    later = np.r_[np.maximum.accumulate(fc_e[::-1])[::-1][1:], -np.inf]
    return np.maximum(strat.k_min, strat.alpha * later)


def value_offer_price(bat: Battery, strat: Strategy, t: int, j: int, s: float, budget: float, planned: float,
                      sell: float, fc_e, fc_f, price_now: float, window, risk, site, load_fc, room,
                      terminal_value: float = 0.0, fcess_now: dict | None = None) -> tuple[float, float]:
    """"value" rule: offer price for the rest of interval t from re-planning the rest of the day twice,
    running at the planned power until the interval ends or selling at `sell` MW instead. Energy and
    FCESS forecasts are both nudged towards the prices just seen (fcess_now: 5-min FCESS prices).

    Returns (offer price, the FCESS part of the value lost per MWh sold).
    """
    rem = (STEPS - j) * DT5
    extra = (sell - planned) * rem  # MWh more delivered to the grid by selling
    if extra <= 1e-9:
        return np.inf, 0.0
    dw = decision_wear(bat, strat)
    lost = lost_fcess = 0.0
    if t < N - 1:
        fc_next = nowcast(fc_e, t, price_now)
        f_next = nowcast_fcess(fc_f, t, fcess_now)
        l_next = None if site is None else np.asarray(load_fc, float)[t + 1:]
        branches = []
        for power in (planned, sell):
            s1 = s - power * rem / bat.eta if power > 0 else s - power * rem * bat.eta
            s1 = min(max(s1, 0.0), bat.energy_mwh)
            b1 = budget - max(power, 0.0) * rem
            try:
                branches.append(plan(bat, fc_next, f_next, s1, b1, plan_floor(bat, t + 1, s1, window, risk, strat, room),
                                     site, l_next, terminal_value, dw))
            except RuntimeError:
                return np.inf, 0.0
        lost = branches[0]["value"] - branches[1]["value"]
        lost_fcess = branches[0]["fcess_value"] - branches[1]["fcess_value"]
    wear_now = dw * (sell - max(planned, 0.0)) * rem
    return max(strat.k_min, (strat.alpha * lost + wear_now) / extra), lost_fcess / extra


def run_day(bat: Battery, strat: Strategy, fc_e, fc_f, rtp, act_f, p5, window=None, risk=None, s_start=0.0, p5_f=None,
            site: Site | None = None, load_fc=None, load_act=None, load_margin: float | None = None):
    """Plan at 08:15 on the forecast, execute in 5-min steps with the standing offer, settle at actuals.

    fc_e/rtp: 48 forecast / actual energy prices; fc_f/act_f: {product: 48 prices} or fc_f=None for
    energy only; p5 / p5_f: 288 actual 5-min energy / {FCESS product: price} prices; window: capacity
    obligation slots or None; risk: 48 spike probabilities; site, load_fc / load_act: the connection
    and the site's forecast / actual load (MW) per interval; load_margin overrides site.load_margin
    (0 when the load is known). Returns (summary dict, per-interval log).
    """
    P, E, eta, tau = bat.power_mw, bat.energy_mwh, bat.eta, bat.fcess_sustain_h
    fcess = fc_f is not None
    if site is not None:
        load_margin = site.load_margin if load_margin is None else load_margin
    lf = None if site is None else np.asarray(load_fc, float) * (1 + load_margin)  # what plans assume the site draws
    la = np.zeros(N) if site is None else np.asarray(load_act, float)
    imp_room = np.full(N, np.inf) if site is None else site.import_limit_mw - la  # most net charging, MW
    exp_room = np.full(N, np.inf) if site is None else site.export_limit_mw + la  # most net discharging, MW
    room = charge_room(bat, site, lf)
    value_rule = strat.offer and strat.offer_rule == "value"
    k_offer = np.full(N, np.inf) if value_rule else offer_prices(strat, fc_e)
    offer_fcess, offer_mwh = np.full(N, np.nan), np.zeros(N)
    tv = stored_energy_value(bat, fc_e)
    dw = decision_wear(bat, strat)
    floor = plan_floor(bat, 0, s_start, window, risk, strat, room)
    morning = cur = plan(bat, fc_e, fc_f, s_start, None, floor, site, lf, tv, dw)
    cur_t0, s, budget = 0, s_start, bat.cycles_per_day * E
    shortfall = site_short = wear = 0.0
    energy, soc = np.zeros(N), np.zeros(N)
    mw_h = {k: np.zeros(N) for k in FCESS}
    triggered, replanned = np.zeros(N, bool), np.zeros(N, bool)
    for t in range(N):
        i = t - cur_t0
        r_plan = sum(cur[k][i] for k in RAISE) if fcess else 0.0
        l_plan = sum(cur[k][i] for k in LOWER) if fcess else 0.0
        k_ready = not value_rule
        for j in range(STEPS):
            hours = t * DT + (j + 1) * DT5
            keep = keep_for_obligation(bat, window, hours, room) if window is not None else 0.0
            sellable = max(0.0, min(P, exp_room[t], (s - keep) * eta / DT5, budget / DT5))
            pw = min(cur["net"][i], sellable)
            price = p5[t * STEPS + j]
            if strat.offer and price >= strat.k_min and sellable > max(pw, 0.0) + 1e-6:
                if not k_ready:
                    f_now = None if p5_f is None or not fcess else {k: p5_f[k][t * STEPS + j] for k in FCESS}
                    k_offer[t], offer_fcess[t] = value_offer_price(
                        bat, strat, t, j, s, budget, pw, sellable, fc_e, fc_f, price, window, risk, site, lf, room, tv, f_now)
                    k_ready = True
                margin = price - k_offer[t]
                if margin >= 0:
                    # co-optimised: discharging more uses headroom the plan had enabled for FCESS raise
                    raise_lost = max(0.0, min(r_plan, P - pw)) - max(0.0, min(r_plan, P - sellable))
                    raise_price = (sum(cur[k][i] * p5_f[k][t * STEPS + j] for k in RAISE) / r_plan
                                   if p5_f is not None and r_plan > 1e-9 else 0.0)
                    if margin * (sellable - max(pw, 0.0)) >= raise_price * raise_lost:
                        offer_mwh[t] += (sellable - max(pw, 0.0)) * DT5
                        pw, triggered[t] = sellable, True
            if pw <= 0 and s - pw * DT5 * eta < keep:
                # obligation safety: stored energy is at the least that can still be refilled in time
                pw = -min(P, max(-pw, (keep - s) / (eta * DT5)))
            pw = min(max(pw, -imp_room[t]), exp_room[t])  # the connection, with the site's actual load
            if pw > 0:
                pw = min(pw, s * eta / DT5, budget / DT5)
                s -= pw * DT5 / eta
                budget -= pw * DT5
                wear += bat.degradation_per_mwh * pw * DT5
            else:
                pw = max(pw, -(E - s) / (eta * DT5))
                s -= pw * DT5 * eta
            site_short = max(site_short, -imp_room[t] - pw)  # site import over the limit the battery could not cover
            energy[t] += pw * DT5
            if fcess:
                raise_ok = max(0.0, min(r_plan, P - pw, s * eta / tau, exp_room[t] - pw))
                lower_ok = max(0.0, min(l_plan, P + pw, (E - s) / (eta * tau), imp_room[t] + pw))
                for k in RAISE:
                    mw_h[k][t] += (cur[k][i] * raise_ok / r_plan if r_plan > 1e-9 else 0.0) * DT5
                for k in LOWER:
                    mw_h[k][t] += (cur[k][i] * lower_ok / l_plan if l_plan > 1e-9 else 0.0) * DT5
            if window is not None:
                shortfall = max(shortfall, obligation_need(bat, window, hours) - s)
        soc[t] = s
        if triggered[t] and t < N - 1:
            cur_t0 = t + 1
            last = t * STEPS + STEPS - 1
            f_now = None if p5_f is None or not fcess else {k: p5_f[k][last] for k in FCESS}
            args = (nowcast(fc_e, t, p5[last]), nowcast_fcess(fc_f, t, f_now), s, budget)
            rest = None if lf is None else lf[cur_t0:]
            try:
                cur = plan(bat, *args, plan_floor(bat, cur_t0, s, window, risk, strat, room), site, rest, tv, dw)
            except RuntimeError:
                cur = plan(bat, *args, None, site, rest, tv, dw)
            replanned[cur_t0] = True
    energy_rev = float((np.asarray(rtp) * energy).sum())
    fcess_rev = float(sum((np.asarray(act_f[k]) * mw_h[k]).sum() for k in FCESS)) if fcess else 0.0
    summary = {"energy": energy_rev, "fcess": fcess_rev, "wear": wear, "total": energy_rev + fcess_rev - wear,
               "discharged_mwh": float(energy.clip(min=0).sum()), "triggers": int(triggered.sum()),
               "replans": int(replanned.sum()), "obligation_shortfall_mwh": max(0.0, shortfall),
               "site_shortfall_mw": max(0.0, site_short), "soc_end": s}
    log = pd.DataFrame({
        "slot": np.arange(N), "forecast": fc_e, "rtp": rtp, "max_5min_price": np.asarray(p5).reshape(N, STEPS).max(axis=1),
        "plan_net": morning["net"], "plan_soc": morning["soc"], "floor": floor, "offer_price": k_offer,
        "offer_fcess": offer_fcess, "offer_mwh": offer_mwh, "stored_value": tv, "soc_start": s_start,
        "risk": np.nan if risk is None else risk, "energy_mwh": energy, "soc": soc,
        "triggered": triggered, "replanned": replanned,
        "site_load_fc": np.nan if lf is None else lf / (1 + load_margin), "site_load_plan": np.nan if lf is None else lf,
        "site_load": la if site is not None else np.nan, **{f"{k}_mw": mw_h[k] / DT for k in FCESS},
    })
    return summary, log


def _runs(labels) -> list[tuple[int, int, object]]:
    """Consecutive runs of equal labels as (first, last + 1, label)."""
    out, i = [], 0
    while i < len(labels):
        j = i
        while j + 1 < len(labels) and labels[j + 1] == labels[i]:
            j += 1
        out.append((i, j + 1, labels[i]))
        i = j + 1
    return out


def explain(log: pd.DataFrame, summary: dict, bat: Battery, strat: Strategy, window=None, site: Site | None = None) -> list[str]:
    """Plain-language account of one day: what the morning plan does and why, then what happened."""
    clock = pd.date_range("2000-01-01 08:00", periods=N + 1, freq="30min").strftime("%H:%M")
    fc = log["forecast"].to_numpy()
    wear = decision_wear(bat, strat)
    costs = (f"${bat.degradation_per_mwh:.0f}/MWh wear and a ${strat.hurdle_per_mwh:.0f}/MWh margin for forecast error"
             if strat.hurdle_per_mwh else f"${wear:.0f}/MWh wear")
    lines = []
    if site is not None:
        lines.append(f"Site: load {log['site_load_fc'].min():.2f}-{log['site_load_fc'].max():.2f} MW expected (planned with "
                     f"{site.load_margin:.0%} spare for forecast error), sharing a {site.import_limit_mw:.1f} MW import / "
                     f"{site.export_limit_mw:.1f} MW export connection with the battery.")
    lines.append(f"Starts the day with {log['soc_start'].iloc[0]:.1f} of {bat.energy_mwh:.1f} MWh stored.")
    act = np.where(log["plan_net"] > 0.01, "discharge", np.where(log["plan_net"] < -0.01, "charge", "idle"))
    for i, j, a in _runs(list(act)):
        power = log["plan_net"].iloc[i:j].abs().to_numpy()
        if a == "idle" or power.sum() * DT < 0.05:
            continue
        price = f"forecast ${fc[i:j].min():.0f}-{fc[i:j].max():.0f}/MWh"
        if a == "charge":
            later = fc[j:].max() if j < N else -np.inf
            if (later - wear) * bat.round_trip > fc[i:j].mean():
                why = (f"selling later at up to ${later:.0f} still pays after the {1 - bat.round_trip:.0%} round-trip "
                       f"loss, {costs}")
            elif window is not None and i < window[0]:
                why = f"the spread does not cover losses and wear, but the battery must be full by {clock[window[0]]} for the capacity obligation"
            elif strat.risk_threshold is not None:
                why = "keeps energy for the spike reserve"
            else:
                why = "cheap energy kept for later; stored energy counts at what it would cost to buy back"
            if site is not None:
                load = log["site_load_plan"].iloc[i:j].to_numpy()
                cap = np.minimum(bat.power_mw, site.import_limit_mw - load)
                if (cap < bat.power_mw - 0.05).any() and (power >= cap - 0.02).all():
                    expected = log["site_load_fc"].iloc[i:j]
                    why += (f"; charging is held to {power.min():.2f}-{power.max():.2f} MW because the site is expected to draw "
                            f"{expected.min():.2f}-{expected.max():.2f} MW (plus the safety margin) of the "
                            f"{site.import_limit_mw:.1f} MW connection")
        else:
            in_window = window is not None and window[0] <= i < window[1]
            why = "paced so the capacity obligation stays covered" if in_window else "the best forecast prices left for the energy on hand"
        lines.append(f"{clock[i]}-{clock[j]} {a} {power.sum() * DT:.1f} MWh: {price}; {why}.")
    if window is not None:
        lines.append(f"Capacity obligation: full by {clock[window[0]]}, then enough stored energy to deliver "
                     f"{bat.credited_mw:.2f} MW until {clock[window[1]]}.")
    if strat.risk_threshold is not None:
        risky = log["risk"].to_numpy() >= strat.risk_threshold
        for i, j, r in _runs(list(risky)):
            if r:
                lines.append(f"{clock[i]}-{clock[j]} keep at least {strat.reserve_mwh:.1f} MWh stored: spike risk up to "
                             f"{log['risk'].iloc[i:j].max():.0%} (threshold {strat.risk_threshold:.0%}).")
    if strat.offer:
        k = log["offer_price"].to_numpy()
        k = k[np.isfinite(k)]
        if strat.offer_rule == "forecast":
            lines.append(f"Standing offer all day: discharge as soon as the price reaches ${k.min():.0f}-{k.max():.0f}/MWh "
                         f"({strat.alpha:.1f}x the best price still expected, never below ${strat.k_min:.0f}).")
        elif len(k):
            f = log["offer_fcess"].to_numpy()
            f = f[np.isfinite(f)]
            lines.append(f"Standing offer: each time the price passed ${strat.k_min:.0f}, the rest of the day was re-planned with and "
                         f"without selling now; the battery sells early only if the price beats {strat.alpha:.1f}x what keeping the "
                         f"energy is worth plus {costs}. Offer prices today: ${k.min():.0f}-{k.max():.0f}/MWh, of which the "
                         f"FCESS the energy would back was up to ${strat.alpha * max(f.max(), 0.0):.0f}/MWh.")
        else:
            lines.append(f"Standing offer: the price never passed ${strat.k_min:.0f} while there was energy to sell, so no early sale was considered.")
    raise_mw = (log["reg_raise_mw"] + log["cont_raise_mw"]).mean()
    lower_mw = (log["reg_lower_mw"] + log["cont_lower_mw"]).mean()
    if raise_mw + lower_mw > 0.01:
        lines.append(f"FCESS: spare headroom offered at $0, on average {raise_mw:.2f} MW raise and {lower_mw:.2f} MW lower.")
    for i, j, trig in _runs(list(log["triggered"])):
        if trig:
            lines.append(f"{clock[i]}-{clock[j]} the price reached up to ${log['max_5min_price'].iloc[i:j].max():.0f}: the standing "
                         f"offer sold {log['offer_mwh'].iloc[i:j].sum():.2f} MWh more than planned, and the rest of the day was re-planned.")
    done_dis, done_chg = log["energy_mwh"].clip(lower=0).sum(), abs(log["energy_mwh"].clip(upper=0).sum())
    plan_dis, plan_chg = log["plan_net"].clip(lower=0).sum() * DT, abs(log["plan_net"].clip(upper=0).sum()) * DT
    if abs(done_dis - plan_dis) > 0.05 or abs(done_chg - plan_chg) > 0.05:
        lines.append(f"Executed in the end: discharged {done_dis:.1f} MWh and charged {done_chg:.1f} MWh "
                     f"(morning plan: {plan_dis:.1f} and {plan_chg:.1f}).")
    if log["soc"].iloc[-1] > 0.05:
        lines.append(f"Ends the day holding {log['soc'].iloc[-1]:.1f} MWh, counted at ${log['stored_value'].iloc[0]:.0f}/MWh "
                     f"(what buying it back would cost): selling it for less, after losses and wear, would not pay.")
    lines.append(f"Result: ${summary['energy']:.0f} energy + ${summary['fcess']:.0f} FCESS - ${summary['wear']:.0f} wear "
                 f"= ${summary['total']:.0f}.")
    return lines
