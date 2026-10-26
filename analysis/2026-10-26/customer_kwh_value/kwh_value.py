"""客户电池每 1 kWh 的机会价值：决策时刻 09:00，对客户和对 Zenith 各值多少（本目录 PLAN.md）。

只读调用 vpp.policy：policy_simulator 的家庭物理（step、baseline）、09:00 输入（load_forecasts、expected_profiles、
energy_scores）、调用规则（fit_rule、select_calls）和账本（account、evaluate_policy）；policy_optimizer 只取 SCENARIOS
和 make_policy，不运行它的 main。不写 vpp/、reports/。

分母（PLAN.md 第 3 节）：
  - 所有"每次"的量：每响应户、每次调用。
  - "每 kWh" = 每 kWh 事件期间计费净出口 Q（family().paid_kwh，封顶电池 kWh）：补偿的计费电量。
  - 市场价值的金额按相对"不调用"的逐时段净出口变化 ΔN 乘价格算，再除以 Q，只为和补偿单价同一把尺子。ΔN 单独给出。
内部金额一律 A$，电量 kWh；输出时 A$/kWh × 100 = c/kWh。

用法（从仓库根目录）：.venv/bin/python analysis/2026-10-26/customer_kwh_value/run.py
单日查询：from kwh_value import kwh_value; kwh_value("2026-01-21", "S2b", "17:30-19:30")
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # never write .pyc into vpp/ (outside this task folder)

from dataclasses import dataclass, replace  # noqa: E402
from functools import lru_cache  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import contracts as ct  # noqa: E402
from vpp.evaluate import CUTOFF, TRAIN_END  # noqa: E402
from vpp.policy import policy_optimizer as po  # noqa: E402  (SCENARIOS, make_policy only)
from vpp.policy import policy_simulator as ps  # noqa: E402

HH = ps.Household()
EVENT_H, STANDBY_H, RESERVE = 2.0, 2.0, 0.20          # the physical design B and C share
WINDOW_RANGE = (15.0, 21.0)
LAST = str(ps.LAST_DAY.date())
FAM_KEY = (EVENT_H, STANDBY_H, RESERVE, HH, WINDOW_RANGE, LAST)  # the key evaluate_policy uses for these policies
PART = ps.Participation()                              # central curve; availability 0.9
AVAIL = PART.availability
SCENARIOS = po.SCENARIOS
REF_70C = 0.70     # Synergy Battery Rewards V3 PD 4: reference term, not a Zenith tariff
MODEL_85C = 0.85   # reports/policy/RECOMMENDATION.md: model recommendation, not a contract
POLICIES = {
    "B": ps.SYNERGY_LIKE,                                                                 # 70c/kWh, 30 calls
    "C-S2b": po.make_policy(0.85, "event", 45, EVENT_H, STANDBY_H, RESERVE, "optimised S2b"),  # $8.7089 per event
    "C-S3": po.make_policy(0.85, "energy", 45, EVENT_H, STANDBY_H, RESERVE, "optimised S3"),   # 85c/kWh
}
# whose call days carry the per-kWh IRCR allocation in each scenario (S2a has no C policy: C-S2b's days are used)
C_OF = {"S1": None, "S2a": "C-S2b", "S2b": "C-S2b", "S3": "C-S3"}
PHASES = ("standby", "event", "after")
PEAK0 = ps.PEAK[0]
DEFAULT_START = ps.ircr_start(EVENT_H)                 # 17:30 for a 2-h event


def clock(slot: int) -> str:
    t = 8 * 60 + int(slot) * 30
    return f"{(t // 60) % 24:02d}:{t % 60:02d}"


def window_label(start_slot: int) -> str:
    return f"{clock(start_slot)}-{clock(start_slot + int(round(2 * EVENT_H)))}"


def period_label(days: pd.DatetimeIndex) -> np.ndarray:
    """样本内 = 合同年 2025-26；测试期分已看过和未看过两段（AGENTS.md）；其余空。"""
    d = pd.DatetimeIndex(days)
    out = np.full(len(d), "", object)
    out[(d >= "2025-07-01") & (d <= "2026-06-30")] = "样本内 合同年2025-26"
    out[(d >= "2026-07-01") & (d <= "2026-10-15")] = "测试期 已看过 07-01至10-15"
    out[(d >= "2026-10-16") & (d <= "2026-10-22")] = "测试期 未看过 10-16至10-22"
    return out


# ------------------------------------------------------------------------------------- physics, split by phase
@dataclass
class Parts:
    """Every candidate call of the design on every day, against the baseline, split by phase. Arrays are (start, day)
    unless noted; per responding customer per call; kWh and A$."""
    starts: np.ndarray
    q: np.ndarray                 # billed event net export Q (capped at the battery's kWh)
    q_raw: np.ndarray             # event net export before the cap
    d_net: np.ndarray             # (start, day, 62) change in net export per slot, kWh
    dn: np.ndarray                # ΔN: the change in net export over the horizon
    w_phase: dict                 # standby / event / after / gap -> A$ at actual prices
    d_imp: dict                   # phase -> extra import, kWh
    d_exp: dict                   # phase -> extra export, kWh
    debs: dict                    # phase -> change in DEBS income, A$
    standby_kwh: np.ndarray       # extra standby import (positive part), capped: the offset-credit base
    gap: np.ndarray               # state of charge less the baseline's at 15:00 next day, kWh
    d_dis: np.ndarray             # extra battery discharge over the horizon, kWh AC
    d_loss: np.ndarray            # extra charge + discharge losses over the horizon, kWh
    delivered: np.ndarray         # battery discharge in the event, kWh AC
    ev: dict                      # event balance terms (gross, not incremental), kWh
    net_peak: np.ndarray          # (start, day, 10) net import kW in slots 16..25 on the call day
    base_err: float               # max |re-stepped baseline - saved baseline|

    @property
    def w(self) -> np.ndarray:
        return self.w_phase["standby"] + self.w_phase["event"] + self.w_phase["after"] + self.w_phase["gap"]

    @property
    def d_imp_total(self) -> np.ndarray:
        return self.d_imp["standby"] + self.d_imp["event"] + self.d_imp["after"]

    @property
    def d_exp_total(self) -> np.ndarray:
        return self.d_exp["standby"] + self.d_exp["event"] + self.d_exp["after"]

    def index(self, start: int) -> int:
        return int(np.flatnonzero(self.starts == start)[0])


def _loss(ch, dis, eta):
    return (ch * (1 - eta) + dis * (1 / eta - 1)) * ps.DT


@lru_cache(maxsize=1)
def decompose(last_day: str = LAST) -> Parts:
    """The same calls as ps.family (same step, same baseline, same horizon), re-run here with each item kept by phase,
    the baseline re-stepped alongside (for its charge and losses), and the event's energy balance terms."""
    m, b, hh = ps.load_market(last_day), ps.baseline(HH, last_day), HH
    eta = hh.eta
    starts = ps.window_starts(EVENT_H, WINDOW_RANGE)
    n_ev, n_sb = int(round(2 * EVENT_H)), int(round(2 * STANDBY_H))
    reserve = RESERVE * hh.usable_kwh
    nd = m.n_days
    base_idx = np.arange(nd) * ps.N
    shp = (len(starts), nd)

    def z():
        return np.zeros(shp)

    d_imp, d_exp, debs = ({p: z() for p in PHASES} for _ in range(3))
    w_phase = {p: z() for p in PHASES + ("gap",)}
    ev = {k: z() for k in ("avail", "pv", "load", "import", "export", "loss", "retained")}
    q_raw, sb_pos, gap, d_dis, d_loss, delivered = z(), z(), z(), z(), z(), z()
    d_net = np.zeros(shp + (ps.HORIZON,))
    net_peak = np.zeros(shp + (ps.PEAK[1] - ps.PEAK[0],))
    base_err = 0.0
    p_eve = m.slot_price[14:26].mean()
    for j, s in enumerate(starts):
        s0 = s - n_sb
        soc = b.soc[base_idx + s0].copy()
        rate = np.zeros(nd)
        for k in range(s0, ps.HORIZON):
            i = base_idx + k
            phase = "standby" if k < s else "event" if k < s + n_ev else "after"
            mode = {"standby": ps.STANDBY, "event": ps.EVENT, "after": ps.SELF}[phase]
            if k == s:
                rate = np.minimum(hh.power_kw, np.maximum(soc - reserve, 0.0) * eta / (n_ev * ps.DT))
                ev["avail"][j] = soc - reserve
            soc, imp, exp, dis, ch, _ = ps.step(soc, b.pv[i], b.load[i], np.full(nd, mode), rate, hh, reserve)
            bsoc, bimp, bexp, bdis, bch, _ = ps.step(b.soc[i], b.pv[i], b.load[i], np.full(nd, ps.SELF),
                                                     np.zeros(nd), hh, 0.0)
            base_err = max(base_err, float(np.abs(bsoc - b.soc[i + 1]).max()), float(np.abs(bimp - b.imp[i]).max()),
                           float(np.abs(bexp - b.exp[i]).max()), float(np.abs(bdis - b.dis[i]).max()))
            di, de = imp - b.imp[i], exp - b.exp[i]
            dn = (de - di) * ps.DT
            d_net[j, :, k] = dn
            d_imp[phase][j] += di * ps.DT
            d_exp[phase][j] += de * ps.DT
            debs[phase][j] += de * ps.DT * ps.feed_in(np.array(k))
            w_phase[phase][j] += dn * m.rtp[i] / 1000
            d_dis[j] += (dis - b.dis[i]) * ps.DT
            d_loss[j] += _loss(ch, dis, eta) - _loss(bch, bdis, eta)
            if phase == "standby":
                sb_pos[j] += np.maximum(di, 0.0) * ps.DT
            elif phase == "event":
                q_raw[j] += exp * ps.DT
                delivered[j] += dis * ps.DT
                ev["pv"][j] += b.pv[i] * ps.DT
                ev["load"][j] += b.load[i] * ps.DT
                ev["import"][j] += imp * ps.DT
                ev["export"][j] += exp * ps.DT
                ev["loss"][j] += dis * (1 / eta - 1) * ps.DT
                if k == s + n_ev - 1:
                    ev["retained"][j] = soc - reserve
            if ps.PEAK[0] <= k < ps.PEAK[1]:
                net_peak[j, :, k - PEAK0] = imp - exp
        gap[j] = soc - b.soc[base_idx + ps.HORIZON]
        w_phase["gap"][j] = gap[j] * eta * p_eve / 1000
        for k in range(ps.PEAK[0], min(s0, ps.PEAK[1])):
            net_peak[j, :, k - PEAK0] = b.imp[base_idx + k] - b.exp[base_idx + k]
    ev["home"] = ev["load"] - ev["import"]
    return Parts(starts=starts, q=np.minimum(q_raw, hh.usable_kwh), q_raw=q_raw, d_net=d_net, dn=d_net.sum(axis=2),
                 w_phase=w_phase, d_imp=d_imp, d_exp=d_exp, debs=debs, standby_kwh=np.minimum(sb_pos, hh.usable_kwh),
                 gap=gap, d_dis=d_dis, d_loss=d_loss, delivered=delivered, ev=ev, net_peak=net_peak, base_err=base_err)


