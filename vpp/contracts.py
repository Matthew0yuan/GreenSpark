"""Contracts and market obligations of the 10 MW / 40 MWh battery behind the factory's meter, turned
into the dispatcher's terms (vpp.dispatch.DayTerms) day by day, and the prices that settle them. Terms
are checked against the primary sources in reports/step0_terms.md (2026-10-20); labels as there.

Registration [verified]: a system size of 10 MW or more (export capacity plus the storage's charging
capacity up to the connection's CMD) must register as a Scheduled facility (ESM Rules 2.29.4), and the
battery and the factory behind one meter are one facility (2.29.1B(c)). Market obligations are net
injection at the meter: the battery first supplies the factory's load. A registered facility can only
sign the registered-facility form of a contract.

Capacity credits [verified]: certified capacity is net of the loads behind the meter (4.11.3); the
netted credits here are a proxy, as AEMO's method is not published. The obligation: in every 5-minute
interval of the ESR obligation window, 12 x the stored energy at its start must cover the credited MW
(4.26.1E-F), whatever dispatch used before; nothing requires a full battery at the window's start.
Refund = refund factor (0.25-6) x RCP / 365 / the window's trading intervals per MW short per trading
interval, capped at a year's capacity payments. Windows are published daily by 06:50 (6.3A.2). A
facility with capacity credits and a load must not run its battery to reduce its IRCR (7.10.6B).
Supplementary Capacity, registered-facility form [verified]: no events or notices - AEMO dispatches it
like any offer, so in every 5-minute interval 16:00-21:00 (Dec-Mar) the facility must offer the
contracted MW of net injection; each available 5 minutes earns the daily price x MW / 60; no
activation payment (energy earns the market price); unavailability only loses that payment (no
termination for it). The unregistered-equipment form (events on notice, 90% delivery) and
activation-only contracts are for unregistered equipment only. AEMO procures SCM only when it forecasts
a shortfall: none is planned for 2026-27.
NCESS [verified]: AEMO's 2025-27 peak demand round was awarded in 2024 and ends by 1 Oct 2027, with no
new round (its contracts may be ended if more than 10% of service intervals in 90 days are
unavailable). Western Power's network support has never been awarded; its SWIS round starts 1 Dec 2027
on 7 named feeders (calls of up to 2-6 h by feeder, >= 12 h notice, >= 95% delivered, the battery's
own output); its fee is not published.
PPA / energy supply to the factory: a backup reserve kept for outages (battery only) [assumed].
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from vpp import dispatch as dp
from vpp import site as st
from vpp.config import Battery
from vpp.site_bill import IRCR_WINDOW_H
from vpp.spike.outages import capacity_year
from vpp.wa_calendar import business_day

RCP = {2024: 194_783.54, 2025: 251_420.0, 2026: 216_091.78, 2027: 360_700.0, 2028: 488_500.0}  # $/MW/yr; 2028-29 = BRCP
REFUND_FACTOR = 1.0   # capacity refunds: central; 0.25-6 by how tight the system is [verified range]
EVENT_PENALTY = 5e4   # $/MWh short in a called event: decisions only
IRCR_PENALTY = 2e3    # $/MWh imported in a flagged peak interval: decisions only
WP_MAX_EVENT_H = 6.0    # longest Western Power network support call (2-6 h by feeder) [verified]
SCM_AO_PRICE = 500.0    # $/MWh, activation-only contracts (unregistered equipment only; 2024-25: $1,450-21,000/MW/h)
HOT_DAY = pd.Timestamp("2026-02-03")  # a hot-season Tuesday: the load profile capacity is certified on
ESR_DURATION_H = 4.0  # duration requirement behind an ESR's capacity credits before the 7 h rule [researched]
SCHEDULED_MIN_MW = 10.0  # a facility of 10 MW or more must register as Scheduled (ESM Rules 2.29.4) [verified]
FCESS_MIN_MW = {"reg_raise": 10.0, "cont_raise": 5.0, "reg_lower": 10.0, "cont_lower": 5.0}  # accredited capability
                                                         # (AEMO FCESS accreditation procedure v3.0) [verified]
SCM_MIN_MW = 5.0         # each 2025-26 Supplementary Capacity contract is at least 5 MW [verified]
RETAIL_FIXED = 150.0     # $/MWh: the energy price of a fixed retail contract, for the "fixed" sensitivity [assumed]
REG_UTIL = (0.14, 0.05)  # MWh the battery delivers / takes per MW-hour of regulation raise / lower enabled: large
                         # batteries in Sep 2026 (0.10-0.18 raise, 0.04-0.06 lower) [estimated, step0_terms.md section 6]


def slot(hhmm: str) -> int:
    """Trading interval of a clock time: 08:00 -> 0 ... 07:30 -> 47."""
    h, m = map(int, hhmm.split(":"))
    return ((h - 8) % 24) * 2 + m // 30


def rcp(day: pd.Timestamp) -> float:
    return RCP[int(capacity_year(pd.DatetimeIndex([day]))[0])]


def window_intervals(window: str) -> int:
    """Trading intervals of a capacity obligation window: the historical 4 h ESR window or the 7 h one."""
    return 14 if window == "7h" else 8


def refund_rate(day: pd.Timestamp, window: str) -> float:
    """$ refunded per MW short per trading interval of the window."""
    return REFUND_FACTOR * rcp(day) / 365 / window_intervals(window)


def window_7h(day: pd.Timestamp) -> tuple[int, int]:
    """ESR obligation window of a battery first credited for 2029-30 (7 h) on this calendar day."""
    if business_day([day])[0]:
        return slot("16:00"), slot("23:00")
    return (slot("15:30"), slot("22:30")) if day.month in (6, 7, 8) else (slot("15:00"), slot("22:00"))


def hot_weekday_load(site: st.Site) -> np.ndarray:
    ts = pd.date_range(HOT_DAY + pd.Timedelta(hours=8), periods=dp.N, freq="30min")
    return st.example_load(ts)[0] * site.load_scale


def credited_mw(bat: Battery, duration_h: float = ESR_DURATION_H) -> float:
    """Capacity credits of a battery on its own: the power it can sustain for the duration requirement."""
    return min(bat.power_mw, bat.energy_mwh * bat.eta / duration_h)


def capacity_obligation(bat: Battery, window: tuple[int, int]) -> dp.Obligation:
    """Capacity credits: deliver the credited MW from the ESR obligation window's first slot until it ends."""
    return dp.Obligation("capacity", window[0], window[1], credited_mw(bat))


