"""Market-ready offers (analysis/2026-10-26/market_offer/PLAN.md): the dispatcher's 5-min decisions written as AEMO
Real-Time Market Submissions (RTMS) for the battery, one per Dispatch Interval (DI), for energy and the four FCESS.

Rules (ESM Rules, 1 Oct 2026; WEM Procedure: WEM Submissions v1.0; AEMO's RTMS OpenAPI, data/raw/aemo_rtms/; ERA's
price limits, data/raw/era/; the claims in analysis/2026-10-26/market_offer/CLAIMS.csv):
  energy  one curve for an Electric Storage Resource: negative quantities bid to withdraw, positive ones offer to
          inject (7.4.46); 1-10 tranches per service and DI (procedure 2.2.2(b)); prices rise strictly with quantity
          (7.4.47(b)), in whole cents, every withdrawal price below every injection price (7.4.47(c)), "MIN" / "MAX"
          only first / last; MW to 0.001, the negative ones summing to the most withdrawal and the positive ones to the
          most injection (7.4.40(g)); within the floor and the month's Energy Offer Price Ceiling
  FCESS   maximumCapacity, its trapezium (enablementMinimum <= lowBreakpoint <= highBreakpoint <= enablementMaximum,
          7.4.41) and tranches summing to it, $/MW/h in whole cents from 0 to the month's ceiling (7.4.51A)
  timing  prices locked from Gate Closure (7.4.35), at most 15 minutes before the DI (7.4.31): GATE steps

The energy curve (net MW rising with the price) is the dispatcher's own decision at every price:
  1 the withdrawal the plan does not use, at MIN: it charges no more than the plan
  2 the plan's charging, a cent below the first layered threshold (or the ceiling): at any lower price it charges,
    as run_day charges whatever the price
  3 the plan's discharging a cent above the floor: a price taker, as run_day sells the plan's MW at any price
  4 the layered offers: LadderBids.sell itself at each threshold that moves it; each rise in its MW is a tranche at
    that threshold rounded up to the cent (so never sold cheaper than the dispatcher would)
  5 the rest of the injection capability at MAX: protected energy and thresholds at or above the ceiling
FCESS: the plan's enablement at the price run_day offers it at (regulation's use cost, contingency 0), on a 45-degree
trapezium within the battery's power. ASSUMED holds what the rules leave to the facility (or we could not confirm).

Collector is route_days' on_step and on_day: each DI's offers at its Gate Closure (the submission), at the step that
ran it (to check against the dispatcher) and from the 09:00 plan (what the plan expects to offer, for the MVP page).
"""

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from vpp import dispatch as dp
from vpp import storage_value as sv

FLOOR = -1000.0  # $/MWh, the Energy Offer Price Floor (ERA, Energy Offer Price Floor review, final, 2025-05-23)
# $/MWh, the Energy Offer Price Ceiling from the 1st of the month (ERA indexed values, data/raw/era/wb_a.xlsx; 2025-01:
# the base value from 2025-01-01)
ENERGY_CEILING = {
    "2025-01": 1000, "2025-02": 1000, "2025-03": 1100, "2025-04": 1100, "2025-05": 1100, "2025-06": 1000,
    "2025-07": 900, "2025-08": 900, "2025-09": 900, "2025-10": 900, "2025-11": 900, "2025-12": 1000,
    "2026-01": 1000, "2026-02": 900, "2026-03": 900, "2026-04": 1000, "2026-05": 1500, "2026-06": 1700,
    "2026-07": 1500, "2026-08": 1200, "2026-09": 1000, "2026-10": 1100, "2026-11": 1300,
}
# $/MW/h for regulation raise, regulation lower, contingency raise, contingency lower from the 1st of the month (ERA
# FCESS Offer Price Ceilings, data/raw/era/wb_b.xlsx)
FCESS_CEILING = {
    "2025-04": (300, 300, 300, 300), "2025-05": (300, 300, 300, 300), "2025-06": (300, 300, 250, 250),
    "2025-07": (250, 250, 250, 250), "2025-08": (250, 250, 250, 250), "2025-09": (250, 250, 250, 250),
    "2025-10": (300, 300, 300, 300), "2025-11": (300, 300, 300, 300), "2025-12": (300, 300, 300, 300),
    "2026-01": (300, 300, 300, 300), "2026-02": (300, 300, 300, 300), "2026-03": (250, 250, 250, 250),
    "2026-04": (300, 300, 300, 300), "2026-05": (400, 400, 400, 400), "2026-06": (400, 400, 400, 400),
    "2026-07": (250, 300, 300, 50), "2026-08": (223, 281, 261, 22), "2026-09": (226, 288, 263, 23),
    "2026-10": (229, 294, 266, 24), "2026-11": (229, 293, 266, 24),
}
SERVICES = {"reg_raise": "regulationRaise", "reg_lower": "regulationLower", "cont_raise": "contingencyRaise",
            "cont_lower": "contingencyLower"}
