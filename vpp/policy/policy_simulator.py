"""Customer-owned battery activation policies: simulation and financial accounting
(reports/policy/POLICY_MODEL.md; assumptions with their status in reports/policy/POLICY_ASSUMPTIONS.md).

Zenith's own 10 MW / 40 MWh battery is dispatched by the existing Q2B engine (vpp.v2, adopted configuration
"rw50+state t5+roll"); its results are read from that engine's saved ledger, unchanged. Customer-owned household
batteries are added on top. They are price takers on separate meters with no constraint shared with Zenith's battery
(no FCESS, no common connection), so Zenith's incremental profit from them is the customer fleet's own account.

One household archetype stands for every enrolled battery. Outside events it runs in self-consumption mode: it stores
rooftop PV surplus and serves the home's load. An activation has three parts:
  standby   the battery stops serving the home and fills to full, from PV first and then from the grid;
  event     it discharges at a flat rate that brings it to the customer's reserve at the event's end; the home's load
            is served first and the rest is exported;
  after     self-consumption resumes from the lower state of charge; the home imports more until PV refills it.
Every effect is measured against the same home without the activation (the baseline), from the start of standby to
15:00 the next day; a state-of-charge gap still left then is valued as energy the home would have used.

Decisions use only what is public at 09:00 on the trading day (the 09:00 price forecast and spike risk, AEMO's
pre-dispatch demand forecast for the peak-day flag) and the policy's terms; everything is settled at actual prices.

Accounting per responding customer and call, A$ (Zenith / customer / the customer's retailer, so that they reconcile):
  W        wholesale value of the change in the meter's net export, at the 30-min price (RTP), standby charging and
           the rebound included; to Zenith
  pay      per-kWh payment on battery-sourced export in the event + per-event payment; Zenith -> customer
  offset   energy offset credit on the extra import in standby; Zenith -> customer
  bill     extra retail import less extra feed-in income; customer -> retailer
  wear     the customer's extra battery throughput x its degradation cost; customer
  hassle   the customer's disutility per event; customer
  ops      Zenith's cost per customer per event
Plus once a year: the IRCR value (capacity scenarios only; the fleet's lower median import in the 12 Peak SWIS
Trading Intervals x the Reserve Capacity Price), the annual participation fee and Zenith's cost per customer.

Run (a summary of the Synergy-like reference policy):  python -m vpp.policy.policy_simulator
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache

import numpy as np
import pandas as pd

from vpp import contracts as ct
from vpp import site_bill as sb
from vpp.data import PROCESSED
from vpp.evaluate import CUTOFF, TRAIN_END

H = pd.Timedelta(hours=8)          # a trading day starts at 08:00
DT = 0.5                            # hours per trading interval
N = 48                              # trading intervals a day
HORIZON = 62                        # each activation is followed to 15:00 the next day (slot 62 of its day)
PEAK = (16, 26)                     # slots kept for the IRCR: 16:00..20:30 (every Peak SWIS interval 2023-26 is 17:00-19:00)
DECISION_SLOT = 2                   # 09:00: when the day's calls are decided
FIRST_DAY, LAST_DAY = pd.Timestamp("2023-10-01"), pd.Timestamp("2026-10-22")  # vpp.backtest.LAST_DAY
FORECAST_FROM = pd.Timestamp("2025-04-01")  # first day with the 09:00 price forecast (vpp.forecast.FORECAST_START)
SEASONS = (2023, 2024, 2025)        # Hot Seasons (1 Dec - 1 Apr, by starting year) with data
IRCR_QUANTILES = (0.9, 0.75, 0.5)   # 0.9: the v2 rule (vpp.site_bill.FLAG_QUANTILE); lower flags fewer days
SELF, STANDBY, EVENT = 0, 1, 2

# Customer tariffs, Synergy, from 2026-07-01 [verified, memory wa-household-battery-payments]
RETAIL = 0.3326                     # A1 home plan, $/kWh incl. GST
DEBS_PEAK, DEBS_OFF = 0.10, 0.02    # DEBS buyback, $/kWh exported 15:00-21:00 / otherwise


# ----------------------------------------------------------------------------------------------- set-up objects
@dataclass(frozen=True)
class Household:
    """The customer battery archetype and its home [assumed, POLICY_ASSUMPTIONS.md section 3]."""
    usable_kwh: float = 13.5        # usable storage
    power_kw: float = 5.0           # inverter limit, charge and discharge (Western Power's 5 kW export limit)
    round_trip: float = 0.90
    pv_kw: float = 5.0              # PV AC output limit (6.6 kWp on a 5 kW inverter)
    pv_kwp: float = 6.6
    load_kwh_day: float = 18.0      # average daily household load
    own_reserve: float = 0.10       # backup share the customer keeps outside events
    hot_gamma: float = 1.5          # load x (day's peak underlying system demand / month's median) ** gamma

    @property
    def eta(self) -> float:
        return self.round_trip ** 0.5


@dataclass(frozen=True)
class Policy:
    """Zenith's offer to customers: the decision variables (POLICY_MODEL.md section 2)."""
    name: str = "custom"
    comp_type: str = "energy"       # energy | event | fixed+variable (a label; the amounts below are what is paid)
    pay_per_kwh: float = 0.70       # $ per kWh of net export at the meter in an event, capped at the battery's kWh
    pay_per_event: float = 0.0      # $ per activation the battery responds to
    annual_fee: float = 0.0         # $ per enrolled customer per contract year
    max_events: int = 30            # activations per contract year (1 July - 30 June)
    event_h: float = 2.0            # event length; the most a call may last
    standby_h: float = 2.0          # control before an event (battery held and filled)
    max_control_h: float = 6.0      # standby + event may not exceed this (Synergy: 6 h per event)
    reserve_soc: float = 0.20       # share of usable storage Zenith may not discharge below
    notice_h: float = 0.0           # least notice before standby starts (Synergy gives none)
    window: tuple = (15.0, 21.0)    # clock hours events may run in
    max_events_per_day: int = 1
    offset_rate: float = RETAIL     # $ per kWh of extra standby import credited (Synergy: the retail tariff), capped
                                    # at the battery's kWh per event
    min_usable_kwh: float = 5.0     # eligibility: batteries smaller than this are not enrolled

    def nominal_kwh(self, hh: Household) -> float:
        """kWh a full battery delivers in one event at this reserve (AC)."""
        return hh.usable_kwh * (1 - self.reserve_soc) * hh.eta