def credits(bat: Battery, site: st.Site | None, window: tuple[int, int], preset: str, load_margin: float = 0.0) -> float:
    """Capacity credits (MW) for a window: "gross" (the battery alone), "netted" (behind the factory's
    meter, on its planned hot-weekday load) or "netted_margin" (with the plans' load margin)."""
    hours = (window[1] - window[0]) * dp.DT
    gross = min(bat.power_mw, bat.energy_mwh * bat.eta / hours)
    if preset == "gross" or site is None:
        return gross
    load = hot_weekday_load(site)[window[0]:window[1]] * (1 + load_margin if preset == "netted_margin" else 1.0)
    by_power = bat.power_mw - load.max()
    by_energy = (bat.energy_mwh * bat.eta - load.sum() * dp.DT) / hours
    return max(0.0, min(gross, by_power, by_energy))


@dataclass(frozen=True)
class ContractSpec:
    """A contract the battery may sign (Supplementary Capacity, NCESS).

    form "registered": the registered-facility form - no events or notices; the facility must offer `mw`
    of net injection at the meter in every 5-minute interval of the service window, and is paid `rate` x
    mw / 60 per available 5 minutes of a 5-hour window (the payment is pro rata to the window here).
    form "unregistered": the unregistered-equipment form - events called on notice, delivery checked per
    half hour; only equipment that is not registered may sign it.
    kind "AA" (availability and activation) cannot be breached: a compliance constraint. kind "AO"
    (activation only, unregistered load reduction): responding is a choice paid activation_price per MWh.
    max_unavailable: the share of service intervals in 90 days above which the buyer may end the contract
    (None: no such term). on_offer: why it cannot be obtained now ("" if it can).
    """
    name: str
    months: tuple[int, ...]
    window: tuple[int, int] | str  # (first slot, end slot), or "esroi": the day's ESR obligation window
    mw: float
    rate: float                    # availability, $/MW per service day
    form: str = "registered"       # "registered" | "unregistered"
    kind: str = "AA"               # "AA" availability and activation (cannot be breached) | "AO" activation only
    at_meter: bool = True          # net injection at the meter, or (unregistered) the battery's own output
    event_hours: float | None = None  # unregistered: the longest event it can call, the envelope to cover
    sustain_h: float | None = None    # registered: how long the MW must be deliverable once called (None: 5 min)
    notice_h: float = 0.0          # unregistered: notice before an event; >= 24: known at the morning plan
    min_delivery: float = 0.9      # unregistered: share of the called MW to deliver
    calls: str = "none"            # unregistered: events the backtest calls: "topN" | "2324" (14 days, 5 h) | "none"
    n_calls: int = 5
    call_hours: float | None = None
    activation_price: float = 0.0  # $/MWh delivered in an event beyond the market price (AO)
    excludes_capacity: bool = True
    max_unavailable: float | None = None
    on_offer: str = ""

    @property
    def breachable(self) -> bool:
        """Whether not delivering is a choice (AO) rather than a breach (AA)."""
        return self.kind == "AO"