MAX_TRANCHES = 10
GATE = 3          # 5-min steps from Gate Closure to the DI's start: 15 minutes, the most 7.4.31 allows
FORWARD_AT = 12   # the step of the 09:00 decision
ASSUMED = {"facilityCode": "EXAMPLE_ESR_01",  # a placeholder, no real facility
           "ramp_mw_per_min": 10.0, "inflexibleFlag": "NO", "capacityType": "IN-SERVICE",
           "unconstrained_forecasts": 0.0,  # the rules ask them of semi- and non-scheduled facilities
           "gate_closure_min": 15, "di_1_starts": "08:00"}


def month(day) -> str:
    return pd.Timestamp(day).strftime("%Y-%m")


def ceiling(day) -> float:
    """$/MWh, the Energy Offer Price Ceiling of the trading day's month."""
    return float(ENERGY_CEILING[month(day)])


def fcess_ceiling(day) -> dict:
    """$/MW/h by service, the FCESS Offer Price Ceilings of the trading day's month."""
    return dict(zip(SERVICES, map(float, FCESS_CEILING[month(day)])))


def cents_up(x: float) -> float:
    return math.ceil(round(x * 100.0, 6)) / 100.0


def mw(x: float) -> float:
    """MW to the 0.001 the submissions take (never -0.0)."""
    return round(float(x), 3) + 0.0


@dataclass
class Tranche:
    mw: float       # negative: a bid to withdraw
    price: float    # $/MWh (FLOOR and the ceiling are MIN and MAX)
    kind: str       # unwanted_charge, planned_charge, planned, ladder, withheld


@dataclass
class Offer:
    """One DI's energy offer: tranches in rising price, from the most withdrawal up to the most injection."""
    tranches: list
    ceiling: float
    merged: int = 0  # tranches merged to keep within MAX_TRANCHES

    @property
    def max_injection(self) -> float:
        return mw(sum(x.mw for x in self.tranches if x.mw > 0))

    @property
    def max_withdrawal(self) -> float:
        return mw(sum(x.mw for x in self.tranches if x.mw < 0))


@dataclass
class Fcess:
    service: str
    mw: float
    price: float    # $/MW/h
    en_min: float
    low: float
    high: float
    en_max: float


def ladder(bids, t: int, s: float, base: float, sellable: float, seen: float, eta: float, alpha: float, dw: float,
           k_min: float) -> list[tuple[float, float]]:
    """(threshold, MW the dispatcher discharges at that price) at each threshold that raises the MW, in rising price:
    LadderBids.sell at the thresholds of the segments between the plan's stored energy and the most it may sell to
    (only those can move its target)."""
    if bids is None or sellable <= base + 1e-6:  # run_day calls sell only with room beyond the plan
        return []
    thr = bids.thresholds(t, seen, eta, alpha, dw, k_min)
    sl = bids.locked_stored(s)
    lo = max(0, int(np.floor(max(0.0, sl - sellable * dp.DT5 / eta) / sv.GRID + 1e-9)))
    hi = min(len(thr), int(np.ceil((sl - base * dp.DT5 / eta) / sv.GRID - 1e-9)))
    out, prev = [], base
    for p in sorted({float(x) for x in thr[lo:hi] if np.isfinite(x)}):
        m = bids.sell(t, s, base, sellable, p, seen, eta, alpha, dw, k_min)[0]
        if m > prev + 1e-9:
            out.append((p, m))
            prev = m
    return out