@dataclass(frozen=True)
class Obligations:
    """Which revenue streams reach Zenith, and its costs: the commercial and market setting (scenarios S1-S3,
    POLICY_MODEL.md section 3.1)."""
    name: str = "S1 energy only"
    energy_to_zenith: bool = True   # Zenith settles the change in the meters' net position at RTP
    capacity: str = "none"          # none | floor (IRCR, net export counts 0) | net (net export counts negative)
    capacity_share: float = 1.0     # share of the IRCR value that reaches Zenith (S3: what Synergy would pay a TPA)
    rcp_ratio: float = 1.0          # AEMO's IRCR scaling ratio applied to the households [assumed]
    rcp: float | None = None        # $/MW/yr for every season; None: the capacity year each season sets (as if)
    fcess: bool = False             # customer batteries provide no FCESS [restricted]
    ops_per_customer: float = 25.0  # $ per enrolled customer per year (platform, communications) [assumed]
    ops_per_event: float = 0.05     # $ per responding customer per event [assumed]
    fixed_cost: float = 0.0         # $ per year for the programme [assumed]
    downside_limit: float = 0.0     # the risk-adjusted fleet profit must be at least -this


@dataclass(frozen=True)
class Participation:
    """Customer willingness to participate: a scenario, not measured data [assumed]."""
    name: str = "central"
    eligible: int = 10_000          # households Zenith can offer the contract to
    max_share: float = 0.5          # most that would ever enrol
    b50: float = 100.0              # expected net benefit ($/yr) at which half of max_share enrol
    slope: float = 40.0             # $/yr: how fast enrolment rises with the benefit
    min_benefit: float = 0.0        # an offer whose expected customer benefit is below this is infeasible
    availability: float = 0.9       # share of enrolled batteries that respond to a call
    deg_per_kwh: float = 0.05       # the customer's cost of extra battery throughput, $/kWh
    hassle_per_event: float = 0.50  # the customer's disutility per event, $
    debs_on_event_exports: bool = True   # DEBS is paid on all metered net export, events included [verified]

    def share(self, benefit: float) -> float:
        if benefit < self.min_benefit:
            return 0.0
        return self.max_share / (1 + np.exp(-(benefit - self.b50) / self.slope))


@dataclass(frozen=True)
class CallRule:
    """How the dispatcher decides calls under a policy (fitted per policy on data up to 2026-06-30)."""
    kind: str = "optimised"         # optimised | simple | perfect
    theta: float = np.inf           # an energy call needs its 09:00 expected margin ($/customer) at least this
    ircr_q: float = 0.9             # tolerance quantile of the peak-day flag rule
    reserve_for_ircr: bool = True   # energy calls leave the calls the rest of the Hot Season is expected to need


@dataclass(frozen=True)
class Portfolio:
    """The batteries: Zenith's own (dispatched by the Q2B engine) and the customer archetype."""
    household: Household = field(default_factory=Household)
    zenith_strategy: str = "rw50+state t5+roll"   # v2's adopted configuration (reports/v2_plan.md section 14)


# --------------------------------------------------------------------------------------------- market and forecasts
@dataclass
class Market:
    """Actual data on whole trading days, flattened: entry i*48 + k is slot k of day i; 14 slots of padding at the end."""
    days: pd.DatetimeIndex
    rtp: np.ndarray                 # $/MWh
    pv_cf: np.ndarray               # rooftop PV output per kWp, AC, before the inverter limit
    load_factor: np.ndarray         # per slot: the day's load factor
    demand: pd.Series               # operational demand (MW), for the Peak SWIS intervals where not published
    slot_price: np.ndarray          # mean RTP by slot on days up to 2026-06-30 (values beyond the 09:00 forecast)

    @property
    def n_days(self) -> int:
        return len(self.days)


@lru_cache(maxsize=1)
def load_market(last_day: str = str(LAST_DAY.date())) -> Market:
    df = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["energy", "demand_mw", "dpv_mw"])
    days = pd.date_range(FIRST_DAY, last_day)
    ts = pd.date_range(days[0] + H, periods=len(days) * N, freq="30min")
    a = df.reindex(ts)
    if a["energy"].isna().any():
        raise ValueError(f"missing prices on {a.index[a['energy'].isna()][:3].tolist()}")
    pad = lambda x: np.r_[x, x[-N:-N + 14]]  # noqa: E731  the last day's horizon repeats its own morning
    # rooftop PV per kWp: AEMO's estimated DPV output over its centred one-year maximum (the fleet's capacity proxy)
    dpv = df["dpv_mw"]
    cap = dpv.rolling(365 * N, center=True, min_periods=N).max().reindex(ts)
    pv_cf = (a["dpv_mw"] / cap).clip(0, 1).fillna(0.0).to_numpy()
    # hot and cold days: the day's peak underlying demand (operational + rooftop PV) over its calendar month's median
    under = (df["demand_mw"] + df["dpv_mw"]).reindex(ts)
    dmax = under.groupby((ts - H).normalize()).max()
    month_med = dmax.groupby(dmax.index.month).transform("median")
    factor = (dmax / month_med).clip(0.7, 1.8).reindex(days).fillna(1.0).to_numpy()
    build = (days <= TRAIN_END).repeat(N)
    rtp = a["energy"].to_numpy()
    slot_price = rtp[build].reshape(-1, N).mean(axis=0)
    return Market(days=days, rtp=pad(rtp), pv_cf=pad(pv_cf), load_factor=pad(np.repeat(factor, N)),
                  demand=df["demand_mw"], slot_price=slot_price)


@dataclass
class Forecasts:
    """What is public at 09:00 on each trading day."""
    price: np.ndarray               # (days, 48) the 09:00 energy price forecast; NaN where none
    p_spike: np.ndarray             # (days, 48) probability that the interval's price reaches $200/MWh
    uplift: float                   # $/MWh: expected price = forecast + p_spike x uplift (fitted up to 2026-06-30)
    flags: dict                     # ircr_q -> boolean (days,) peak-day flags
    mean_flags: dict                # ircr_q -> flags per Hot Season, averaged over 2023-24 .. 2025-26
    has_fc: np.ndarray              # (days,) a complete forecast for slots 2..47


@lru_cache(maxsize=1)
def load_forecasts(last_day: str = str(LAST_DAY.date())) -> Forecasts:
    m = load_market(last_day)
    fc = pd.read_parquet(PROCESSED / "forecast_dispatch_0900.parquet")
    fc = fc[fc["days_ahead"] == 0]
    risk = pd.read_parquet(PROCESSED / "spike_risk_0900.parquet")
    risk = risk[risk["days_ahead"] == 0]

    def grid(frame: pd.DataFrame, col: str) -> np.ndarray:
        s = frame.set_index("ts")[col]
        s = s[~s.index.duplicated()]
        ts = pd.date_range(m.days[0] + H, periods=m.n_days * N, freq="30min")
        return s.reindex(ts).to_numpy().reshape(-1, N)

    price, p = grid(fc, "energy"), np.nan_to_num(grid(risk, "p_spike"))
    has_fc = ~np.isnan(price[:, DECISION_SLOT:]).any(axis=1) & (m.days >= FORECAST_FROM)
    # uplift: the forecast's mean shortfall in 15:00-21:00, spread over the spike probability (days up to 2026-06-30)
    build = has_fc & (m.days <= TRAIN_END)
    act = m.rtp[:m.n_days * N].reshape(-1, N)
    w = slice(14, 26)
    uplift = float((act[build, w].mean() - price[build, w].mean()) / max(p[build, w].mean(), 1e-6))
    flags, mean_flags = ircr_flags(m)
    return Forecasts(price=price, p_spike=p, uplift=uplift, flags=flags, mean_flags=mean_flags, has_fc=has_fc)