def scm(mw: float, rate: float = 1250.0) -> ContractSpec:
    """Supplementary Capacity, registered-facility form: offer `mw` at the meter every 5 minutes 16:00-21:00,
    Dec-Mar. rate: $/MW/day (2024-25 contracts: $590-1,610) [assumed within that range]."""
    return ContractSpec("scm", (12, 1, 2, 3), (slot("16:00"), slot("21:00")), mw, rate)


def scm_unregistered(mw: float, call_hours: float = 2.0, calls: str = "topN") -> ContractSpec:
    """Supplementary Capacity, unregistered-equipment form: events of 2-5 h on up to 5 h notice (2 h here)."""
    return ContractSpec("scm_unreg", (12, 1, 2, 3), (slot("16:00"), slot("21:00")), mw, 1250.0, "unregistered",
                        at_meter=False, event_hours=5.0, notice_h=2.0, calls=calls, call_hours=call_hours)


def scm_ao(mw: float, call_hours: float = 2.0) -> ContractSpec:
    """Supplementary Capacity, activation only: unregistered load reduction; responding is optional."""
    return ContractSpec("scm_ao", (12, 1, 2, 3), (slot("16:00"), slot("21:00")), mw, 0.0, "unregistered", "AO",
                        at_meter=False, event_hours=5.0, notice_h=2.0, calls="topN", call_hours=call_hours,
                        activation_price=SCM_AO_PRICE)


def ncess_aemo(mw: float) -> ContractSpec:
    """AEMO's 2025-27 peak demand NCESS, registered form: offer `mw` in the ESR window and be able to keep it
    up for 4 h; a registered battery's contract was $591,000/MW-yr; not on offer to a new battery."""
    return ContractSpec("ncess_aemo", tuple(range(1, 13)), "esroi", mw, 591_000 / 365, sustain_h=4.0,
                        max_unavailable=0.10,
                        on_offer="AEMO's 2025-27 round was awarded in 2024 and ends by 1 Oct 2027; no new round")


def ncess_wp(mw: float, call_hours: float = 2.0, n_calls: int = 10) -> ContractSpec:
    """Western Power network support (SWIS round from 1 Dec 2027, 7 named feeders): calls of up to
    WP_MAX_EVENT_H on 12 h notice, 16:30-20:30 here (feeder windows lie in 16:00-22:00), >= 95% of the
    battery's own output. Fee unpublished: $150k/MW-yr assumed [no source]."""
    return ContractSpec("ncess_wp", (12, 1, 2, 3), (slot("16:30"), slot("20:30")), mw, 150_000 / 121, "unregistered",
                        at_meter=False, event_hours=WP_MAX_EVENT_H, notice_h=24.0, min_delivery=0.95, calls="topN",
                        n_calls=n_calls, call_hours=call_hours, excludes_capacity=False)