def energy_curve(bids, t: int, s: float, plan_mw: float, sellable: float, seen: float, inj_cap: float,
                 wd_cap: float, eta: float, alpha: float, dw: float, k_min: float, ceil: float) -> Offer:
    """The DI's energy offer from what the dispatcher decides with: stored energy s (as LadderBids.sell takes it), the
    plan's MW (negative: charging), the most it may sell, the price seen, and the most it can inject and withdraw."""
    base = max(plan_mw, 0.0)
    charge = min(max(-plan_mw, 0.0), wd_cap)
    steps = ladder(bids, t, s, base, sellable, seen, eta, alpha, dw, k_min)
    inj_cap = max(inj_cap, steps[-1][1] if steps else base)
    first = min(cents_up(steps[0][0]), ceil) if steps else ceil
    pieces = [(-charge, FLOOR, "unwanted_charge"), (0.0, first - 0.01, "planned_charge"), (base, FLOOR + 0.01, "planned")]
    for p, m in steps:
        price = min(cents_up(p), ceil)
        pieces.append((m, price, "ladder" if price < ceil else "withheld"))
    pieces.append((inj_cap, ceil, "withheld"))
    tranches, level = [], mw(-wd_cap)
    for top, price, kind in pieces:  # each piece from the level reached so far up to its top (net MW)
        q = mw(mw(top) - level)
        if q <= 0:
            continue
        x = Tranche(q if top > 0 else -q, price, kind)
        if tranches and tranches[-1].price == price and (tranches[-1].mw > 0) == (x.mw > 0):
            tranches[-1].mw = mw(tranches[-1].mw + x.mw)
        else:
            tranches.append(x)
        level = mw(top)
    offer = Offer(tranches, ceil)
    while len(offer.tranches) > MAX_TRANCHES:  # the closest-priced injection tranches, at the higher price
        k = min((k for k in range(1, len(offer.tranches)) if offer.tranches[k - 1].mw > 0),
                key=lambda k: offer.tranches[k].price - offer.tranches[k - 1].price)
        offer.tranches[k].mw = mw(offer.tranches[k].mw + offer.tranches.pop(k - 1).mw)
        offer.merged += 1
    return offer


def clear(offer: Offer, price: float) -> float:
    """Net MW dispatched at price, as AEMO reads the curve: a withdrawal tranche at or below its price (Dispatch
    Algorithm Formulation 2.2.3(b)), an injection tranche at or above it."""
    return mw(sum(x.mw for x in offer.tranches if (price <= x.price if x.mw < 0 else price >= x.price)))


def fcess_offer(service: str, q: float, cost: float, P: float, ceil: float) -> Fcess:
    """q MW at the price run_day enables it at (cost, $/MW/h), on a 45-degree trapezium within -P..P MW."""
    q = min(max(0.0, math.floor(round(q * 1000.0, 6)) / 1000.0), P)
    return Fcess(service, q, min(max(cents_up(cost), 0.0), ceil), mw(-P), mw(-P + q), mw(P - q), mw(P))


def energy_interval(di: int, o: Offer) -> dict:
    n = len(o.tranches)

    def price(k: int, x: Tranche):
        if k == 0 and x.price == FLOOR:
            return "MIN"
        if k == n - 1 and x.price == o.ceiling:
            return "MAX"
        return round(x.price, 2)
    a = ASSUMED
    return {"dispatchIntervalFrom": di, "dispatchIntervalTo": di,
            "unconstrainedInjectionForecast": a["unconstrained_forecasts"],
            "unconstrainedWithdrawalForecast": a["unconstrained_forecasts"],
            "maxInjectionCapacity": o.max_injection, "maxWithdrawalCapacity": o.max_withdrawal,
            "inflexibleFlag": a["inflexibleFlag"], "maxUpwardRampRate": a["ramp_mw_per_min"],
            "maxDownwardRampRate": a["ramp_mw_per_min"],
            "tranches": [{"tranche": k + 1, "quantity": x.mw, "price": price(k, x), "capacityType": a["capacityType"]}
                         for k, x in enumerate(o.tranches)]}


def ess_interval(di: int, f: Fcess) -> dict:
    return {"dispatchIntervalFrom": di, "dispatchIntervalTo": di, "maximumCapacity": f.mw,
            "enablementMinimum": f.en_min, "lowBreakpoint": f.low, "highBreakpoint": f.high,
            "enablementMaximum": f.en_max,
            "tranches": [{"tranche": 1, "quantity": f.mw, "price": f.price, "capacityType": ASSUMED["capacityType"]}]}


