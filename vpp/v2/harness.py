"""v2's day-by-day loop (reports/v2_plan.md, step 2): vpp.backtest._route_days, copied with its arithmetic in the
same order, plus v2's hooks:
  ② site reserve   tomorrow's site needs as stacked economic floors (vpp.v2.reserves), in place of v1's overnight
                   reserve for a flagged IRCR day
  value curve      the plan's end-of-day value and the layered offers from the relative value curve (vpp.v2.value,
                   vpp.v2.bids)
  records          every adopted plan and what it was made from (vpp.v2.record)
With every feature off (config.V1) it is v1's loop for forecast+offer(value), result for result (python -m
vpp.v2.checks --parity). Everything else - strategy, contracts, bills, ledger, the dispatcher - is v1's, called.
It reads the days and shared inputs the worker was given (vpp.backtest._DAYS, _AUX)."""

from dataclasses import replace

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp import dispatch as dp
from vpp import site_bill as sb
from vpp.v2 import bids as bd
from vpp.v2 import horizon as hz
from vpp.v2 import record as rc
from vpp.v2 import reserves as rv
from vpp.v2 import value as vl
from vpp.v2.config import V2Config

VALUE_INPUTS: dict = {}  # the relative value curve's per-day arrays by (level statistic, MWh), loaded on first use
FLOORS: dict = {}  # f5's cheap charging price per day by (CMD, load scale, load margin), made on first use


def charge_floors(cmd_mw: float, load_scale: float, margin: float) -> pd.Series:
    """Round 5, f5: each day's floor price for the buy side and the values (vpp.v2.bids.charge_floor)."""
    key = (cmd_mw, load_scale, margin)
    if key not in FLOORS:
        FLOORS[key] = bd.charge_floor(vl.prices(), cmd_mw, load_scale, margin)
    return FLOORS[key]


def value_inputs(cfg: V2Config, bat) -> dict:
    """The relative value curve's per-day arrays for cfg's level statistic, hurdle and the battery the dispatcher
    sees (built beforehand: python -m vpp.v2.value)."""
    key = (cfg.level_stat, bat.energy_mwh, bt.HURDLE if cfg.hurdle is None else cfg.hurdle, cfg.state, cfg.regime)
    if key not in VALUE_INPUTS:
        p = vl.path(*key)
        if not p.exists():
            raise FileNotFoundError(f"{p.name}: build it first (python -m vpp.v2.value)")
        VALUE_INPUTS[key] = vl.load(p)
    return VALUE_INPUTS[key]


D1_INPUTS: dict = {}  # the 09:00 day-ahead forecast by issue day, loaded on first use


def tomorrow_part(day, tomorrow: dict, after: dict | None, route, site, asset, prefs, mtd: dict, slots,
                  inputs: dict, ircr_value_mw: float, enuc: float, shift: bool) -> hz.Tomorrow | None:
    """Tomorrow's part of the plans made from 09:00 today (R3), or None without the day-ahead forecast. slots:
    tomorrow's flagged peak hours (None if it is not flagged); after: the day after tomorrow (its CMD needs shift the
    end value; its own flag is not public yet)."""
    if not D1_INPUTS:
        D1_INPUTS.update(hz.tomorrow_prices(hz.forecast_d1()))
    d1 = D1_INPUTS.get(pd.Timestamp(day).normalize())
    if d1 is None:
        return None
    bat = asset.battery
    expected = tomorrow["load_fc"] * site.load_scale  # economic caps are priced on the expected load (run_day)
    caps, extra = {}, {}  # each cap as the plan takes it: the most net charging it allows (cap - load), MW
    if route.cmd_mw is not None:
        caps["cmd"] = np.array([max(route.cmd_mw, mtd.get(m, 0.0)) for m in tomorrow["slot_month"]]) - expected
    if slots is not None:
        ircr = np.full(dp.N, np.inf)
        ircr[slots] = 0.0
        caps["ircr"] = ircr - expected
        extra["ircr"] = ("peak", ircr_value_mw)
    level = float(inputs["level"][inputs["index"][pd.Timestamp(day).normalize()]])
    curve = vl.curve_at(inputs, str(pd.Timestamp(day).to_period("M")), pd.Timestamp(tomorrow["day"]).dayofweek, level)
    q = 0.0
    if after is not None:
        q = rv.site_reserve(after, site, bat, prefs.load_margin, "plan", route.cmd_mw, mtd, None, ircr_value_mw,
                            enuc).total_mwh
    return hz.Tomorrow(d1[0], d1[1], tomorrow["load_fc"] * site.load_scale * (1 + prefs.load_margin), caps, extra,
                       vl.shifted(curve, q, shift), asset.limits.cycles_per_day * bat.energy_mwh)


