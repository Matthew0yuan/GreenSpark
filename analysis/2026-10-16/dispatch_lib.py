"""Daily battery plan for one WEM trading day: energy arbitrage + FCESS enablement as a MILP.

Battery: P = 1 MW, E = 2 MWh, 85% round trip, at most one full discharge a day, starts and ends
the trading day at the same state of charge s0. No simultaneous charge and discharge.
FCESS (price-taker, paid the clearing price per MW enabled per hour; RoCoF is always $0, skipped):
  raise:  reg_raise + cont_raise <= P   and  net discharge + raise <= P
  lower:  reg_lower + cont_lower <= P   and  net charge    + lower <= P
  energy: stored energy covers TAU hours of the enabled raise, and spare room covers TAU hours of
          the enabled lower, at both ends of every interval.
Capacity-credit obligation (optional): under the 4-hour duration requirement a 2-hour battery
gets credits RCOQ = E*eta/4 MW; at every point of the ESR obligation window it must hold the
energy to deliver RCOQ until the window ends (so it is full when the window opens).
"""

from functools import lru_cache

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

N, DT = 48, 0.5
P, E = 1.0, 2.0
ETA = np.sqrt(0.85)
TAU = 0.25
RCOQ = E * ETA / 4
FCESS = ["reg_raise", "cont_raise", "reg_lower", "cont_lower"]
C, D, S, U, RR, CR, RL, CL = (np.arange(N) + k * N for k in range(8))
NV = 8 * N
INTEG = np.zeros(NV)
INTEG[U] = 1


@lru_cache(maxsize=None)
def _constraints(s0: float) -> LinearConstraint:
    rows, cols, vals, lb, ub = [], [], [], [], []

    def add(c, v, lo, hi):
        rows.extend([len(lb)] * len(c)); cols.extend(c); vals.extend(v); lb.append(lo); ub.append(hi)

    kr, kl = TAU / ETA, TAU * ETA
    for t in range(N):
        # s_t = s_{t-1} + eta*c_t*DT - d_t*DT/eta
        c, v = [S[t], C[t], D[t]], [1, -ETA * DT, DT / ETA]
        if t:
            c, v = c + [S[t - 1]], v + [-1]
        add(c, v, 0 if t else s0, 0 if t else s0)
        add([C[t], U[t]], [1, P], -np.inf, P)
        add([D[t], U[t]], [1, -P], -np.inf, 0)
        add([RR[t], CR[t]], [1, 1], -np.inf, P)
        add([D[t], C[t], RR[t], CR[t]], [1, -1, 1, 1], -np.inf, P)
        add([RL[t], CL[t]], [1, 1], -np.inf, P)
        add([C[t], D[t], RL[t], CL[t]], [1, -1, 1, 1], -np.inf, P)
        add([S[t], RR[t], CR[t]], [1, -kr, -kr], 0, np.inf)
        add([S[t], RL[t], CL[t]], [1, kl, kl], -np.inf, E)
        if t:
            add([S[t - 1], RR[t], CR[t]], [1, -kr, -kr], 0, np.inf)
            add([S[t - 1], RL[t], CL[t]], [1, kl, kl], -np.inf, E)
        else:
            add([RR[t], CR[t]], [kr, kr], -np.inf, s0)
            add([RL[t], CL[t]], [kl, kl], -np.inf, E - s0)
    add(list(D), [DT] * N, 0, E)
    add([S[N - 1]], [1], s0, s0)
    A = coo_matrix((vals, (rows, cols)), shape=(len(lb), NV)).tocsr()
    return LinearConstraint(A, lb, ub)


def _bounds(fcess: bool, window: tuple[int, int] | None) -> Bounds:
    lo = np.zeros(NV)
    hi = np.r_[np.full(2 * N, P), np.full(N, E), np.ones(N), np.full(4 * N, P if fcess else 0.0)]
    if window is not None:
        k0, k1 = window  # obligation covers slots k0 .. k1-1
        for k in range(max(k0, 1), k1):  # energy held at the start of slot k = end of slot k-1
            lo[S[k - 1]] = min(E, RCOQ * (k1 - k) * DT / ETA)
    return Bounds(lo, hi)


def plan(price_e, price_f=None, window=None, s0=0.0) -> dict:
    """Optimal plan for prices price_e ($/MWh, length N) and price_f ({product: $/MW/h}) or energy only."""
    cost = np.zeros(NV)
    cost[C] = np.asarray(price_e) * DT
    cost[D] = -np.asarray(price_e) * DT
    if price_f is not None:
        for blk, k in zip((RR, CR, RL, CL), FCESS):
            cost[blk] = -np.asarray(price_f[k]) * DT
    res = milp(cost, constraints=_constraints(float(s0)), bounds=_bounds(price_f is not None, window),
               integrality=INTEG)
    if res.status != 0:
        raise RuntimeError(res.message)
    x = res.x
    return {"net": x[D] - x[C], "soc": x[S], **{k: x[blk] for blk, k in zip((RR, CR, RL, CL), FCESS)}}


def settle(p: dict, actual_e, actual_f) -> tuple[float, float]:
    """(energy $, FCESS $) earned by plan p at actual prices."""
    energy = float((np.asarray(actual_e) * p["net"]).sum() * DT)
    fcess = float(sum((np.asarray(actual_f[k]) * p[k]).sum() for k in FCESS) * DT)
    return energy, fcess