@dataclass(frozen=True)
class Route:
    """What a site has, one backtest scenario's configuration: how the battery is connected and registered,
    which markets it may enter, how its meter's energy is settled and what it commits to. How strictly the
    capacity obligation is held is a risk preference (vpp.config.RiskPrefs.capacity_floor)."""
    name: str
    metering: str = "behind"            # "behind" the factory's meter, or "standalone" (own connection, no factory)
    registration: str = "scheduled"     # "scheduled" (required from 10 MW of system size) or "none"
    fcess: tuple = tuple(dp.FCESS)      # FCESS services the facility is accredited for; () for none
    retail: str = "wholesale"           # the meter's energy: "wholesale" (RTP passed through) or "fixed" (RETAIL_FIXED, no paid export)
    export_service: bool = True         # a network service that lets the meter export (RT7 is import-only) [assumed, cost not modelled]
    rcm: str | None = None              # capacity credits: None, "gross", "netted", "netted_margin"
    window: str = "esroi"               # capacity window: "esroi" (historical 4 h) or "7h"
    contracts: tuple = ()               # ContractSpecs it has signed
    reg_util: tuple = REG_UTIL          # regulation actually used, raise and lower (vpp.dispatch.run_day)
    cmd_mw: float | None = None         # contract maximum demand cap at the meter (MW); None: no cap
    ircr: tuple | str | None = None     # IRCR peak days: "dynamic" (the season so far, vpp.site_bill.dynamic_flags) or
                                        # fixed thresholds (same day, day before; MW of AEMO's forecast)
    ircr_hindsight: bool = False        # flag the actual 12 peak intervals instead (upper bound)
    backup_mwh: float = 0.0             # PPA backup reserve, kept all day
    issue: str = "0900"                 # forecast issue: "0900" (strict, with the 08:00 bridge) or "0815"
    notes: tuple = field(default=())

    def check(self, bat: Battery, site: st.Site | None = None, load_margin: float = 0.0) -> list[str]:
        """Raise if the configuration is not allowed or cannot be had; return warnings about what it
        commits to that the battery cannot always cover (see firmness)."""
        power_mw = bat.power_mw
        errors, warnings = [], []
        behind = self.metering == "behind"
        if self.rcm == "gross" and behind:
            errors.append("gross credits need the battery metered on its own")
        if self.rcm and behind and (self.ircr is not None or self.ircr_hindsight):
            errors.append("a facility with capacity credits and a load must not run its battery to reduce IRCR (ESM 7.10.6B)")
        if power_mw >= SCHEDULED_MIN_MW and self.registration != "scheduled":
            errors.append(f"a system size of {SCHEDULED_MIN_MW:g} MW or more must register as Scheduled (ESM Rules 2.29.4)")
        if self.fcess and self.registration != "scheduled":
            errors.append("only a registered (Scheduled) facility can be enabled for FCESS (ESM 7.6.2)")
        errors += [f"{k} needs at least {FCESS_MIN_MW[k]:g} MW" for k in self.fcess if power_mw < FCESS_MIN_MW[k]]
        names = [c.name for c in self.contracts]
        if len(set(names)) < len(names):
            errors.append("at most one contract of each kind")
        if "scm" in names and "ncess_aemo" in names:
            errors.append("SCM cannot be held with AEMO's peak demand NCESS")
        for c in self.contracts:
            if c.on_offer:
                errors.append(f"{c.name} is not on offer: {c.on_offer}")
            if self.registration == "scheduled" and c.form == "unregistered" and c.name.startswith("scm"):
                errors.append(f"{c.name} is the unregistered-equipment form; a registered facility signs the registered form")
            if self.rcm and c.excludes_capacity:
                errors.append(f"{c.name} cannot be held with capacity credits for the same MW")
            if c.name.startswith("scm") and c.mw < SCM_MIN_MW:
                errors.append(f"a Supplementary Capacity contract is at least {SCM_MIN_MW:g} MW")
            if c.at_meter and behind and not self.export_service:
                errors.append(f"{c.name} needs net injection at the meter, which needs an export service")
            if not c.breachable:
                e, w = firmness(c, bat, site if behind else None, load_margin)
                errors, warnings = errors + e, warnings + w
        if errors:
            raise ValueError(f"{self.name}: " + "; ".join(errors))
        return warnings

    def assumptions(self) -> list[str]:
        """The market access this configuration assumes that the project has not verified."""
        out = []
        if self.fcess:
            out.append(f"FCESS accreditation for {', '.join(self.fcess)} [not verified]")
        if self.retail == "wholesale" and self.metering == "behind":
            out.append("the factory's retail contract passes the wholesale price through [no such product found]")
        if self.export_service and self.metering == "behind":
            out.append("a Western Power entry or bidirectional service lets the meter export (RT7 is import-only) "
                       "[not verified; its cost is not included]")
        if any(self.reg_util):
            out.append(f"regulation used at {self.reg_util[0]:.0%} (raise) / {self.reg_util[1]:.0%} (lower) of the MW "
                       "enabled [estimated from Sep 2026 data]")
        if self.rcm and self.metering == "behind":
            out.append("AEMO nets the factory's load off the capacity credits as assumed [method not published]")
        for c in self.contracts:
            if c.name == "scm":
                out.append("AEMO procures SCM only when it forecasts a shortfall; none planned for 2026-27")
            if c.name == "ncess_wp":
                out.append("the factory is on one of Western Power's 7 named feeders (round from 1 Dec 2027); "
                           "fee assumed [no source]")
        return out