def ircr_flags(m: Market) -> tuple[dict, dict]:
    """The v2 peak-day flag rule (vpp.site_bill.dynamic_flags): a Hot Season day is flagged if AEMO's forecast of its
    highest energy requirement, public at 09:00 on the day or on the day before, reaches the season's 4th-highest
    actual daily maximum known by then less a tolerance (the q-quantile of the forecast's shortfall in the other two
    seasons; for 2025-26 these are earlier seasons, for 2023-24 and 2024-25 one is later)."""
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["req_energy_mw"])
    actual = sb.daily_max(df30["req_energy_mw"])
    p0, p1 = sb.peak_day_predictor(0), sb.peak_day_predictor(1)
    flags, mean_flags = {}, {}
    for q in IRCR_QUANTILES:
        days = set()
        for y in SEASONS:
            others = [x for x in SEASONS if x != y]
            f0 = sb.dynamic_flags(p0, actual, sb.forecast_tolerance(p0, actual, others, q), 1)
            f1 = sb.dynamic_flags(p1, actual, sb.forecast_tolerance(p1, actual, others, q), 2)
            days |= {d for d in f0.union(f1) if sb.hot_season(pd.DatetimeIndex([d]))[0] == y}
        flags[q] = m.days.isin(sorted(days))
        mean_flags[q] = float(flags[q].sum() / len(SEASONS))
    return flags, mean_flags


@lru_cache(maxsize=1)
def peak_intervals(last_day: str = str(LAST_DAY.date())) -> dict:
    """Season -> (day index, slot) of its 12 Peak SWIS Trading Intervals (AEMO's for 2025-26, else on demand)."""
    m = load_market(last_day)
    out = {}
    for y in SEASONS:
        ts = sb.ircr_intervals(m.demand, y)
        day = (ts - H).normalize()
        idx = m.days.get_indexer(day)
        slots = np.asarray((ts - day - H) / pd.Timedelta(minutes=30), int)
        if (idx < 0).any() or (slots < PEAK[0]).any() or (slots >= PEAK[1]).any():
            raise ValueError(f"peak intervals of {y} outside the kept slots")
        out[y] = (idx, slots)
    return out


# ------------------------------------------------------------------------------------------------- household physics
def household_series(m: Market, hh: Household) -> tuple[np.ndarray, np.ndarray]:
    """(PV, load) in kW per slot. Load: a typical Perth evening-peaking profile scaled to load_kwh_day, times the
    day's hot/cold factor ** hot_gamma [assumed]."""
    hourly = np.array([0.45] * 6 + [0.8] * 3 + [0.55] * 6 + [0.8] * 2 + [1.4] * 5 + [0.7] * 2)  # by clock hour 0..23
    shape = np.repeat(hourly, 2)  # 00:00 .. 23:30
    shape = np.roll(shape, -16)   # slot 0 = 08:00
    shape = shape / (shape.sum() * DT) * hh.load_kwh_day
    load = np.resize(shape, len(m.rtp)) * m.load_factor ** hh.hot_gamma
    pv = np.minimum(hh.pv_kw, hh.pv_kwp * 0.85 * m.pv_cf)
    return pv, load


def step(soc, pv, load, mode, rate, hh: Household, reserve_kwh: float):
    """One trading interval for arrays of batteries. Returns (soc after, import, export, discharge, charge, grid
    charge), power in kW (AC) and soc in kWh."""
    eta, p, cap = hh.eta, hh.power_kw, hh.usable_kwh
    room = np.maximum(cap - soc, 0.0) / (eta * DT)
    surplus = pv - load
    pv_ch = np.where(surplus > 0, np.minimum(np.minimum(surplus, p), room), 0.0)
    self_dis = np.where(surplus < 0, np.minimum(np.minimum(-surplus, p),
                                                 np.maximum(soc - hh.own_reserve * cap, 0.0) * eta / DT), 0.0)
    grid_ch = np.where(mode == STANDBY, np.maximum(np.minimum(p - pv_ch, room - pv_ch), 0.0), 0.0)
    ev_dis = np.minimum(np.minimum(p, rate), np.maximum(soc - reserve_kwh, 0.0) * eta / DT)
    ch = np.where(mode == EVENT, 0.0, pv_ch) + grid_ch
    dis = np.where(mode == SELF, self_dis, np.where(mode == EVENT, ev_dis, 0.0))
    net = pv + dis - load - ch
    soc = np.clip(soc + (ch * eta - dis / eta) * DT, 0.0, cap)
    return soc, np.maximum(-net, 0.0), np.maximum(net, 0.0), dis, ch, grid_ch


@dataclass
class Baseline:
    soc: np.ndarray                 # kWh at the start of each slot (one more entry than slots)
    imp: np.ndarray
    exp: np.ndarray
    dis: np.ndarray
    pv: np.ndarray
    load: np.ndarray


def simulate(pv: np.ndarray, load: np.ndarray, hh: Household, modes: np.ndarray | None = None,
             rates: dict | None = None, reserve_kwh: float = 0.0, soc0: float | None = None) -> Baseline:
    """Run one battery through every slot in order (the baseline when modes is None; the exact sequential run of a
    policy's calls otherwise: modes per slot, rates {first event slot: event length in slots}). Starts half full
    unless soc0 is given."""
    n = len(pv)
    soc = np.empty(n + 1)
    imp, exp, dis = np.empty(n), np.empty(n), np.empty(n)
    s = np.array([hh.usable_kwh * 0.5 if soc0 is None else soc0])
    soc[0] = s[0]
    rate = np.array([0.0])
    for i in range(n):
        mode = SELF if modes is None else modes[i]
        if mode == EVENT and rates is not None and i in rates:
            rate = np.array([min(hh.power_kw, max(s[0] - reserve_kwh, 0.0) * hh.eta / (rates[i] * DT))])
        s, im, ex, di, _, _ = step(s, pv[i:i + 1], load[i:i + 1], np.array([mode]), rate, hh, reserve_kwh)
        soc[i + 1], imp[i], exp[i], dis[i] = s[0], im[0], ex[0], di[0]
    return Baseline(soc=soc, imp=imp, exp=exp, dis=dis, pv=pv, load=load)


@lru_cache(maxsize=4)
def baseline(hh: Household = Household(), last_day: str = str(LAST_DAY.date())) -> Baseline:
    m = load_market(last_day)
    pv, load = household_series(m, hh)
    return simulate(pv, load, hh)


def feed_in(slots: np.ndarray) -> np.ndarray:
    hour = (8 + slots / 2) % 24
    return np.where((hour >= 15) & (hour < 21), DEBS_PEAK, DEBS_OFF)


