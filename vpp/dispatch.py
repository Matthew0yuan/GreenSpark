"""Q2B dispatcher core for one battery behind a site's grid connection in the WEM: morning plan +
standing spike offer (+ optional risk reserve).

Every trading day:
1. Morning plan (08:15). A MILP schedules charging, discharging and FCESS enablement for the 48
   trading intervals on the forecast prices, within the battery's limits, a daily cycle budget, the
   site's grid connection (the site's own load shares its import/export limits), the obligations
   the battery has committed to (each holds back the stored energy it needs, see Obligation) and
   optionally a risk reserve (keep `reserve_mwh` stored in intervals whose spike risk is high).
   Every MWh discharged costs the asset's `wear_per_mwh`, so the plan only cycles when the spread
   pays for losses and wear. Decisions (the plan and the offer price) also add the risk preferences'
   `hurdle_per_mwh`, a margin for forecast error that is never settled: a trade that only just pays
   on the forecast tends to lose on the actual prices.
2. Real time (every 5-min dispatch interval). Follow the plan, plus a standing offer to discharge
   more when the price reaches the offer price K_t:
     offer_rule "forecast": K_t = max(k_min, alpha x the highest forecast price later today).
     offer_rule "value":    the first time the price reaches k_min in an interval, re-plan the rest
                            of the day twice - keeping the energy, and selling it now - and offer at
                            the price that beats alpha x the value lost plus the wear of selling now.
                            The value lost covers everything the energy would still earn: later energy
                            sales, the FCESS it would back, the obligations.
   Energy and FCESS are co-optimised, so the extra discharge also has to beat the FCESS raise revenue
   its headroom earns in that interval. It never sells energy the obligations still need
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
time for its obligations when the site is busier than expected.

Assumptions: price-taker; RoCoF ($0) not offered; enabled FCESS must be deliverable for the
limits' `fcess_sustain_h` and its activation energy is ignored.

What the battery is set up with comes by category (vpp.config): its physics (Battery), the operating
limits it runs under (Limits) and its wear cost, together an Asset; and the risk preferences (RiskPrefs).
"""

from dataclasses import dataclass, replace
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, linprog, milp
from scipy.sparse import block_diag, coo_matrix
from scipy.sparse import hstack as sp_hstack
from scipy.sparse import vstack as sp_vstack

from vpp.config import Asset, Battery, RiskPrefs
from vpp.site import Site

N, DT = 48, 0.5          # trading intervals per day, hours per trading interval
STEPS, DT5 = 6, 5 / 60   # dispatch intervals per trading interval, hours per dispatch interval
FCESS = ["reg_raise", "cont_raise", "reg_lower", "cont_lower"]
RAISE, LOWER = FCESS[:2], FCESS[2:]
OFFER_EPS = 0.01  # $/MW/h added to FCESS planning prices: spare headroom is worth offering at $0


@dataclass(frozen=True)
class Strategy:
    """How the dispatcher decides when to sell beyond its plan (an economic choice). Its margins for
    forecast error are the risk preferences (vpp.config.RiskPrefs)."""
    name: str
    offer: bool = False                  # standing spike offer in real time
    offer_rule: str = "forecast"         # "forecast" or "value", see the module docstring
    k_min: float = 150.0                 # $/MWh, lowest offer price
    alpha: float = 1.2                   # safety margin on what selling early gives up
    # "point": plan on the point forecast, re-planned when information arrives (a reveal, an offer sale). The
    # others also re-plan every `every` intervals from the actual state of charge, the prices nudged towards the
    # latest seen: "roll" on the point forecast; from the scenarios' issue (run_day's scen) on, "mean" on the
    # scenarios' mean, "sp" with the two-stage stochastic plan (plan_sp), "dro" the same at the worst weights the
    # risk preferences' ambiguity allows
    planner: str = "point"
    every: int = 2
    # what stored energy is worth at the end of the plan: "buyback" (stored_energy_value) or "table" (run_day's
    # terminal curve, vpp.storage_value). offer_rule "table": layered offers from run_day's bids (DayBids)
    terminal: str = "buyback"


def decision_wear(asset: Asset, prefs: RiskPrefs) -> float:
    """$ per MWh discharged that plans and offer prices charge themselves: wear plus the hurdle."""
    return asset.wear_per_mwh + prefs.hurdle_per_mwh


@lru_cache(maxsize=None)
def _structure(n: int, bat: Battery, sustain_h: float, kinds: tuple = (), comp: bool = False, n_soft: int = 0,
               util: tuple = (0.0, 0.0)):
    """Constraint matrix of an n-interval plan, plus the rows whose bounds are set per call.

    sustain_h: how long enabled FCESS must be deliverable (vpp.config.Limits). kinds: one (kind,
    compliance) per cap on net charging (see CapLayer): "slot" adds a slack column per interval, "peak"
    one for the whole plan; each cap adds two rows per interval (net charging, and net charging plus
    enabled FCESS lower, at most the cap plus its slack). comp: the compliance floor as rows
    s_t + f_t >= floor_t (f_t >= 0: the shortfall) and one row bounding the total compliance shortfall
    (f, and the slack of compliance caps in MWh). n_soft: economic floors, each one column z (its
    largest shortfall) and rows s_t + z >= floor_t. util: MWh delivered / taken per MW-hour of regulation
    raise / lower enabled, which then move the stored energy and count in the cycle budget."""
    P, E, eta = bat.power_mw, bat.energy_mwh, bat.eta
    kr, kl = sustain_h / eta, sustain_h * eta
    C, D, S, U, RR, CR, RL, CL = (np.arange(n) + k * n for k in range(8))
    rows, cols, vals, lb, ub = [], [], [], [], []
    idx = {"export": [], "import": [], "raise_site": [], "lower_site": []}

    def add(c, v, lo, hi):
        rows.extend([len(lb)] * len(c)); cols.extend(c); vals.extend(v); lb.append(lo); ub.append(hi)
        return len(lb) - 1

    for t in range(n):
        # s_t = s_{t-1} + eta*c_t*DT - d_t*DT/eta
        c, v = [S[t], C[t], D[t]], [1, -eta * DT, DT / eta]
        if util[0]:
            c, v = c + [RR[t]], v + [util[0] * DT / eta]
        if util[1]:
            c, v = c + [RL[t]], v + [-util[1] * DT * eta]
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
    idx["budget"] = add(list(D) + (list(RR) if util[0] else []), [DT] * n + ([util[0] * DT] * n if util[0] else []), 0, 0)
    ncol, layer_rows, slack = 8 * n, [], []
    for kind, _ in kinds:
        cols_l = np.arange(ncol, ncol + (n if kind == "slot" else 1))
        ncol += len(cols_l)
        r1, r2 = [], []
        for t in range(n):
            x = cols_l[t] if kind == "slot" else cols_l[0]
            r1.append(add([C[t], D[t], x], [1, -1, -1], -np.inf, np.inf))
            r2.append(add([C[t], D[t], RL[t], CL[t], x], [1, -1, 1, 1, -1], -np.inf, np.inf))
        layer_rows.append((np.array(r1), np.array(r2)))
        slack.append(cols_l)
    if comp:
        f = np.arange(ncol, ncol + n)
        ncol += n
        idx["floor"] = [add([S[t], f[t]], [1, 1], -np.inf, np.inf) for t in range(n)]
        cols_r = [int(c) for c in f] + [int(c) for (_, compliance), cols_l in zip(kinds, slack) if compliance for c in cols_l]
        vals_r = [1.0] * n + [DT] * (len(cols_r) - n)
        idx["comp"] = add(cols_r, vals_r, -np.inf, np.inf)
        idx["comp_cols"], idx["comp_vals"] = cols_r, vals_r
    soft_rows, soft_cols = [], []
    for _ in range(n_soft):
        z = ncol
        ncol += 1
        soft_rows.append(np.array([add([S[t], z], [1, 1], -np.inf, np.inf) for t in range(n)]))
        soft_cols.append(z)
    A = coo_matrix((vals, (rows, cols)), shape=(len(lb), ncol)).tocsr()
    idx = {k: np.array(v) if isinstance(v, list) else v for k, v in idx.items()}
    idx["layers"], idx["slack"], idx["soft"], idx["soft_cols"] = layer_rows, slack, soft_rows, soft_cols
    return A, np.array(lb, float), np.array(ub, float), idx


@dataclass(eq=False)
class _Milp:
    """plan's MILP at one set of prices: minimise cost @ x with lb <= A x <= ub and lo <= x <= hi."""
    cost: np.ndarray
    A: object
    lb: np.ndarray
    ub: np.ndarray
    lo: np.ndarray
    hi: np.ndarray
    integrality: np.ndarray
    idx: dict
    pe: np.ndarray
    price_f: dict | None
    wear: float
    terminal_value: float | np.ndarray
    util: tuple
    report: bool  # caps, a compliance floor or economic floors: the result reports their penalty and slack
    nx0: int = 0  # columns before the terminal value's segments (all columns without them)

    def solve(self, cost=None):
        return milp(self.cost if cost is None else cost, constraints=LinearConstraint(self.A, self.lb, self.ub),
                    bounds=Bounds(self.lo, self.hi), integrality=self.integrality)


