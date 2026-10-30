"""Plain-language reasons for each decision the app shows (Explainability, reports/tool_model.md 1.2): every half-hour
of the storage network's 09:00 plan, the plan's runs of one action, what happened after 09:00, and the customer-battery
call.

Each reason compares numbers the decision itself used: for the plan, its forecast prices, the value it gives energy
kept to 08:00 tomorrow, wear plus the safety margin, the factory's import caps and reserve, the forecast grid-service
prices; for a call, AEMO's 09:00 demand forecast against the rule's threshold. A reason is written only when its
comparison holds in those numbers, and every number goes through Facts, so unaccounted() confirms the text holds no
number that was not computed (the pattern of vpp/v2/explain.py). The plan is solved for the whole day at once, so a
reason names the comparison that holds, not the only one the solver weighed.

A reason is a point: `say` (a short lead such as "Not selling", optional), `why` (a plain phrase without numbers),
`nums` (the numbers it compares: {"v": value with unit, "l": short label, "note": hover} and {"op": "<"}) and `text`
(the whole plain sentence, for hover and screen readers). The page lays points out; the sentences are the same facts."""

import re
from decimal import ROUND_HALF_UP, Decimal

import numpy as np

from .common import FIRST, N, clock, num

NUMBER = re.compile(r"(?<![\w.])[-−+]?\d(?:[\d,]*\d)?(?:\.\d+)?%?")
SKIP = re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{4}-\d{2}(?:-\d{2})?\b")  # clock times, dates, contract years
EPS_MW, EPS_MWH = 0.01, 0.05
K_PEAK = 4  # the call rule's threshold is the season's 4th-highest daily peak so far (Fleet._season_tables)
RULE_YEAR = 2027  # scenario S2b: the assumed capacity rule in which export counts against the peak (fleet.OBL)
BASIS = ("Each reason compares numbers the 09:00 plan itself used. The plan is solved for the whole day at once, so one "
         "half-hour can also depend on the others; hover a row for the full sentence.")
KEEP_NOTE = ("What one more MWh still in the battery at 08:00 tomorrow is expected to earn later, estimated from past price "
             "patterns. Selling and buying today are judged against it.")
MARGIN_NOTE = "Taken off every MWh sold: battery wear, plus a safety margin in case the price forecast is wrong."
SALE_NOTE = "What selling one MWh leaves: the forecast price less wear and the safety margin."
STORED_NOTE = "What each MWh that reaches the battery costs: the forecast price divided by the charging efficiency."
UP_NOTE = "Ready to send more power into the grid at short notice (discharge more, or charge less)."
DOWN_NOTE = "Ready to send less power into the grid at short notice (discharge less, or charge more)."
PAY_NOTE = "Forecast payment for this half-hour's standby, at the plan's forecast grid-service prices."


def _half_up(v, fmt: str):
    """v rounded to fmt's decimals with halves away from zero, as the page's toLocaleString rounds (Python's format
    rounds an exact half to even: A$35,174.50 would read A$35,174 here and A$35,175 on the page)."""
    m = re.search(r"\.(\d+)([f%])", fmt)
    if not m or isinstance(v, (int, np.integer)):
        return v
    d = int(m.group(1)) + (2 if m.group(2) == "%" else 0)
    return float(Decimal(float(v)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))


class Facts:
    """The numbers a text uses, each formatted once: text -> [(what it is, value)]."""

    def __init__(self):
        self.used: dict[str, list] = {}

    def __call__(self, name: str, value: float, fmt: str = "{:,.0f}") -> str:
        text = fmt.format(_half_up(abs(value), fmt))
        self.used.setdefault(text, []).append((name, float(value)))
        return ("−" if value < 0 and text.strip("0.,") else "") + text

    def aud(self, name: str, v: float, fmt: str = "{:,.0f}") -> str:
        s = self(name, v, fmt)
        return f"−A${s[1:]}" if s.startswith("−") else f"A${s}"

    def price(self, name: str, v: float) -> str:
        return f"{self.aud(name, v)}/MWh"

    def mw(self, name: str, v: float) -> str:
        return f"{self(name, v, '{:.1f}')} MW"

    def mwh(self, name: str, v: float) -> str:
        return f"{self(name, v, '{:.1f}')} MWh"

    def pct(self, name: str, v: float) -> str:
        return self(name, v, "{:.0%}")


def unaccounted(lines, facts: Facts) -> list[str]:
    """Numbers in the text that no fact produced (empty when every number is accounted for)."""
    bad = []
    for line in lines:
        if not line:
            continue
        for tok in NUMBER.findall(SKIP.sub(" ", line)):
            if tok.lstrip("+-−") not in facts.used:
                bad.append(f"{tok!r} in: {line}")
    return bad


def _strings(x) -> list[str]:
    """Every string inside a point, a list or a dict (what unaccounted() must see)."""
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strings(v)]
    if isinstance(x, (list, tuple)):
        return [s for v in x for s in _strings(v)]
    return []


def V(v: str, label: str, note: str | None = None) -> dict:
    """A number a reason shows, with its short label."""
    return {"v": v, "l": label, **({"note": note} if note else {})}


def OP(o: str) -> dict:
    return {"op": o}


def point(text: str, why: str, nums=(), say: str | None = None) -> dict:
    return {"say": say, "why": why, "nums": list(nums), "text": text}


def _lt(a: float, b: float) -> str:
    return "<" if a < b else "≤"


def _gt(a: float, b: float) -> str:
    return ">" if a > b else "≥"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _runs(action: list) -> list[tuple[int, int, str]]:
    """Runs of one action from 09:00, as (first half-hour, end, action); the Plan page's segments."""
    out, k = [], FIRST
    while k < N:
        e = k
        while e + 1 < N and action[e + 1] == action[k]:
            e += 1
        out.append((k, e + 1, action[k]))
        k = e + 1
    return out