@dataclass
class Family:
    """Every possible activation of one physical design (event length, standby, reserve) on every day, against the
    baseline. Arrays are (start, day); per-slot arrays add the slot of the day (0..61)."""
    household: Household
    event_h: float
    standby_h: float
    reserve_soc: float
    starts: np.ndarray              # first event slot of each candidate window
    w_actual: np.ndarray            # $ wholesale value at actual prices (incl. the gap left at 15:00 next day)
    d_net: np.ndarray               # (start, day, slot) change in net export, kWh
    paid_kwh: np.ndarray            # net export at the meter in the event, capped at the battery's kWh
    delivered_kwh: np.ndarray       # battery discharge in the event, kWh (AC)
    standby_kwh: np.ndarray         # extra import in standby, kWh, capped at the battery's kWh (offset credits)
    d_import_kwh: np.ndarray        # extra import over the whole horizon, kWh
    d_feedin_event: np.ndarray      # extra DEBS income on event exports, $ (paid only if DEBS applies to them)
    d_feedin_other: np.ndarray      # change in DEBS income outside the event, $
    d_through_kwh: np.ndarray       # extra battery discharge, kWh (AC)
    gap_kwh: np.ndarray             # state of charge below the baseline's at 15:00 next day (<= 0), kWh
    net_peak: np.ndarray            # (start, day, PEAK slots) net import, kW, on the activation day
    violations: np.ndarray          # count of physical or contract breaches in the run (must be 0)

    def index(self, start: int) -> int:
        return int(np.flatnonzero(self.starts == start)[0])


def window_starts(event_h: float, window=(15.0, 21.0)) -> np.ndarray:
    """Event start slots: every half hour that keeps the event in the window, plus the IRCR window (centred at 18:30)."""
    n = int(round(2 * event_h))
    first, last = int(2 * (window[0] - 8)), int(2 * (window[1] - 8)) - n
    starts = set(range(first, last + 1))
    if first <= ircr_start(event_h) <= last:
        starts.add(ircr_start(event_h))
    return np.array(sorted(starts), int)


def ircr_start(event_h: float) -> int:
    """IRCR calls run centred on 18:30, the middle of every Peak SWIS interval of 2023-24 .. 2025-26 (17:00-20:00)."""
    return int(round(2 * (18.5 - event_h / 2 - 8)))


@lru_cache(maxsize=64)
def family(event_h: float, standby_h: float, reserve_soc: float, hh: Household = Household(),
           window: tuple = (15.0, 21.0), last_day: str = str(LAST_DAY.date())) -> Family:
    m, b = load_market(last_day), baseline(hh, last_day)
    starts = window_starts(event_h, window)
    n_ev, n_sb = int(round(2 * event_h)), int(round(2 * standby_h))
    reserve = reserve_soc * hh.usable_kwh
    nd = m.n_days
    base_idx = np.arange(nd) * N
    shp = (len(starts), nd)
    out = {k: np.zeros(shp) for k in ("w_actual", "paid_kwh", "delivered_kwh", "standby_kwh", "d_import_kwh",
                                      "d_feedin_event", "d_feedin_other", "d_through_kwh", "gap_kwh")}
    d_net = np.zeros(shp + (HORIZON,))
    net_peak = np.zeros(shp + (PEAK[1] - PEAK[0],))
    viol = np.zeros(shp)
    for j, s in enumerate(starts):
        s0 = s - n_sb
        soc = b.soc[base_idx + s0].copy()
        rate = np.zeros(nd)
        for k in range(s0, HORIZON):
            i = base_idx + k
            mode = STANDBY if k < s else EVENT if k < s + n_ev else SELF
            if k == s:
                rate = np.minimum(hh.power_kw, np.maximum(soc - reserve, 0.0) * hh.eta / (n_ev * DT))
            soc, imp, exp, dis, ch, gch = step(soc, b.pv[i], b.load[i], np.full(nd, mode), rate, hh, reserve)
            dn = (exp - imp) - (b.exp[i] - b.imp[i])
            d_net[j, :, k] = dn * DT
            out["w_actual"][j] += dn * DT * m.rtp[i] / 1000
            out["d_import_kwh"][j] += (imp - b.imp[i]) * DT
            d_fi = (exp - b.exp[i]) * DT * feed_in(np.array(k))
            out["d_through_kwh"][j] += (dis - b.dis[i]) * DT
            if mode == STANDBY:
                out["standby_kwh"][j] += np.maximum(imp - b.imp[i], 0.0) * DT
                out["d_feedin_other"][j] += d_fi
            elif mode == EVENT:
                out["paid_kwh"][j] += exp * DT
                out["delivered_kwh"][j] += dis * DT
                out["d_feedin_event"][j] += d_fi
                viol[j] += (soc < reserve - 1e-6).astype(float)  # never below the customer's reserve
            else:
                out["d_feedin_other"][j] += d_fi
            viol[j] += ((soc < -1e-9) | (soc > hh.usable_kwh + 1e-9)).astype(float)
            viol[j] += (np.abs(dis) > hh.power_kw + 1e-9).astype(float) + (np.abs(ch) > hh.power_kw + 1e-9).astype(float)
            if PEAK[0] <= k < PEAK[1]:
                net_peak[j, :, k - PEAK[0]] = imp - exp
        out["paid_kwh"][j] = np.minimum(out["paid_kwh"][j], hh.usable_kwh)      # capped at the battery's kWh
        out["standby_kwh"][j] = np.minimum(out["standby_kwh"][j], hh.usable_kwh)
        gap = soc - b.soc[base_idx + HORIZON]
        out["gap_kwh"][j] = gap
        out["w_actual"][j] += gap * hh.eta * m.slot_price[14:26].mean() / 1000  # the gap is evening energy bought later
        # slots before standby are the baseline's
        for k in range(PEAK[0], min(s0, PEAK[1])):
            net_peak[j, :, k - PEAK[0]] = b.imp[base_idx + k] - b.exp[base_idx + k]
    return Family(household=hh, event_h=event_h, standby_h=standby_h, reserve_soc=reserve_soc, starts=starts, d_net=d_net,
                  net_peak=net_peak, violations=viol, **out)


# ------------------------------------------------------------------------------------------------------ the calls
@dataclass
class Calls:
    day: np.ndarray                 # day index
    start: np.ndarray               # index into Family.starts
    kind: np.ndarray                # "ircr" | "energy"


def reprice(fam: Family, rtp: np.ndarray, m: Market) -> Family:
    """The same activations settled at other prices (stress tests): W is linear in the price of each slot."""
    idx = np.arange(m.n_days)[:, None] * N + np.arange(HORIZON)[None, :]
    w = (fam.d_net * rtp[idx][None]).sum(axis=2) / 1000 + fam.gap_kwh * fam.household.eta * m.slot_price[14:26].mean() / 1000
    return replace(fam, w_actual=w)


