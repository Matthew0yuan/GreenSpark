"""The five outputs of one decision (reports/v2_plan.md, step 7; the requirements' section 7), from its record:

  1 charging plan    each half-hour's charging, its expected price and cost, the layer it fills, and the most a MWh
                     stored is worth at 08:00 tomorrow (the price above which charging would not pay)
  2 reserve plan     now, 16:00 and 08:00 tomorrow: mandatory (①), site reserve (②, CMD and IRCR parts) and
                     opportunity energy (③), and what one more MWh is worth
  3 bidding plan     the layered offers of the next hours (with the offer rule "table"): the price of the first MWh,
                     of 5 and 10 MWh, the protected energy and its lowest price, whether the offer is already locked,
                     and how often the price has reached the first layer in that half-hour (uncalibrated)
  4 allocation       per half-hour: discharge to the factory and to the grid, charging, FCESS by service; stored
                     energy split into ①, FCESS raise backing, ②, ③ and the empty room
  5 money and risk   the plan's expected money by source and, for the 09:00 plan, the money of the same actions in
                     the day's 20 price scenarios (without the real-time offers)
Every number comes from the record and the day's inputs; vpp.v2.explain words them.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from vpp import dispatch as dp
from vpp.v2 import bids as bd

SITE_FLOORS = ("site reserve: IRCR hours", "site reserve: CMD and IRCR", "peak-day reserve")
HOURS_AHEAD = 4  # the bidding plan's horizon


def clock(slot: int) -> str:
    """HH:MM of the start of a trading interval (slot 48: the next 08:00)."""
    return "次日 08:00" if slot >= dp.N else (pd.Timestamp("2000-01-01 08:00") + pd.Timedelta(minutes=30 * slot)).strftime("%H:%M")


@dataclass
class Snapshot:
    """One decision: its record (vpp.v2.record), the battery, the day's inputs it used."""
    rec: dict
    bat: object                     # vpp.config.Battery as the dispatcher sees it
    load_margin: float              # plans assume load_fc x (1 + load_margin)
    sustain_h: float                # FCESS raise must be backed for this long
    wear: float                     # $ per MWh discharged (settled)
    alpha: float = 1.2              # the offers' margin on what keeping energy is worth
    dw: float = 105.0               # $ per MWh discharged that decisions charge themselves (wear + hurdle)
    k_min: float = 150.0            # the lowest offer price
    bids: bd.LadderBids | None = None
    ladder: bool = False            # the configuration offers in layers (bids may be absent outside a replay)
    offer_lag: int = 1
    scen: dict | None = None        # the day's price scenarios (vpp.stochastic.attach)
    p5_history: tuple | None = None  # (relative 5-min maxima per day x slot, levels) for the fill frequencies

    @property
    def t0(self) -> int:
        return int(self.rec["t0"])

    def arr(self, key: str) -> np.ndarray:
        x = self.rec.get(key)
        return np.zeros(dp.N - self.t0) if x is None else np.asarray(x, float)

    def soc_before(self) -> np.ndarray:
        return np.r_[self.rec["stored"], self.arr("soc")[:-1]]

    def site_floor(self) -> np.ndarray:
        """The stored MWh the site floors ask for at the end of each interval (the highest of them)."""
        out = np.zeros(dp.N - self.t0)
        for name, f in zip(self.rec["soft_names"], self.rec["soft_floors"]):
            if name in SITE_FLOORS:
                out = np.maximum(out, np.asarray(f, float))
        return out

    def curve_value(self, mwh: float) -> float:
        """$ per MWh of the next MWh stored at 08:00 tomorrow, by the plan's end-of-day value."""
        c = np.atleast_1d(self.arr("terminal"))
        if len(c) == 1:
            return float(c[0])
        return float(c[min(len(c) - 1, max(0, int(np.floor(mwh + 1e-9))))])


def charging_plan(s: Snapshot) -> pd.DataFrame:
    net, soc0, hard, site = s.arr("net"), s.soc_before(), s.arr("hard"), s.site_floor()
    rows = []
    for k in np.flatnonzero(net < -1e-6):
        grid = -net[k] * dp.DT
        layer = "① 守约" if soc0[k] < hard[k] - 1e-6 else "② 工厂备电" if soc0[k] < max(site[k], hard[k]) - 1e-6 else "③ 机会电"
        after = soc0[k] + grid * s.bat.eta
        rows.append({"slot": s.t0 + k, "time": clock(s.t0 + k), "charge_mw": -net[k], "grid_mwh": grid,
                     "stored_mwh": grid * s.bat.eta, "price": s.arr("prices")[k], "cost": s.arr("prices")[k] * grid,
                     "layer": layer, "stored_after": after,
                     "worth_at_0800": s.curve_value(after), "max_price": s.curve_value(after) * s.bat.eta})
    return pd.DataFrame(rows)