def _milp(asset: Asset, price_e, price_f=None, s_init: float = 0.0, budget: float | None = None, floor=None,
          site: Site | None = None, load=None, terminal_value: float = 0.0, wear_per_mwh: float | None = None,
          layers=(), fcess_hi: dict | None = None, export_energy: bool = True, comp_floor=None, soft=(),
          reg_util: tuple = (0.0, 0.0)) -> _Milp:
    """plan's MILP (the arguments are plan's)."""
    bat = asset.battery
    n = len(price_e)
    P, E = bat.power_mw, bat.energy_mwh
    wear = asset.wear_per_mwh if wear_per_mwh is None else wear_per_mwh
    kinds = tuple((kind, bool(comp)) for _, kind, _, comp in layers)
    util = tuple(reg_util) if price_f is not None else (0.0, 0.0)
    A, lb, ub, idx = _structure(n, bat, asset.limits.fcess_sustain_h, kinds, comp_floor is not None, len(soft), util)
    lb, ub = lb.copy(), ub.copy()
    lb[idx["dyn0"]] = ub[idx["dyn0"]] = s_init
    ub[idx["raise0"]], ub[idx["lower0"]] = s_init, E - s_init
    ub[idx["budget"]] = max(0.0, asset.limits.cycles_per_day * E if budget is None else budget)
    if site is not None:
        load = np.asarray(load, float)
        ub[idx["raise_site"]] = site.export_limit_mw + load
        ub[idx["export"]] = (site.export_limit_mw if export_energy else 0.0) + load
        ub[idx["import"]] = ub[idx["lower_site"]] = site.import_limit_mw - load
    nx = A.shape[1]
    lo = np.zeros(nx)
    hi = np.r_[np.full(2 * n, P), np.full(n, E), np.ones(n), np.full(4 * n, P if price_f is not None else 0.0)]
    if nx > 8 * n:
        hi = np.r_[hi, np.full(nx - 8 * n, np.inf)]
    if floor is not None:
        lo[2 * n:3 * n] = np.minimum(floor, E)
    if fcess_hi is not None and price_f is not None:
        for k, name in enumerate(FCESS):
            hi[(4 + k) * n:(5 + k) * n] = np.clip(np.minimum(P, fcess_hi[name]), 0.0, None)
    pe = np.asarray(price_e, float)
    cost = np.zeros(nx)
    cost[:n], cost[n:2 * n] = pe * DT, (wear - pe) * DT
    if price_f is not None:
        for k, name in enumerate(FCESS):
            cost[(4 + k) * n:(5 + k) * n] = -np.asarray(price_f[name], float) * DT
        if util[0]:  # regulation raise used: energy sold at the price, less the battery's wear (no hurdle: AGC
            # dispatches it, it is not a trade on the forecast)
            cost[4 * n:5 * n] += util[0] * DT * (asset.wear_per_mwh - pe)
        if util[1]:  # regulation lower used: energy bought at the price
            cost[6 * n:7 * n] += util[1] * DT * pe
    curve = np.ndim(terminal_value) > 0
    cost[3 * n - 1] = 0.0 if curve else -terminal_value
    for (cap, kind, penalty, comp), (r1, r2), cols in zip(layers, idx["layers"], idx["slack"]):
        ub[r1] = ub[r2] = np.asarray(cap, float)
        cost[cols] = 0.0 if comp else (penalty * DT if kind == "slot" else penalty)
    if comp_floor is not None:
        lb[idx["floor"]] = np.asarray(comp_floor, float)
        ub[idx["comp"]] = 0.0
    for (fl, value), rows_s, z in zip(soft, idx["soft"], idx["soft_cols"]):
        lb[rows_s] = np.asarray(fl, float)
        cost[z] = value
    integrality = np.r_[np.zeros(3 * n), np.ones(n), np.zeros(nx - 4 * n)]
    if curve:
        # stored energy at the end valued by a concave curve: segment j (MWh) is worth terminal_value[j] per MWh,
        # the segments together hold at most the energy stored (non-increasing values fill in order)
        m = np.asarray(terminal_value, float)
        width = E / len(m)
        row = coo_matrix((np.r_[-1.0, np.ones(len(m))], (np.zeros(len(m) + 1, int), np.r_[3 * n - 1, nx + np.arange(len(m))])),
                         shape=(1, nx + len(m)))
        A = sp_vstack([sp_hstack([A, coo_matrix((A.shape[0], len(m)))]), row]).tocsr()
        lb, ub = np.r_[lb, -np.inf], np.r_[ub, 0.0]
        lo, hi = np.r_[lo, np.zeros(len(m))], np.r_[hi, np.full(len(m), width)]
        cost, integrality = np.r_[cost, -m], np.r_[integrality, np.zeros(len(m))]
    return _Milp(cost, A, lb, ub, lo, hi, integrality, idx, pe, price_f, wear, terminal_value, util,
                 bool(layers or comp_floor is not None or soft), nx)


def _least_shortfall(m: _Milp) -> float | None:
    """The least total compliance shortfall a schedule can reach (None if the solver finds none); the MILP is left
    bounding its shortfall to that."""
    least = np.zeros(len(m.cost))
    least[m.idx["comp_cols"]] = m.idx["comp_vals"]
    m.ub[m.idx["comp"]] = np.inf
    first = m.solve(least)
    if first.status != 0:
        return None
    shortfall = max(0.0, float(first.fun))
    m.ub[m.idx["comp"]] = shortfall * (1 + 1e-6) + 1e-6
    return shortfall


def _schedule(m: _Milp, asset: Asset, x: np.ndarray, shortfall: float) -> dict:
    """plan's result from a solution x of its MILP."""
    n, pe, price_f, util = len(m.pe), m.pe, m.price_f, m.util
    c, d = x[:n], x[n:2 * n]
    out = {"net": d - c, "soc": x[2 * n:3 * n], **{name: x[(4 + k) * n:(5 + k) * n] for k, name in enumerate(FCESS)}}
    out["energy_value"] = float((pe * (d - c)).sum() * DT)
    out["fcess_value"] = float(sum((np.asarray(price_f[k], float) * out[k]).sum() for k in FCESS) * DT) if price_f is not None else 0.0
    out["wear"] = float(m.wear * d.sum() * DT)
    if np.ndim(m.terminal_value) > 0:
        out["stored_value"] = float(np.asarray(m.terminal_value, float) @ x[m.nx0:])
    else:
        out["stored_value"] = float(m.terminal_value * out["soc"][-1])
    out["value"] = out["energy_value"] + out["fcess_value"] - out["wear"] + out["stored_value"]
    if any(util):
        rr, rl = x[4 * n:5 * n], x[6 * n:7 * n]
        out["value"] += float(((pe - asset.wear_per_mwh) * util[0] * rr - pe * util[1] * rl).sum() * DT)
    out["money"] = out["value"] + out["wear"] - float(asset.wear_per_mwh * d.sum() * DT)  # the value without the hurdle
    out["shortfall"] = shortfall  # the least total compliance shortfall (0 if every requirement holds)
    out["shortfall_mwh"] = float(x[m.idx["comp_cols"][:n]].max()) if "comp_cols" in m.idx and shortfall > 0 else 0.0
    if m.report:
        out["penalty"] = float(m.cost[8 * n:m.nx0] @ x[8 * n:m.nx0])
        out["slack"] = [x[cols] for cols in m.idx["slack"]]
    return out


def plan(asset: Asset, price_e, price_f=None, s_init: float = 0.0, budget: float | None = None, floor=None,
         site: Site | None = None, load=None, terminal_value: float = 0.0, wear_per_mwh: float | None = None,
         layers=(), fcess_hi: dict | None = None, export_energy: bool = True, comp_floor=None, soft=(),
         reg_util: tuple = (0.0, 0.0)) -> dict:
    """Best schedule for the remaining n intervals on the given prices.

    price_e: $/MWh per interval; price_f: {product: $/MW/h} or None for energy only; budget: MWh the
    battery may still discharge today (default: the limits' cycles a day); floor: minimum stored MWh
    at the end of each interval; site, load: the connection limits and the site's load (MW) per
    interval; terminal_value: $ per MWh still stored at the end, or a concave curve: one $/MWh per equal
    segment of the battery's energy, non-increasing (vpp.storage_value); wear_per_mwh: $ per MWh discharged
    (default: the asset's wear).
    layers: caps on net charging (charge - discharge, MW) as (cap (n,), kind, penalty, compliance):
    an economic cap pays `penalty` $/MWh over the cap in each interval ("slot") or $/MW on the largest
    excess ("peak"); the penalties steer the plan and are not part of its value. A compliance cap must
    hold (its penalty is ignored). comp_floor: stored MWh that compliance requires at the end of each
    interval (instead of `floor`, a plain bound). If the compliance floor and caps cannot all hold, the
    plan first keeps their total shortfall to the least possible, then optimises value with that
    shortfall ("shortfall" in the result: summed over intervals; "shortfall_mwh": the largest floor
    shortfall). soft: economic floors as (floor (n,), $ per MWh of its largest shortfall). fcess_hi:
    {product: (n,) most MW enabled} or None. export_energy=False: no net energy export at the meter (it
    would earn nothing under a fixed retail contract); FCESS raise headroom still has the connection's
    export limit. reg_util: MWh delivered / taken per MW-hour of regulation raise / lower enabled - it moves
    the stored energy, earns or pays the energy price and wears the battery. Returns the schedule and its
    value at these prices (energy + FCESS - wear + stored energy left).
    """
    m = _milp(asset, price_e, price_f, s_init, budget, floor, site, load, terminal_value, wear_per_mwh, layers, fcess_hi,
              export_energy, comp_floor, soft, reg_util)
    res = m.solve()
    shortfall = 0.0
    if res.status != 0 and comp_floor is not None:
        # no plan meets every compliance requirement: first the least total shortfall, then the best value with it
        least = _least_shortfall(m)
        if least is not None:
            shortfall, res = least, m.solve()
    if res.status != 0:
        raise RuntimeError(f"plan infeasible ({res.message}); s_init={s_init:.3f}, budget={budget}")
    return _schedule(m, asset, res.x, shortfall)


def worst_weights(values, K, lo: float, hi: float, w_lo: float, w_hi: float) -> np.ndarray:
    """The scenario weights w that make sum_s w_s values_s least, with w_lo <= w_s <= w_hi, sum 1 and
    lo <= sum_s w_s K_s <= hi."""
    K = np.asarray(K, float)
    res = linprog(np.asarray(values, float), A_ub=np.vstack([-K, K]), b_ub=[-lo, hi], A_eq=np.ones((1, len(K))), b_eq=[1.0],
                  bounds=(w_lo, w_hi), method="highs")
    if res.status != 0:
        raise RuntimeError(f"no weights in the ambiguity set ({res.message})")
    return res.x