def contract_years(m: Market) -> dict:
    """Contract year (by the year it starts, 1 July) -> day indices, up to the last day."""
    fy = np.where(m.days.month >= 7, m.days.year, m.days.year - 1)
    return {int(y): np.flatnonzero(fy == y) for y in np.unique(fy)}


def season_of(m: Market) -> np.ndarray:
    return sb.hot_season(m.days)


def call_cost(fam: Family, policy: Policy, obl: Obligations, j, d) -> np.ndarray:
    """Zenith's cost of one call per responding customer, at actual delivery: payments, offset credits, operations."""
    return (policy.pay_per_kwh * fam.paid_kwh[j, d] + policy.pay_per_event
            + policy.offset_rate * fam.standby_kwh[j, d] + obl.ops_per_event)


@dataclass
class Scores:
    """09:00 expected margin of an energy call on each day, its best window, and the simple rule's test."""
    best: np.ndarray                # (days,) $ per customer; -inf where no forecast
    start: np.ndarray               # (days,) index into Family.starts
    naive_ok: np.ndarray            # (days,) the simple rule: forecast window price at least the payment


def expected_profiles(fam: Family, m: Market) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per start and calendar month, on days up to 2026-06-30: mean change in net export by slot, mean paid kWh and
    mean standby kWh. What the dispatcher expects one call to deliver, without the day's own weather."""
    build = m.days <= TRAIN_END
    month = m.days.month.to_numpy()
    prof = np.zeros((len(fam.starts), 13, HORIZON))
    paid, stand = np.zeros((len(fam.starts), 13)), np.zeros((len(fam.starts), 13))
    for mo in range(1, 13):
        sel = build & (month == mo)
        prof[:, mo] = fam.d_net[:, sel].mean(axis=1)
        paid[:, mo] = fam.paid_kwh[:, sel].mean(axis=1)
        stand[:, mo] = fam.standby_kwh[:, sel].mean(axis=1)
    return prof, paid, stand


@lru_cache(maxsize=256)
def _profiles(fam_key: tuple):
    fam = family(*fam_key)
    return expected_profiles(fam, load_market(fam_key[-1]))


def energy_scores(fam_key: tuple, policy: Policy, obl: Obligations, fc: Forecasts, m: Market,
                  energy_starts: np.ndarray) -> Scores:
    fam = family(*fam_key)
    prof, paid, stand = _profiles(fam_key)
    month = m.days.month.to_numpy()
    exp_price = np.where(np.isnan(fc.price), 0.0, fc.price) + fc.p_spike * fc.uplift      # (days, 48)
    price = np.concatenate([exp_price, np.broadcast_to(m.slot_price[:HORIZON - N], (m.n_days, HORIZON - N))], axis=1)
    sc = np.full((len(fam.starts), m.n_days), -np.inf)
    naive = np.zeros((len(fam.starts), m.n_days), bool)
    n_ev = int(round(2 * fam.event_h))
    for j in energy_starts:
        p = prof[j, month]                                                                    # (days, 62)
        value = (p * price).sum(axis=1) / 1000
        cost = policy.pay_per_kwh * paid[j, month] + policy.pay_per_event + policy.offset_rate * stand[j, month] \
            + obl.ops_per_event
        sc[j] = np.where(fc.has_fc, value - cost, -np.inf)
        s = fam.starts[j]
        win = np.nan_to_num(fc.price[:, s:s + n_ev]).mean(axis=1)
        naive[j] = fc.has_fc & (paid[j, month] * win / 1000 >= policy.pay_per_kwh * paid[j, month] + policy.pay_per_event)
    best = sc.argmax(axis=0)
    cols = np.arange(m.n_days)
    return Scores(best=sc[best, cols], start=best, naive_ok=naive.any(axis=0) & fc.has_fc)


def select_calls(days: np.ndarray, fam: Family, policy: Policy, rule: CallRule, fc: Forecasts, scores: Scores,
                 capacity_on: bool, energy_on: bool, m: Market, cap: int | None = None, w_margin=None) -> Calls:
    """The day's call decision at 09:00, in date order, within the contract year's cap.
    optimised: IRCR call on a flagged Hot Season day; else an energy call when its expected margin >= theta and the
               calls left exceed what the rest of the Hot Season is expected to need.
    simple:    IRCR call on every day the v2 rule (q = 0.9) flags; energy call whenever the forecast window price is at
               least the payment; first come, first served.
    perfect:   hindsight: the season's 4 peak days, then the days of highest realised margin (w_margin) above 0."""
    cap = policy.max_events if cap is None else cap
    ircr_j = fam.index(ircr_start(fam.event_h))
    season = season_of(m)
    out_d, out_j, out_k = [], [], []
    if rule.kind == "perfect":
        chosen = set()
        if capacity_on:
            for y in SEASONS:
                for d in sorted(set(peak_intervals()[y][0])):
                    if d in days and len(chosen) < cap:
                        chosen.add(d)
                        out_d.append(d), out_j.append(ircr_j), out_k.append("ircr")
        if energy_on and w_margin is not None:
            margin, start = w_margin
            order = [d for d in days[np.argsort(-margin[days])] if d not in chosen and margin[d] > 0]
            for d in order[:max(0, cap - len(chosen))]:
                out_d.append(d), out_j.append(start[d]), out_k.append("energy")
        o = np.argsort(out_d)
        return Calls(np.array(out_d, int)[o], np.array(out_j, int)[o], np.array(out_k, object)[o])
    q = 0.9 if rule.kind == "simple" else rule.ircr_q
    flags = fc.flags[q]
    left = cap
    hs_left = None
    if capacity_on and rule.kind == "optimised" and rule.reserve_for_ircr:
        # expected IRCR calls still to come in the contract year: per-season mean x share of the season ahead
        hs_days = np.array([season[d] >= 0 for d in days])
        ahead = hs_days[::-1].cumsum()[::-1] - hs_days  # Hot Season days after each day
        hs_left = fc.mean_flags[q] * ahead / max(hs_days.sum(), 1)
    for n, d in enumerate(days):
        if left <= 0:
            break
        if capacity_on and flags[d]:
            out_d.append(d), out_j.append(ircr_j), out_k.append("ircr")
            left -= 1
            continue
        if not energy_on:
            continue
        ok = scores.naive_ok[d] if rule.kind == "simple" else scores.best[d] >= rule.theta
        if ok:
            need = 0.0 if hs_left is None else hs_left[n]
            if left - 1 >= need:
                out_d.append(d), out_j.append(scores.start[d]), out_k.append("energy")
                left -= 1
    return Calls(np.array(out_d, int), np.array(out_j, int), np.array(out_k, object))


