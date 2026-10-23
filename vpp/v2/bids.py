"""Layered offers from the relative value curve, the factory's energy protected (reports/v2_plan.md, S3).

Each 5 minutes the standing offer sells, beyond the plan, the MWh whose threshold the price reaches, from the top of
the stored energy down, as v1's layered offers (vpp.storage_value.DayBids). The threshold of the MWh in segment k is
max(k_min, alpha x its value / eta + wear + hurdle), the value being the day's table at the end of the half-hour in
the price state of the latest 5-min price seen less the day's level (vpp.v2.value).

From 09:00 the energy the factory still needs is protected: the rest of today's needs above the caps (the CMD, a
flagged day's peak hours) and tomorrow's site reserve by 04:00 (vpp.v2.reserves), less what can still be charged
before each, on top of what compliance needs. Its threshold is at least alpha x (its value + the lowest value per MWh
among those needs) / eta + wear + hurdle, so it sells only into a price that pays for the bill it would cost.

Round 4, B3 (reports/v2_plan.md, section 11.5): with buy_margin h set, the offers also bid to buy. The buy limit of
segment k is eta x its value / alpha - h (from the same values and price state). In a step whose plan charges or
idles, the plan's charging up to the protected level of the next half-hour is kept whatever the price; the rest of it
only fills segments whose limit is at least the price; and beyond the plan, the battery buys into every such segment.
A step whose plan discharges is left to the plan and the offers to sell.
"""

from dataclasses import dataclass, field

import numpy as np

from vpp import dispatch as dp
from vpp import storage_value as sv

SPIKE = 150.0  # $/MWh: a settled half-hour at least this has spiked (round 5, c5; as vpp.v2.value.SPIKE)
OFFER_CAP = 1000.0  # $/MWh, the WEM's energy offer price ceiling (ERA, 2024-12; v2_plan.md section 10.3)
NIGHT = 32          # slot of 00:00: f5's reference fills from here
FLOOR_DAYS, FLOOR_Q, FLOOR_ROOM = 28, 0.25, 1.0  # f5's floor: days, quantile, MW below the CMD [fixed, 12.6]


def charge_floor(table, cmd_mw: float, load_scale: float, margin: float) -> "pd.Series":
    """Round 5, f5: for each trading day, the FLOOR_Q quantile of the 30-min RTP of the previous FLOOR_DAYS trading
    days in the half-hours the battery could charge in: the plans' factory load (the example profile x load_scale x
    (1 + margin), vpp.site) at most the CMD less FLOOR_ROOM. Only days before the day: known at 08:00."""
    import pandas as pd

    from vpp import site as st
    idx = table.index
    ts = pd.DatetimeIndex([d + pd.Timedelta(hours=8, minutes=30 * k) for d in idx for k in range(dp.N)])
    ok = (st.example_load(ts)[0].reshape(len(idx), dp.N) * load_scale * (1 + margin)) <= cmd_mw - FLOOR_ROOM
    X = table.to_numpy()
    out = np.full(len(idx), np.nan)
    for i, d in enumerate(idx):
        lo = idx.searchsorted(d - pd.Timedelta(days=FLOOR_DAYS))
        x = X[lo:i][ok[lo:i]]
        x = x[np.isfinite(x)]
        if len(x):
            out[i] = np.quantile(x, FLOOR_Q)
    return pd.Series(out, index=idx)