def plan_sp(asset: Asset, prices_e, prices_f, first: int, ambiguity: tuple | None = None, **kw) -> dict:
    """Two-stage stochastic plan over S price scenarios (rows of prices_e; prices_f: one {product: prices} per
    scenario, or None). The first `first` intervals have one schedule, the same in every scenario, because it is
    carried out before the next re-plan (non-anticipativity); after them each scenario has its own schedule, which
    knows that scenario's prices, so the plan's value is optimistic. Every scenario keeps all of plan's limits,
    floors and caps (kw: plan's other arguments).

    Objective: the expected value over equally weighted scenarios, or, with ambiguity = (K, lo, hi, w_lo, w_hi),
    the worst expected value over the weights w with w_lo <= w_s <= w_hi, sum 1 and lo <= sum_s w_s K_s <= hi
    (K: a count per scenario, such as its intervals at or above a spike price). Writing w = w_lo + u, the worst
    case enters through LP duality - maximise w_lo sum_s value_s + a (1 - S w_lo) + b (lo - w_lo sum K)
    - g (hi - w_lo sum K) - (w_hi - w_lo) sum_s e_s with a + (b - g) K_s - e_s <= value_s and b, g, e >= 0 - so
    the plan stays one MILP.

    Returns plan's keys, the schedule being the first stage and then the scenarios' weighted average, and every
    amount weighted by the weights the objective used ("weights"); "values" / "moneys": each scenario's value less
    penalties / money (plan's "money");
    "objective": the objective's value (weights @ values)."""
    pe = np.asarray(prices_e, float)
    S, n = pe.shape
    first = min(first, n)
    ms = [_milp(asset, pe[s], None if prices_f is None else prices_f[s], **kw) for s in range(S)]
    nx, nr = ms[0].A.shape[1], ms[0].A.shape[0]
    stage1 = np.array([k * n + t for k in range(8) for t in range(first)])
    m1 = (S - 1) * len(stage1)
    cols = np.ravel([[s * nx + j, j] for s in range(1, S) for j in stage1])
    same = coo_matrix((np.tile([1.0, -1.0], m1), (np.repeat(np.arange(m1), 2), cols)), shape=(m1, S * nx))
    extra = 0 if ambiguity is None else S + 3  # a, b, g, e_1..e_S

    def solve():
        A = sp_vstack([block_diag([m.A for m in ms]), same])
        lb = np.r_[np.concatenate([m.lb for m in ms]), np.zeros(m1)]
        ub = np.r_[np.concatenate([m.ub for m in ms]), np.zeros(m1)]
        lo, hi = np.concatenate([m.lo for m in ms]), np.concatenate([m.hi for m in ms])
        integrality = np.concatenate([m.integrality for m in ms])
        if ambiguity is None:
            cost = np.concatenate([m.cost / S for m in ms])
        else:
            K, k_lo, k_hi, w_lo, w_hi = ambiguity
            K = np.asarray(K, float)
            # cost_s @ x_s + a + (b - g) K_s - e_s <= 0, i.e. a + (b - g) K_s - e_s <= value_s
            dual = sp_hstack([block_diag([m.cost[None, :] for m in ms]),
                              coo_matrix(np.c_[np.ones(S), K, -K, -np.eye(S)])])
            A = sp_vstack([sp_hstack([A, coo_matrix((A.shape[0], extra))]), dual])
            lb, ub = np.r_[lb, np.full(S, -np.inf)], np.r_[ub, np.zeros(S)]
            lo, hi = np.r_[lo, -np.inf, np.zeros(S + 2)], np.r_[hi, np.full(S + 3, np.inf)]
            integrality = np.r_[integrality, np.zeros(extra)]
            cost = np.r_[np.concatenate([w_lo * m.cost for m in ms]), -(1 - S * w_lo), -(k_lo - w_lo * K.sum()),
                         k_hi - w_lo * K.sum(), np.full(S, w_hi - w_lo)]
        return milp(cost, constraints=LinearConstraint(A.tocsr(), lb, ub), bounds=Bounds(lo, hi), integrality=integrality)

    res, shortfall = solve(), 0.0
    if res.status != 0 and kw.get("comp_floor") is not None:
        # as plan: the least total compliance shortfall first (it does not depend on the prices), then the best value
        least = _least_shortfall(ms[0])
        if least is not None:
            for m in ms[1:]:
                m.ub[m.idx["comp"]] = ms[0].ub[ms[0].idx["comp"]]
            res, shortfall = solve(), least
    if res.status != 0:
        raise RuntimeError(f"stochastic plan infeasible ({res.message})")
    outs = [_schedule(m, asset, res.x[s * nx:(s + 1) * nx], shortfall) for s, m in enumerate(ms)]
    values = np.array([-m.cost @ res.x[s * nx:(s + 1) * nx] for s, m in enumerate(ms)])  # value less penalties
    w = np.full(S, 1 / S) if ambiguity is None else worst_weights(values, *ambiguity)
    out = {k: np.r_[outs[0][k][:first], sum(wi * o[k][first:] for wi, o in zip(w, outs))] for k in ["net", "soc", *FCESS]}
    for k in ("energy_value", "fcess_value", "wear", "stored_value", "value", "money", "penalty"):
        if k in outs[0]:
            out[k] = float(sum(wi * o[k] for wi, o in zip(w, outs)))
    out.update(shortfall=shortfall, shortfall_mwh=max(o["shortfall_mwh"] for o in outs), weights=w, values=values,
               moneys=np.array([o["money"] for o in outs]),
               objective=-float(res.fun))
    return out


def stored_energy_value(bat: Battery, price_e) -> float:
    """$ per MWh still stored at the end of the day: what buying it back would cost (cheapest 4 hours / eta)."""
    return max(0.0, float(np.sort(np.asarray(price_e, float))[:8].mean())) / bat.eta


def charge_room(bat: Battery, site: Site | None = None, load=None, caps=None) -> np.ndarray:
    """Most stored MWh the battery can add in each interval: full power, within the site's import room
    and, if given, `caps` (MW the battery may charge under caps that rank above obligations)."""
    rate = np.full(N, bat.power_mw)
    if site is not None:
        rate = np.clip(np.minimum(rate, site.import_limit_mw - np.asarray(load, float)), 0.0, None)
    if caps is not None:
        rate = np.clip(np.minimum(rate, caps), 0.0, None)
    return bat.eta * rate * DT