def scenario(sc: bt.Scenario, cfg: V2Config) -> bt.Scenario:
    """The scenario as cfg runs it: with a usable-energy window the dispatcher sees a smaller battery, and the daily
    cycle limit is scaled so it still allows the full battery's cycle."""
    if cfg.usable_mwh is None:
        return sc
    battery, limits = sc.asset.battery, sc.asset.limits
    asset = replace(sc.asset, battery=replace(battery, energy_mwh=cfg.usable_mwh),
                    limits=replace(limits, cycles_per_day=limits.cycles_per_day * battery.energy_mwh / cfg.usable_mwh))
    return replace(sc, asset=asset)


def _ladder(inputs: dict, i: int, level: float, bat, site, prefs, load_fc, cmd_cap, ircr_cap, terms, site_res,
            reserve: float, enuc: float, ircr_value_mw: float, cost: float | None = None,
            buy_margin: float | None = None, marg: np.ndarray | None = None) -> bd.LadderBids:
    """The day's layered offers (S3): the curve's offer values at the day's level, the factory's energy protected at
    the lowest value per MWh among its needs today (CMD, a flagged day's peak hours) and tomorrow (②). cost: $ per
    MWh sold the offers charge in place of the plans' (round 4, B2); buy_margin: h of the buy limits (B3); marg: the
    day's offer values in place of the pooled ones (its market state's, B4)."""
    marg = inputs["marg"][i] if marg is None else marg
    if site is None:
        return bd.LadderBids(inputs["edges"][i], marg, level, np.zeros(dp.N), 0.0, cost=cost, buy_margin=buy_margin)
    plan_load = load_fc * (1 + prefs.load_margin)  # what the plans assume the factory draws (run_day)
    caps = np.full(dp.N, np.inf)
    values = []
    if cmd_cap is not None:
        caps = np.minimum(caps, cmd_cap)
        hours = float((plan_load > cmd_cap).sum()) * dp.DT
        if hours > 0:
            values.append(enuc * bat.eta / hours)
    if ircr_cap is not None:
        caps = np.minimum(caps, ircr_cap)
        values.append(ircr_value_mw / sb.IRCR_WINDOW_H)
    tomorrow = site_res.total_mwh if site_res is not None else reserve
    if site_res is not None:
        values += ([site_res.ircr_value] if site_res.ircr_mwh > 0 else []) + \
                  ([site_res.cmd_value] if site_res.total_mwh - site_res.ircr_mwh >= rv.MIN_MWH else [])
    elif reserve > 0:
        values.append(ircr_value_mw / sb.IRCR_WINDOW_H)
    comp = [o for o in terms.obligations if o.compliance]
    compliance = (np.array([dp.obligation_need(bat, comp, (k + 1) * dp.DT, plan_load) for k in range(dp.N)])
                  if comp else np.zeros(dp.N))
    protect = bd.protection(bat, site, plan_load, caps, tomorrow, compliance) if values or comp else np.zeros(dp.N)
    return bd.LadderBids(inputs["edges"][i], marg, level, protect, min(values, default=0.0), cost=cost,
                         buy_margin=buy_margin)