def balances(p: Parts) -> dict:
    """PLAN.md 5.2: residuals, kWh per responding customer per call.
    event:   (stored above reserve at the event start + PV in the event)
             - (home use + meter export + discharge loss + stored above reserve at the event end)
    horizon: (extra import - extra export) - (extra losses + gap at 15:00 next day), against no call."""
    e = p.ev
    event = e["avail"] + e["pv"] - (e["home"] + e["export"] + e["loss"] + e["retained"])
    horizon = (p.d_imp_total - p.d_exp_total) - (p.d_loss + p.gap)
    return {"event": event, "horizon": horizon}


# ------------------------------------------------------------------------------------------- money per kWh of Q
def customer_items(q, imp, debs_ev, debs_other, gap, standby, wear_kwh, offset_rate: float,
                   part: ps.Participation = PART) -> dict:
    """A$ per kWh of billed event net export (per responding customer per call). Positive = a cost to the customer.
    The bill is the model's (account()): retail x extra import - DEBS change - gap x eta x retail."""
    bill = ps.RETAIL * imp - debs_ev - debs_other - gap * HH.eta * ps.RETAIL
    offset = offset_rate * standby
    with np.errstate(divide="ignore", invalid="ignore"):
        v_self = (bill + debs_ev - offset) / q          # (a) lost self-use, net of the offset credit, before event DEBS
        debs_c = debs_ev / q                           # DEBS on the event's extra export: the customer gets it anyway
        wear_c = part.deg_per_kwh * wear_kwh / q
        hassle_c = part.hassle_per_event / q
        offset_c = offset / q
    p_c = v_self - debs_c + wear_c + hassle_c          # (d) the least per-kWh payment that leaves the customer whole
    return {"bill": bill, "offset": offset, "v_self": v_self, "debs_c": debs_c, "wear_c": wear_c,
            "hassle_c": hassle_c, "offset_c": offset_c, "p_c": p_c}


