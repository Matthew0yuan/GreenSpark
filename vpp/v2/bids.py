"""Layered offers from the relative value curve, the factory's energy protected (reports/v2_plan.md, S3).

Each 5 minutes the standing offer sells, beyond the plan, the MWh whose threshold the price reaches, from the top of
the stored energy down, as v1's layered offers (vpp.storage_value.DayBids). The threshold of the MWh in segment k is
max(k_min, alpha x its value / eta + wear + hurdle), the value being the day's table at the end of the half-hour in
the price state of the latest 5-min price seen less the day's level (vpp.v2.value).

From 09:00 the energy the factory still needs is protected: the rest of today's needs above the caps (the CMD, a
flagged day's peak hours) and tomorrow's site reserve by 04:00 (vpp.v2.reserves), less what can still be charged
before each, on top of what compliance needs. Its threshold is at least alpha x (its value + the lowest value per MWh
among those needs) / eta + wear + hurdle, so it sells only into a price that pays for the bill it would cost.
"""

from dataclasses import dataclass

import numpy as np

from vpp import dispatch as dp
from vpp import storage_value as sv


@dataclass
class LadderBids:
    edges: np.ndarray    # (48, B + 1) price-state bin edges, relative to the level
    marg: np.ndarray     # (48, B, segments) $ per MWh of each segment at the end of slot t, given its price state
    level: float         # the day's price level L ($/MWh)
    protect: np.ndarray  # (48,) stored MWh protected at each slot (0 before 09:00)
    value: float         # $ per MWh of the protected energy
    alpha2: float | None = None  # the margin on the value from 09:00 (a candidate chosen then, vpp.v2.candidates)

    def thresholds(self, t: int, seen: float, eta: float, alpha: float, dw: float, k_min: float) -> np.ndarray:
        """$/MWh at which each segment of stored energy sells in slot t (non-increasing in the segment)."""
        if self.alpha2 is not None and t >= 2:
            alpha = self.alpha2
        b = int(np.clip(np.searchsorted(self.edges[t, 1:-1], seen - self.level, side="right"), 0, sv.B - 1))
        m = self.marg[t, b]
        thr = np.maximum(k_min, alpha * m / eta + dw)
        protected = np.arange(len(m)) * sv.GRID < self.protect[t] - 1e-9
        if protected.any():
            thr = np.where(protected, np.maximum(thr, alpha * (m + self.value) / eta + dw), thr)
        return thr

    def sell(self, t: int, s: float, base: float, sellable: float, price: float, seen: float, eta: float,
             alpha: float, dw: float, k_min: float) -> tuple[float, float]:
        """(MW to discharge this 5-min step, the threshold of the last MWh sold - or of the next, if none), as
        DayBids.sell: beyond the plan's base MW, the MWh whose threshold the price reaches; at most sellable MW."""
        thr = self.thresholds(t, seen, eta, alpha, dw, k_min)
        x_plan = s - base * dp.DT5 / eta
        x_end = max(0.0, s - sellable * dp.DT5 / eta)
        ok = np.flatnonzero(thr <= price)
        if not len(ok) or x_plan <= x_end + 1e-12:
            return base, float(thr[min(len(thr) - 1, max(0, int(np.ceil(x_plan / sv.GRID - 1e-9)) - 1))])
        x_star = min(x_plan, max(x_end, ok[0] * sv.GRID))  # sell down to the lowest segment the price clears
        target = max(base, min(sellable, (s - x_star) * eta / dp.DT5))
        return target, float(thr[min(len(thr) - 1, int(np.floor(x_star / sv.GRID + 1e-9)))])


def protection(bat, site, plan_load: np.ndarray, caps: np.ndarray, tomorrow_mwh: float, compliance: np.ndarray,
               start: int = 2, at: int = 39) -> np.ndarray:
    """Stored MWh to protect at the start of each slot: compliance's need plus the most, over the rest of the day, of
    the factory's energy needed by then (today above the caps, tomorrow_mwh by the end of slot `at`) less what the
    battery can still charge before; 0 before slot `start` (09:00, when the needs are public)."""
    P, eta = bat.power_mw, bat.eta
    need = np.minimum(P, np.clip(plan_load - caps, 0.0, None)) * dp.DT / eta
    room = np.where(need > 0, 0.0, eta * np.clip(np.minimum(np.minimum(P, site.import_limit_mw - plan_load),
                                                             caps - plan_load), 0.0, None) * dp.DT)
    out = np.zeros(dp.N)
    for t in range(start, dp.N):
        req = np.cumsum(need[t:]) - np.r_[0.0, np.cumsum(room[t:])[:-1]]
        req = req + np.where(np.arange(t, dp.N) >= at, tomorrow_mwh, 0.0)
        out[t] = compliance[t] + max(0.0, float(req.max(initial=0.0)))
    return out