@dataclass
class LadderBids:
    edges: np.ndarray    # (48, B + 1) price-state bin edges, relative to the level
    # (48, B, segments) $ per MWh of each segment at the end of slot t, given its price state; round 5, c5:
    # (48, B, 2, segments), also by whether the day has spiked (vpp.v2.value, regime)
    marg: np.ndarray
    level: float         # the day's price level L ($/MWh)
    protect: np.ndarray  # (48,) stored MWh protected at each slot (0 before 09:00)
    value: float         # $ per MWh of the protected energy
    alpha2: float | None = None  # the margin on the value from 09:00 (a candidate chosen then, vpp.v2.candidates)
    cost: float | None = None    # $ per MWh sold the offers charge in place of the plans' wear + hurdle (round 4, B2)
    buy_margin: float | None = None  # h of the buy limits; None: the offers only sell (round 4, B3)
    # round 5, f5 (reports/v2_plan.md section 12.6): the buy limits' own margin on the value (None: alpha, as B3), their
    # cap ($/MWh), a floor under the values both sides use ($ per stored MWh: the floor price / eta), and the reference
    # that bids the cap for every segment from 00:00
    buy_alpha: float | None = None
    buy_cap: float | None = None
    value_floor: float | None = None
    night_fill: bool = False
    spiked: bool = field(default=False, init=False, repr=False)  # c5: a half-hour settled so far today reached SPIKE

    def observe(self, i5: int, settled: np.ndarray) -> None:
        """Round 5, c5: each 5 minutes run_day passes the day's half-hours settled by the offers' information time;
        with values by regime, whether one of them reached SPIKE picks the values."""
        if self.marg.ndim == 4:
            self.spiked = bool(np.any(np.asarray(settled, float) >= SPIKE))

    def _values(self, t: int, seen: float) -> np.ndarray:
        """$ per MWh of each segment at the end of slot t, in the price state of the latest 5-min price seen (and, with
        values by regime, whether the day has spiked so far)."""
        b = int(np.clip(np.searchsorted(self.edges[t, 1:-1], seen - self.level, side="right"), 0, sv.B - 1))
        m = self.marg[t, b, int(self.spiked)] if self.marg.ndim == 4 else self.marg[t, b]
        return m if self.value_floor is None else np.maximum(np.asarray(m, float), self.value_floor)  # exact floor

    def thresholds(self, t: int, seen: float, eta: float, alpha: float, dw: float, k_min: float) -> np.ndarray:
        """$/MWh at which each segment of stored energy sells in slot t (non-increasing in the segment)."""
        if self.alpha2 is not None and t >= 2:
            alpha = self.alpha2
        if self.cost is not None:
            dw = self.cost
        m = self._values(t, seen)
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

    def buy_limits(self, t: int, seen: float, eta: float, alpha: float) -> np.ndarray:
        """$/MWh up to which each segment of stored energy is bought in slot t: eta x its value / alpha - h
        (non-increasing in the segment, and below the segment's sell threshold)."""
        if self.alpha2 is not None and t >= 2:
            alpha = self.alpha2
        lim = eta * self._values(t, seen) / (alpha if self.buy_alpha is None else self.buy_alpha) - self.buy_margin
        if self.buy_cap is not None:
            lim = np.minimum(lim, self.buy_cap)
            if self.night_fill and t >= NIGHT:
                lim = np.full_like(lim, self.buy_cap)
        return lim

    def buy(self, t: int, s: float, pw: float, price: float, seen: float, eta: float, alpha: float, E: float,
            P: float) -> float:
        """MW to execute this 5-min step (negative: charge) in a step whose plan pw charges or idles: the plan's
        charging up to the protected level of the next half-hour whatever the price, the rest of it and any more
        only into the segments, from the one being filled up, whose buy limit is at least the price; at most P MW.
        The connection, the caps on the meter and the battery's room are applied by the caller."""
        lim = self.buy_limits(t, seen, eta, alpha)
        k0 = min(len(lim) - 1, int(np.floor(s / sv.GRID + 1e-9)))  # the segment being filled
        ok = lim[k0:] >= price
        n_ok = len(ok) if ok.all() else int(np.argmin(ok))  # the leading segments worth buying (limits fall with k)
        x_star = min(E, (k0 + n_ok) * sv.GRID)
        want = max(0.0, x_star - s) / (eta * dp.DT5)
        need = max(0.0, float(self.protect[min(t + 1, dp.N - 1)]) - s) / (eta * dp.DT5)
        c_plan = max(0.0, -pw)
        return -min(P, max(min(c_plan, need), want))


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