def _span(a: int, b: int) -> str:
    return f"{clock(a)}–{clock(b)}"


# ---------------------------------------------------------------------------------------------------- storage: plan
class _Plan:
    """One adopted plan's numbers, indexed from its first half-hour t0 (j = half-hour - t0): the 09:00 plan (t0 =
    FIRST), or a later re-plan. action and action_cap are aligned with the plan's arrays."""

    def __init__(self, r: dict, rec: dict, f: dict, hard: np.ndarray, site: dict, action: list, action_cap: list,
                 flagged: bool | None, t0: int = FIRST):
        sc, bat = r["sc"], r["bat"]
        self.F = Facts()
        self.t0 = t0
        self.rest = "of the day" if t0 == FIRST else "left today"  # a re-plan sees only the rest of the day
        self.net, self.soc = np.asarray(rec["net"], float), np.asarray(rec["soc"], float)
        self.soc0 = np.r_[rec["stored"], self.soc[:-1]]
        self.p = np.asarray(rec["prices"], float)
        self.terminal = np.atleast_1d(np.asarray(rec["terminal"], float))
        self.f, self.hard, self.site = f, hard, site
        self.fp = {k: np.asarray(v, float) for k, v in (rec.get("fcess_prices") or {}).items()}
        self.action, self.cap = action, action_cap
        self.P, self.E, self.eta, self.rt = bat.power_mw, bat.energy_mwh, bat.eta, bat.round_trip
        self.dw = sc.asset.wear_per_mwh + sc.prefs.hurdle_per_mwh
        self.wear, self.hurdle = sc.asset.wear_per_mwh, sc.prefs.hurdle_per_mwh
        self.tau = sc.asset.limits.fcess_sustain_h
        self.util = sc.route.reg_util
        self.cmd_mw = sc.route.cmd_mw if sc.site is not None else None
        self.pen = dict(zip(rec.get("cap_names") or (), rec.get("cap_penalties") or ()))
        floors = [np.asarray(x, float) for n, x in zip(rec.get("soft_names") or (), rec.get("soft_floors") or ())
                  if n.startswith("site reserve") or n == "peak-day reserve"]
        self.site_need = max((float(x[-1]) for x in floors), default=0.0)  # stored MWh the factory needs at 08:00 tomorrow
        self.site_ircr = float(rec.get("site_ircr_mwh") or 0.0)
        self.site_value = float(rec.get("site_cmd_value") or 0.0)
        self.flagged = flagged
        self.n = len(self.net)
        self.lo, self.hi = np.quantile(self.p, [0.25, 0.75])
        # one value of energy kept, used in every comparison of the day: the next MWh at the planned level at 08:00
        # tomorrow ($ per MWh stored). A MWh sold uses 1/eta stored, so a sale is said to beat keeping only when it nets
        # at least v_end / eta, and keeping to beat a sale only when the sale nets less than v_end.
        self.v_end = self.keep_worth(self.soc[-1])

    # --- helpers
    def keep_worth(self, mwh: float) -> float:
        """$ per MWh stored of the next MWh kept at 08:00 tomorrow, by the plan's end-of-day value."""
        c = self.terminal
        return float(c[0]) if len(c) == 1 else float(c[min(len(c) - 1, max(0, int(np.floor(mwh + 1e-9))))])

    def rank(self, j: int) -> tuple[str, str]:
        """(in a sentence, as a short tag) where the half-hour's forecast sits in the day's."""
        p = self.p[j]
        if p >= self.p.max() - 0.5:
            return f"the highest forecast {self.rest}", "today's high"
        if p <= self.p.min() + 0.5:
            return f"the lowest forecast {self.rest}", "today's low"
        if p >= self.hi:
            return f"among the highest quarter of the forecasts {self.rest}", "high for today"
        if p <= self.lo:
            return f"among the lowest quarter of the forecasts {self.rest}", "low for today"
        return "a middling price for today", "typical"

    def later(self, j: int, sign: int):
        """The first later half-hour of discharge (sign 1) or charge (-1), and the best forecast price among them."""
        idx = [i for i in range(j + 1, self.n) if sign * self.net[i] > EPS_MW and self.action[i] != "shave"]
        if not idx:
            return None
        best = max(idx, key=lambda i: self.p[i]) if sign > 0 else min(idx, key=lambda i: self.p[i])
        return idx[0], best

    def raise_mw(self, j: int) -> float:
        return float(self.f["reg_raise"][j] + self.f["cont_raise"][j])

    def lower_mw(self, j: int) -> float:
        return float(self.f["reg_lower"][j] + self.f["cont_lower"][j])

    def kept(self) -> dict:
        return V(self.F.price("keep value", self.v_end), "if kept for tomorrow", KEEP_NOTE)

    def kept_text(self) -> str:
        return f"the {self.F.price('keep value', self.v_end)} it is worth if kept until 08:00 tomorrow"

    def margin(self) -> dict:
        return V(self.F.price("wear and margin", self.dw), "wear + safety margin", MARGIN_NOTE)

    def forecast(self, j: int) -> dict:
        return V(self.F.price("forecast price", self.p[j]), "forecast")

    def sale(self, j: int) -> list:
        """What selling one MWh leaves, worked from the forecast: price − wear and margin = what is left."""
        return [self.forecast(j), OP("−"), self.margin(), OP("="),
                V(self.F.price("net per MWh sold", self.p[j] - self.dw), "left from a sale", SALE_NOTE)]

    def sale_text(self, j: int) -> str:
        F = self.F
        return (f"at the forecast {F.price('forecast price', self.p[j])}, a sale leaves "
                f"{F.price('net per MWh sold', self.p[j] - self.dw)} after {F.price('wear and margin', self.dw)} for "
                "battery wear and a safety margin")

    def stored(self, j: int) -> list:
        """What one MWh in the battery costs, worked from the forecast: price ÷ charging efficiency = cost once stored."""
        F = self.F
        loss = F.pct("charging loss", 1 - self.eta)
        return [self.forecast(j), OP("÷"),
                V(F("charging efficiency", self.eta, "{:.1%}"), "charging efficiency",
                  f"About {loss} of the energy bought is lost when charging."),
                OP("="), V(F.price("cost per MWh stored", self.p[j] / self.eta), "once stored", STORED_NOTE)]

    def stored_text(self, j: int) -> str:
        F = self.F
        return (f"about {F.pct('charging loss', 1 - self.eta)} of the energy is lost when charging, so at the forecast "
                f"{F.price('forecast price', self.p[j])} each MWh stored costs "
                f"{F.price('cost per MWh stored', self.p[j] / self.eta)}")

    # --- one half-hour: (reason key, lead sentence or None, points)
    def reason(self, j: int) -> tuple[str, str | None, list]:
        a, mw = self.action[j], float(self.net[j])
        if a == "shave":
            return self._shave(j, mw)
        if a == "discharge":
            return self._discharge(j, mw)
        if a == "charge":
            return self._charge(j, -mw)
        return self._hold(j)

    def _lead(self, verb: str, j: int, mw: float) -> str:
        F = self.F
        return f"{verb} {F.mw(verb + ' MW', mw)} at a forecast {F.price('forecast price', self.p[j])}, {self.rank(j)[0]}."

    def _shave(self, j: int, mw: float):
        F, cap = self.F, self.cap[j]
        cap_mw = float(self.site["caps"][cap][j] + self.site["lfe"][j])
        if cap == "ircr":
            imp, val = F.mw("IRCR cap", cap_mw), F.aud("IRCR value per MW", self.pen.get("ircr", np.nan))
            note = ("The yearly capacity charge (IRCR) per MW of peak demand, shared over the days the rule flags in a "
                    "typical season.")
            return "shave ircr", None, [point(
                f"Today is flagged as a likely capacity peak day, so it discharges {F.mw('shave MW', mw)} to hold the "
                f"factory's grid import to {imp} in the evening peak window. The plan values each MW kept off the grid "
                f"then at {val}: the yearly capacity charge (IRCR) per MW, shared over the days the rule flags in a "
                "typical season.",
                "Likely capacity peak day: keeps the factory off the grid in the evening peak",
                [V(imp, "grid import"), V(val, "saved per MW kept off", note)])]
        charge = F.aud("CMD charge per MW", self.pen.get("cmd", np.nan))
        note = "Added to the month's network bill for each MW the month's highest import goes above the limit."
        if self.cmd_mw is not None and abs(cap_mw - self.cmd_mw) < 0.05:
            lim = F("CMD MW", self.cmd_mw, "{:g}")
            text = f"its {lim} MW import contract (CMD)"
            why, nums = "Keeps the factory's import at its contract limit (CMD)", [V(f"{lim} MW", "import limit")]
        else:
            high = F.mw("month high MW", cap_mw)
            text = f"{high}, the most it has drawn so far this month"
            why, nums = "Keeps the factory's import at this month's highest so far", [V(high, "month high")]
        return "shave cmd", None, [point(
            f"Discharges {F.mw('shave MW', mw)} so the factory's grid import stays at {text}. Each MW above that adds "
            f"about {charge} to the month's network bill.", why, nums + [V(charge, "added per MW over", note)])]

    def _discharge(self, j: int, mw: float):
        F, p = self.F, self.p[j]
        lead = self._lead("Sells", j, mw)
        net_sale = p - self.dw
        refill = self.later(j, -1)
        pts, key = [], "discharge"
        if refill is not None and net_sale >= self.p[refill[1]] / self.rt:
            c, t = self.p[refill[1]], clock(self.t0 + refill[1])
            back = F.price("refill per MWh sold", c / self.rt)
            kept = F.pct("round trip", self.rt)
            pts.append(point(
                f"{_cap(self.sale_text(j))}. Buying the energy back at {t} (forecast "
                f"{F.price('refill price', c)}) costs {back} per MWh sold, since only {kept} of what is bought comes "
                "back out.",
                f"It sells now and buys the energy back cheaper at {t}",
                [*self.sale(j), OP(_gt(net_sale, c / self.rt)),
                 V(back, f"to buy back at {t}", f"The forecast price at {t} ({F.price('refill price', c)}) divided by "
                                                f"the {kept} left after charging and discharging.")]))
            key = "discharge refill"
        elif net_sale >= self.v_end / self.eta:
            pts.append(point(f"{_cap(self.sale_text(j))}, more than {self.kept_text()}.",
                             "Selling now earns more than keeping the energy for tomorrow",
                             [*self.sale(j), OP(">"), self.kept()]))
            key = "discharge keep"
        up = self.raise_mw(j)
        if mw < self.P - 0.05 and up > 0.05:
            sb = F.mw("raise standby", min(up, self.P - mw))
            pts.append(point(f"{sb} of its power stays ready for grid services.",
                             "The rest of its power stays on grid-service standby", [V(sb, "standby")]))
        return key, lead, pts

    def _charge(self, j: int, mw: float):
        F, p = self.F, self.p[j]
        lead = self._lead("Buys", j, mw)
        cost = p / self.eta  # per MWh stored
        backing = self.hard[j] + self.raise_mw(j) * self.tau / self.eta
        sell = self.later(j, 1)
        if self.hard[j] > self.soc0[j] + EPS_MWH:
            fl = F.mwh("mandatory floor", self.hard[j])
            return "charge floor", lead, [point(f"It refills the {fl} it must always keep stored.",
                                                "It refills the minimum it must always keep stored",
                                                [V(fl, "minimum", "A hard rule: stored energy never goes below this.")])]
        if self.site_need > self.soc0[j] + EPS_MWH:
            peak = " and through tomorrow's likely peak hours (IRCR)" if self.site_ircr > EPS_MWH else ""
            need, short = F.mwh("site need at 08:00", self.site_need), F.aud("site value per MWh short", self.site_value)
            st = F.aud("cost per MWh stored", cost)
            return "charge site", lead, [point(
                f"The factory needs {need} stored at 08:00 tomorrow to keep its grid import under its contract limit "
                f"(CMD){peak}. Each MWh short would cost about {short} in extra charges; storing one now costs {st}.",
                "The factory needs this energy stored by 08:00 tomorrow",
                [V(need, "needed by 08:00"),
                 V(short, "cost per MWh short", "Extra network or capacity charges tomorrow for each MWh the factory's "
                                                "reserve is short."),
                 OP(">"), V(st, "per MWh stored", STORED_NOTE)])]
        if backing > self.soc0[j] + EPS_MWH:
            up, mins = F.mw("raise standby", self.raise_mw(j)), F("sustain minutes", self.tau * 60)
            return "charge backing", lead, [point(
                f"It must hold enough energy to deliver the {up} it has on standby for {mins} minutes if the grid calls "
                "on it.",
                "It keeps enough stored to deliver its grid-service standby if called",
                [V(up, "standby up", UP_NOTE), V(f"{mins} min", "must last")])]
        if sell is not None and self.p[sell[1]] - self.dw >= p / self.rt:
            q, t = self.p[sell[1]], clock(self.t0 + sell[0])
            even = F.price("break-even sale", p / self.rt + self.dw)
            how = (f"the {F.price('forecast price', p)} price now ÷ the {F.pct('round trip', self.rt)} left after "
                   f"charging and discharging, + {F.price('wear and margin', self.dw)} for wear and a safety margin")
            return "charge sell", lead, [point(
                f"It sells the energy again from {t} at up to {F.price('later sell price', q)}, above the {even} it "
                f"needs to break even: {how}.",
                f"It sells the energy again from {t} for more than it costs",
                [V(F.price("later sell price", q), f"sale from {t}"), OP(_gt(q - self.dw, p / self.rt)),
                 V(even, "break-even", f"{_cap(how)}.")])]
        if self.v_end >= cost:
            return "charge keep", lead, [point(
                f"{_cap(self.stored_text(j))}, less than {self.kept_text()}.",
                "Storing it costs less than it is worth tomorrow",
                [*self.stored(j), OP(_lt(cost, self.v_end)), self.kept()])]
        drawn = float(self.util[0] * self.f["reg_raise"][j + 1:].sum() * 0.5)
        if drawn > 0.5:
            d = F.mwh("regulation draw", drawn)
            return "charge regulation", lead, [point(
                f"The grid is expected to draw {d} from it later today through its regulation standby.",
                "The grid is expected to draw on its standby later today", [V(d, "expected draw")])]
        return "charge", lead, []

    def _hold(self, j: int):
        F, p, pts = self.F, self.p[j], []
        full = self.soc0[j] >= self.E - EPS_MWH
        floor = self.hard[j] + self.raise_mw(j) * self.tau / self.eta
        key = "hold"
        if self.soc0[j] <= EPS_MWH:
            pts.append(point("Not selling: the battery is empty.", "the battery is empty", say="Not selling"))
            key = "hold empty"
        elif self.site_need > self.soc0[j] + EPS_MWH:
            need, now = F.mwh("site need at 08:00", self.site_need), F.mwh("stored", self.soc0[j])
            pts.append(point(f"Not selling: the factory needs {need} stored at 08:00 tomorrow to keep its grid import "
                             f"under its limits, and only {now} is stored now.",
                             "the factory needs more energy stored by 08:00 tomorrow",
                             [V(now, "stored now"), OP("<"), V(need, "needed by 08:00")], say="Not selling"))
            key = "hold site"
        elif floor > EPS_MWH and self.soc0[j] <= floor + EPS_MWH:
            now, up = F.mwh("stored", self.soc0[j]), F.mw("raise standby", self.raise_mw(j))
            pts.append(point(f"Not selling: the {now} stored is needed in case the grid calls on the {up} it has on "
                             "standby.",
                             "the stored energy must stay in case the grid calls on its standby",
                             [V(now, "stored"), V(up, "standby up", UP_NOTE)], say="Not selling"))
            key = "hold backing"
        else:
            sell = self.later(j, 1)
            if sell is not None and self.p[sell[1]] > p + 0.5:
                t, q = clock(self.t0 + sell[1]), F.price("later sell price", self.p[sell[1]])
                pts.append(point(f"Not selling: the forecast at {t} is higher ({q}), so it saves the energy for then.",
                                 f"it saves the energy to sell at {t}, when the price is higher",
                                 [self.forecast(j), OP("<"), V(q, f"at {t}")], say="Not selling"))
                key = "hold later"
            elif p <= self.dw:
                pts.append(point(f"Not selling: the forecast {F.price('forecast price', p)} does not cover the "
                                 f"{F.price('wear and margin', self.dw)} taken off each MWh sold for battery wear and a "
                                 "safety margin.",
                                 "the price is too low to cover battery wear and a safety margin",
                                 [self.forecast(j), OP(_lt(p, self.dw)), self.margin()], say="Not selling"))
                key = "hold cost"
            elif p - self.dw < self.v_end:
                pts.append(point(f"Not selling: {self.sale_text(j)}, less than {self.kept_text()}.",
                                 "selling now earns less than keeping the energy for tomorrow",
                                 [*self.sale(j), OP("<"), self.kept()], say="Not selling"))
                key = "hold keep"
        if full:
            e = F("energy MWh", self.E, "{:g}")
            pts.append(point(f"Not buying: it is full at {e} MWh.", "the battery is full", [V(f"{e} MWh", "full")],
                             say="Not buying"))
            key += " full"
        else:
            buy = self.later(j, -1)
            if buy is not None and self.p[buy[1]] < p - 0.5:
                t, c = clock(self.t0 + buy[1]), F.price("later buy price", self.p[buy[1]])
                pts.append(point(f"Not buying: the forecast at {t} is lower ({c}), so it buys then.",
                                 f"it buys at {t} instead, when the price is lower",
                                 [self.forecast(j), OP(">"), V(c, f"at {t}")], say="Not buying"))
                key += " later"
            elif p / self.eta > self.v_end:
                pts.append(point(f"Not buying: {self.stored_text(j)}, more than {self.kept_text()}.",
                                 "some energy is lost when charging, so storing it costs more than it is worth tomorrow",
                                 [*self.stored(j), OP(">"), self.kept()], say="Not buying"))
                key += " dear"
        drop = self.soc0[j] - self.soc[j]
        if drop > EPS_MWH:
            d = F.mwh("regulation drop", drop)
            pts.append(point(f"Stored energy still falls {d} as the grid draws on its regulation standby.",
                             "the grid draws on its regulation standby", [V(d, "used")], say="Stored energy falls"))
        return key, None, pts

    def grid_services(self, j: int) -> dict | None:
        F = self.F
        up, down = self.raise_mw(j), self.lower_mw(j)
        if up + down <= 0.05:
            return None
        aud = sum(self.f[k][j] * self.fp[k][j] for k in self.fp) * 0.5 if self.fp else np.nan
        nums = ([V(F.mw("raise standby", up), "up", UP_NOTE)] if up > 0.05 else []) + \
               ([V(F.mw("lower standby", down), "down", DOWN_NOTE)] if down > 0.05 else [])
        parts = " or ".join(f"{x['l']} by {x['v']}" for x in nums)
        if np.isfinite(aud) and aud >= 0.5:
            a = F.aud("grid services this half-hour", aud)
            nums.append(V(a, "expected pay", PAY_NOTE))
            earn = f" The standby is expected to pay {a} this half-hour."
        else:
            earn = " The forecast pay is close to zero." if np.isfinite(aud) else ""
        return point(f"Grid services: AEMO pays it to stand by, ready to push its power {parts} at short notice if the "
                     f"grid needs it.{earn}", "AEMO pays it to stand by, ready to push its power up or down if the grid "
                     "needs it", nums, say="Grid services")

    def price_tag(self, a: int, b: int) -> str:
        """The forecast over half-hours a..b-1, as one value or a range (A$/MWh)."""
        F = self.F
        lo, hi = float(self.p[a - self.t0:b - self.t0].min()), float(self.p[a - self.t0:b - self.t0].max())
        x, y = F.aud("forecast low", lo), F.aud("forecast high", hi)
        return x if x == y else f"{x}–{F('forecast high', hi)}" if lo >= 0 else f"{x} to {y}"