def request(day, intervals: dict, reference: str, comment: str) -> dict:
    """The RTMS request (createSubmissionJsonRequest) of a trading day: a MARKET variation with {DI: (Offer, {service:
    Fcess})}, DI 1 starting at 08:00."""
    dis = sorted(intervals)
    facility = ASSUMED["facilityCode"]
    td = {"dateFrom": str(pd.Timestamp(day).date()), "dateTo": str(pd.Timestamp(day).date()),
          "energy": {"facilities": [{"facilityCode": facility,
                                     "dispatchIntervals": [energy_interval(i, intervals[i][0]) for i in dis]}]}}
    for k, name in SERVICES.items():
        td[name] = {"facilities": [{"facilityCode": facility,
                                    "dispatchIntervals": [ess_interval(i, intervals[i][1][k]) for i in dis]}]}
    return {"referenceId": reference[:100],
            "submission": {"variation": {"submissionCode": "MARKET", "allowGateClosureViolation": "NO",
                                         "comment": comment[:280], "tradingDays": [td]}}}


def offer_bands(o: Offer) -> list[dict]:
    """The MVP's offer_bands (analysis/2026-10-25/mvp_product_design/HANDOFF.md): {price, mw, kind} per tranche."""
    return [{"price": x.price, "mw": x.mw, "kind": x.kind} for x in o.tranches]


# --- checks of a request: AEMO's own schema, then the rules it does not encode ---

def schema_errors(x, s: dict, root: dict, at: str = "$") -> list[str]:
    """Where x breaks the JSON schema s (the subset the RTMS OpenAPI uses; root: its components.schemas)."""
    if "$ref" in s:
        s = root[s["$ref"].rsplit("/", 1)[1]]
    errs = []
    if "oneOf" in s and sum(not schema_errors(x, o, root, at) for o in s["oneOf"]) != 1:
        errs.append(f"{at}: not exactly one of oneOf")
    if "anyOf" in s and all(schema_errors(x, o, root, at) for o in s["anyOf"]):
        errs.append(f"{at}: none of anyOf")
    kind = s.get("type")
    ok = {"object": isinstance(x, dict), "array": isinstance(x, list), "string": isinstance(x, str),
          "integer": isinstance(x, int) and not isinstance(x, bool),
          "number": isinstance(x, (int, float)) and not isinstance(x, bool), None: True}[kind]
    if not ok:
        return errs + [f"{at}: not {kind}"]
    if "enum" in s and x not in s["enum"]:
        errs.append(f"{at}: {x!r} not in enum")
    if isinstance(x, (int, float)) and not isinstance(x, bool):  # keywords apply by the instance's own type
        if "minimum" in s and x < s["minimum"]:
            errs.append(f"{at}: {x} < minimum {s['minimum']}")
        if "maximum" in s and x > s["maximum"]:
            errs.append(f"{at}: {x} > maximum {s['maximum']}")
        if "multipleOf" in s and abs(x / s["multipleOf"] - round(x / s["multipleOf"])) > 1e-6:
            errs.append(f"{at}: {x} not a multiple of {s['multipleOf']}")
    if isinstance(x, str) and "maxLength" in s and len(x) > s["maxLength"]:
        errs.append(f"{at}: longer than {s['maxLength']}")
    if isinstance(x, dict):
        errs += [f"{at}: missing {k}" for k in s.get("required", []) if k not in x]
        props = s.get("properties", {})
        if s.get("additionalProperties") is False:
            errs += [f"{at}: unexpected {k}" for k in x if k not in props]
        for k, v in x.items():
            if k in props:
                errs += schema_errors(v, props[k], root, f"{at}.{k}")
    if isinstance(x, list):
        if len(x) < s.get("minItems", 0):
            errs.append(f"{at}: fewer than {s['minItems']} items")
        if "maxItems" in s and len(x) > s["maxItems"]:
            errs.append(f"{at}: more than {s['maxItems']} items")
        if "items" in s:
            for k, v in enumerate(x):
                errs += schema_errors(v, s["items"], root, f"{at}[{k}]")
    return errs


def _cents(x: float) -> bool:
    return abs(x * 100.0 - round(x * 100.0)) < 1e-6


