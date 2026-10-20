"""Contracts and market obligations of the 10 MW / 40 MWh battery behind the factory's meter, turned
into the dispatcher's terms (vpp.dispatch.DayTerms) day by day, and the prices that settle them.

Behind one meter the battery and the factory are one facility (ESM Rules 2.29.1B(c)), so market
obligations (capacity credits, a registered facility's Supplementary Capacity or AEMO NCESS contract)
are net export at the meter: the battery first supplies the factory's load. Load-reduction contracts
(Western Power NCESS network support) count the battery's own output. Sources and labels:
reports/tool_model.md sections 1.3 and 4 (checked 2026-10-17/19).

Capacity credits [verified rule, researched numbers]: certified capacity is net of the loads behind the
meter (4.11.3). Gross credits = min(P, E x eta / duration); the netted credits here are a proxy, as
AEMO's method is not published: the most MW the battery can add to the factory's hot-weekday load over
the whole window, by power and by energy. Revenue = credits x Reserve Capacity Price / 365 per day;
refunds = refund factor x RCP / 365 / window trading intervals per MW short per trading interval
(5-min charge test, vpp.dispatch.CapacityTest). Windows: the historical 4 h ESR obligation intervals
(data), or the 7 h windows of a battery first credited for 2029-30 (business days 16:00-23:00,
non-business days 15:00-22:00, Jun-Aug 15:30-22:30) [researched].
Supplementary Capacity, 2025-26 terms [researched]: 1 Dec - 1 Apr, 16:00-21:00 every day, events of up
to `event_hours` on 2 h notice, availability paid per available interval (a tenth of the daily price
each), no separate activation price for a registered facility (energy earns the market price),
unavailable if it cannot deliver or delivers < 90% in an event; not with capacity credits.
NCESS [researched]: AEMO's 2025-27 peak demand service - the ESR obligation intervals every day, able
to hold the MW for 4 h, availability ~$300k/MW-yr, offset by capacity credits (so exclusive here);
Western Power network support - 1 Dec - 1 Apr 16:30-20:30, up to 20 calls of 2-6 h, >= 12 h notice,
>= 95% delivered, availability ~$150k/MW-yr (fee unknown; low confidence), load reduction counts.
PPA / energy supply to the factory: a backup reserve kept for outages (battery only) [assumed].
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from vpp import dispatch as dp
from vpp import site as st
from vpp.spike.outages import capacity_year
from vpp.wa_calendar import business_day

RCP = {2024: 194_783.54, 2025: 251_420.0, 2026: 216_091.78, 2027: 360_700.0, 2028: 488_500.0}  # $/MW/yr; 2028-29 = BRCP
EVENT_PENALTY = 5e4   # $/MWh short in a called event: decisions only
IRCR_PENALTY = 2e3    # $/MWh imported in a flagged peak interval: decisions only
HOT_DAY = pd.Timestamp("2026-02-03")  # a hot-season Tuesday: the load profile capacity is certified on


def slot(hhmm: str) -> int:
    """Trading interval of a clock time: 08:00 -> 0 ... 07:30 -> 47."""
    h, m = map(int, hhmm.split(":"))
    return ((h - 8) % 24) * 2 + m // 30


def rcp(day: pd.Timestamp) -> float:
    return RCP[int(capacity_year(pd.DatetimeIndex([day]))[0])]


def window_7h(day: pd.Timestamp) -> tuple[int, int]:
    """ESR obligation window of a battery first credited for 2029-30 (7 h) on this calendar day."""
    if business_day([day])[0]:
        return slot("16:00"), slot("23:00")
    return (slot("15:30"), slot("22:30")) if day.month in (6, 7, 8) else (slot("15:00"), slot("22:00"))


def hot_weekday_load(site: st.Site) -> np.ndarray:
    ts = pd.date_range(HOT_DAY + pd.Timedelta(hours=8), periods=dp.N, freq="30min")
    return st.example_load(ts)[0] * site.load_scale


def credits(bat: dp.Battery, site: st.Site | None, window: tuple[int, int], preset: str) -> float:
    """Capacity credits (MW) for a window: "gross" (the battery alone), "netted" (behind the factory's
    meter, on its planned hot-weekday load) or "netted_margin" (with the plans' load margin)."""
    hours = (window[1] - window[0]) * dp.DT
    gross = min(bat.power_mw, bat.energy_mwh * bat.eta / hours)
    if preset == "gross" or site is None:
        return gross
    load = hot_weekday_load(site)[window[0]:window[1]] * (1 + site.load_margin if preset == "netted_margin" else 1.0)
    by_power = bat.power_mw - load.max()
    by_energy = (bat.energy_mwh * bat.eta - load.sum() * dp.DT) / hours
    return max(0.0, min(gross, by_power, by_energy))


@dataclass(frozen=True)
class ContractSpec:
    """A contract that keeps the battery on standby and may call events (Supplementary Capacity, NCESS)."""
    name: str
    months: tuple[int, ...]
    window: tuple[int, int] | str  # (first slot, end slot), or "esroi": the day's ESR obligation window
    mw: float
    event_hours: float
    notice_h: float                # notice before an event; >= 24: known at the morning plan
    at_meter: bool                 # net export at the meter (registered facility) or the battery's own output
    min_delivery: float
    rate: float                    # availability, $/MW per service day
    calls: str = "none"            # events called: "none" | "topN" (n_calls highest-demand days) | "2324" (14 days, 5 h)
    n_calls: int = 5
    excludes_capacity: bool = True


def scm(mw: float, hours: float = 2.0, at_meter: bool = True, calls: str = "topN", n_calls: int = 5) -> ContractSpec:
    return ContractSpec("scm", (12, 1, 2, 3), (slot("16:00"), slot("21:00")), mw, hours, 2.0, at_meter, 0.9, 1250.0,
                        calls, n_calls)


def ncess_aemo(mw: float) -> ContractSpec:
    return ContractSpec("ncess_aemo", tuple(range(1, 13)), "esroi", mw, 4.0, 0.0, True, 0.9, 300_000 / 365)


def ncess_wp(mw: float, hours: float = 2.0, n_calls: int = 10) -> ContractSpec:
    return ContractSpec("ncess_wp", (12, 1, 2, 3), (slot("16:30"), slot("20:30")), mw, hours, 24.0, False, 0.95,
                        150_000 / 121, "topN", n_calls)


@dataclass(frozen=True)
class Route:
    """How the battery is connected and what it commits to (one backtest scenario's terms)."""
    name: str
    metering: str = "behind"            # "behind" the factory's meter, or "standalone" (own connection, no factory)
    rcm: str | None = None              # capacity credits: None, "gross", "netted", "netted_margin"
    window: str = "esroi"               # capacity window: "esroi" (historical 4 h) or "7h"
    floor_rule: str = "sustain"         # "sustain": keep what delivering to the window's end needs; "refund_test": 5 min
    contract: ContractSpec | None = None
    cmd_mw: float | None = None         # contract maximum demand cap at the meter (MW); None: no cap
    ircr: tuple[float, float] | None = None  # IRCR peak-day thresholds (same day, day before; MW of AEMO's forecast)
    ircr_hindsight: bool = False        # flag the actual 12 peak intervals instead (upper bound)
    backup_mwh: float = 0.0             # PPA backup reserve, kept all day
    issue: str = "0900"                 # forecast issue: "0900" (strict, with the 08:00 bridge) or "0815"
    notes: tuple = field(default=())

    def check(self) -> None:
        if self.rcm == "gross" and self.metering == "behind":
            raise ValueError(f"{self.name}: gross credits need the battery metered on its own")
        if self.rcm and self.contract is not None and self.contract.excludes_capacity:
            raise ValueError(f"{self.name}: {self.contract.name} cannot be held with capacity credits for the same MW")


def call_days(spec: ContractSpec, demand: pd.Series) -> dict:
    """Events called under a contract: {trading day: (first slot, end slot)}, on the highest-demand days
    of each Hot Season, centred on the day's demand peak and kept inside the service window."""
    if spec.calls == "none":
        return {}
    n, hours = (14, 5.0) if spec.calls == "2324" else (spec.n_calls, spec.event_hours)
    day = (demand.index - pd.Timedelta(hours=8)).normalize()
    served = demand[np.isin(day.month, spec.months)]
    sday = (served.index - pd.Timedelta(hours=8)).normalize()
    season = np.where(sday.month >= 10, sday.year, sday.year - 1)
    out = {}
    for _, g in served.groupby(season):
        gday = (g.index - pd.Timedelta(hours=8)).normalize()
        for d in g.groupby(gday).max().nlargest(n).index:
            peak = g[gday == d].idxmax()
            k = int((peak - d - pd.Timedelta(hours=8)) / pd.Timedelta("30min"))
            first, end = (spec.window if isinstance(spec.window, tuple) else (slot("17:30"), slot("21:30")))
            length = int(round(hours * 2))
            a = int(np.clip(k - length // 2, first, max(first, end - length)))
            out[d] = (a, min(end, a + length))
    return out


def day_terms(route: Route, d: dict, bat: dp.Battery, site: st.Site | None, cmd_cap: np.ndarray | None,
              ircr_slots: np.ndarray | None, ircr_at: int, forecast: np.ndarray | None, credit_mw: float,
              events: dict, enuc_per_mw: float, reserve_mwh: float = 0.0) -> dp.DayTerms:
    """One day's DayTerms for a route. d: the backtest day (vpp.backtest.load_days); cmd_cap: per-slot CMD
    cap (MW, the month-to-date peak if higher) or None; ircr_slots: per-slot cap for a flagged day or None,
    revealed at slot ircr_at; forecast: the 09:00 energy forecast revealed at slot 2 (None: no reveal);
    reserve_mwh: stored energy to hold from 04:00 to the end of the day, for tomorrow's flagged peak."""
    obls, layers, reveals, avail, cap_test = [], [], [], [], None
    at_meter = route.metering == "behind" and site is not None
    if route.rcm:
        w = d["window"] if route.window == "esroi" else d["window7"]
        hold = 1 / 12 if route.floor_rule == "refund_test" else None
        obls.append(dp.Obligation("capacity", w[0], w[1], credit_mw, hold, at_meter=at_meter))
        cap_test = dp.CapacityTest(w[0], w[1], credit_mw, at_meter)
    spec = route.contract
    if spec is not None and d["day"].month in spec.months:
        w = d["window"] if spec.window == "esroi" else spec.window
        on_meter = spec.at_meter and site is not None
        standby = dp.Obligation(spec.name, w[0], w[1], spec.mw, spec.event_hours, at_meter=on_meter)
        called = ()
        if d["day"] in events:
            a, b = events[d["day"]]
            cap = np.full(dp.N, np.inf)
            cap[a:b] = -spec.mw
            event = dp.CapLayer(f"{spec.name} event", cap, "slot", EVENT_PENALTY, at_meter=on_meter, above_floors=True)
            hold = dp.Obligation(f"{spec.name} event", a, b, spec.mw, at_meter=on_meter)
            notice = max(0, a - int(round(spec.notice_h * 2)))
            clock = pd.date_range("2000-01-01 08:00", periods=dp.N + 1, freq="30min").strftime("%H:%M")
            reveals.append(dp.Reveal(notice, f"{spec.name} event notice ({clock[a]}-{clock[b]})", layers=(event,),
                                     obligations=(hold,), drop=(spec.name,), budget_extra_mwh=spec.mw * (b - a) * dp.DT))
            called = ((a, b),)
        obls.append(standby)
        avail.append(dp.AvailabilityTest(spec.name, w[0], w[1], spec.mw, standby, called, spec.min_delivery))
    if route.backup_mwh > 0:
        obls.append(dp.Obligation("backup", 0, dp.N, route.backup_mwh * bat.eta / 2.0, 2.0, through_end=True))
    if reserve_mwh > 0:
        obls.append(dp.Obligation("peak-day reserve", slot("04:00"), dp.N, reserve_mwh * bat.eta / 2.0, 2.0, through_end=True))
    if cmd_cap is not None and site is not None:
        layers.append(dp.CapLayer("cmd", cmd_cap, "peak", enuc_per_mw, at_meter=True, above_floors=True))
    if forecast is not None:
        reveals.append(dp.Reveal(2, "forecast issued", energy=forecast))
    if ircr_slots is not None and site is not None:
        layer = dp.CapLayer("ircr", ircr_slots, "slot", IRCR_PENALTY, at_meter=True)
        if ircr_at <= 0:
            layers.append(layer)
        else:
            reveals.append(dp.Reveal(ircr_at, "possible system peak day flagged", layers=(layer,)))
    return dp.DayTerms(tuple(obls), tuple(layers), tuple(reveals), cap_test, tuple(avail))