# --------------------------------------------------------------------------------------------------- the accounts
def account(calls: Calls, fam: Family, policy: Policy, obl: Obligations, part: Participation, m: Market,
            season: int | None, peaks: dict | None) -> dict:
    """Per enrolled customer, over the calls given (no annual items): money by item and party, energy, counts."""
    a = part.availability
    j, d = calls.start, calls.day
    pay_e = policy.pay_per_kwh * fam.paid_kwh[j, d]
    pay_v = np.full(len(d), policy.pay_per_event)
    offset = policy.offset_rate * fam.standby_kwh[j, d]
    ops = np.full(len(d), obl.ops_per_event)
    w_all = fam.w_actual[j, d]
    w = w_all if obl.energy_to_zenith else np.zeros(len(d))
    bill = RETAIL * fam.d_import_kwh[j, d] - fam.d_feedin_other[j, d] \
        - (fam.d_feedin_event[j, d] if part.debs_on_event_exports else 0.0) - fam.gap_kwh[j, d] * fam.household.eta * RETAIL
    wear = part.deg_per_kwh * np.maximum(fam.d_through_kwh[j, d], 0.0)
    hassle = np.full(len(d), part.hassle_per_event)
    per_call_zen = w - pay_e - pay_v - offset - ops
    per_call_cust = pay_e + pay_v + offset - bill - wear - hassle
    cap_full = 0.0
    if season is not None and obl.capacity != "none" and peaks is not None and season in peaks:
        cap_full = ircr_value(calls, fam, obl, m, season, peaks) * a
    cap_value = cap_full * obl.capacity_share
    # the customer's retailer (or whoever holds the meters' market position and capacity cost) gets the rest
    retailer = a * bill.sum() + (0.0 if obl.energy_to_zenith else a * w_all.sum()) + cap_full - cap_value
    return {
        "calls": len(d), "ircr_calls": int((calls.kind == "ircr").sum()), "energy_calls": int((calls.kind == "energy").sum()),
        "wholesale": a * w.sum(), "capacity": cap_value, "fcess": 0.0, "retailer": retailer,
        "system": a * w_all.sum() + cap_full - a * (wear.sum() + hassle.sum() + ops.sum()),
        "pay_energy": a * pay_e.sum(), "pay_event": a * pay_v.sum(), "offset": a * offset.sum(), "ops_event": a * ops.sum(),
        "bill": a * bill.sum(), "wear": a * wear.sum(), "hassle": a * hassle.sum(),
        "paid_kwh": a * fam.paid_kwh[j, d].sum(), "delivered_kwh": a * fam.delivered_kwh[j, d].sum(),
        "net_export_kwh": a * fam.d_net[j, d].sum(), "standby_kwh": a * fam.standby_kwh[j, d].sum(),
        "zenith_calls": a * per_call_zen.sum() + cap_value, "customer_calls": a * per_call_cust.sum(),
        "per_call_zenith": per_call_zen * a, "per_call_customer": per_call_cust * a,
        "violations": float(fam.violations[j, d].sum()),
    }


def ircr_value(calls: Calls, fam: Family, obl: Obligations, m: Market, season: int, peaks: dict) -> float:
    """$ per responding customer: the meter's IRCR (median import in the season's 12 Peak SWIS intervals; net export
    as 0 under 'floor', negative under 'net') without the calls less with them, x the next capacity year's RCP."""
    b = baseline(fam.household)
    idx, slots = peaks[season]
    base = b.imp[idx * N + slots] - b.exp[idx * N + slots]
    with_calls = base.copy()
    called = dict(zip(calls.day.tolist(), calls.start.tolist()))
    for n, (d, s) in enumerate(zip(idx, slots)):
        if d in called:
            with_calls[n] = fam.net_peak[called[d], d, s - PEAK[0]]
    if obl.capacity == "floor":
        base, with_calls = np.maximum(base, 0.0), np.maximum(with_calls, 0.0)
    kw = float(np.median(base) - np.median(with_calls))
    return kw / 1000 * (ct.RCP[season + 1] if obl.rcp is None else obl.rcp) * obl.rcp_ratio


def peak_days_covered(calls: Calls, m: Market, season: int, peaks: dict) -> int:
    days = set(peaks[season][0].tolist())
    return len(days & set(calls.day.tolist()))


# ------------------------------------------------------------------------------------------------ the evaluation API
@dataclass
class PolicyEvaluation:
    policy: dict
    rule: dict
    obligations: str
    participation: str
    period: str
    days: int
    enrolled: float                 # customers
    participation_share: float
    zenith_profit: float            # fleet, $ over the period (annual items pro rata)
    zenith_per_customer: float
    customer_benefit: float         # $ per enrolled customer over the period
    risk_adjusted_profit: float     # fleet, $ per year (building data only; nan for the test)
    activations: int
    ircr_calls: int
    energy_calls: int
    remaining_allowance: int        # left in the contract year at the period's end
    energy_kwh: float               # fleet battery-sourced export paid for, kWh
    net_export_kwh: float           # fleet change in net export over the horizons, kWh
    revenue: dict                   # fleet $ by source: wholesale, capacity, fcess
    compensation: dict              # fleet $: pay_energy, pay_event, annual_fee, offset
    costs: dict                     # fleet $: ops_event, ops_customer, fixed
    customer_items: dict            # per customer $: payments, bill, wear, hassle
    parties: dict                   # fleet $: zenith, customers, retailer (and other holders), system (their sum)
    violations: dict
    peak_days_covered: dict         # season -> peak days with a call
    feasible: bool

    def row(self) -> dict:
        r = {k: v for k, v in asdict(self).items() if not isinstance(v, dict)}
        r.update({f"policy_{k}": v for k, v in self.policy.items()})
        r.update({f"rule_{k}": v for k, v in self.rule.items()})
        for g in ("revenue", "compensation", "costs"):
            r.update({f"{g}_{k}": v for k, v in getattr(self, g).items()})
        r.update({f"customer_{k}": v for k, v in self.customer_items.items()})
        r["violations_total"] = sum(self.violations.values())
        r.update({f"covered_{k}": v for k, v in self.peak_days_covered.items()})
        return r


@dataclass
class Fit:
    """A policy's call rule chosen on data up to 2026-06-30, and its expected annual figures per customer."""
    rule: CallRule
    zen: float                      # expected Zenith margin per enrolled customer per year
    zen_risk: float                 # risk-adjusted (10th percentile of the energy part + the worst Hot Season)
    cust: float                     # expected customer benefit per year
    cust_worst: float
    parts: dict                     # FY2025 energy part and per-season IRCR parts (zen, cust)
    violations: float
    covered: dict


BOOT = np.random.default_rng(0).integers(0, 12, size=(2000, 12))  # month-block resamples of one contract year


def _energy_part(acc: dict, calls: Calls, m: Market) -> tuple[float, float, float]:
    sel = calls.kind == "energy"
    zen, cust = acc["per_call_zenith"][sel], acc["per_call_customer"][sel]
    months = np.zeros(12)
    np.add.at(months, (m.days.month.to_numpy()[calls.day[sel]] - 1), zen)
    return float(zen.sum()), float(cust.sum()), float(np.quantile(months[BOOT].sum(axis=1), 0.10))