def rule_errors(req: dict, P: float) -> list[str]:
    """Where a request breaks the rules beyond the schema (each message names its rule)."""
    errs = []
    for td in req["submission"]["variation"]["tradingDays"]:
        ceil, fc = ceiling(td["dateFrom"]), fcess_ceiling(td["dateFrom"])
        for f in td["energy"]["facilities"]:
            for it in f["dispatchIntervals"]:
                at, tr = f"energy DI {it['dispatchIntervalFrom']}", it["tranches"]
                n = len(tr)
                if not 1 <= n <= MAX_TRANCHES:
                    errs.append(f"{at}: {n} tranches (WEM Submissions 2.2.2(b))")
                if [x["tranche"] for x in tr] != list(range(1, n + 1)):
                    errs.append(f"{at}: tranches not numbered 1..n")
                prices = []
                for k, x in enumerate(tr):
                    p = x["price"]
                    if p == "MIN":
                        if k != 0:
                            errs.append(f"{at}: MIN not in the first tranche (RTMS spec C047)")
                        p = FLOOR
                    elif p == "MAX":
                        if k != n - 1:
                            errs.append(f"{at}: MAX not in the last tranche (RTMS spec C047)")
                        p = ceil
                    elif not _cents(p):
                        errs.append(f"{at}: price {p} not in whole cents (7.4.40(g)(i)(1))")
                    if not FLOOR - 1e-9 <= p <= ceil + 1e-9:
                        errs.append(f"{at}: price {p} outside {FLOOR:.0f}..{ceil:.0f} (Energy Offer Price limits)")
                    prices.append(p)
                if any(b <= a for a, b in zip(prices, prices[1:])):
                    errs.append(f"{at}: prices not strictly rising (7.4.47(b); RTMS spec C036)")
                wd = [p for p, x in zip(prices, tr) if x["quantity"] < 0]
                inj = [p for p, x in zip(prices, tr) if x["quantity"] > 0]
                if wd and inj and max(wd) >= min(inj):
                    errs.append(f"{at}: a withdrawal price not below every injection price (7.4.47(c))")
                if any(x["quantity"] == 0 for x in tr):
                    errs.append(f"{at}: a 0 MW tranche (WEM Submissions E[A])")
                if abs(sum(x["quantity"] for x in tr if x["quantity"] < 0) - it["maxWithdrawalCapacity"]) > 1e-6:
                    errs.append(f"{at}: withdrawal tranches do not sum to maxWithdrawalCapacity (7.4.40(g)(ii))")
                if abs(sum(x["quantity"] for x in tr if x["quantity"] > 0) - it["maxInjectionCapacity"]) > 1e-6:
                    errs.append(f"{at}: injection tranches do not sum to maxInjectionCapacity (7.4.40(g)(iii))")
                if it["maxInjectionCapacity"] > P + 1e-9 or it["maxWithdrawalCapacity"] < -P - 1e-9:
                    errs.append(f"{at}: capability beyond the battery's {P:g} MW")
        for k, name in SERVICES.items():
            for f in td[name]["facilities"]:
                for it in f["dispatchIntervals"]:
                    at, tr = f"{name} DI {it['dispatchIntervalFrom']}", it["tranches"]
                    if not 1 <= len(tr) <= MAX_TRANCHES:
                        errs.append(f"{at}: {len(tr)} tranches (WEM Submissions 2.2.2(b))")
                    if abs(sum(x["quantity"] for x in tr) - it["maximumCapacity"]) > 1e-6 or any(
                            x["quantity"] < 0 for x in tr):
                        errs.append(f"{at}: tranches do not sum to maximumCapacity, or one is negative (7.4.41(h))")
                    e = [it["enablementMinimum"], it["lowBreakpoint"], it["highBreakpoint"], it["enablementMaximum"]]
                    if any(b < a - 1e-9 for a, b in zip(e, e[1:])):
                        errs.append(f"{at}: trapezium out of order (7.4.41)")
                    q = it["maximumCapacity"]
                    if e[0] < -P - 1e-9 or e[3] > P + 1e-9 or e[1] - q < e[0] - 1e-6 or e[2] + q > e[3] + 1e-6:
                        errs.append(f"{at}: trapezium not 45 degrees within -{P:g}..{P:g} MW")
                    for x in tr:
                        p = x["price"]
                        if isinstance(p, str) or not _cents(p) or not -1e-9 <= p <= fc[k] + 1e-9:
                            errs.append(f"{at}: price {p} not whole cents in 0..{fc[k]:g} (7.4.51A; ERA)")
    return errs


# --- collecting the offers from a run ---