def plan(r: dict, rec: dict, f: dict, hard: np.ndarray, site: dict, action: list, action_cap: list,
         value: dict, flagged: bool | None) -> dict:
    """The reasons of the 09:00 plan:
      halfhours   48 entries (None before 09:00): the forecast and where it sits in the day, the lead sentence, the
                  points, the grid-service point, and the whole sentence (also by_halfhour)
      segments    each run of one action, split into stretches with one reason (each worded at its half-hour with the
                  most power, the first when all hold)
      yardsticks  the numbers most reasons are weighed against
      day         a few sentences on the day; unaccounted lists numbers no fact produced (empty when the text is sound)"""
    x = _Plan(r, rec, f, hard, site, action[FIRST:], action_cap[FIRST:], flagged)
    F = x.F
    pre = "Ran under the 08:00 plan; the 09:00 plan starts at 09:00."
    by_hh: list = [pre] * FIRST
    halfhours: list = [None] * FIRST
    keys = []
    for j in range(x.n):
        key, lead, pts = x.reason(j)
        keys.append(key)
        g = x.grid_services(j)
        text = " ".join(([lead] if lead else []) + [p["text"] for p in pts] + ([g["text"]] if g else []))
        by_hh.append(text)
        halfhours.append({"price": F.price("forecast price", x.p[j]), "rank": x.rank(j)[1], "lead": lead,
                          "points": pts, "grid": g, "text": text})
    segments = []
    for a, b, act in _runs(action):
        stretches, k = [], a
        while k < b:
            e = k
            while e + 1 < b and keys[e + 1 - FIRST] == keys[k - FIRST]:
                e += 1
            rep = max(range(k, e + 1), key=lambda i: abs(x.net[i - FIRST]))
            _, lead, pts = x.reason(rep - FIRST)
            stretches.append({"from": k, "to": e + 1, "at": rep, "price": x.price_tag(k, e + 1), "points": pts,
                              "mw": num(max(abs(float(x.net[i - FIRST])) for i in range(k, e + 1)), 3),
                              "text": " ".join(([lead] if lead else []) + [p["text"] for p in pts])})
            k = e + 1
        mw = max(abs(float(x.net[i - FIRST])) for i in range(a, b))
        text = " ".join((f"{_span(s['from'], s['to'])}: " if len(stretches) > 1 else "") + s["text"] for s in stretches)
        segments.append({"from": a, "to": b, "action": act, "mw": num(mw, 3), "text": text, "stretches": stretches})
    jl, jh = int(np.argmin(x.p)), int(np.argmax(x.p))
    start, end = F.mwh("stored at 09:00", rec["stored"]), F.mwh("stored at 08:00 tomorrow", float(x.soc[-1]))
    worth = F.aud("stored value", value.get("stored_end") or 0.0)
    day = [f"At 09:00 the battery holds {start} of {F('energy MWh', x.E, '{:g}')} MWh. The 09:00 forecast runs from "
           f"{F.price('day low', x.p[jl])} at {clock(FIRST + jl)} to {F.price('day high', x.p[jh])} at {clock(FIRST + jh)}."]
    if flagged:
        day.append("Today is flagged as a likely capacity peak day (IRCR), so the factory's import is kept low in the "
                   "evening peak window.")
    day.append(f"It plans to have {end} left at 08:00 tomorrow, valued at {worth} for later use. Each MWh kept until "
               f"then is worth {F.price('keep value', x.v_end)}: selling or buying today is judged against that.")
    fc = value.get("fcess") or 0.0
    if fc >= 0.5:
        day.append(f"Grid services are forecast to earn {F.aud('grid services', fc)}: AEMO pays the battery to stand by, "
                   "so power held for them is not sold as energy.")
    yard = [V(F.price("keep value", x.v_end), "if kept for tomorrow", KEEP_NOTE),
            V(F.price("wear and margin", x.dw), "wear + safety margin",
              f"Taken off every MWh sold: {F.aud('wear', x.wear)} for battery wear plus a {F.aud('hurdle', x.hurdle)} "
              "safety margin in case the price forecast is wrong."),
            V(F.pct("round-trip loss", 1 - x.rt), "lost charging + discharging",
              "Energy lost between buying a MWh and selling it again.")]
    if "cmd" in x.pen and x.cmd_mw is not None:
        yard.append(V(F.aud("CMD charge per MW", x.pen["cmd"]), "per MW over CMD",
                      f"Added to the month's network bill for each MW the month's highest import goes above the "
                      f"{F('CMD MW', x.cmd_mw, '{:g}')} MW import contract (CMD)."))
    if flagged and "ircr" in x.pen:
        yard.append(V(F.aud("IRCR value per MW", x.pen["ircr"]), "per MW off at peak",
                      "The yearly capacity charge (IRCR) per MW, shared over the days the rule flags in a typical season."))
    if x.site_need > EPS_MWH:
        yard.append(V(F.aud("site value per MWh short", x.site_value), "per MWh reserve short",
                      f"Extra charges tomorrow for each MWh the factory's reserve is short; it needs "
                      f"{F.mwh('site need at 08:00', x.site_need)} stored at 08:00 tomorrow."))
    out = {"by_halfhour": by_hh, "halfhours": halfhours, "segments": segments, "day": day, "yardsticks": yard,
           "start": start, "end": end, "end_value": worth, "flagged": bool(flagged), "basis": BASIS}
    out["unaccounted"] = unaccounted(_strings(out), F)
    return out