def route_days(sc: bt.Scenario, cfg: V2Config, soc0: float = 0.0, mtd0: dict | None = None, until=None, on_day=None,
               decide=None):
    """One v2 configuration through every day on a 10 MW route, yielding (day, terms, summary, log, records): as
    vpp.backtest._route_days for forecast+offer(value), with cfg's features (records: [] unless cfg.record).
    To replay one day of a run (vpp.v2.report): soc0 and mtd0, the stored energy and each month's highest import
    when the first day starts; until, the last day; on_day(day, kw, summary, log), called after each day.
    decide(d, kw): a choice made at 09:00 (vpp.v2.candidates), returning run_day arguments to change."""
    sc = scenario(sc, cfg)
    route, asset, site = sc.route, sc.asset, sc.site
    bat = asset.battery
    source, strat, prefs = bt.strategy(bt.VALUE, sc.prefs)
    if cfg.value == "relative":
        strat = replace(strat, name=cfg.name, terminal="table")
    if cfg.ladder:
        strat = replace(strat, name=cfg.name, offer_rule="table")
    if cfg.replan_every:  # e5 (round 5): the plans are also redone on the clock, the forecast nudged to the latest price
        strat = replace(strat, name=cfg.name, planner="roll", every=cfg.replan_every)
    inputs = value_inputs(cfg, bat) if cfg.value == "relative" or cfg.ladder else None
    if inputs is not None and inputs["curve"].shape[1] != round(bat.energy_mwh / vl.sv.GRID):
        raise ValueError("the value curve's segments do not match the battery the dispatcher sees")
    res_mwh = cfg.spike_reserve_mwh
    perfect = source == "actual"
    fixed = route.retail == "fixed"
    if fixed:
        strat = replace(strat, offer=False)
    aux, days = bt._AUX, bt._DAYS
    events = {c.name: ct.call_days(c, aux["demand"]) for c in route.contracts}
    peaks = aux["ircr_peaks"] if route.ircr_hindsight else {}
    same_day, day_before = set(), set(peaks)  # possible peak days flagged at 09:00 on the day / the day before
    if route.ircr == "dynamic":
        same_day, day_before = set(aux["flags0"]), set(aux["flags1"])
    elif route.ircr is not None and not route.ircr_hindsight:
        same_day = set(sb.flag_days(route.ircr[0], aux["predictor0"]))
        day_before = set(sb.flag_days(route.ircr[1], aux["predictor1"]))
    by_date = {d["day"]: d for d in days}
    window = np.zeros(dp.N, bool)
    window[sb.IRCR_SLOTS[0]:sb.IRCR_SLOTS[1]] = True
    enuc = sb.RT7(route.cmd_mw / sb.PF * 1000).enuc_per_mw() if route.cmd_mw else 0.0
    ref = (19, 27) if route.window == "esroi" else ct.window_7h(ct.HOT_DAY)  # credits are certified once, on a hot weekday
    credit = ct.credits(bat, site, ref, route.rcm, sc.prefs.load_margin) if route.rcm else 0.0
    soc, mtd = soc0, dict(mtd0 or {})
    for d in days:
        day = d["day"]
        if until is not None and day > until:
            break
        forecast = None
        if perfect or route.issue == "0815":
            pe, pf = d["prices"][source]
        else:
            (pe, pf), forecast = d["prices"]["bridge"], d["fc0900"]
        rtp, p5 = d["rtp"], d["p5"]
        if fixed:
            pe, forecast = np.full(dp.N, ct.RETAIL_FIXED), None
            rtp, p5 = pe, np.full(dp.N * dp.STEPS, ct.RETAIL_FIXED)
        load_fc = load_act = None
        if site is not None:
            load_fc = d["load_act" if perfect else "load_fc"] * site.load_scale
            load_act = d["load_act"] * site.load_scale
        cmd_cap = None if route.cmd_mw is None else np.array([max(route.cmd_mw, mtd.get(m, 0.0)) for m in d["slot_month"]])
        ircr_cap, ircr_at, reserve = None, 0 if day in day_before else 2, 0.0
        if site is not None and (day in same_day or day in day_before):
            ircr_cap = np.full(dp.N, np.inf)
            ircr_cap[peaks[day] if day in peaks else window] = 0.0
        tomorrow = by_date.get(day + pd.Timedelta(days=1))
        site_res = None
        if cfg.site_reserve and site is not None and tomorrow is not None:
            # ② tomorrow's site needs (S2), in place of v1's overnight IRCR reserve below
            slots = None
            if tomorrow["day"] in day_before:
                slots = peaks[tomorrow["day"]] if tomorrow["day"] in peaks else np.flatnonzero(window)
            site_res = rv.site_reserve(tomorrow, site, bat, sc.prefs.load_margin, cfg.site_load_basis, route.cmd_mw, mtd,
                                       slots, aux["ircr_value_mw"], enuc)
        elif site is not None and tomorrow is not None and tomorrow["day"] in day_before:
            # hold overnight what tomorrow's flagged hours need: the factory's planned load then
            need = tomorrow["load_fc"] * site.load_scale * (1 + sc.prefs.load_margin)
            slots = peaks[tomorrow["day"]] if tomorrow["day"] in peaks else np.flatnonzero(window)
            reserve = min(bat.energy_mwh, float(np.minimum(bat.power_mw, need[slots]).sum()) * dp.DT / bat.eta)
        carry = 0.0
        if tomorrow is not None:  # tomorrow's standing compliance obligations are known today
            plan_next = None if site is None else tomorrow["load_fc"] * site.load_scale * (1 + sc.prefs.load_margin)
            standing = ct.day_terms(route, tomorrow, bat, site, None, None, 0, None, credit, {}, enuc, 0.0,
                                    sc.prefs.capacity_floor)
            today = ct.day_terms(route, d, bat, site, None, None, 0, None, credit, {}, enuc, 0.0, sc.prefs.capacity_floor)
            plan_today = None if site is None else d["load_fc"] * site.load_scale * (1 + sc.prefs.load_margin)
            carry = ct.carry_in(standing, bat, site, plan_next, today, plan_today)
        terms = ct.day_terms(route, d, bat, site, cmd_cap, ircr_cap, ircr_at, forecast, credit, events, enuc, reserve,
                             sc.prefs.capacity_floor, carry, aux["ircr_value_mw"])
        context = {}
        if site_res is not None:
            obs = site_res.obligations(carry, bat.eta)
            if obs and cfg.plan_days == 1:  # a 48-hour plan has tomorrow's needs in itself
                terms = replace(terms, reveals=terms.reveals + (dp.Reveal(2, "site reserve for tomorrow", obligations=obs),))
            context.update(site_ircr_mwh=site_res.ircr_mwh, site_total_mwh=site_res.total_mwh,
                           site_cmd_hours=site_res.cmd_hours, site_ircr_value=site_res.ircr_value,
                           site_cmd_value=site_res.cmd_value, site_layers=len(obs))
        kw = dict(asset=asset, strat=strat, prefs=prefs, fc_e=pe, fc_f=pf, rtp=rtp, act_f=d["act_f"], p5=p5,
                  obligations=None, risk=d["risk"], s_start=soc, p5_f=d["p5_f"], site=site, load_fc=load_fc,
                  load_act=load_act, terms=terms, export_energy=not fixed, reg_util=route.reg_util,
                  scen=None if strat.planner == "point" else d.get("scen"))
        if res_mwh > 0:
            kw["reserve"] = dp.Reserve(res_mwh, *bt.RESERVE_WINDOW)
        if inputs is not None:
            i = inputs["index"][pd.Timestamp(day).normalize()]
            level = float(inputs["level"][i])
            held = carry + (site_res.total_mwh if site_res is not None else reserve)  # ① + ② at 08:00 tomorrow
            # t5 (round 5): before 09:00 only what today already commits; ② is public at 09:00
            early = carry if cfg.terminal_0900 else held
            context.update(level=level, curve_shift_mwh=held if cfg.curve_shift else 0.0)
            if cfg.terminal_0900:
                context.update(curve_shift_early_mwh=early if cfg.curve_shift else 0.0)
            if cfg.value == "relative":
                kw["terminal"] = vl.terminal(inputs, day, early, cfg.curve_shift)
            marg = None
            end_at_0900 = False  # a reveal at 09:00 already sets the plans' end value (B4's tomorrow's state)
            if cfg.state:
                # B4: today's state (called at 09:00 yesterday) prices the offers all day; tomorrow's (called at 09:00
                # today) the plans' end-of-day value from 09:00 on; the pooled values where a call is missing
                today, nxt = inputs["state_today"][i], inputs["state_tomorrow"][i]
                if np.isfinite(today):
                    marg = inputs["marg_state"][i, int(today)]
                if cfg.regime:  # c5 (round 5): today's state (pooled without a call), by whether the day has spiked
                    marg = inputs["marg_regime"][i]
                if np.isfinite(nxt) and cfg.value == "relative":
                    end = vl.shifted(inputs["curve_state"][i, int(nxt)].astype(float), held, cfg.curve_shift)
                    terms = replace(terms, reveals=terms.reveals + (dp.Reveal(2, "tomorrow's market state", terminal=end),))
                    kw["terms"] = terms
                    end_at_0900 = True
                context.update(state_today=float(today), state_tomorrow=float(nxt))
            if cfg.terminal_0900 and cfg.value == "relative" and not end_at_0900:
                end = vl.terminal(inputs, day, held, cfg.curve_shift)
                terms = replace(terms, reveals=terms.reveals + (dp.Reveal(2, "tomorrow's site reserve", terminal=end),))
                kw["terms"] = terms
            if cfg.ladder:
                h = bt.HURDLE if cfg.hurdle is None else cfg.hurdle
                kw["bids"] = _ladder(inputs, i, level, bat, site, prefs, load_fc, cmd_cap, ircr_cap, terms, site_res,
                                     reserve, enuc, aux["ircr_value_mw"],
                                     None if cfg.hurdle is None else asset.wear_per_mwh + cfg.hurdle,
                                     h if cfg.buy else None, marg)
                if cfg.bid:  # f5 (round 5): B3's buy side at the full value, capped; a floor; or the night fill
                    floor = np.nan
                    if cfg.bid == "floor" and site is not None and route.cmd_mw:
                        floor = float(charge_floors(route.cmd_mw, site.load_scale, sc.prefs.load_margin).get(day, np.nan))
                        context.update(bid_floor=floor)
                    kw["bids"] = replace(kw["bids"], buy_margin=0.0, buy_alpha=1.0,
                                         buy_cap=(bd.OFFER_CAP - asset.wear_per_mwh - h) * bat.eta ** 2,
                                         value_floor=floor / bat.eta if np.isfinite(floor) else None,
                                         night_fill=cfg.bid == "fill")
                if cfg.buy_room:  # 13.1 (round 5): buying beyond the plan only within the room the caps leave
                    kw["bids"] = replace(kw["bids"], room_rule=True)
                if cfg.offer_lock or cfg.offer_cap is not None:  # g5 (round 5): the gate closure and the price ceiling
                    kw["bids"] = replace(kw["bids"], lock=cfg.offer_lock, lock_drain=bat.power_mw * dp.DT5 / bat.eta,
                                         offer_cap=cfg.offer_cap)
        if cfg.plan_days == 2 and site is not None and tomorrow is not None and inputs is not None:
            slots = None
            if tomorrow["day"] in day_before:
                slots = peaks[tomorrow["day"]] if tomorrow["day"] in peaks else np.flatnonzero(window)
            ext = tomorrow_part(day, tomorrow, by_date.get(day + pd.Timedelta(days=2)), route, site, asset, prefs, mtd,
                                slots, inputs, aux["ircr_value_mw"], enuc, cfg.curve_shift)
            if ext is not None:
                kw["extend"] = ext
        if cfg.offer_lag != 1:
            kw["offer_lag"] = cfg.offer_lag
        if cfg.offer_end:
            kw["offer_end"] = True
        if cfg.curve:  # round 6 (v2_plan.md section 15): the plans' own bid curve
            kw["curve"] = cfg.curve
        if decide is not None:
            kw.update(decide(d, kw))
        recorder = rc.Recorder(day, {"config": cfg.name, "carry_mwh": carry, **context}) if cfg.record else None
        if recorder is not None:
            kw["on_plan"] = recorder
        summary, log = dp.run_day(**kw)
        if on_day is not None:
            on_day(day, kw, summary, log)
        soc = summary["soc_end"]
        for m, x in zip(d["slot_month"], log["meter_mw"].to_numpy()):
            mtd[m] = max(mtd.get(m, 0.0), x)
        yield day, terms, {"credit_mw": credit, **summary}, log, ([] if recorder is None else recorder.rows)
