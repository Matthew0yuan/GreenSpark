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
from vpp.v2 import record as rc
from vpp.v2 import reserves as rv
from vpp.v2.config import V2Config

VALUE_INPUTS: dict = {}  # the relative value curve's per-day arrays, loaded by the worker (vpp.v2.backtest)


def scenario(sc: bt.Scenario, cfg: V2Config) -> bt.Scenario:
    """The scenario as cfg runs it: with a usable-energy window the dispatcher sees a smaller battery, and the daily
    cycle limit is scaled so it still allows the full battery's cycle."""
    if cfg.usable_mwh is None:
        return sc
    battery, limits = sc.asset.battery, sc.asset.limits
    asset = replace(sc.asset, battery=replace(battery, energy_mwh=cfg.usable_mwh),
                    limits=replace(limits, cycles_per_day=limits.cycles_per_day * battery.energy_mwh / cfg.usable_mwh))
    return replace(sc, asset=asset)


def route_days(sc: bt.Scenario, cfg: V2Config):
    """One v2 configuration through every day on a 10 MW route, yielding (day, terms, summary, log, records): as
    vpp.backtest._route_days for forecast+offer(value), with cfg's features (records: [] unless cfg.record)."""
    sc = scenario(sc, cfg)
    route, asset, site = sc.route, sc.asset, sc.site
    bat = asset.battery
    source, strat, prefs = bt.strategy(bt.VALUE, sc.prefs)
    if cfg.value == "relative":
        strat = replace(strat, name=cfg.name, terminal="table")
    if cfg.ladder:
        strat = replace(strat, name=cfg.name, offer_rule="table")
    if (cfg.value == "relative" or cfg.ladder) and not VALUE_INPUTS:
        raise ValueError("the relative value curve needs its inputs (vpp.v2.backtest loads them)")
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
    soc, mtd = 0.0, {}
    for d in days:
        day = d["day"]
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
            if obs:
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
        if cfg.offer_lag != 1:
            kw["offer_lag"] = cfg.offer_lag
        recorder = rc.Recorder(day, {"config": cfg.name, "carry_mwh": carry, **context}) if cfg.record else None
        if recorder is not None:
            kw["on_plan"] = recorder
        summary, log = dp.run_day(**kw)
        soc = summary["soc_end"]
        for m, x in zip(d["slot_month"], log["meter_mw"].to_numpy()):
            mtd[m] = max(mtd.get(m, 0.0), x)
        yield day, terms, {"credit_mw": credit, **summary}, log, ([] if recorder is None else recorder.rows)