@dataclass
class Collector:
    """vpp.v2.harness.route_days' on_step (and on_day for the 09:00 offers). bat: the battery as the dispatcher sees
    it. leak (a check only): the Gate Closure offers priced from the DI's own price instead of the one seen at Gate
    Closure. Per DI it keeps the Gate Closure offer and the 09:00 one (by day), and one row of the step's decisions and
    the offers' MW at its price (steps). The connection's rooms on the forecast load are the step's rooms moved from
    the actual load to the forecast one."""
    bat: object
    leak: bool = False
    steps: list = field(default_factory=list)
    submitted: dict = field(default_factory=dict)  # day -> {DI: (Offer, {service: Fcess})}
    forward: dict = field(default_factory=dict)    # day -> {DI: (Offer, {service: Fcess})}
    leaked: dict = field(default_factory=dict)     # day -> {DI: Offer}, with leak
    _state: dict = field(default_factory=dict, repr=False)
    _at9: dict = field(default_factory=dict, repr=False)

    def __call__(self, day, x: dict) -> None:
        P, E, eta = self.bat.power_mw, self.bat.energy_mwh, self.bat.eta
        i, t, price, bids = x["step"], x["t"], x["price"], x["bids"]
        if i == 0:
            self._state, self._at9 = {}, {}
            self.submitted[day], self.leaked[day] = {}, {}
        ceil, fc = ceiling(day), fcess_ceiling(day)
        plan, k0 = x["plan"], x["t"] - x["plan_t0"]
        ahead = slice(k0, min(k0 + 2, len(plan["net"])))  # this interval and the next, from the plan in force
        self._state[i] = {"s": x["s"], "budget": x["budget"], "t": t, "net": np.array(plan["net"][ahead], float),
                          "fcess": {k: np.array(plan[k][ahead], float) for k in SERVICES}, "price": price,
                          "offer_f": dict(x["offer_f"])}
        args = (eta, x["alpha"], x["dw"], x["k_min"], ceil)
        # the offer at the step that ran it: from what run_day decided with
        inj = max(0.0, min(P, x["exp_room"], x["s"] * eta / dp.DT5))
        wd = max(0.0, min(P, x["imp_room"], (E - x["s"]) / (eta * dp.DT5)))
        ex = energy_curve(bids, t, x["s"], x["pw_plan"], x["sellable"], x["seen"], inj, wd, *args)
        bad = {}  # planted errors the check must catch: the first ladder tranche $1 dearer, or 0.01 MW larger
        for kind in ("price", "mw"):
            o = Offer([replace(tr) for tr in ex.tranches], ex.ceiling)
            for tr in o.tranches:
                if tr.kind == "ladder":
                    tr.price, tr.mw = (tr.price + 1.0, tr.mw) if kind == "price" else (tr.price, mw(tr.mw + 0.01))
                    break
            bad[kind] = clear(o, price)
        # the offer at Gate Closure: only what was known GATE steps before (the day's first steps: its first state)
        sub = self._state.get(i - GATE, self._state[0])
        k = t - sub["t"]
        s_lock = max(0.0, sub["s"] - GATE * P * dp.DT5 / eta)  # the least stored energy possible by the DI (as bids)
        load = x["load_act"] if x["load_fc"] is None else float(x["load_fc"])
        exp = x["exp_room"] - x["load_act"] + load  # energy export room on the forecast load
        sellable = max(0.0, min(P, exp, (s_lock - x["keep_e"]) * eta / dp.DT5,
                                (sub["budget"] - GATE * P * dp.DT5) / dp.DT5))
        plan_mw = min(float(sub["net"][k]), sellable)
        inj_s = max(0.0, min(P, exp, s_lock * eta / dp.DT5))
        s_hi = min(E, sub["s"] + GATE * P * dp.DT5 * eta)  # the most stored energy possible by the DI
        wd_s = max(0.0, min(P, x["imp_room"] + x["load_act"] - load, (E - s_hi) / (eta * dp.DT5)))
        so = energy_curve(bids, t, sub["s"], plan_mw, sellable, x["seen"], inj_s, wd_s, *args)
        fo = {s: fcess_offer(s, sub["fcess"][s][k], sub["offer_f"][s], P, fc[s]) for s in SERVICES}
        self.submitted[day][i + 1] = (so, fo)
        if self.leak:
            self.leaked[day][i + 1] = energy_curve(bids, t, sub["s"], plan_mw, sellable, price, inj_s, wd_s, *args)
        if i == FORWARD_AT:
            b9 = None
            if bids is not None:  # unlocked: the 09:00 offers are priced and sized for the plan's own path
                b9 = replace(bids, lock=0)
                b9.spiked = bids.spiked  # replace() resets the fields run_day's steps fill
            self._at9 = {"bids": b9, "plan": plan,
                         "t0": x["plan_t0"], "s": x["s"], "budget": x["budget"], "keep_e": x["keep_e"],
                         "seen": self._state[i - 1]["price"], "offer_f": dict(x["offer_f"]), "args": args}
        base = max(x["pw_plan"], 0.0)
        want = x["sold"] if x["sold"] is not None and x["sold"] > base + 1e-9 else x["pw_plan"]
        sub_mw = clear(so, price)
        raise_q = fo["reg_raise"].mw + fo["cont_raise"].mw
        self.steps.append({
            "day": day, "step": i, "price": price, "ceiling": ceil,
            "want": want, "exec_mw": clear(ex, price), "exec_n": len(ex.tranches),
            "bad_price_mw": bad["price"], "bad_mw_mw": bad["mw"],
            "charge_over": max(-x["pw_plan"], 0.0) > wd + 1e-9,
            "plan_exec": x["pw_plan"], "plan_sub": plan_mw, "sellable_exec": x["sellable"], "sellable_sub": sellable,
            "sold": np.nan if x["sold"] is None else x["sold"], "pw_offer": x["pw_offer"], "pw": x["pw"],
            "safety": bool(x["safety"]), "forced": x["forced_mw"] > 0, "load_act": x["load_act"], "load_fc": load,
            "sub_mw": sub_mw, "sub_n": len(so.tranches), "sub_merged": so.merged,
            "sub_inj": so.max_injection, "sub_wd": so.max_withdrawal,
            "sub_ladder_mw": sum(tr.mw for tr in so.tranches if tr.kind == "ladder"),
            "raise_out": any(f.mw > 0 and not f.low - 1e-9 <= sub_mw <= f.high + 1e-9
                             for s, f in fo.items() if s in ("reg_raise", "cont_raise")),
            "lower_out": any(f.mw > 0 and not f.low - 1e-9 <= sub_mw <= f.high + 1e-9
                             for s, f in fo.items() if s in ("reg_lower", "cont_lower")),
            "raise_q": raise_q, "lower_q": fo["reg_lower"].mw + fo["cont_lower"].mw,
            "reg_raise_price": fo["reg_raise"].price, "reg_lower_price": fo["reg_lower"].price,
        })

    def on_day(self, day, kw: dict, summary: dict, log) -> None:
        """The 09:00 offers for every DI still open at 09:00 (from GATE steps after it), from the 09:00 plan: its
        stored energy and MW path, the cycle budget it leaves, the forecast load and the price state seen at 09:00."""
        a = self._at9
        if not a:
            return
        P, E, eta = self.bat.power_mw, self.bat.energy_mwh, self.bat.eta
        plan, t0, load, site = a["plan"], a["t0"], np.asarray(kw["load_fc"], float), kw["site"]
        export = site.export_limit_mw if kw.get("export_energy", True) else 0.0
        fc, out = fcess_ceiling(day), {}
        s0, soc = a["s"], np.asarray(plan["soc"], float)  # stored at 09:00; the plan's at the end of each interval
        budget = a["budget"]
        for i in range(FORWARD_AT, dp.N * dp.STEPS):
            t, j = divmod(i, dp.STEPS)
            net = float(plan["net"][t - t0])
            start = s0 if t == FORWARD_AT // dp.STEPS else float(soc[t - t0 - 1])
            s = start - net * j * dp.DT5 / eta if net > 0 else start - net * j * dp.DT5 * eta
            if i >= FORWARD_AT + GATE:
                exp = export + load[t]
                sellable = max(0.0, min(P, exp, (s - a["keep_e"]) * eta / dp.DT5, budget / dp.DT5))
                inj = max(0.0, min(P, exp, s * eta / dp.DT5))
                wd = max(0.0, min(P, site.import_limit_mw - load[t], (E - s) / (eta * dp.DT5)))
                o = energy_curve(a["bids"], t, s, min(net, sellable), sellable, a["seen"], inj, wd, *a["args"])
                f = {k: fcess_offer(k, float(plan[k][t - t0]), a["offer_f"][k], P, fc[k]) for k in SERVICES}
                out[i + 1] = (o, f)
            budget -= max(net, 0.0) * dp.DT5
        self.forward[day] = out