def zenith_items(q, w, offset_c, obl: ps.Obligations) -> dict:
    """A$ per kWh of Q. v_energy is the market value (to Zenith only if energy_to_zenith); p_z without IRCR."""
    with np.errstate(divide="ignore", invalid="ignore"):
        v_energy = w / q
        ops_c = obl.ops_per_event / q
    p_z0 = v_energy * float(obl.energy_to_zenith) - offset_c - ops_c
    return {"v_energy": v_energy, "ops_c": ops_c, "p_z0": p_z0}


def actual_items(p: Parts, policy: ps.Policy, obl: ps.Obligations) -> dict:
    """Per day and window at actual prices and the day's own physics (after the fact; for the reconciliation)."""
    c = customer_items(p.q, p.d_imp_total, p.debs["event"], p.debs["standby"] + p.debs["after"], p.gap,
                       p.standby_kwh, np.maximum(p.d_dis, 0.0), policy.offset_rate)
    return {**c, **zenith_items(p.q, p.w, c["offset_c"], obl)}


# ------------------------------------------------------------------------------------------- 09:00 expectations
@dataclass
class Monthly:
    """Per start and calendar month, means over the days up to 2026-06-30 (what is known at 09:00)."""
    prof: np.ndarray              # (start, 13, 62) mean change in net export by slot
    mean: dict                    # name -> (start, 13)