def _ircr_part(acc: dict, calls: Calls) -> tuple[float, float]:
    sel = calls.kind == "ircr"
    return float(acc["per_call_zenith"][sel].sum() + acc["capacity"]), float(acc["per_call_customer"][sel].sum())


def fit_rule(policy: Policy, obl: Obligations, part: Participation, port: Portfolio = Portfolio(),
             rules: list[CallRule] | None = None) -> Fit:
    """Choose the call rule for a policy on data up to 2026-06-30 (POLICY_MODEL.md section 4.3): the one with the
    highest expected fleet profit; ties (nobody enrols) by the margin per customer."""
    return choose_fit(rule_fits(policy, obl, part, port, rules), part, obl)


def choose_fit(fits: list[Fit], part: Participation, obl: Obligations) -> Fit:
    key = lambda x: (round(fleet(x, part, obl)[0], 2), x.zen)  # noqa: E731
    return max(fits, key=key)


def rule_fits(policy: Policy, obl: Obligations, part: Participation, port: Portfolio = Portfolio(),
              rules: list[CallRule] | None = None) -> list[Fit]:
    """Every candidate call rule of a policy on data up to 2026-06-30 (POLICY_MODEL.md section 4.3): the energy and
    Hot Season parts of contract year 2025-26 (the one year with both the 09:00 forecast and a Hot Season), and the
    Hot Season parts of 2023-24 and 2024-25 (IRCR calls only: no 09:00 forecast then), each season's calls limited to
    what 2025-26's energy calls before December left."""
    m, fc = load_market(), load_forecasts()
    key = (policy.event_h, policy.standby_h, policy.reserve_soc, port.household, tuple(policy.window), str(LAST_DAY.date()))
    fam = family(*key)
    years = contract_years(m)
    peaks = peak_intervals() if obl.capacity != "none" else None
    cap_on = obl.capacity != "none"
    energy_starts = np.flatnonzero(fam.starts + 2 * policy.event_h <= 2 * (policy.window[1] - 8))
    scores = energy_scores(key, policy, obl, fc, m, energy_starts)
    if rules is None:
        thetas = (0.0, 0.5, 1.0, 2.0, 4.0, np.inf) if obl.energy_to_zenith else (np.inf,)
        qs = IRCR_QUANTILES if cap_on else (0.9,)
        rules = [CallRule("optimised", t, q) for t in thetas for q in qs]
    fits = []
    for rule in rules:
        calls = select_calls(years[2025], fam, policy, rule, fc, scores, cap_on, obl.energy_to_zenith, m)
        acc = account(calls, fam, policy, obl, part, m, 2025, peaks)
        ez, ec, ez10 = _energy_part(acc, calls, m)
        parts = {"energy": (ez, ec)}
        viol, covered = acc["violations"], {}
        if cap_on:
            pre_dec = int(((calls.kind == "energy") & (m.days[calls.day] < pd.Timestamp("2025-12-01"))).sum())
            parts[2025] = _ircr_part(acc, calls)
            covered[2025] = peak_days_covered(calls, m, 2025, peaks)
            for y in (2023, 2024):
                c = select_calls(years[y], fam, policy, rule, fc, scores, True, False, m, cap=policy.max_events - pre_dec)
                a = account(c, fam, policy, obl, part, m, y, peaks)
                parts[y] = _ircr_part(a, c)
                viol += a["violations"]
                covered[y] = peak_days_covered(c, m, y, peaks)
        seasons = [parts[y] for y in SEASONS if y in parts] or [(0.0, 0.0)]
        annual_z = policy.annual_fee + obl.ops_per_customer
        zen = ez + np.mean([s[0] for s in seasons]) - annual_z
        zen_risk = ez10 + min(s[0] for s in seasons) - annual_z
        cust = ec + np.mean([s[1] for s in seasons]) + policy.annual_fee
        cust_worst = ec + min(s[1] for s in seasons) + policy.annual_fee
        fits.append(Fit(rule, zen, zen_risk, cust, cust_worst, parts, viol, covered))
    return fits


def fleet(fit: Fit, part: Participation, obl: Obligations) -> tuple[float, float, float]:
    """(expected fleet profit $/yr, risk-adjusted fleet profit $/yr, enrolled customers)."""
    enrolled = part.eligible * part.share(fit.cust)
    if enrolled <= 0:
        return 0.0, 0.0, 0.0
    return enrolled * fit.zen - obl.fixed_cost, enrolled * fit.zen_risk - obl.fixed_cost, enrolled