# ---------------------------------------------------------------------------------------------------- storage: outcome
def _desc(F: Facts, name: str, v: float) -> str:
    return "hold" if abs(v) < EPS_MW else f"{'discharge' if v > 0 else 'charge'} {F.mw(name, abs(v))}"


def decisions(r: dict, plans: list, pick: list, flagged: bool | None, upto: int, plan_mw: list) -> dict:
    """The decision in effect at the start of each half-hour from 09:00 until `upto` (on a live day, the half-hour in
    progress is the decision now): the plan adopted by then (e5 re-plans at the start of every half-hour; v1 keeps the
    09:00 plan until an offer sells), worded as the 09:00 plan's reasons are, from that plan's own numbers.
      plans    [(t0, kind, (rec, f, hard, site, action, action_cap))] as adopted
      pick     per half-hour, the index into plans of the plan in effect at its start (None before 09:00)
      plan_mw  the 09:00 plan's MW per half-hour, to say when a re-plan changed it
    Per half-hour (None outside 09:00..upto): head (when and what was decided), lead, points, grid, text."""
    made: dict = {}
    out: list = [None] * N
    bad: list = []
    for k in range(FIRST, upto):
        i = pick[k]
        if i is None:
            continue
        t0, kind, parts = plans[i]
        if i not in made:
            made[i] = _Plan(r, *parts, flagged, t0=t0)
        x = made[i]
        F, j = x.F, k - t0
        mw, mw0 = float(x.net[j]), float(plan_mw[k] or 0.0)
        now, then = _desc(F, "decided MW", mw), _desc(F, "planned MW", mw0)
        changed = abs(mw - mw0) > 0.1
        if t0 == FIRST and kind == "reveal":
            head = point(f"Decided at 09:00: {now}. This is the 09:00 plan; it was not re-planned before this half-hour.",
                         "the 09:00 plan above, not re-planned since", [V(now, "decided")], say="Decided 09:00")
            lead, pts, g = None, [], None
        else:
            at = clock(t0)
            how = "after an extra offer sold" if kind == "trigger" else f"at {at} with the latest prices"
            head = point(f"Decided: {now}, re-planned {how}." + (f" The 09:00 plan was {then}." if changed else
                                                                 " Same as the 09:00 plan."),
                         f"re-planned {how}" + ("; the 09:00 plan was different" if changed else
                                                ", same as the 09:00 plan"),
                         [V(now, "decided")] + ([OP("vs"), V(then, "09:00 plan")] if changed else []),
                         say=f"Decided {at}")
            _, lead, pts = x.reason(j)
            g = x.grid_services(j)
        d = {"at": clock(t0), "kind": kind, "is_0900": t0 == FIRST and kind == "reveal", "changed": changed,
             "action": x.action[j],
             "mw": num(mw, 3), "head": head, "lead": lead, "points": pts, "grid": g,
             "text": " ".join([head["text"]] + ([lead] if lead else []) + [p["text"] for p in pts] +
                              ([g["text"]] if g else []))}
        out[k] = d
        bad += unaccounted(_strings(d), F)
    return {"by_halfhour": out, "unaccounted": bad}