def firmness(spec: ContractSpec, bat: Battery, site: st.Site | None, load_margin: float) -> tuple[list[str], list[str]]:
    """(errors, warnings) on how firmly the battery can cover a contract it cannot breach, on the factory's
    planned load (net injection at the meter also supplies it). Power: the share of window intervals over a
    90-day summer in which it cannot offer the MW - an error above the contract's termination share, else a
    warning (those intervals lose their payment). Energy: an error if the longest event, or the time the
    MW must be kept up, needs more than the battery holds."""
    w = spec.window if isinstance(spec.window, tuple) else (19, 27)  # the ESR window on the reference hot weekday
    on_meter = spec.at_meter and site is not None
    if on_meter:
        ts = pd.date_range("2025-12-01 08:00", periods=90 * dp.N, freq="30min")
        summer = st.example_load(ts)[0].reshape(90, dp.N) * site.load_scale * (1 + load_margin)
    else:
        summer = np.zeros((90, dp.N))
    errors, warnings = [], []
    short = float(np.mean(spec.mw + summer[:, w[0]:w[1]] > bat.power_mw + 1e-9))
    if short > 0:
        msg = f"{spec.name} {spec.mw:g} MW: power falls short in {short:.0%} of the window's intervals over a summer"
        if spec.max_unavailable is not None and short > spec.max_unavailable:
            errors.append(f"{msg} (more than {spec.max_unavailable:.0%} unavailable in 90 days allows termination)")
        else:
            warnings.append(f"{msg}: those intervals are unavailable and lose their payment")
    hours = spec.event_hours if spec.form == "unregistered" else spec.sustain_h
    if hours:
        load = hot_weekday_load(site) * (1 + load_margin) if on_meter else None
        need = dp.Obligation(spec.name, w[0], w[1], spec.mw, hours, at_meter=on_meter).need(bat, w[0] * dp.DT, load)
        if need > bat.energy_mwh + 1e-9:
            errors.append(f"{spec.name} {spec.mw:g} MW: keeping it up for {hours:g} h needs {need:.1f} MWh stored, more "
                          f"than {bat.energy_mwh:g} MWh")
    return errors, warnings


def carry_in(terms: dp.DayTerms, bat: Battery, site: st.Site | None, load_plan, today: dp.DayTerms | None = None,
             load_today=None) -> float:
    """Stored MWh a day must end with for the next day's compliance obligations (`terms`, before any event
    is known): what they need at 08:00 or, for a window that opens later, what the battery could not charge
    by then (load_plan: that day's planned site load), beyond what today's own obligations (`today`)
    already hold at the end of the day: an obligation running across midnight needs its energy once."""
    comp = [o for o in terms.obligations if o.compliance]
    if not comp:
        return 0.0
    need = dp.keep_for_obligation(bat, comp, 0.0, dp.charge_room(bat, site, load_plan), load_plan)
    held = 0.0 if today is None else dp.obligation_need(bat, [o for o in today.obligations if o.compliance], dp.N * dp.DT,
                                                        load_today)
    return max(0.0, need - held)