def monthly_means(p: Parts, m: ps.Market) -> Monthly:
    build = (m.days <= TRAIN_END)
    month = m.days.month.to_numpy()
    src = {"q": p.q, "dn": p.dn, "gap": p.gap, "standby": p.standby_kwh, "imp": p.d_imp_total,
           "debs_ev": p.debs["event"], "debs_other": p.debs["standby"] + p.debs["after"],
           "wear_kwh": np.maximum(p.d_dis, 0.0)}
    mean = {k: np.full((len(p.starts), 13), np.nan) for k in src}
    prof = np.zeros((len(p.starts), 13, ps.HORIZON))
    for mo in range(1, 13):
        sel = build & (month == mo)
        for k, v in src.items():
            mean[k][:, mo] = v[:, sel].mean(axis=1)
        prof[:, mo] = p.d_net[:, sel].mean(axis=1)
    return Monthly(prof=prof, mean=mean)


def expected_price(fc: ps.Forecasts, m: ps.Market) -> np.ndarray:
    """(days, 62) $/MWh: the 09:00 forecast + p_spike x uplift, then beyond the trading day the slot's mean price up to
    2026-06-30. The same construction as ps.energy_scores."""
    exp_price = np.where(np.isnan(fc.price), 0.0, fc.price) + fc.p_spike * fc.uplift
    return np.concatenate([exp_price, np.broadcast_to(m.slot_price[:ps.HORIZON - ps.N], (m.n_days, ps.HORIZON - ps.N))],
                          axis=1)