def evaluate_policy(policy: Policy, battery_portfolio: Portfolio | None = None, historical_market_data: Market | None = None,
                    forecast_data: Forecasts | None = None, market_obligations: Obligations | None = None,
                    participation_scenario: Participation | None = None, rule: CallRule | None = None,
                    period: str = "in-sample", fit: Fit | None = None, prices: np.ndarray | None = None) -> PolicyEvaluation:
    """Evaluate one policy (POLICY_MODEL.md section 5). The call rule is fitted on data up to 2026-06-30 unless given;
    enrolment follows the expected customer benefit there. period: "in-sample" (contract year 2025-26, settled as it
    happened), "test" (2026-07-01 to the last day; annual items pro rata), or "FY2023" / "FY2024" (Hot Season calls
    only, no forecast-based calls). With fit given, enrolment and the fitted rule come from it and rule (if given)
    overrides only the dispatch (the simple and perfect-foresight comparisons). prices: settle at these instead of
    the actual prices (stress tests; decisions unchanged)."""
    port = battery_portfolio or Portfolio()
    m = historical_market_data or load_market()
    fc = forecast_data or load_forecasts()
    obl = market_obligations or Obligations()
    part = participation_scenario or Participation()
    if fit is None:
        fit = fit_rule(policy, obl, part, port, None if rule is None else [rule])
    rule = rule or fit.rule
    key = (policy.event_h, policy.standby_h, policy.reserve_soc, port.household, tuple(policy.window), str(LAST_DAY.date()))
    fam = family(*key)
    if prices is not None:
        fam = reprice(fam, prices, m)
    years = contract_years(m)
    cap_on = obl.capacity != "none"
    peaks = peak_intervals() if cap_on else None
    energy_starts = np.flatnonzero(fam.starts + 2 * policy.event_h <= 2 * (policy.window[1] - 8))
    scores = energy_scores(key, policy, obl, fc, m, energy_starts)
    if period == "in-sample":
        y, days, energy_on = 2025, years[2025], obl.energy_to_zenith
    elif period == "test":
        y = 2026
        days = years[2026][m.days[years[2026]] >= CUTOFF]
        energy_on = obl.energy_to_zenith
    elif period in ("FY2023", "FY2024"):
        y, days, energy_on = int(period[2:]), years[int(period[2:])], False
    else:
        raise ValueError(period)
    w_margin = None
    if rule.kind == "perfect":
        cost = (policy.pay_per_kwh * fam.paid_kwh + policy.pay_per_event + policy.offset_rate * fam.standby_kwh
                + obl.ops_per_event)
        margin = fam.w_actual[energy_starts] - cost[energy_starts]
        best = margin.argmax(axis=0)
        w_margin = (margin[best, np.arange(m.n_days)], energy_starts[best])
        energy_on = obl.energy_to_zenith
    calls = select_calls(days, fam, policy, rule, fc, scores, cap_on, energy_on, m, w_margin=w_margin)
    season = y if y in SEASONS else None
    acc = account(calls, fam, policy, obl, part, m, season, peaks)
    enrolled = part.eligible * part.share(fit.cust)
    share = len(days) / 365.0
    annual = {"annual_fee": policy.annual_fee * share, "ops_customer": obl.ops_per_customer * share}
    zen_pc = acc["zenith_calls"] - annual["annual_fee"] - annual["ops_customer"]
    cust_pc = acc["customer_calls"] + annual["annual_fee"]
    _, risk, _ = fleet(fit, part, obl)
    fixed = obl.fixed_cost * share if enrolled > 0 else 0.0
    covered = {s: peak_days_covered(calls, m, s, peaks) for s in SEASONS if cap_on and s == season}
    viol = {"physical": acc["violations"],
            "cap": float(max(0, acc["calls"] - policy.max_events)),
            "per_day": float(len(calls.day) - len(np.unique(calls.day))),
            "window": float(((fam.starts[calls.start] < 2 * (policy.window[0] - 8))
                             | (fam.starts[calls.start] + 2 * policy.event_h > 2 * (policy.window[1] - 8))).sum()),
            "notice": float((fam.starts[calls.start] - 2 * policy.standby_h < DECISION_SLOT + 2 * policy.notice_h).sum()),
            "control": float(len(calls.day)) if policy.standby_h + policy.event_h > policy.max_control_h + 1e-9 else 0.0}
    return PolicyEvaluation(
        policy=asdict(policy), rule=asdict(rule), obligations=obl.name, participation=part.name, period=period,
        days=len(days), enrolled=enrolled, participation_share=part.share(fit.cust),
        zenith_profit=enrolled * zen_pc - fixed, zenith_per_customer=zen_pc, customer_benefit=cust_pc,
        risk_adjusted_profit=risk if period == "in-sample" else np.nan,
        activations=acc["calls"], ircr_calls=acc["ircr_calls"], energy_calls=acc["energy_calls"],
        remaining_allowance=policy.max_events - acc["calls"],
        energy_kwh=enrolled * acc["paid_kwh"], net_export_kwh=enrolled * acc["net_export_kwh"],
        revenue={"wholesale": enrolled * acc["wholesale"], "capacity": enrolled * acc["capacity"], "fcess": 0.0},
        compensation={"pay_energy": enrolled * acc["pay_energy"], "pay_event": enrolled * acc["pay_event"],
                      "annual_fee": enrolled * annual["annual_fee"], "offset": enrolled * acc["offset"]},
        costs={"ops_event": enrolled * acc["ops_event"], "ops_customer": enrolled * annual["ops_customer"], "fixed": fixed},
        customer_items={"payments": acc["pay_energy"] + acc["pay_event"] + annual["annual_fee"] + acc["offset"],
                        "bill": -acc["bill"], "wear": -acc["wear"], "hassle": -acc["hassle"]},
        parties={"zenith": enrolled * zen_pc - fixed, "customers": enrolled * cust_pc, "retailer": enrolled * acc["retailer"],
                 "system": enrolled * (acc["system"] - annual["ops_customer"]) - fixed},
        violations=viol, peak_days_covered=covered,
        feasible=bool(fit.cust >= part.min_benefit and sum(viol.values()) == 0 and fit.violations == 0),
    )


# --------------------------------------------------------------------------------------------- Zenith's own battery
def zenith_only(port: Portfolio = Portfolio()) -> pd.DataFrame:
    """Scenario A: Zenith's own battery as the Q2B engine (vpp.v2, adopted configuration) ran it, from its saved
    ledger. $ by source for contract year 2025-26 and for the test (2026-07-01 to the last day)."""
    from vpp.backtest import REPORTS
    daily = pd.read_csv(REPORTS / "v2_test_daily.csv", parse_dates=["day"])
    bills = pd.read_csv(REPORTS / "v2_test_bills.csv")
    ircr = pd.read_csv(REPORTS / "v2_test_ircr.csv")
    d = daily[(daily["scenario"] == "m1") & (daily["strategy"] == port.zenith_strategy)]
    b = bills[(bills["scenario"] == "m1") & (bills["strategy"] == port.zenith_strategy)].copy()
    b["month"] = pd.PeriodIndex(b["month"], freq="M")
    rows = {}
    for name, lo, hi in (("FY2025-26", "2025-07-01", "2026-06-30"), ("test", str(CUTOFF.date()), str(LAST_DAY.date()))):
        x = d[(d["day"] >= lo) & (d["day"] <= hi)]
        months = x["day"].dt.to_period("M")
        per_day = b.set_index("month")["saving_per_day"]
        bill = float(per_day.reindex(months).fillna(0.0).sum())
        cap = float(ircr.loc[ircr["strategy"] == port.zenith_strategy, "saving_per_year"].iloc[0]) if name == "FY2025-26" else 0.0
        rows[name] = {"days": len(x), "energy": x["energy"].sum(), "fcess": x["fcess"].sum(), "wear": -x["wear"].sum(),
                      "network_bill_saving": bill, "factory_ircr_saving": cap}
    out = pd.DataFrame(rows).T
    out["total"] = out[["energy", "fcess", "wear", "network_bill_saving", "factory_ircr_saving"]].sum(axis=1)
    return out


# ------------------------------------------------------------------------------------------------ reference policy
SYNERGY_LIKE = Policy(name="Synergy-like reference", comp_type="energy", pay_per_kwh=0.70, max_events=30, event_h=2.0,
                      standby_h=2.0, reserve_soc=0.20, notice_h=0.0)


def main() -> None:
    pd.set_option("display.width", 200)
    for obl in (Obligations(), Obligations("S2b capacity (net)", capacity="net")):
        for period in ("in-sample", "test"):
            e = evaluate_policy(SYNERGY_LIKE, market_obligations=obl, period=period)
            print(f"{obl.name:22s} {period:9s} rule={e.rule['theta']},{e.rule['ircr_q']} enrolled={e.enrolled:7.0f} "
                  f"calls={e.activations:2d} (ircr {e.ircr_calls}, energy {e.energy_calls}) zenith=${e.zenith_profit:12,.0f} "
                  f"per cust ${e.zenith_per_customer:8.2f} customer ${e.customer_benefit:8.2f} viol={sum(e.violations.values())}")
    print(zenith_only().round(0))


if __name__ == "__main__":
    main()