def outcome(plan_mw: list, out: dict, decided: list, dispatch_mw: np.ndarray, load_exp: list | None, energy_mwh: float,
            known: int = N) -> dict:
    """What happened in each half-hour against the decision in effect at its start (decisions()), from the replay: a
    point per half-hour (points) and its sentence (by_halfhour). The actual MW splits into the five-minute dispatch
    (dispatch_mw: the decision, an extra offer that sold, the factory's actual load against the meter's limits, a full
    or empty battery) and the energy regulation standby moved (the rest). load_exp: the expected factory load the
    deciding plan used (None on a standalone connection). known: the half-hours already over (a live day)."""
    F = Facts()
    pts = []
    act, cleared, sold = out["battery_mw"], out["offer_cleared_mw"], out["offer_sold_aud"]
    p5, live, soc = out["price_actual_max_5min"], out["offer_price_live"], out["soc_mwh"]
    load = (out.get("site") or {}).get("load_mw")

    for k in range(N):
        a, d = act[k] or 0.0, decided[k]
        dm, at = (d["mw"], d["at"]) if d else (plan_mw[k] or 0.0, "09:00")
        if k >= known:
            pts.append(point("Not over yet: AEMO has not published its prices.", "not over yet", say="Actual"))
        elif k < FIRST:
            pts.append(point("Ran under the 08:00 plan.", "ran under the 08:00 plan", say="Actual"))
        elif (cleared[k] or 0.0) > EPS_MW:
            spike, aud = F.price("max 5-min price", p5[k] or 0.0), F.aud("sold extra", sold[k] or 0.0)
            # a small sale keeps two decimals, so it never reads "0.0 MW"
            mw = F.mw("sold extra MW", cleared[k]) if cleared[k] >= 0.05 else \
                f"{F('sold extra MW', cleared[k], '{:.2f}')} MW"
            nums = [V(spike, "spike")]
            price = ""
            if live[k] is not None:
                o = F.price("live offer", live[k])
                nums += [OP(">"), V(o, "offer")]
                price = f" at {o}"
            pts.append(point(f"A five-minute price reached {spike}, so the extra offer{price} sold {mw} more than "
                             f"decided, for {aud}.", "a price spike reached its extra offer",
                             nums + [V(mw, "sold"), V(aud, "earned")], say="Actual"))
        elif abs(a - dm) <= 0.1:
            pts.append(point(f"Ran as decided at {at}.", f"ran as decided at {at}", say="Actual"))
        else:
            x, y = _desc(F, "actual MW", a), _desc(F, "decided MW", dm)
            nums, says, whys = [V(x, "actual"), OP("vs"), V(y, "decided")], [], []
            dev, reg = float(dispatch_mw[k]) - dm, a - float(dispatch_mw[k])
            if abs(dev) > 0.05:
                le, la = (load_exp[k] if load_exp else None), (load[k] if load else None)
                verb = (("discharged more" if dev > 0 else "discharged less") if dm > EPS_MW else
                        ("charged less" if dev > 0 else "charged more") if dm < -EPS_MW else
                        ("discharged" if dev > 0 else "charged"))
                if le is not None and la is not None and (la - le) * dev > 0 and abs(la - le) > 0.05:
                    more = "more" if la > le else "less"
                    says.append(f"the factory used {F.mw('factory load', la)}, {more} than the "
                                f"{F.mw('expected load', le)} expected, so the battery {verb} to keep the grid import "
                                "within its limits")
                    whys.append(f"the factory used {more} power than expected")
                    nums += [V(F.mw("factory load", la), "factory used"), V(F.mw("expected load", le), "expected")]
                elif dev > 0 and dm < -EPS_MW and (soc[k + 1] or 0.0) >= energy_mwh - EPS_MWH:
                    says.append("the battery was full")
                    whys.append("the battery was full")
                elif dev < 0 and dm > EPS_MW and (soc[k + 1] or 0.0) <= EPS_MWH:
                    says.append("the battery ran empty")
                    whys.append("the battery ran empty")
                else:
                    says.append(f"within the half-hour the battery {verb} for a reason this page does not break out")
                    whys.append("adjusted within the half-hour")
            if abs(reg) > 0.05:
                r_ = F.mw("regulation MW", abs(reg))
                says.append(f"its regulation standby {'sent out' if reg > 0 else 'took in'} {r_} on average when the "
                            "grid called on it")
                whys.append("the grid called on its regulation standby")
                nums.append(V(r_, "regulation", "Regulation standby keeps the grid's frequency steady by moving the "
                                                "battery's power up and down within the half-hour."))
            pts.append(point(f"Actual: {x}; decided at {at}: {y}. " + _cap("; ".join(says)) + ".",
                             "; ".join(whys), nums, say="Actual"))
    return {"by_halfhour": [p["text"] for p in pts], "points": pts, "unaccounted": unaccounted(_strings(pts), F)}