def exante(p: Parts, mm: Monthly, fc: ps.Forecasts, m: ps.Market, policy: ps.Policy, obl: ps.Obligations) -> dict:
    """09:00 expected values per (start, day): expected profiles of the day's calendar month x expected prices. The
    day's own weather and prices after 09:00 are not read."""
    month = m.days.month.to_numpy()
    price = expected_price(fc, m)
    g = {k: v[:, month] for k, v in mm.mean.items()}                     # (start, day)
    w_main = np.einsum("jdt,dt->jd", mm.prof[:, month], price) / 1000    # what energy_scores calls the value
    w = w_main + g["gap"] * HH.eta * m.slot_price[14:26].mean() / 1000
    c = customer_items(g["q"], g["imp"], g["debs_ev"], g["debs_other"], g["gap"], g["standby"], g["wear_kwh"],
                       policy.offset_rate)
    zz = zenith_items(g["q"], w, c["offset_c"], obl)
    has = np.broadcast_to(fc.has_fc, w.shape)
    zz["v_energy"] = np.where(has, zz["v_energy"], np.nan)
    zz["p_z0"] = np.where(has, zz["p_z0"], np.nan)
    return {**c, **zz, "q": g["q"], "dn": g["dn"], "w": np.where(has, w, np.nan), "w_main": w_main,
            "standby": g["standby"]}


# ----------------------------------------------------------------------------------------------------- the calls
@lru_cache(maxsize=32)
def fit_of(policy_name: str, scenario: str) -> ps.Fit:
    """The policy's call rule, fitted by the existing code on data up to 2026-06-30 (a reproduction, not a new choice)."""
    return ps.fit_rule(POLICIES[policy_name], SCENARIOS[scenario], PART)


def calls_for(policy: ps.Policy, obl: ps.Obligations, period: str, fit: ps.Fit, rule: ps.CallRule | None = None):
    """The calls evaluate_policy makes (same steps): (calls, contract year, season or None, days)."""
    m, fc = ps.load_market(), ps.load_forecasts()
    fam = ps.family(*FAM_KEY)
    years = ps.contract_years(m)
    cap_on = obl.capacity != "none"
    energy_starts = np.flatnonzero(fam.starts + 2 * policy.event_h <= 2 * (policy.window[1] - 8))
    scores = ps.energy_scores(FAM_KEY, policy, obl, fc, m, energy_starts)
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
    rule = rule or fit.rule
    w_margin = None
    if rule.kind == "perfect":
        cost = (policy.pay_per_kwh * fam.paid_kwh + policy.pay_per_event + policy.offset_rate * fam.standby_kwh
                + obl.ops_per_event)
        margin = fam.w_actual[energy_starts] - cost[energy_starts]
        best = margin.argmax(axis=0)
        w_margin = (margin[best, np.arange(m.n_days)], energy_starts[best])
        energy_on = obl.energy_to_zenith
    calls = ps.select_calls(days, fam, policy, rule, fc, scores, cap_on, energy_on, m, w_margin=w_margin)
    return calls, y, (y if y in ps.SEASONS else None), days


