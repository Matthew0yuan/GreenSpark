"""② the site reserve (reports/v2_plan.md, S2): the energy tomorrow's factory needs from the battery, held from 04:00.

Tomorrow's need per half-hour, at the meter: on a day flagged as a possible system peak, the planned load in its
peak hours (the meter at 0, v1's IRCR rule); otherwise the planned load above the cap, which is the CMD or the month's
peak so far if higher. The stored energy needed at 08:00 is the most, over the day, of the energy needed so far less
what the battery could still charge before (within the CMD and the import limit). Planned load is the load forecast
times (1 + load margin), as v1 holds its overnight IRCR reserve, or the forecast itself ("expected").

In the plan the reserve is two economic floors from 04:00, public at 09:00 today: ① plus the flagged hours' energy,
valued as v1's IRCR reserve, and ① plus the whole need, valued at the RT7 excess charge the shortfall would cause
(spread over tomorrow's hours of need). Floors are not added up by the planner, which keeps the largest; stacking
them keeps both.
"""

from dataclasses import dataclass

import numpy as np

from vpp import contracts as ct
from vpp import dispatch as dp
from vpp import site_bill as sb

MIN_MWH = 0.1             # a layer less than this above the one below is left out: it would only reshuffle near-ties
START = ct.slot("04:00")  # the floors hold from here to the end of the trading day, as v1's overnight IRCR reserve


@dataclass(frozen=True)
class SiteReserve:
    need_mw: np.ndarray    # tomorrow's MW per half-hour the battery must supply at the meter
    room_mwh: np.ndarray   # stored MWh it could add per half-hour tomorrow
    ircr_mwh: float        # stored MWh tomorrow's flagged hours need (v1's overnight reserve)
    total_mwh: float       # stored MWh needed at 08:00 tomorrow for every need
    cmd_hours: float       # hours tomorrow with a need above the cap
    ircr_value: float      # $ per MWh short of the flagged hours' floor
    cmd_value: float       # $ per MWh short of the whole need's floor

    def obligations(self, carry_mwh: float, eta: float) -> tuple:
        """The floors stacked on ① (carry_mwh: what tomorrow's compliance obligations need stored tonight)."""
        out = []
        if self.ircr_mwh > 0:
            out.append(dp.Obligation("site reserve: IRCR hours", START, dp.N, (carry_mwh + self.ircr_mwh) * eta / 2.0, 2.0,
                                     through_end=True, compliance=False, value_per_mwh=self.ircr_value, known_h=1.0))
        if self.total_mwh - self.ircr_mwh >= MIN_MWH:
            out.append(dp.Obligation("site reserve: CMD and IRCR", START, dp.N, (carry_mwh + self.total_mwh) * eta / 2.0,
                                     2.0, through_end=True, compliance=False, value_per_mwh=self.cmd_value, known_h=1.0))
        return tuple(out)


def site_reserve(tomorrow: dict, site, bat, load_margin: float, basis: str, cmd_mw: float | None, mtd: dict,
                 slots: np.ndarray | None, ircr_value_mw: float, enuc_per_mw: float) -> SiteReserve:
    """Tomorrow's site reserve. tomorrow: the next backtest day (vpp.backtest.load_days); mtd: each month's highest
    meter import so far, as at today's start; slots: tomorrow's flagged peak hours, None if it is not flagged;
    ircr_value_mw: $ per MW of a flagged day's largest import (v1's); enuc_per_mw: RT7 excess charge per MW-month."""
    P, E, eta = bat.power_mw, bat.energy_mwh, bat.eta
    load = tomorrow["load_fc"] * site.load_scale * (1 + (load_margin if basis == "plan" else 0.0))
    flagged = slots is not None
    ircr = min(E, float(np.minimum(P, load[slots]).sum()) * dp.DT / eta) if flagged else 0.0
    cap = (np.full(dp.N, np.inf) if cmd_mw is None
           else np.array([max(cmd_mw, mtd.get(m, 0.0)) for m in tomorrow["slot_month"]]))
    window = np.zeros(dp.N, bool)
    if flagged:
        window[slots] = True
    need = np.minimum(P, np.where(window, load, np.clip(load - cap, 0.0, None)))
    room = np.where(window | (need > 0), 0.0,
                    eta * np.clip(np.minimum(np.minimum(P, site.import_limit_mw - load), cap - load), 0.0, None) * dp.DT)
    short = np.cumsum(need * dp.DT / eta) - np.r_[0.0, np.cumsum(room)[:-1]]
    total = min(E, max(ircr, float(short.max(initial=0.0))))
    cmd_hours = float(((need > 0) & ~window).sum()) * dp.DT
    return SiteReserve(need, room, ircr, total, cmd_hours, ircr_value_mw / sb.IRCR_WINDOW_H,
                       enuc_per_mw * eta / max(cmd_hours, dp.DT))