# ---------------------------------------------------------------------------------------------------- customer call
def call(c: dict) -> dict:
    """The reasons of one day's call decision, from Fleet.call_day's output: the model's verdict and the comparison
    behind it, how the threshold is set, why it matters; the operator's own decision and hindsight when there are."""
    F = Facts()
    m, d, fc, calls = c["model"], c["decision"], c["forecast"] or {}, c["calls"]
    model_say = f"Call {m['start']}–{m['end']}" if m["call"] else "No call"
    pts = []
    reason = m["reason"]
    thr_note = None
    if fc.get("threshold_mw") is not None and fc.get("fourth_highest_mw") is not None:
        kth, mg = F("kth", fc["fourth_highest_mw"]), F("margin", fc["margin_mw"])
        thr_note = point(f"The call threshold is the {F('rank', K_PEAK)}th-highest daily peak so far this season ({kth} MW) "
                         f"less {mg} MW for forecast error.",
                         "this season's 4th-highest daily peak so far, less a margin for forecast error",
                         [V(f"{kth} MW", "4th-highest"), OP("−"), V(f"{mg} MW", "margin",
                                                                       "Learnt from how far 09:00 forecasts fell short of "
                                                                       "actual peaks in the two seasons before.")],
                         say="Threshold")
        F("rank", K_PEAK)  # the "4th" in the phrase above
    matters = point("If today is one of the Hot Season's peak days, the customers' export in the evening lowers the demand "
                    "that sets Zenith's capacity charge (IRCR) for a whole year, under the assumed "
                    f"{F('assumed rule year', RULE_YEAR, '{:d}')} rule in which export counts against the peak.",
                    "if today is one of the season's peak days, customers' export lowers Zenith's capacity charge (IRCR) "
                    f"for a whole year (assumed {F('assumed rule year', RULE_YEAR, '{:d}')} rule)", say="Why it matters")
    if reason == "peak-day flag":
        if fc.get("first_days_auto"):
            k = F("rank", K_PEAK)
            pts.append(point(f"The Hot Season has just begun: fewer than {k} daily peaks are known, so the rule treats every "
                             "day as a possible peak day.", "the Hot Season has just begun, so every day counts as a "
                             "possible peak day", [V(f"< {k}", "peaks known")], say=model_say))
        elif "same_day" in (fc.get("routes") or []) and fc.get("threshold_mw") is not None:
            pk, th = F("forecast peak", fc["peak_mw"]), F("threshold", fc["threshold_mw"])
            pts.append(point(f"AEMO's 09:00 forecast puts today's peak demand at {pk} MW, above the call threshold of "
                             f"{th} MW.", "AEMO's 09:00 forecast peak is above the call threshold",
                             [V(f"{pk} MW", "forecast peak"), OP(">"), V(f"{th} MW", "threshold")], say=model_say))
            pts.append(thr_note)
        elif fc.get("dayahead_mw") is not None and fc.get("dayahead_threshold_mw") is not None:
            pk, th = F("day-ahead peak", fc["dayahead_mw"]), F("day-ahead threshold", fc["dayahead_threshold_mw"])
            pts.append(point(f"Yesterday's 09:00 forecast put today's peak demand at {pk} MW, above that day's call "
                             f"threshold of {th} MW.", "yesterday's 09:00 forecast peak was above the call threshold",
                             [V(f"{pk} MW", "forecast peak"), OP(">"), V(f"{th} MW", "threshold")], say=model_say))
        else:
            pts.append(point("AEMO's forecast marks today as a likely peak day.", "AEMO's forecast marks today as a "
                             "likely peak day", say=model_say))
        pts.append(matters)
    elif reason == "not flagged":
        if fc.get("peak_mw") is not None and fc.get("threshold_mw") is not None:
            pk, th = F("forecast peak", fc["peak_mw"]), F("threshold", fc["threshold_mw"])
            pts.append(point(f"AEMO's 09:00 forecast puts today's peak demand at {pk} MW, below the call threshold of "
                             f"{th} MW.", "AEMO's 09:00 forecast peak is below the call threshold",
                             [V(f"{pk} MW", "forecast peak"), OP("<"), V(f"{th} MW", "threshold")], say=model_say))
            if thr_note:
                pts.append(thr_note)
        else:
            pts.append(point("There is no AEMO demand forecast at 09:00 for this day, so the rule cannot flag it.",
                             "no AEMO demand forecast at 09:00, so the rule cannot flag the day", say=model_say))
        pts.append(point("Calls are kept for likely peak days: on other days a call costs the payment and earns little "
                         "back (policy study, training period).",
                         "on other days a call costs the payment and earns little back (policy study, training period)",
                         say="Calls kept"))
    elif reason == "outside Hot Season":
        pts.append(point("Today is outside the Hot Season (December to March).",
                         "outside the Hot Season (December to March)", say=model_say))
        pts.append(point("The capacity charge is set only by that season's peak half-hours, and calls for energy alone "
                         "did not pay in the policy study.", "the capacity charge is set only in that season, and calls "
                         "for energy alone did not pay in the policy study", say="Why it matters"))
    elif reason == "cap reached":
        cap = F("cap", calls["cap"])
        pts.append(point(f"All {cap} calls of contract year {c['contract_year']} are used.",
                         f"all calls of contract year {c['contract_year']} are used", [V(f"{cap} of {cap}", "used")],
                         say=model_say))
    elif reason == "price margin":
        pts.append(point("At 09:00 the expected market margin of a call passes the rule's minimum, and enough calls remain "
                         "for the rest of the Hot Season.", "the expected market margin passes the rule's minimum",
                         say=model_say))
    if pts:  # the sentence opens with the verdict, as the layout does with `say`
        pts[0]["text"] = f"{model_say}. {pts[0]['text']}"
    operator = None
    if d["source"] == "operator":
        mine = f"call {d['start']}–{d['end']}" if d["call"] else "no call"
        operator = f"Your decision: {mine}. The model's rule says {model_say.lower()}."
    hind = None
    h = c.get("hindsight")
    if h and c.get("hot_season") and h.get("actual_peak_mw") is not None:
        ap = F("actual peak", h["actual_peak_mw"])
        if h.get("official_peak_day"):
            hind = point(f"In hindsight: today's actual peak was {ap} MW and today was one of the season's peak days (peak "
                         f"half-hours at {', '.join(h['peak_intervals'])}).",
                         f"today was one of the season's peak days (peak half-hours at {', '.join(h['peak_intervals'])})",
                         [V(f"{ap} MW", "actual peak")], say="In hindsight")
        else:
            hind = point(f"In hindsight: today's actual peak was {ap} MW; today was not one of the season's peak days.",
                         "today was not one of the season's peak days", [V(f"{ap} MW", "actual peak")], say="In hindsight")
    lines = ([operator] if operator else []) + [p["text"] for p in pts] + ([hind["text"]] if hind else [])
    out = {"model_say": model_say, "operator": operator, "points": pts, "hindsight": hind, "lines": lines}
    out["unaccounted"] = unaccounted(_strings(out), F)
    return out