def my_ircr(p: Parts, days: np.ndarray, starts_j: np.ndarray, obl: ps.Obligations, season: int,
            peaks: dict) -> tuple[float, float]:
    """(kW reduction, A$ per responding customer for the season) from this module's own re-run: the median of the
    meter's net import in the season's 12 Peak SWIS intervals, without the calls less with them, x RCP of the capacity
    year the season sets. Same rule as ps.ircr_value (floor under 'floor', net under 'net')."""
    b = ps.baseline(HH)
    idx, slots = peaks[season]
    base = b.imp[idx * ps.N + slots] - b.exp[idx * ps.N + slots]
    with_calls = base.copy()
    called = dict(zip(np.asarray(days).tolist(), np.asarray(starts_j).tolist()))
    for n, (d, s) in enumerate(zip(idx, slots)):
        if d in called:
            with_calls[n] = p.net_peak[called[d], d, s - PEAK0]
    if obl.capacity == "floor":
        base, with_calls = np.maximum(base, 0.0), np.maximum(with_calls, 0.0)
    kw = float(np.median(base) - np.median(with_calls))
    rcp = ct.RCP[season + 1] if obl.rcp is None else obl.rcp
    return kw, kw / 1000 * rcp * obl.rcp_ratio


@dataclass
class Alloc:
    """One Hot Season's IRCR value spread over the season's calls (PLAN.md 3(c))."""
    season: int
    scenario: str
    method: str
    start: int
    n_calls: int
    q_sum: float                  # kWh per responding customer over the season's calls
    kw: float
    value_full: float             # A$ per responding customer (before the share)
    value_zenith: float           # x the capacity share
    rcp: float
    days: tuple

    @property
    def per_kwh(self) -> float:   # A$ per kWh of Q, Zenith's share
        return self.value_zenith / self.q_sum if self.q_sum > 0 else 0.0

    @property
    def per_kwh_2027(self) -> float:  # at the 2027-28 price $360,700/MW-yr, Zenith's share
        if self.q_sum <= 0:
            return 0.0
        return self.kw / 1000 * ct.RCP[2027] * SCENARIOS[self.scenario].capacity_share / self.q_sum


@lru_cache(maxsize=64)
def season_call_days(method: str, scenario: str, season: int) -> np.ndarray:
    """Hot Season call days of a method. C / B: the fitted rule's IRCR calls in that season (as evaluate_policy makes
    them); peak4: the season's 4 peak days (hindsight, not attainable)."""
    if method == "peak4":
        return np.array(sorted(set(ps.peak_intervals()[season][0].tolist())), int)
    pol_name = C_OF[scenario] if method == "C" else "B"
    obl = SCENARIOS["S2b" if (method == "C" and scenario == "S2a") else scenario]
    fit = fit_of(pol_name, "S2b" if (method == "C" and scenario == "S2a") else scenario)
    period = {2023: "FY2023", 2024: "FY2024", 2025: "in-sample"}[season]
    calls, _, _, _ = calls_for(POLICIES[pol_name], obl, period, fit)
    return calls.day[calls.kind == "ircr"]


def allocation(p: Parts, scenario: str, method: str, season: int, start: int) -> Alloc:
    obl = SCENARIOS[scenario]
    if obl.capacity == "none":
        return Alloc(season, scenario, method, start, 0, 0.0, 0.0, 0.0, 0.0, 0.0, ())
    days = season_call_days(method, scenario, season)
    j = p.index(start)
    kw, value = my_ircr(p, days, np.full(len(days), j), obl, season, ps.peak_intervals())
    rcp = ct.RCP[season + 1] if obl.rcp is None else obl.rcp
    return Alloc(season, scenario, method, start, len(days), float(p.q[j, days].sum()), kw, value,
                 value * obl.capacity_share, rcp, tuple(int(d) for d in days))