def reserve_plan(s: Snapshot) -> pd.DataFrame:
    soc, hard, site = s.arr("soc"), s.arr("hard"), s.site_floor()
    rec = s.rec
    rows = []
    for label, slot in (("现在", s.t0), ("16:00", 16), ("次日 08:00", dp.N)):
        k = slot - 1 - s.t0  # the end of the interval before
        if k < -1 or slot < s.t0:
            continue
        stored = rec["stored"] if k < 0 else soc[k]
        m = 0.0 if k < 0 else hard[k]
        two = 0.0 if k < 0 else max(0.0, max(site[k], m) - m)
        rows.append({"time": label, "stored": stored, "mandatory": m, "site": two,
                     "site_ircr": min(two, rec.get("site_ircr_mwh") or 0.0) if slot == dp.N else np.nan,
                     "opportunity": max(0.0, stored - m - two),
                     "next_mwh_worth": s.curve_value(stored) if slot == dp.N else np.nan})
    return pd.DataFrame(rows)


def fill_frequency(s: Snapshot, slot: int, price: float) -> float:
    """How often, over the 365 days of history kept, a 5-min price in this half-hour reached `price` (relative to
    each day's level, at today's level). Uncalibrated, for reference."""
    if s.p5_history is None or s.bids is None:
        return np.nan
    rel_max, _ = s.p5_history
    return float(np.mean(rel_max[:, slot] >= price - s.bids.level))


def bidding_plan(s: Snapshot) -> pd.DataFrame:
    if s.bids is None:
        return pd.DataFrame()
    eta, prices, soc0 = s.bat.eta, s.arr("prices"), s.soc_before()
    rows = []
    for k in range(min(len(prices), 2 * HOURS_AHEAD)):
        t = s.t0 + k
        seen = prices[k - 1] if k else s.rec["prices"][0]
        thr = s.bids.thresholds(t, float(seen), eta, s.alpha, s.dw, s.k_min)
        top = int(np.clip(np.ceil(soc0[k] - 1e-9) - 1, 0, len(thr) - 1))  # the segment of the first MWh sold

        def price_for(mwh: float) -> float:
            return float(thr[max(0, int(np.floor(soc0[k] - mwh + 1e-9)))]) if soc0[k] >= mwh else np.nan
        protect = float(s.bids.protect[t])
        rows.append({"slot": t, "time": clock(t), "stored": soc0[k], "first_mwh": float(thr[top]),
                     "for_5_mwh": price_for(5.0), "for_10_mwh": price_for(10.0), "protected_mwh": min(protect, soc0[k]),
                     "protected_from": float(thr[max(0, int(np.ceil(protect) - 1))]) if protect > 1e-9 else np.nan,
                     "locked": k * 6 < s.offer_lag, "first_fill_freq": fill_frequency(s, t, float(thr[top]))})
    return pd.DataFrame(rows)


def allocation(s: Snapshot) -> pd.DataFrame:
    net, soc, hard, site = s.arr("net"), s.arr("soc"), s.arr("hard"), s.site_floor()
    load = s.arr("load") / (1 + s.load_margin)  # expected load
    rr, cr, rl, cl = (s.arr(k) for k in dp.FCESS)
    out = pd.DataFrame({"slot": np.arange(s.t0, dp.N), "time": [clock(t) for t in range(s.t0, dp.N)]})
    dis = np.clip(net, 0.0, None)
    out["to_factory_mw"] = np.minimum(dis, load)
    out["to_grid_mw"] = dis - out["to_factory_mw"]
    out["charge_mw"] = np.clip(-net, 0.0, None)
    out["reg_raise"], out["cont_raise"], out["reg_lower"], out["cont_lower"] = rr, cr, rl, cl
    out["stored"] = soc
    out["mandatory"] = hard
    out["site"] = np.maximum(np.maximum(site, hard) - hard, 0.0)
    backing = (rr + cr) * s.sustain_h / s.bat.eta
    rest = np.maximum(soc - hard - out["site"], 0.0)
    out["fcess_backing"] = np.minimum(backing, rest)
    out["opportunity"] = rest - out["fcess_backing"]
    out["empty"] = s.bat.energy_mwh - soc
    num = out.columns.drop(["slot", "time"])
    out[num] = out[num].where(out[num].abs() >= 5e-4, 0.0)  # the solver's -0.0000x are zero
    return out


def money(s: Snapshot) -> dict:
    r = s.rec
    return {"energy": r["energy_value"], "fcess": r["fcess_value"], "wear": r["wear"], "stored": r["stored_value"],
            "money": r["money"], "penalty": r["penalty"]}


def risk(s: Snapshot) -> dict | None:
    """The plan's own actions in each price scenario (09:00 plans): cash to 08:00 tomorrow, stored energy at the
    plan's value. Without the real-time offers, so it is the plan's risk, not the day's."""
    if s.scen is None or s.t0 != s.scen["slot"]:
        return None
    net = s.arr("net")
    e = np.asarray(s.scen["energy"], float)[:, s.t0:]
    cash = (e * net).sum(axis=1) * dp.DT
    for k in dp.FCESS:
        cash = cash + (np.asarray(s.scen["fcess"][k], float)[:, s.t0:] * s.arr(k)).sum(axis=1) * dp.DT
    cash = cash - s.wear * np.clip(net, 0.0, None).sum() * dp.DT + s.rec["stored_value"]
    worst = np.sort(cash)[: max(1, int(np.ceil(0.05 * len(cash))))]
    return {"scenarios": len(cash), "mean": float(cash.mean()), "worst_5pct": float(worst.mean()),
            "best": float(cash.max())}