def call_days(spec: ContractSpec, demand: pd.Series) -> dict:
    """Events called under an unregistered-form contract: {trading day: (first slot, end slot)}, on the
    highest-demand days of each Hot Season, centred on the day's demand peak and kept inside the service
    window. A registered-form contract has no events: AEMO dispatches its offer."""
    if spec.calls == "none" or spec.form == "registered":
        return {}
    n, hours = (14, 5.0) if spec.calls == "2324" else (spec.n_calls, spec.call_hours or spec.event_hours)
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


def day_terms(route: Route, d: dict, bat: Battery, site: st.Site | None, cmd_cap: np.ndarray | None,
              ircr_slots: np.ndarray | None, ircr_at: int, forecast: np.ndarray | None, credit_mw: float,
              events: dict, enuc_per_mw: float, reserve_mwh: float = 0.0, capacity_floor: str = "sustain",
              carry_mwh: float = 0.0, ircr_value: float = IRCR_PENALTY) -> dp.DayTerms:
    """One day's DayTerms for a route. d: the backtest day (vpp.backtest.load_days); cmd_cap: per-slot CMD
    cap (MW, the month-to-date peak if higher) or None; ircr_slots: per-slot cap for a flagged day or None,
    revealed at slot ircr_at; forecast: the 09:00 energy forecast revealed at slot 2 (None: no reveal);
    events: {contract name: {trading day: (first slot, end slot)}} (call_days); reserve_mwh: stored energy
    to hold from 04:00 to the end of the day, for tomorrow's flagged peak; capacity_floor: how strictly
    capacity credits are held (vpp.config.RiskPrefs); carry_mwh: stored energy the day must end with
    because tomorrow's compliance obligations need it and it cannot be charged in time tomorrow (see
    carry_in); ircr_value: $ per MW of the largest import in a flagged day's peak hours (an economic cap:
    see vpp.backtest.q2b_aux)."""
    obls, layers, reveals, avail, cap_test = [], [], [], [], None
    at_meter = route.metering == "behind" and site is not None
    if route.rcm:
        w = d["window"] if route.window == "esroi" else d["window7"]
        # the rule: able to deliver the credited MW in each 5-min interval (compliance)
        obls.append(dp.Obligation("capacity", w[0], w[1], credit_mw, 1 / 12, at_meter=at_meter))
        if capacity_floor == "sustain":
            # stricter than the rule: able to deliver to the window's end (economic, valued at the refund
            # it avoids if the battery had to: 1 MWh short ~ 2 x eta MW x trading intervals short)
            value = 2 * bat.eta * refund_rate(d["day"], route.window)
            obls.append(dp.Obligation("capacity margin", w[0], w[1], credit_mw, None, at_meter=at_meter,
                                      compliance=False, value_per_mwh=value))
        cap_test = dp.CapacityTest(w[0], w[1], credit_mw, at_meter)
    for spec in route.contracts:
        if d["day"].month not in spec.months:
            continue
        w = d["window"] if spec.window == "esroi" else spec.window
        on_meter = spec.at_meter and site is not None
        if spec.form == "registered":
            # offer the MW at the meter in every 5 minutes of the window: able to deliver it for 5 minutes, or
            # for sustain_h (compliance); availability is scored per 5 minutes
            standby = dp.Obligation(spec.name, w[0], w[1], spec.mw, spec.sustain_h or 1 / 12, at_meter=on_meter)
            obls.append(standby)
            avail.append(dp.AvailabilityTest(spec.name, w[0], w[1], spec.mw, standby, (), 1.0, per_5min=True))
            continue
        aa = not spec.breachable
        # AA: stand by for the longest event the contract can call (compliance); AO: nothing to hold
        standby = dp.Obligation(spec.name, w[0], w[1], spec.mw if aa else 0.0, spec.event_hours, at_meter=on_meter)
        called = ()
        if d["day"] in events.get(spec.name, {}):
            a, b = events[spec.name][d["day"]]
            cap = np.full(dp.N, np.inf)
            cap[a:b] = -spec.mw
            notice = max(0, a - int(round(spec.notice_h * 2)))
            clock = pd.date_range("2000-01-01 08:00", periods=dp.N + 1, freq="30min").strftime("%H:%M")
            label = f"{spec.name} event notice ({clock[a]}-{clock[b]})"
            if aa:  # must deliver: a compliance cap, the energy held for it, and the event's MWh on top of the cycle limit
                event = dp.CapLayer(f"{spec.name} event", cap, "slot", EVENT_PENALTY, at_meter=on_meter, compliance=True,
                                    known_h=notice * dp.DT)
                hold = dp.Obligation(f"{spec.name} event", a, b, spec.mw, at_meter=on_meter, known_h=notice * dp.DT)
                reveals.append(dp.Reveal(notice, label, layers=(event,), obligations=(hold,), drop=(spec.name,),
                                         budget_extra_mwh=spec.mw * (b - a) * dp.DT))
            else:  # may decline: delivering is worth the activation price per MWh (economic)
                event = dp.CapLayer(f"{spec.name} event", cap, "slot", spec.activation_price, at_meter=on_meter,
                                    known_h=notice * dp.DT)
                reveals.append(dp.Reveal(notice, label, layers=(event,)))
            called = ((a, b),)
        if aa:
            obls.append(standby)
        avail.append(dp.AvailabilityTest(spec.name, w[0], w[1], spec.mw, standby, called, spec.min_delivery))
    if carry_mwh > 0:  # what tomorrow's compliance obligations need from tonight (compliance)
        obls.append(dp.Obligation("tomorrow", dp.N - 1, dp.N, carry_mwh * bat.eta / dp.DT, dp.DT, through_end=True))
    if route.backup_mwh > 0:  # the factory's supply agreement: backup kept for outages (compliance) [assumed]
        obls.append(dp.Obligation("backup", 0, dp.N, route.backup_mwh * bat.eta / 2.0, 2.0, through_end=True))
    if reserve_mwh > 0:  # held for tomorrow's possible system peak (flagged by the run public at 09:00 today):
        # an economic choice, valued as the IRCR cap
        reserve = dp.Obligation("peak-day reserve", slot("04:00"), dp.N, reserve_mwh * bat.eta / 2.0, 2.0,
                                through_end=True, compliance=False, value_per_mwh=ircr_value / IRCR_WINDOW_H, known_h=1.0)
        reveals.append(dp.Reveal(2, "tomorrow flagged as a possible system peak day", obligations=(reserve,)))
    if cmd_cap is not None and site is not None:  # the network tariff: an economic choice, priced by the tariff
        layers.append(dp.CapLayer("cmd", cmd_cap, "peak", enuc_per_mw, at_meter=True, running_max=True))
    if forecast is not None:
        reveals.append(dp.Reveal(2, "forecast issued", energy=forecast))
    if ircr_slots is not None and site is not None:
        # flagged by the 08:00 run: today's is public at 09:00, the day before's at 09:00 yesterday
        # the day's peak intervals count through the median of 12: valued on the day's largest import there
        layer = dp.CapLayer("ircr", ircr_slots, "peak", ircr_value, at_meter=True, known_h=1.0 if ircr_at > 0 else -23.0)
        if ircr_at <= 0:
            layers.append(layer)
        else:
            reveals.append(dp.Reveal(ircr_at, "possible system peak day flagged", layers=(layer,)))
    fcess_hi = None
    if set(route.fcess) != set(dp.FCESS):  # services it is not accredited for are never enabled
        fcess_hi = {k: np.full(dp.N, np.inf if k in route.fcess else 0.0) for k in dp.FCESS}
    return dp.DayTerms(tuple(obls), tuple(layers), tuple(reveals), cap_test, tuple(avail), fcess_hi)