def _deliver_mwh(bat: Battery, mw: float, load, a: float, b: float) -> float:
    """MWh the battery sends out from `a` to `b` hours after 08:00 to deliver `mw` net at the meter while
    also supplying the site's load (MW per interval), at most its power."""
    load = np.asarray(load, float)
    total = 0.0
    for k in range(int(a // DT), min(N, int(np.ceil(b / DT - 1e-9)))):
        lo, hi = max(a, k * DT), min(b, (k + 1) * DT)
        if hi > lo:
            total += min(bat.power_mw, mw + load[k]) * (hi - lo)
    return total


@dataclass(frozen=True)
class Obligation:
    """A commitment that holds stored energy back from trading: from trading interval `start` to
    `end` (slots after 08:00) the battery must be able to deliver `mw`, so it must be ready when the
    window opens.

    event_hours=None: deliver until the window closes, as for capacity credits (the Reserve Capacity
    Obligation in the ESR obligation window). event_hours=h: an event of up to h hours may be called
    at any time in the window, as for a Supplementary Capacity or NCESS contract.
    at_meter: `mw` is net export at the site's meter, so while it delivers the battery also supplies the
    site's load (a market obligation of a facility that contains the load). through_end: the need stays
    at mw x event_hours to the end of the window instead of running down with it (a backup reserve).
    compliance: it cannot be breached (a hard floor; real time never sells the energy it needs). If
    False it is an economic choice: a soft floor worth value_per_mwh $ per MWh of its largest shortfall.
    known_h: hours after this trading day's 08:00 at which the information behind it is public
    (negative: before the day); see timing_errors.
    """
    name: str
    start: int
    end: int
    mw: float
    event_hours: float | None = None
    at_meter: bool = False
    through_end: bool = False
    compliance: bool = True
    value_per_mwh: float = 0.0
    known_h: float = 0.0

    def need(self, bat: Battery, hours: float, load=None) -> float:
        """Stored MWh it requires `hours` after 08:00; load: the site's planned load (MW per interval)."""
        start, end = self.start * DT, self.end * DT
        if not start <= hours <= end:
            return 0.0
        if self.through_end:
            left = self.event_hours
        else:
            left = end - hours if self.event_hours is None else min(self.event_hours, end - hours)
        if not self.at_meter or load is None:
            return self.mw * left / bat.eta
        return _deliver_mwh(bat, self.mw, load, hours, hours + left) / bat.eta


@dataclass(frozen=True, eq=False)
class CapLayer:
    """A cap on net import at the site's meter (site load + charging - discharging, MW per interval;
    negative = the meter must export), or on the battery's own net charging if not at_meter.

    compliance: a requirement that cannot be breached (a called contract event): plans must keep it
    (see plan) and real time meets it with any stored energy, even past the cycle limit. Otherwise it
    is an economic choice (the network demand charge, IRCR, an activation-only call): kind "slot" pays
    `penalty` $/MWh over the cap in each interval, "peak" `penalty` $/MW on the day's largest excess
    (a monthly demand charge); penalties steer decisions and are never settled, and real time meets
    the cap only from energy above what compliance needs and within the cycle limit.
    running_max: the cap is a billing period's highest half-hourly import (a demand charge): once an
    interval has raised that maximum, later intervals up to the new level cost nothing more, so the cap
    rises with the highest import already metered today. known_h: as for Obligation.
    """
    name: str
    cap: np.ndarray
    kind: str = "slot"
    penalty: float = 1e4
    at_meter: bool = True
    compliance: bool = False
    running_max: bool = False
    known_h: float = 0.0

    def limit(self, load) -> np.ndarray:
        """Most net charging (MW) the cap allows per interval, given the site's load."""
        cap = np.asarray(self.cap, float)
        return cap - np.asarray(load, float) if self.at_meter and load is not None else cap


@dataclass(frozen=True, eq=False)
class Reveal:
    """Information that arrives at the start of interval `slot`: a new energy / FCESS forecast from that
    slot on, caps and obligations that start to apply, ones that stop (`drop`, by name), and MWh added
    to the day's cycle budget. The rest of the day is re-planned from the actual state of charge."""
    slot: int
    label: str
    energy: np.ndarray | None = None
    fcess: dict | None = None
    layers: tuple = ()
    obligations: tuple = ()
    drop: tuple = ()
    budget_extra_mwh: float = 0.0


@dataclass(frozen=True)
class CapacityTest:
    """Reserve Capacity refunds: in each 5-min dispatch interval of the window the facility must be able
    to deliver rcoq_mw (net at the meter if at_meter) from its stored energy (12 x MWh for 5 minutes).
    run_day reports the shortfall in MW x trading intervals; the refund price is applied by the caller."""
    start: int
    end: int
    rcoq_mw: float
    at_meter: bool = True


@dataclass(frozen=True)
class AvailabilityTest:
    """A contract's availability: each trading interval of the service window is available if the
    battery could deliver mw then - enough power (net of the site's load at the meter if
    standby.at_meter) and what `standby` needs stored at its start - or, during a called event, if it
    delivers at least min_delivery x mw (net export at the meter if standby.at_meter, else the
    battery's own output). After the day's event it is not called again, so the rest of the window
    counts as available."""
    name: str
    start: int
    end: int
    mw: float
    standby: Obligation
    events: tuple = ()
    min_delivery: float = 0.9
    per_5min: bool = False  # registered form: each 5 minutes is available if the facility could inject mw then

    def in_event(self, t: int) -> bool:
        return any(a <= t < b for a, b in self.events)

    def after_events(self, t: int) -> bool:
        return bool(self.events) and t >= max(b for _, b in self.events)


@dataclass(frozen=True)
class Reserve:
    """Stored energy kept for price spikes, an economic choice made at the start of interval `slot`. From then on
    plans keep `mwh` stored at the end of every interval from start - 1 to end - 1, so it is there when the
    window opens and is not planned away inside it (a soft floor worth `value` $ per MWh of its largest
    shortfall: it steers and is never settled). Only the standing offer sells it, priced as if the reserve
    were not there; what the offer sells inside the window comes off it for the rest of the day."""
    mwh: float
    start: int
    end: int
    slot: int = 2
    value: float = 1000.0


@dataclass(frozen=True, eq=False)
class DayTerms:
    """Everything a day's contracts and site bill add to the dispatcher (see run_day)."""
    obligations: tuple = ()
    layers: tuple = ()
    reveals: tuple = ()
    capacity: CapacityTest | None = None
    availability: tuple = ()
    fcess_hi: dict | None = None


def timing_errors(terms: DayTerms) -> list[str]:
    """Items a day's terms would use before their information is public: at the morning plan (08:00)
    or at the start of the interval that reveals them. Empty when the terms respect the timing."""
    out = [f"{x.name}: used at 08:00, public {x.known_h:+g} h" for x in (*terms.obligations, *terms.layers) if x.known_h > 1e-9]
    for r in terms.reveals:
        at = max(r.slot, 0) * DT
        out += [f"{x.name}: revealed at {at:g} h, public {x.known_h:+g} h"
                for x in (*r.obligations, *r.layers) if x.known_h > at + 1e-9]
    return out


def obligation_need(bat: Battery, obligations, hours: float, load=None) -> float:
    """Stored MWh all obligations require `hours` after 08:00."""
    return sum(o.need(bat, hours, load) for o in obligations)


def keep_for_obligation(bat: Battery, obligations, hours: float, room=None, load=None) -> float:
    """Stored MWh that cannot be sold at `hours`: what the obligations need now or, before a window
    opens, what could not be recharged in time (room = most MWh the battery can add per interval)."""
    keep = obligation_need(bat, obligations, hours, load)
    for start in sorted({o.start for o in obligations if hours < o.start * DT}):
        if room is None:
            refill = bat.eta * bat.power_mw * (start * DT - hours)
        else:
            t = int(hours // DT)
            refill = room[t] * ((t + 1) * DT - hours) / DT + room[t + 1:start].sum()
        keep = max(keep, obligation_need(bat, obligations, start * DT, load) - refill)
    return max(0.0, keep)


def plan_floor(bat: Battery, t0: int, s_init: float, obligations=None, risk=None, prefs: RiskPrefs | None = None,
               room=None, load=None, forced=None) -> np.ndarray:
    """Minimum stored MWh at the end of intervals t0..N-1 from the obligations and the risk reserve
    (risk: spike probabilities; prefs: the reserve's threshold and size), capped at what charging from
    s_init can reach (forced: MWh per interval that caps ranking above the obligations take out, so
    the reachable energy can also fall)."""
    t = np.arange(t0, N)
    floor = np.zeros(len(t))
    if obligations:
        floor = np.maximum(floor, [obligation_need(bat, obligations, (k + 1) * DT, load) for k in t])
    if prefs is not None and prefs.risk_threshold is not None and risk is not None:
        risky_next = np.r_[np.asarray(risk)[t0 + 1:], 0.0] >= prefs.risk_threshold
        floor = np.maximum(floor, np.where(risky_next, prefs.reserve_mwh, 0.0))
    step = np.full(len(t), bat.eta * bat.power_mw * DT) if room is None else np.asarray(room)[t0:]
    if forced is None:
        return np.minimum(floor, np.minimum(s_init + np.cumsum(step), bat.energy_mwh))
    reach, s = np.empty(len(t)), s_init
    for k, (up, down) in enumerate(zip(step, np.asarray(forced)[t0:])):
        s = min(bat.energy_mwh, max(0.0, s + (up if down <= 0 else -down)))
        reach[k] = s
    return np.minimum(floor, reach)


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


def value_offer_price(asset: Asset, strat: Strategy, prefs: RiskPrefs, t: int, j: int, s: float, budget: float,
                      planned: float, sell: float, fc_e, fc_f, price_now: float, replan,
                      fcess_now: dict | None = None, last: bool = False) -> tuple[float, float]:
    """"value" rule: offer price for the rest of interval t from re-planning the rest of the day twice,
    running at the planned power until the interval ends or selling at `sell` MW instead. Energy and
    FCESS forecasts are both nudged towards the latest prices seen before the offer is made, those of the
    previous 5-min step (price_now; fcess_now: its FCESS prices).
    replan(first interval, energy forecast, FCESS forecast, stored MWh, budget) returns the plan of the
    rest of the day with the day's floors and caps, or None if there is none. last: the last interval is
    priced the same way, replan(N, ...) returning what is stored at the day's end is worth; without it the
    last interval loses nothing by selling and is offered at k_min.

    Returns (offer price, the FCESS part of the value lost per MWh sold).
    """
    bat = asset.battery
    rem = (STEPS - j) * DT5
    extra = (sell - planned) * rem  # MWh more delivered to the grid by selling
    if extra <= 1e-9:
        return np.inf, 0.0
    dw = decision_wear(asset, prefs)
    lost = lost_fcess = 0.0
    if t < N - 1 or last:
        fc_next = nowcast(fc_e, t, price_now)
        f_next = nowcast_fcess(fc_f, t, fcess_now)
        branches = []
        for power in (planned, sell):
            s1 = s - power * rem / bat.eta if power > 0 else s - power * rem * bat.eta
            s1 = min(max(s1, 0.0), bat.energy_mwh)
            b1 = budget - max(power, 0.0) * rem
            branch = replan(t + 1, fc_next, f_next, s1, b1)
            if branch is None:
                return np.inf, 0.0
            branches.append(branch)
        if branches[1].get("shortfall", 0.0) > branches[0].get("shortfall", 0.0) + 1e-6:
            return np.inf, 0.0  # selling now would leave a compliance requirement short later
        # caps the sale would break later (their penalties) count as value lost
        lost = (branches[0]["value"] - branches[0].get("penalty", 0.0)) - (branches[1]["value"] - branches[1].get("penalty", 0.0))
        lost_fcess = branches[0]["fcess_value"] - branches[1]["fcess_value"]
    wear_now = dw * (sell - max(planned, 0.0)) * rem
    return max(strat.k_min, (strat.alpha * lost + wear_now) / extra), lost_fcess / extra


def _head(out: dict, n: int) -> dict:
    """A plan cut back to its first n intervals (the rest of a plan that ran past the end of the day)."""
    cut = {k: np.asarray(v)[:n] for k, v in out.items() if k in ("net", "soc", *FCESS)}
    if "slack" in out:
        cut["slack"] = [np.atleast_1d(np.asarray(a))[:n] for a in out["slack"]]
    return {**out, **cut}


def _copy_plan(out: dict) -> dict:
    """A copy of a plan's results that an observer may keep or change without touching the plan in use."""
    return {k: v.copy() if isinstance(v, np.ndarray) else [a.copy() for a in v] if isinstance(v, list) else v
            for k, v in out.items()}


def _reveal(r: Reveal, t: int, fc_e, fc_f, obligations: list, layers: list, budget: float):
    """Apply a Reveal at the start of interval t: forecasts from t on, obligations and caps, budget."""
    if r.energy is not None:
        fc_e = np.r_[np.asarray(fc_e, float)[:t], np.asarray(r.energy, float)[t:]]
    if r.fcess is not None and fc_f is not None:
        fc_f = {k: np.r_[np.asarray(fc_f[k], float)[:t], np.asarray(r.fcess[k], float)[t:]] for k in FCESS}
    obligations = [o for o in obligations if o.name not in r.drop] + list(r.obligations)
    layers = [x for x in layers if x.name not in r.drop] + list(r.layers)
    return fc_e, fc_f, obligations, layers, budget + r.budget_extra_mwh


def run_day(asset: Asset, strat: Strategy, prefs: RiskPrefs, fc_e, fc_f, rtp, act_f, p5, obligations=None, risk=None,
            s_start=0.0, p5_f=None, site: Site | None = None, load_fc=None, load_act=None, terms: DayTerms | None = None,
            export_energy: bool = True, reg_util: tuple = (0.0, 0.0), scen: dict | None = None,
            reserve: Reserve | None = None, terminal: np.ndarray | None = None, bids=None, on_plan=None,
            offer_lag: int = 1, extend=None, offer_end: bool = False):
    """Plan at 08:15 on the forecast, execute in 5-min steps with the standing offer, settle at actuals.

    asset: the battery, its operating limits and wear; strat: how it decides; prefs: the risk
    preferences (prefs.load_margin is 0 when the load is known). fc_e/rtp: 48 forecast / actual energy
    prices; fc_f/act_f: {product: 48 prices} or fc_f=None for energy only; p5 / p5_f: 288 actual 5-min
    energy / {FCESS product: price} prices; obligations: the day's Obligations or None; risk: 48 spike
    probabilities; site, load_fc / load_act: the connection and the site's forecast / actual load (MW)
    per interval; export_energy=False: no net energy export at the meter (see plan). reg_util: MWh the
    battery delivers (regulation raise) / takes (lower) per MW-hour enabled, settled at the energy price:
    plans include it, real time moves the stored energy by it, and regulation is offered at what its use
    costs (wear and efficiency loss), enabled only when its price covers that. Returns (summary dict, per-interval log).

    terms (DayTerms): what contracts and the site bill add - more obligations and caps on the meter
    (CapLayer), each a compliance requirement or an economic choice. Plans keep every compliance floor
    and cap, or the least total shortfall when they cannot all hold (reported as plans_short and
    plan_shortfall_mwh); economic obligations are soft floors. Real time meets, in this order, the
    compliance caps (from any stored energy, even past the cycle limit), the compliance obligations'
    safety charging, then the economic caps (only from energy above what compliance needs, within the
    cycle limit). Information arriving during the day (Reveal: e.g. the 09:00 forecast after an 08:00
    bridge plan, event notices) is followed by a re-plan from the actual state of charge; the capacity
    and availability tests are scored. With terms the summary and log also report the meter, the caps
    and the tests. terms=None is the plain dispatcher of the 1 MW study, unchanged.

    scen: the day's price scenarios for the planners "mean", "sp" and "dro" (see Strategy; they need terms):
    {"slot": the interval they are issued at, "energy": (S, 48) $/MWh, "fcess": {product: (S, 48)} planning
    prices, "spike": the $/MWh whose count per scenario the ambiguity bounds}; plans from "slot" on use them.
    Without them those planners plan on the forecast ("roll"). With terms the summary also compares the first
    plan on a revealed forecast with what followed: its expected money from that interval on (at the asset's
    wear, stored energy at the plan's value; the scenarios' equal-weight mean) and the money actually made then.
    reserve: stored energy kept for spikes (Reserve; needs terms).
    terminal: with terms, the plans value the energy stored at the end of the day by this concave curve ($ per MWh
    of each equal segment, vpp.storage_value) instead of its buy-back value. bids: with offer_rule "table", the
    day's layered offers (vpp.storage_value.DayBids): each 5 minutes the standing offer sells, beyond the plan,
    the MWh whose threshold the price reaches. Bids with a buy_margin (vpp.v2.bids.LadderBids, v2 round 4) also bid
    to buy in steps whose plan charges or idles (their buy()); a step that departs from the plan that way is followed,
    at the end of its interval, by a re-plan as after a sale.
    on_plan(t0, kind, plan, ctx): called with a copy of every plan the day adopts, from interval t0 on; kind is
    "morning", "reveal", "scheduled" or "trigger" (after an offer sale), ctx what the plan was made from (stored
    energy, budget, floors with their names, caps, prices, load, terminal value). It only observes. offer_lag: the
    standing offer is priced from the 5-min price this many steps back (1: the step just before), a sensitivity
    for offers that must be in before a gate closure.
    extend(t0, prices, fcess, kw, cap names): plans from t0 may run past the end of the day (vpp.v2.horizon): it
    returns the prices, FCESS prices and plan arguments lengthened; the plan is then cut back to the day.
    offer_end: the "value" rule also prices the last interval, against what the energy stored at the day's end is worth
    (the plans' terminal value) less the economic floors' penalty there (needs terms); without it the last interval
    is offered at k_min.
    """
    bat = asset.battery
    P, E, eta, tau = bat.power_mw, bat.energy_mwh, bat.eta, asset.limits.fcess_sustain_h
    fcess = fc_f is not None
    load_margin = prefs.load_margin
    lf = None if site is None else np.asarray(load_fc, float) * (1 + load_margin)  # what plans assume the site draws
    lfe = None if site is None else np.asarray(load_fc, float)  # what it is expected to draw: economic caps are priced on this
    la = np.zeros(N) if site is None else np.asarray(load_act, float)
    imp_room = np.full(N, np.inf) if site is None else site.import_limit_mw - la  # most net charging, MW
    exp_room_f = np.full(N, np.inf) if site is None else site.export_limit_mw + la  # most net discharging incl. FCESS raise, MW
    exp_room = exp_room_f if export_energy or site is None else la  # most net discharging for energy, MW
    full = terms is not None
    terms = terms if full else DayTerms()
    planner = strat.planner if scen is not None or strat.planner in ("point", "roll") else "roll"
    if planner != "point" and not full:
        raise ValueError(f"planner {planner!r} needs the day's terms")
    if offer_end and not full:
        raise ValueError("offer_end needs the day's terms")
    obls, layers = list(obligations or []) + list(terms.obligations), list(terms.layers)
    budget = asset.limits.cycles_per_day * E
    reveals = sorted(terms.reveals, key=lambda r: r.slot)
    for r in reveals:
        if r.slot <= 0:  # known before the morning plan
            fc_e, fc_f, obls, layers, budget = _reveal(r, 0, fc_e, fc_f, obls, layers, budget)
    reveals = [r for r in reveals if r.slot > 0]
    comp = [o for o in obls if o.compliance]  # obligations that cannot be breached

    def caps_state(layers):
        """MW the battery may charge under the compliance caps, and the MWh per interval those caps force
        out (None, None without such caps)."""
        above = [x.limit(lf) for x in layers if x.compliance]
        if not above:
            return None, None
        lim = np.min(above, axis=0)
        return np.clip(lim, 0.0, None), np.clip(-lim, 0.0, None) * DT / eta

    def econ_room(layers, charge_caps):
        """Most stored MWh the battery can add per interval without breaking the economic caps on the meter:
        the refill discretionary actions may count on (compliance charging may still break those caps)."""
        econ = [np.clip(x.limit(lf), 0.0, None) for x in layers if not x.compliance and x.at_meter and site is not None]
        caps = charge_caps if not econ else np.min(econ + ([] if charge_caps is None else [charge_caps]), axis=0)
        return charge_room(bat, site, lf, caps)

    charge_caps, forced = caps_state(layers)
    room = charge_room(bat, site, lf, charge_caps)
    room_e = econ_room(layers, charge_caps)
    value_rule = strat.offer and strat.offer_rule == "value"
    table_rule = strat.offer and strat.offer_rule == "table"
    if table_rule and bids is None:
        raise ValueError("offer rule 'table' needs the day's bids")
    buy_rule = table_rule and getattr(bids, "buy_margin", None) is not None  # the bids also buy (vpp.v2.bids)
    bid_mwh, moved = np.zeros(N), np.zeros(N, bool)  # MWh bought beyond (+) or short of (-) the plan; departures
    k_offer = np.full(N, np.inf) if value_rule or table_rule else offer_prices(strat, fc_e)
    offer_fcess, offer_mwh = np.full(N, np.nan), np.zeros(N)
    tv = stored_energy_value(bat, fc_e)
    dw = decision_wear(asset, prefs)

    def use_cost(tv):
        """$ per MW-hour enabled that regulation's use costs (wear, and the energy lost to efficiency at the
        stored energy's value), by product; 0 without use."""
        return {"reg_raise": reg_util[0] * (asset.wear_per_mwh + (1 / eta - 1) * tv), "reg_lower": reg_util[1] * (1 - eta) * tv,
                "cont_raise": 0.0, "cont_lower": 0.0}

    offer_f = use_cost(tv)
    reg_out = reg_in = 0.0
    fallbacks = plans_short = 0
    plan_short = 0.0

    def cap_now(x, t0, load):
        """A cap's limit on net charging from interval t0, a running maximum raised to the highest import
        already metered today (intervals before t0)."""
        if x.running_max and t0 > 0 and site is not None:
            x = replace(x, cap=np.maximum(np.asarray(x.cap, float), meter[:t0].max()))
        return x.limit(load)

    def floors(t0, offer=False, s0=None, names=False):
        """With terms: the compliance floor (stored MWh at the end of intervals t0..) and the economic
        floors as (floor, $ per MWh of the largest shortfall): the economic obligations, the risk reserve and,
        except in the offer rule's what-if plans, what is left of the spike reserve (from the window's start,
        at most the s0 MWh stored then: the reserve is never bought inside its window). names: also return
        the economic floors' names, in the same order."""
        t = np.arange(t0, N)
        hard = np.array([obligation_need(bat, comp, (k + 1) * DT, lf) for k in t]) if comp else np.zeros(len(t))
        soft = [(np.array([o.need(bat, (k + 1) * DT, lf) for k in t]), o.value_per_mwh) for o in obls if not o.compliance]
        labels = [o.name for o in obls if not o.compliance]
        if prefs.risk_threshold is not None and risk is not None:
            risky_next = np.r_[np.asarray(risk)[t0 + 1:], 0.0] >= prefs.risk_threshold
            soft.append((np.where(risky_next, prefs.reserve_mwh, 0.0), prefs.reserve_value))
            labels.append("risk reserve")
        if reserve is not None and not offer and t0 >= reserve.slot and res_left > 1e-9:
            level = res_left if t0 < reserve.start or s0 is None else min(res_left, s0)
            soft.append((np.where((t >= reserve.start - 1) & (t < reserve.end), level, 0.0), reserve.value))
            labels.append("spike reserve")
        return (hard, soft, labels) if names else (hard, soft)

    def scen_plan(t0, seen, kw):
        """Plan intervals t0.. on the scenarios (planner "mean", "sp" or "dro"). seen: (interval, energy price, FCESS
        prices) of the latest 5-min prices, which every scenario is nudged towards as the forecast is; None: none."""
        e, f = scen["energy"], scen["fcess"] if fcess else None
        n_s = len(e)
        if seen is None:
            pe = e[:, t0:]
            pf = None if f is None else [{k: f[k][j, t0:] for k in FCESS} for j in range(n_s)]
        else:
            pe = np.array([nowcast(row, seen[0], seen[1]) for row in e])
            pf = None if f is None else [nowcast_fcess({k: f[k][j] for k in FCESS}, seen[0], seen[2]) for j in range(n_s)]
        if planner == "mean":
            return plan(asset, pe.mean(axis=0), None if pf is None else {k: np.mean([x[k] for x in pf], axis=0) for k in FCESS}, **kw)
        amb = None
        if planner == "dro":
            K = (pe >= scen["spike"]).sum(axis=1).astype(float)
            lo, hi = prefs.ambiguity
            amb = (K, lo * K.mean(), hi * K.mean(), lo / n_s, hi / n_s)
        return plan_sp(asset, pe, pf, strat.every - t0 % strat.every, amb, **kw)

    def replan(t0, fe, ff, s0, b0, ladder, seen=None):
        """Plan intervals t0.. from s0 stored and budget b0. With terms: compliance first (see plan), on the
        scenarios from their issue on if the planner uses them (seen: see scen_plan). Without (the 1 MW study):
        ladder "offer": None if infeasible; "trigger": then without the floor."""
        nonlocal fallbacks, plans_short, plan_short, plan_ctx
        rest = None if lf is None else lf[t0:]
        lay = [(cap_now(x, t0, lf if x.compliance else lfe)[t0:], x.kind, x.penalty, x.compliance) for x in layers]
        fh = None if terms.fcess_hi is None else {k: np.asarray(v, float)[t0:] for k, v in terms.fcess_hi.items()}
        watch = on_plan is not None and ladder != "offer"  # an adopted plan the caller observes
        if full:
            hard, soft, *labels = floors(t0, ladder == "offer", s0, names=watch)
            kw = dict(s_init=s0, budget=b0, site=site, load=rest, terminal_value=tv if terminal is None else terminal,
                      wear_per_mwh=dw, layers=lay, fcess_hi=fh, export_energy=export_energy, comp_floor=hard, soft=soft,
                      reg_util=reg_util)
            if watch:
                plan_ctx = {"t0": t0, "stored": s0, "budget": b0, "prices": np.array(fe, float),
                            "fcess": None if ff is None else {k: np.array(v, float) for k, v in ff.items()},
                            "load": None if rest is None else rest.copy(), "hard": hard.copy(),
                            "soft": [(n, f.copy(), v) for n, (f, v) in zip(labels[0], soft)],
                            "caps": [(x.name, c.copy(), k, p, comp_) for x, (c, k, p, comp_) in zip(layers, lay)],
                            "terminal": np.array(kw["terminal_value"], float)}
            if extend is not None:
                fe, ff, kw = extend(t0, np.asarray(fe, float), ff, kw, [x.name for x in layers])
            if ladder != "offer" and planner in ("mean", "sp", "dro") and t0 >= scen["slot"]:
                out = scen_plan(t0, seen, kw)
            else:
                out = plan(asset, fe, ff, **kw)
            if extend is not None:
                out = _head(out, N - t0)
            if out["shortfall"] > 1e-6 and ladder != "offer":  # the offer rule's what-if plans are not adopted
                plans_short += 1
                plan_short = max(plan_short, out["shortfall_mwh"])
            return out
        floor = plan_floor(bat, t0, s0, obls, risk, prefs, room, lf, forced)
        tries = [(floor, lay)] + ([] if ladder == "offer" else [(None, lay)]) + ([(None, [])] if ladder == "full" and lay else [])
        for k, (fl, ly) in enumerate(tries):
            try:
                out = plan(asset, fe, ff, s0, b0, fl, site, rest, tv, dw, ly, fh, export_energy, reg_util=reg_util)
            except RuntimeError:
                if k < len(tries) - 1:
                    continue
                if ladder == "offer":
                    return None
                raise
            fallbacks += k > 0
            if watch:
                plan_ctx = {"t0": t0, "stored": s0, "budget": b0, "prices": np.array(fe, float),
                            "hard": None if fl is None else np.array(fl, float), "soft": [], "caps": [],
                            "terminal": np.array(tv, float)}
            return out

    def offer_replan(t0, fe, ff, s0, b0):
        return end_value(s0) if t0 >= N else replan(t0, fe, ff, s0, b0, "offer")

    def end_value(s0):
        """The offer rule's what-if past the last interval: s0 MWh stored at the day's end at the plans' terminal
        value, the economic floors' penalty and the compliance floor's shortfall there."""
        hard, soft = floors(N - 1, True, s0)
        term = tv if terminal is None else terminal
        if np.ndim(term):
            m = np.asarray(term, float)
            w = E / len(m)
            stored = float(m @ np.clip(s0 - w * np.arange(len(m)), 0.0, w))
        else:
            stored = float(term) * s0
        return {"value": stored, "fcess_value": 0.0, "penalty": float(sum(v * max(0.0, f[-1] - s0) for f, v in soft)),
                "shortfall": max(0.0, float(hard[-1]) - s0)}

    def rest_of(ff, t0):
        return None if ff is None else {k: np.asarray(ff[k], float)[t0:] for k in FCESS}

    meter, wear_t, first_plan = np.zeros(N), np.zeros(N), None
    if reserve is not None and not full:
        raise ValueError("a spike reserve needs the day's terms")
    res_left = 0.0 if reserve is None else reserve.mwh  # MWh of the spike reserve not yet sold
    plan_ctx = None  # what the latest adopted plan was made from (only kept for on_plan)
    if full:
        floor = floors(0)[0]
        morning = cur = replan(0, fc_e, fc_f, s_start, budget, "full")
    else:
        floor = plan_floor(bat, 0, s_start, obls, risk, prefs, room, lf, forced)
        morning = cur = plan(asset, fc_e, fc_f, s_start, None, floor, site, lf, tv, dw)
        if on_plan is not None:
            plan_ctx = {"t0": 0, "stored": s_start, "budget": None, "prices": np.array(fc_e, float),
                        "hard": np.array(floor, float), "soft": [], "caps": [], "terminal": np.array(tv, float)}
    if on_plan is not None:
        on_plan(0, "morning", _copy_plan(cur), plan_ctx)
    cur_t0, s = 0, s_start
    shortfall = site_short = wear = 0.0
    energy, soc = np.zeros(N), np.zeros(N)
    mw_h = {k: np.zeros(N) for k in FCESS}
    triggered, replanned = np.zeros(N, bool), np.zeros(N, bool)
    short_cap, plan_live, cap_min = np.zeros(N), np.zeros(N), np.full(N, np.inf)
    imp_mwh = exp_mwh = self_mwh = 0.0
    budget_low = budget
    excess = {}                                    # cap name -> [MWh over, largest MW over]
    tests = {a.name: [] for a in terms.availability}
    tests5 = {a.name: [0, 0] for a in terms.availability if a.per_5min}  # [window, available] 5-min intervals
    for t in range(N):
        due = [r for r in reveals if r.slot == t]
        if due:
            for r in due:
                fc_e, fc_f, obls, layers, budget = _reveal(r, t, fc_e, fc_f, obls, layers, budget)
            comp = [o for o in obls if o.compliance]
            charge_caps, forced = caps_state(layers)
            room = charge_room(bat, site, lf, charge_caps)
            room_e = econ_room(layers, charge_caps)
            tv = stored_energy_value(bat, fc_e)
            offer_f = use_cost(tv)
            if not (value_rule or table_rule):
                k_offer[t:] = offer_prices(strat, fc_e)[t:]
            cur, cur_t0, replanned[t] = replan(t, np.asarray(fc_e, float)[t:], rest_of(fc_f, t), s, budget, "full"), t, True
            if on_plan is not None:
                on_plan(t, "reveal", _copy_plan(cur), plan_ctx)
            if first_plan is None and any(r.energy is not None for r in due):
                first_plan = (t, float(np.mean(cur["moneys"])) if "moneys" in cur else cur["money"], tv)
        if planner != "point" and t and t % strat.every == 0 and not replanned[t]:
            # scheduled re-plan from the actual state of charge, the prices nudged towards the latest 5-min prices
            last = t * STEPS - 1
            f_now = None if p5_f is None or not fcess else {k: p5_f[k][last] for k in FCESS}
            cur = replan(t, nowcast(fc_e, t - 1, p5[last]), nowcast_fcess(fc_f, t - 1, f_now), s, budget, "full",
                         (t - 1, p5[last], f_now))
            cur_t0, replanned[t] = t, True
            if on_plan is not None:
                on_plan(t, "scheduled", _copy_plan(cur), plan_ctx)
        i = t - cur_t0
        r_plan = sum(cur[k][i] for k in RAISE) if fcess else 0.0
        l_plan = sum(cur[k][i] for k in LOWER) if fcess else 0.0
        k_ready = not value_rule
        s_open = s
        if full:
            plan_live[t] = cur["net"][i]
            for x in layers:
                cap_min[t] = min(cap_min[t], x.cap[t])
        for j in range(STEPS):
            hours = t * DT + (j + 1) * DT5
            if terms.capacity is not None and terms.capacity.start <= t < terms.capacity.end:
                # Reserve Capacity test at the start of the dispatch interval
                can = min(P, 12 * s) - (la[t] if terms.capacity.at_meter else 0.0)
                if site is not None:
                    can = min(can, site.export_limit_mw)
                short_cap[t] += max(0.0, terms.capacity.rcoq_mw - can) / STEPS
            for a in terms.availability:
                if a.per_5min and a.start <= t < a.end:
                    # registered form: could the facility offer mw of net injection at the start of these 5 minutes
                    on_meter = a.standby.at_meter and site is not None
                    can = min(P, 12 * s) - (la[t] if on_meter else 0.0)
                    if site is not None:
                        can = min(can, site.export_limit_mw if on_meter else exp_room_f[t])
                    tests5[a.name][0] += 1
                    tests5[a.name][1] += int(can >= a.mw - 1e-6)
            # keep: what compliance needs stored now, counting on recharging at full import room (below it the
            # battery must charge); keep_e: the same counting only on recharging within the economic caps, the
            # least that selling and economic caps may leave
            keep = keep_for_obligation(bat, comp, hours, room, lf) if comp else 0.0
            keep_e = keep if not full or not comp else max(keep, keep_for_obligation(bat, comp, hours, room_e, lf))
            sellable = max(0.0, min(P, exp_room[t], (s - keep_e) * eta / DT5, budget / DT5))
            pw = min(cur["net"][i], sellable)
            price = p5[t * STEPS + j]
            if buy_rule and pw <= 0:
                # the bids' buy side, priced from the latest step already seen, as the sell side
                i5 = t * STEPS + j
                seen_b = p5[i5 - offer_lag] if i5 >= offer_lag else float(np.asarray(fc_e, float)[t])
                pw_b = bids.buy(t, s, pw, price, seen_b, eta, strat.alpha, E, P)
                if abs(pw_b - pw) > 1e-9:
                    bid_mwh[t] += (pw - pw_b) * DT5
                    moved[t], pw = True, pw_b
            if strat.offer and price >= strat.k_min and sellable > max(pw, 0.0) + 1e-6:
                target = sellable  # MW to discharge if the offer is taken
                if table_rule:
                    # layered: the MWh whose threshold the price reaches, priced from the latest step already seen
                    i5 = t * STEPS + j
                    seen = p5[i5 - offer_lag] if i5 >= offer_lag else float(np.asarray(fc_e, float)[t])
                    target, k_offer[t] = bids.sell(t, s, max(pw, 0.0), sellable, price, seen, eta, strat.alpha, dw,
                                                   strat.k_min)
                elif not k_ready:
                    # the offer is in before this step's prices exist: priced from the latest step already seen
                    i5 = t * STEPS + j
                    seen = p5[i5 - offer_lag] if i5 >= offer_lag else float(np.asarray(fc_e, float)[t])
                    f_seen = (None if p5_f is None or not fcess or i5 < offer_lag
                              else {k: p5_f[k][i5 - offer_lag] for k in FCESS})
                    k_offer[t], offer_fcess[t] = value_offer_price(
                        asset, strat, prefs, t, j, s, budget, pw, sellable, fc_e, fc_f, seen, offer_replan, f_seen,
                        offer_end)
                    k_ready = True
                margin = price - k_offer[t]
                if margin >= 0 and target > max(pw, 0.0) + 1e-9:
                    # co-optimised: discharging more uses headroom the plan had enabled for FCESS raise
                    raise_lost = max(0.0, min(r_plan, P - pw)) - max(0.0, min(r_plan, P - target))
                    raise_price = (sum(cur[k][i] * p5_f[k][t * STEPS + j] for k in RAISE) / r_plan
                                   if p5_f is not None and r_plan > 1e-9 else 0.0)
                    if margin * (target - max(pw, 0.0)) >= raise_price * raise_lost:
                        offer_mwh[t] += (target - max(pw, 0.0)) * DT5
                        if reserve is not None and reserve.start <= t < reserve.end:
                            res_left = max(0.0, res_left - (target - max(pw, 0.0)) * DT5)
                        pw, triggered[t] = target, True
            safety = pw <= 0 and s - pw * DT5 * eta < keep
            if safety:
                # obligation safety: stored energy is at the least that can still be refilled in time
                pw = -min(P, max(-pw, (keep - s) / (eta * DT5)))
            forced_mw = 0.0
            for x in layers:  # caps on the meter, with the site's actual load
                if not np.isfinite(x.cap[t]):
                    continue
                cap_t = max(x.cap[t], meter[:t].max()) if x.running_max and t > 0 and site is not None else x.cap[t]
                need_mw = la[t] - cap_t if x.at_meter and site is not None else -cap_t
                if pw < need_mw:
                    if x.compliance:
                        pw, forced_mw = need_mw, max(forced_mw, need_mw)
                    elif not safety:  # an economic cap never holds back charging that compliance needs
                        pw = max(pw, min(need_mw, max(0.0, (s - keep_e) * eta / DT5)))
            pw = min(max(pw, -imp_room[t]), exp_room[t])  # the connection, with the site's actual load
            if pw > 0:
                pw = min(pw, s * eta / DT5, budget / DT5 if forced_mw <= 0 else max(budget / DT5, forced_mw))
                s -= pw * DT5 / eta
                budget -= pw * DT5
                wear += asset.wear_per_mwh * pw * DT5
                wear_t[t] += asset.wear_per_mwh * pw * DT5
            else:
                pw = max(pw, -(E - s) / (eta * DT5))
                s -= pw * DT5 * eta
            site_short = max(site_short, -imp_room[t] - pw)  # site import over the limit the battery could not cover
            energy[t] += pw * DT5
            m = la[t] - pw  # MW drawn through the meter (negative: sent out)
            meter[t] += m / STEPS
            imp_mwh, exp_mwh = imp_mwh + max(m, 0.0) * DT5, exp_mwh + max(-m, 0.0) * DT5
            self_mwh += min(la[t], max(pw, 0.0)) * DT5
            budget_low = min(budget_low, budget)
            if fcess:
                raise_ok = max(0.0, min(r_plan, P - pw, s * eta / tau, exp_room_f[t] - pw))
                lower_ok = max(0.0, min(l_plan, P + pw, (E - s) / (eta * tau), imp_room[t] + pw))
                enabled = {k: cur[k][i] * raise_ok / r_plan if r_plan > 1e-9 else 0.0 for k in RAISE}
                enabled.update({k: cur[k][i] * lower_ok / l_plan if l_plan > 1e-9 else 0.0 for k in LOWER})
                if any(reg_util) and p5_f is not None:  # offered at its use cost: enabled only if the price covers it
                    enabled = {k: v if p5_f[k][t * STEPS + j] >= offer_f[k] else 0.0 for k, v in enabled.items()}
                for k in FCESS:
                    mw_h[k][t] += enabled[k] * DT5
                if any(reg_util):
                    up = min(reg_util[0] * enabled["reg_raise"] * DT5, s * eta)       # MWh sent out
                    down = min(reg_util[1] * enabled["reg_lower"] * DT5, (E - s) / eta)  # MWh taken in
                    s += down * eta - up / eta
                    energy[t] += up - down
                    meter[t] -= (up - down) / DT5 / STEPS
                    budget -= up
                    wear += asset.wear_per_mwh * up
                    wear_t[t] += asset.wear_per_mwh * up
                    reg_out, reg_in = reg_out + up, reg_in + down
            if comp:
                shortfall = max(shortfall, obligation_need(bat, comp, hours, lf) - s)
        soc[t] = s
        if full:
            for x in layers:
                if np.isfinite(x.cap[t]):
                    over = max(0.0, (meter[t] if x.at_meter and site is not None else -energy[t] / DT) - x.cap[t])
                    e = excess.setdefault(x.name, [0.0, 0.0])
                    e[0], e[1] = e[0] + over * DT, max(e[1], over)
            for a in terms.availability:
                if a.start <= t < a.end and not a.per_5min:
                    event = a.in_event(t)
                    on_meter = a.standby.at_meter and site is not None
                    got = -meter[t] if on_meter else energy[t] / DT
                    power = min(P - la[t], site.export_limit_mw) if on_meter else min(P, exp_room[t])
                    ok = ((got >= a.min_delivery * a.mw - 1e-6) if event else a.after_events(t) or
                          (power >= a.mw - 1e-6 and s_open >= a.standby.need(bat, t * DT, lf) - 1e-6))
                    tests[a.name].append((ok, event, min(max(got, 0.0), a.mw) * DT if event else 0.0, got / a.mw if event else np.nan))
        if (triggered[t] or moved[t]) and t < N - 1:
            cur_t0 = t + 1
            last = t * STEPS + STEPS - 1
            f_now = None if p5_f is None or not fcess else {k: p5_f[k][last] for k in FCESS}
            cur = replan(cur_t0, nowcast(fc_e, t, p5[last]), nowcast_fcess(fc_f, t, f_now), s, budget, "full" if full else "trigger",
                         (t, p5[last], f_now))
            replanned[cur_t0] = True
            if on_plan is not None:
                on_plan(cur_t0, "trigger", _copy_plan(cur), plan_ctx)
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
    if full:
        summary.update({"max_import_mw": float(meter.max()), "import_mwh": imp_mwh, "export_mwh": exp_mwh,
                        "self_mwh": self_mwh, "plans_short": plans_short, "plan_shortfall_mwh": plan_short,
                        "timing_errors": len(timing_errors(terms)), "reg_out_mwh": reg_out, "reg_in_mwh": reg_in,
                        "budget_overrun_mwh": max(0.0, -budget_low),
                        "capacity_short_mw_ti": float(short_cap.sum()), "planner_fallback": int(planner != strat.planner)})
        if reserve is not None:
            summary.update({"reserve_mwh": reserve.mwh, "reserve_sold_mwh": reserve.mwh - res_left,
                            "reserve_held_mwh": float(min(reserve.mwh, soc[reserve.start - 1]))})
        if first_plan is not None:
            t0, money, tv0 = first_plan
            got = (np.asarray(rtp)[t0:] * energy[t0:]).sum() - wear_t[t0:].sum() + tv0 * s
            if fcess:
                got += sum((np.asarray(act_f[k])[t0:] * mw_h[k][t0:]).sum() for k in FCESS)
            summary.update({"plan_slot": t0, "plan_money": money, "plan_got": float(got)})
        for name, (mwh, mw) in excess.items():
            summary[f"excess_{name}_mwh"], summary[f"excess_{name}_mw"] = mwh, mw
        for a in terms.availability:
            rows = tests[a.name]
            ratios = [r for *_, r in rows if np.isfinite(r)]
            summary.update({f"{a.name}_window_ti": len(rows), f"{a.name}_available_ti": int(sum(r[0] for r in rows)),
                            f"{a.name}_event_ti": int(sum(r[1] for r in rows)), f"{a.name}_event_mwh": float(sum(r[2] for r in rows)),
                            f"{a.name}_min_delivery": min(ratios) if ratios else np.nan})
        for name, (n5, ok5) in tests5.items():
            summary.update({f"{name}_window_5min": n5, f"{name}_available_5min": ok5})
        log = log.assign(meter_mw=meter, plan_net_live=plan_live, cap_mw=cap_min, capacity_short_mw=short_cap)
    if buy_rule:
        summary["bid_moves"] = int(moved.sum())
        log = log.assign(bid_mwh=bid_mwh)
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


def _explain_terms(log: pd.DataFrame, summary: dict, terms: DayTerms, clock, site: Site | None) -> list[str]:
    """Lines for what a day's terms added: reveals, the meter, the caps, the capacity and availability tests."""
    lines = []
    soc = log["soc"].to_numpy()
    for r in sorted(terms.reveals, key=lambda r: r.slot):
        if r.slot > 0:
            lines.append(f"{clock[r.slot]} {r.label}: the rest of the day was re-planned from the {soc[r.slot - 1]:.1f} MWh then stored.")
    if site is not None:
        top = int(np.argmax(log["meter_mw"].to_numpy()))
        lines.append(f"Meter: {summary['import_mwh']:.1f} MWh in, {summary['export_mwh']:.1f} MWh out; the battery supplied "
                     f"{summary['self_mwh']:.1f} MWh of the site's own load; highest import {summary['max_import_mw']:.2f} MW "
                     f"at {clock[top]}.")
    caps = {x.name: x for x in terms.layers}
    caps.update({x.name: x for r in terms.reveals for x in r.layers})
    for name, x in caps.items():
        live = np.isfinite(np.asarray(x.cap, float))
        spans = [f"{clock[i]}-{clock[j]}" for i, j, on in _runs(list(live)) if on]
        level = np.asarray(x.cap, float)[live]
        what = f"import at most {level.min():.2f} MW" if level.min() >= 0 else f"send out at least {-level.max():.2f} MW"
        over = summary.get(f"excess_{name}_mw", 0.0)
        kept = "kept" if over <= 1e-6 else f"missed by up to {over:.2f} MW ({summary[f'excess_{name}_mwh']:.2f} MWh)"
        lines.append(f"Cap '{name}' ({what} at the meter, {', '.join(spans)}): {kept}.")
    c = terms.capacity
    if c is not None:
        short = summary.get("capacity_short_mw_ti", 0.0)
        lines.append(f"Capacity credits: able to deliver {c.rcoq_mw:.2f} MW{' net at the meter' if c.at_meter else ''} "
                     f"{clock[c.start]}-{clock[c.end]}" + (": fully, no refund." if short <= 1e-9 else
                                                          f": short {short:.2f} MW x trading intervals (refunded)."))
    for a in terms.availability:
        if a.per_5min:
            n, k = summary[f"{a.name}_window_5min"], summary[f"{a.name}_available_5min"]
            lines.append(f"{a.name}: could offer {a.mw:g} MW at the meter in {k} of {n} five-minute intervals of "
                         f"{clock[a.start]}-{clock[a.end]}.")
            continue
        n, k, ev = summary[f"{a.name}_window_ti"], summary[f"{a.name}_available_ti"], summary[f"{a.name}_event_ti"]
        line = f"{a.name}: available in {k} of {n} intervals of {clock[a.start]}-{clock[a.end]}"
        if ev:
            line += (f"; called for {ev} intervals, delivered {summary[f'{a.name}_event_mwh']:.1f} MWh, at worst "
                     f"{summary[f'{a.name}_min_delivery']:.0%} of the {a.mw:.1f} MW asked")
        lines.append(line + ".")
    if summary.get("plans_short"):
        lines.append(f"{summary['plans_short']} plan(s) could not meet every compliance requirement: each kept the total "
                     f"shortfall to the least possible (at most {summary['plan_shortfall_mwh']:.2f} MWh) before anything else.")
    return lines


def explain(log: pd.DataFrame, summary: dict, asset: Asset, strat: Strategy, prefs: RiskPrefs, obligations=None,
            site: Site | None = None, terms: DayTerms | None = None) -> list[str]:
    """Plain-language account of one day: what the morning plan does and why, then what happened.
    Money the caller settles outside run_day (capacity credits, contracts, the site bill) is listed if
    the summary carries it as ledger_* keys."""
    bat = asset.battery
    clock = pd.date_range("2000-01-01 08:00", periods=N + 1, freq="30min").strftime("%H:%M")
    fc = log["forecast"].to_numpy()
    wear = decision_wear(asset, prefs)
    costs = (f"${asset.wear_per_mwh:.0f}/MWh wear and a ${prefs.hurdle_per_mwh:.0f}/MWh margin for forecast error"
             if prefs.hurdle_per_mwh else f"${wear:.0f}/MWh wear")
    obligations = obligations or []
    lines = []
    if site is not None:
        lines.append(f"Site: load {log['site_load_fc'].min():.2f}-{log['site_load_fc'].max():.2f} MW expected (planned with "
                     f"{prefs.load_margin:.0%} spare for forecast error), sharing a {site.import_limit_mw:.1f} MW import / "
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
            ahead = [o for o in obligations if i < o.start]
            if (later - wear) * bat.round_trip > fc[i:j].mean():
                why = (f"selling later at up to ${later:.0f} still pays after the {1 - bat.round_trip:.0%} round-trip "
                       f"loss, {costs}")
            elif ahead:
                o = min(ahead, key=lambda o: o.start)
                why = (f"the spread does not cover losses and wear, but the battery must hold "
                       f"{obligation_need(bat, obligations, o.start * DT):.1f} MWh by {clock[o.start]} for the {o.name} obligation")
            elif prefs.risk_threshold is not None:
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
            active = [o.name for o in obligations if o.start <= i < o.end]
            why = (f"paced so the {' and '.join(active)} obligation{'s stay' if len(active) > 1 else ' stays'} covered"
                   if active else "the best forecast prices left for the energy on hand")
        lines.append(f"{clock[i]}-{clock[j]} {a} {power.sum() * DT:.1f} MWh: {price}; {why}.")
    for o in obligations:
        deliver = (f"enough stored energy to deliver {o.mw:.2f} MW until {clock[o.end]}" if o.event_hours is None else
                   f"enough stored energy to deliver {o.mw:.2f} MW for up to {o.event_hours:g} h whenever called until {clock[o.end]}")
        lines.append(f"{o.name.capitalize()} obligation: ready by {clock[o.start]}, then {deliver}.")
    if prefs.risk_threshold is not None:
        risky = log["risk"].to_numpy() >= prefs.risk_threshold
        for i, j, r in _runs(list(risky)):
            if r:
                lines.append(f"{clock[i]}-{clock[j]} keep at least {prefs.reserve_mwh:.1f} MWh stored: spike risk up to "
                             f"{log['risk'].iloc[i:j].max():.0%} (threshold {prefs.risk_threshold:.0%}).")
    if strat.offer:
        k = log["offer_price"].to_numpy()
        k = k[np.isfinite(k)]
        if strat.offer_rule == "forecast":
            lines.append(f"Standing offer all day: discharge as soon as the price reaches ${k.min():.0f}-{k.max():.0f}/MWh "
                         f"({strat.alpha:.1f}x the best price still expected, never below ${strat.k_min:.0f}).")
        elif strat.offer_rule == "table":
            lines.append(f"Standing offer in layers: each MWh stored is offered at its own price, higher the less energy is "
                         f"left ({strat.alpha:.1f}x what keeping it is worth, plus {costs}, never below ${strat.k_min:.0f})"
                         + (f"; the layers the price reached today were offered at ${k.min():.0f}-{k.max():.0f}/MWh."
                            if len(k) else f"; the price never passed ${strat.k_min:.0f} while there was energy to sell."))
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
    if log["soc"].iloc[-1] > 0.05 and strat.terminal == "table":
        lines.append(f"Ends the day holding {log['soc'].iloc[-1]:.1f} MWh, valued by the storage-value curve (each further MWh "
                     f"is worth less): selling it for less than that, after losses and wear, would not pay.")
    elif log["soc"].iloc[-1] > 0.05:
        lines.append(f"Ends the day holding {log['soc'].iloc[-1]:.1f} MWh, counted at ${log['stored_value'].iloc[0]:.0f}/MWh "
                     f"(what buying it back would cost): selling it for less, after losses and wear, would not pay.")
    if terms is not None:
        lines.extend(_explain_terms(log, summary, terms, clock, site))
    lines.append(f"Result: ${summary['energy']:.0f} energy + ${summary['fcess']:.0f} FCESS - ${summary['wear']:.0f} wear "
                 f"= ${summary['total']:.0f}.")
    ledger = {k[len("ledger_"):]: v for k, v in summary.items() if k.startswith("ledger_") and k != "ledger_total"}
    if ledger:
        parts = " ".join(f"{'+' if v >= 0 else '-'} ${abs(v):.0f} {k.replace('_', ' ')}" for k, v in ledger.items())
        lines.append(f"With the day's contracts and site bill: ${summary['total']:.0f} market {parts} = ${summary['ledger_total']:.0f}.")
    return lines