def pooled_per_kwh(allocs: list[Alloc]) -> float:
    """A$ per kWh of Q, Zenith's share: the three seasons' value over their kWh (the 09:00 value on a flagged day)."""
    q = sum(a.q_sum for a in allocs)
    return sum(a.value_zenith for a in allocs) / q if q > 0 else 0.0


# ------------------------------------------------------------------------------------------------- one-day query
@lru_cache(maxsize=1)
def _context():
    m, fc = ps.load_market(), ps.load_forecasts()
    p = decompose()
    return m, fc, p, monthly_means(p, m)


def kwh_value(day, scenario: str = "S2b", window: str | int = "17:30-19:30") -> dict:
    """At 09:00 on `day`: (a) the customer's self-use value, (b) the energy market value, (c) the Hot Season IRCR value
    and (d) the two break-even payments, all in c per kWh of billed event net export (per responding customer per
    call), with the denominators. Policy terms: offset credit at the retail tariff, ops $0.05 per event, as in C."""
    m, fc, p, mm = _context()
    d = int(np.flatnonzero(m.days == pd.Timestamp(day))[0])
    start = window if isinstance(window, (int, np.integer)) else \
        int(round(2 * (int(window[:2]) + int(window[3:5]) / 60 - 8)))
    j = p.index(start)
    obl = SCENARIOS[scenario]
    pol = POLICIES[C_OF[scenario] or "B"]
    x = exante(p, mm, fc, m, pol, obl)
    v_ircr, flag, q_rule = 0.0, False, None
    if obl.capacity != "none":
        fit = fit_of(C_OF[scenario], "S2b" if scenario == "S2a" else scenario)
        q_rule = fit.rule.ircr_q
        flag = bool(fc.flags[q_rule][d])
        if flag:
            v_ircr = pooled_per_kwh([allocation(p, scenario, "C", y, start) for y in ps.SEASONS])
    pz = x["p_z0"][j, d] + v_ircr
    c = 100.0
    return {"day": str(m.days[d].date()), "scenario": scenario, "window": window_label(start),
            "flagged_peak_day": flag, "flag_quantile": q_rule, "has_0900_forecast": bool(fc.has_fc[d]),
            "Q_expected_kwh": float(x["q"][j, d]), "dN_expected_kwh": float(x["dn"][j, d]),
            "a_self_use_c": c * float(x["v_self"][j, d]), "event_debs_c": c * float(x["debs_c"][j, d]),
            "wear_c": c * float(x["wear_c"][j, d]), "hassle_c": c * float(x["hassle_c"][j, d]),
            "b_energy_c": c * float(x["v_energy"][j, d]), "energy_to_zenith": obl.energy_to_zenith,
            "c_ircr_c": c * v_ircr, "offset_c": c * float(x["offset_c"][j, d]), "ops_c": c * float(x["ops_c"][j, d]),
            "d_zenith_max_c": c * float(pz), "d_customer_min_c": c * float(x["p_c"][j, d]),
            "denominator": "c per kWh of billed event net export Q, per responding customer per call"}


def stress_copy(p: Parts, days_mask: np.ndarray, rng: np.random.Generator) -> Parts:
    """A copy whose physics on the masked days is scrambled (for the look-ahead check)."""
    def noisy(a):
        a = a.copy()
        a[:, days_mask] = a[:, days_mask] * rng.uniform(0.0, 3.0, a[:, days_mask].shape) + rng.normal(0, 5, a[:, days_mask].shape)
        return a
    d_net = p.d_net.copy()
    d_net[:, days_mask] = d_net[:, days_mask] * rng.uniform(0.0, 3.0, d_net[:, days_mask].shape)
    return replace(p, q=noisy(p.q), d_net=d_net, dn=noisy(p.dn), gap=noisy(p.gap), standby_kwh=noisy(p.standby_kwh),
                   d_imp={k: noisy(v) for k, v in p.d_imp.items()}, debs={k: noisy(v) for k, v in p.debs.items()},
                   d_dis=noisy(p.d_dis))
