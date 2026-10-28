"""One trading day of Zenith's storage network: 10 MW / 40 MWh, registered Scheduled, all four FCESS, either behind the
factory meter (route m1: CMD 9 MW, IRCR on flagged peak days, the synthetic factory load; the default since the user's
2026-10-28 decision) or on its own connection (route sa). Either connection runs e5 (the default) or v1. Replays the
chosen dispatcher from the day's saved start state and splits the result into what was known at 09:00 (plan, offers,
forecasts, checks) and what happened (outcome), which the API serves separately.

Ported from analysis/2026-10-26/ui_redesign/export/site_days_replay.py and site_days_export.py; the factory blocks
(meter, CMD, IRCR, network bill) follow site_days_export.py."""

import json
from collections import OrderedDict
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp import dispatch as dp
from vpp import site as vsite
from vpp import storage_value as sv
from vpp.data import PROCESSED
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs
from vpp.v2 import offers as of
from vpp.v2.config import V1

from . import evidence as ev
from .common import (CONNECTIONS, DEFAULT_CONNECTION, DEFAULT_DISPATCHER, DISPATCHERS, FIRST, HURDLE, N, PERFECT,
                     PLAN_ONLY, ROOT, SAVED_DIR, STRATEGY, arr, check_mode, clock, hot_season, num, other, pad, period,
                     period_key, saved_rel, weekday_label)

# MW or MWh: the plan checks allow the MILP solver's feasibility tolerance (HiGHS: on 2026-01-20 behind the meter the room
# behind FCESS lower is short by 1.5e-6 MWh, 1.5 Wh of 40 MWh)
TOL = 1e-5
SERVICES = tuple(dp.FCESS)  # reg_raise, cont_raise, reg_lower, cont_lower
SCHEMA = ROOT / "data" / "raw" / "aemo_rtms" / "rtms_openapi.json"
CACHE_DAYS = 24
LOAD_BASIS = "synthetic: vpp.site.example_load x 10, the forecast is the profile itself [assumed]"


def scenario(connection: str, fcess: tuple = SERVICES):
    """The route the saved runs used: behind the factory meter m1 (CMD 9 MW, IRCR dynamic) or standalone sa."""
    route = (ct.Route("m1", cmd_mw=vb.CMD, ircr="dynamic", fcess=fcess) if connection == "factory"
             else ct.Route("sa", metering="standalone", fcess=fcess))
    sc = bt._sc(route.name, route, (bt.VALUE,), prefs=replace(bt.PREFS, hurdle_per_mwh=HURDLE))
    if sc is None:
        raise ValueError(f"route refused: {bt.REFUSED}")
    return sc


def config(dispatcher: str, record: bool = False):
    return replace(vb.E5 if dispatcher == "e5" else V1, record=record)


class Storage:
    """Loads the market days and both connections' saved runs once (~10 s) and replays days on request (~1.5-4 s
    each, cached)."""

    def __init__(self):
        assert (STRATEGY["standalone", "v1"], STRATEGY["factory", "v1"], STRATEGY["factory", "e5"]) == \
            (bt.VALUE, V1.name, vb.E5.name), "common.STRATEGY no longer names the saved runs"
        self.days = bt.load_days("0900")
        self.aux = bt.q2b_aux()
        self.index = {d["day"]: i for i, d in enumerate(self.days)}
        self.saved, self.money, self.bills, self.mtd = {}, {}, {}, {}
        for conn, folder in SAVED_DIR.items():  # incl. the 09:00 plan alone ("forecast"), prices known ("perfect")
            daily, self.money[conn], _ = ev.saved_runs(conn)
            self.saved[conn] = {name: g.set_index("day").sort_index() for name, g in daily.groupby("strategy")}
            if conn == "factory":  # money: the daily ledger plus the month's network bill saving per day
                self.bills[conn] = pd.read_csv(folder / "bills.csv")
                self.mtd = self._month_peaks(folder)
        self.verdicts = {conn: ev.verdict(self.money[conn], conn) for conn in CONNECTIONS}
        self.cache: OrderedDict = OrderedDict()
        self.schema = json.loads(SCHEMA.read_text())["components"]["schemas"] if SCHEMA.exists() else None
        sc = pd.read_parquet(PROCESSED / "scenarios_0900.parquet", columns=["day", "member", "slot", "energy"])
        sc["day"] = pd.to_datetime(sc["day"])
        self.scenarios = {d: g for d, g in sc.groupby("day")}
        risk = pd.read_parquet(PROCESSED / "spike_risk_0900.parquet", columns=["ts", "target_date", "days_ahead", "p_spike"])
        risk = risk[risk["days_ahead"] == 0]
        self.risk = {pd.Timestamp(d): g.sort_values("ts")["p_spike"].to_numpy() for d, g in risk.groupby("target_date")}

    def _month_peaks(self, folder) -> dict:
        """{strategy: {day: {month: the highest meter import before the day}}} from the saved log (route_days' mtd0;
        the same running maximum as vpp.v2.report._start_state, by each half-hour's calendar month)."""
        log = pd.read_parquet(folder / "log.parquet", columns=["day", "slot", "strategy", "meter_mw"])
        months = {d["day"]: d["slot_month"] for d in self.days}
        out = {}
        for name, g in log.groupby("strategy"):
            run, by_day = {}, {}
            for day, x in g.sort_values(["day", "slot"]).groupby("day"):
                day = pd.Timestamp(day)
                by_day[day] = dict(run)
                for m, v in zip(months[day], x["meter_mw"].to_numpy()):
                    run[m] = max(run.get(m, 0.0), float(v))
            out[name] = by_day
        return out

    # ------------------------------------------------------------------------------------------------ meta
    def meta(self) -> dict:
        days = [d["day"] for d in self.days]
        return {"first_day": str(days[0].date()), "last_day": str(days[-1].date()), "days": len(days),
                "connections": list(CONNECTIONS), "dispatchers": list(DISPATCHERS),
                "defaults": {"connection": DEFAULT_CONNECTION, "dispatcher": DEFAULT_DISPATCHER},
                "dispatcher": self.dispatcher(DEFAULT_CONNECTION, DEFAULT_DISPATCHER),
                "baseline": self.baseline_info(DEFAULT_CONNECTION, DEFAULT_DISPATCHER),
                "asset": self.asset_info(DEFAULT_CONNECTION), "verdicts": self.verdicts}

    def dispatcher(self, connection: str, dispatcher: str) -> dict:
        v = self.verdicts[connection]
        return {"name": STRATEGY[connection, dispatcher], "short": dispatcher, "connection": connection,
                "e5_passed": v["passed"], "verdict": v, "hurdle_aud_per_mwh": HURDLE}

    def baseline_info(self, connection: str, dispatcher: str) -> dict:
        o = other(dispatcher)
        return {"name": STRATEGY[connection, o], "short": o}

    def asset_info(self, connection: str) -> dict:
        bat = bt.Q2B_ASSET.battery
        out = {"power_mw": bat.power_mw, "energy_mwh": bat.energy_mwh, "round_trip": bat.round_trip,
               "wear_aud_per_mwh": bt.Q2B_ASSET.wear_per_mwh, "registration": "scheduled",
               "metering": "behind_meter" if connection == "factory" else "standalone", "facilities": 1,
               "fcess": list(SERVICES), "status": "assumed", "site": None}
        if connection == "factory":
            s = vsite.FACTORY
            out["site"] = {"import_limit_mw": s.import_limit_mw, "export_limit_mw": s.export_limit_mw, "cmd_mw": vb.CMD,
                           "load": LOAD_BASIS, "ircr": "dynamic: peak days flagged at 09:00 (same day, day before)"}
        return out

    # ------------------------------------------------------------------------------------------------ replay
    @staticmethod
    def _scenario(connection: str, fcess: tuple):
        return scenario(connection, fcess)

    @staticmethod
    def _cfg(dispatcher: str):
        return config(dispatcher, record=True)

    def start_mwh(self, day: pd.Timestamp, connection: str = DEFAULT_CONNECTION,
                  dispatcher: str = DEFAULT_DISPATCHER) -> float:
        g = self.saved[connection][STRATEGY[connection, dispatcher]]
        before = g[g.index < day]
        return float(before["soc_end"].iloc[-1]) if len(before) else 0.0

    def month_peaks(self, day: pd.Timestamp, connection: str, dispatcher: str) -> dict:
        if connection != "factory":
            return {}
        return dict(self.mtd[STRATEGY[connection, dispatcher]].get(pd.Timestamp(day), {}))

    def replay(self, day, connection: str = DEFAULT_CONNECTION, dispatcher: str = DEFAULT_DISPATCHER,
               fcess: tuple = SERVICES, start_mwh: float | None = None, perturb: bool = False) -> dict:
        check_mode(connection, dispatcher)
        day = pd.Timestamp(day)
        key = (day, connection, dispatcher, tuple(fcess), None if start_mwh is None else round(float(start_mwh), 6),
               perturb)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if day not in self.index:
            raise KeyError(f"no market data for trading day {day.date()}")
        i = self.index[day]
        sub = [dict(d) for d in self.days[i:i + 2]]
        if perturb:  # every actual price from 09:00 on x 3: the 09:00 plan must not move (check info_timing)
            d = sub[0]
            d["rtp"], d["p5"] = d["rtp"].copy(), d["p5"].copy()
            d["rtp"][FIRST:] *= 3.0
            d["p5"][FIRST * dp.STEPS:] *= 3.0
            d["p5_f"] = {k: v.copy() for k, v in d["p5_f"].items()}
            d["act_f"] = {k: v.copy() for k, v in d["act_f"].items()}
            for k in d["p5_f"]:
                d["p5_f"][k][FIRST * dp.STEPS:] *= 3.0
                d["act_f"][k][FIRST:] *= 3.0
            d["prices"] = {**d["prices"], "actual": (d["rtp"], d["act_f"])}
        bt._init_worker(sub, self.aux)
        sc, cfg = self._scenario(connection, tuple(fcess)), self._cfg(dispatcher)
        bat = hs.scenario(sc, cfg).asset.battery
        is_e5, factory = dispatcher == "e5", connection == "factory"
        col = of.Collector(bat)
        steps: list = []

        def on_step(d, x):
            x = dict(x)
            if x["load_act"] is None:  # no site behind a standalone connection
                x["load_act"] = 0.0
            if is_e5:
                col(d, x)
            en = x["enabled"] or {}
            steps.append({"step": x["step"], "t": x["t"], "s": x["s"], "pw_plan": x["pw_plan"], "sold": x["sold"],
                          "pw_offer": x["pw_offer"], "pw": x["pw"], "price": x["price"],
                          **{f"en_{k}": float(v) for k, v in en.items()}})

        def on_day(d, kw, summary, log):
            if not is_e5:
                return
            if factory:  # behind the meter the offers' rooms are the site's limits less (plus) the factory's load
                col.on_day(d, kw, summary, log)
            else:  # a standalone connection: export and import rooms are the battery's power, no load
                P = bat.power_mw
                col.on_day(d, {**kw, "load_fc": np.zeros(N), "site": SimpleNamespace(export_limit_mw=P, import_limit_mw=P)},
                           summary, log)

        s0 = self.start_mwh(day, connection, dispatcher) if start_mwh is None else float(start_mwh)
        mtd0 = self.month_peaks(day, connection, dispatcher)
        _, terms, summary, log, recs = next(hs.route_days(sc, cfg, s0, mtd0, until=day, on_day=on_day, on_step=on_step))
        out = {"day": day, "sc": sc, "bat": bat, "summary": summary, "log": log, "records": recs,
               "steps": pd.DataFrame(steps), "forward": col.forward.get(day, {}), "at9": col._at9, "day_input": sub[0],
               "start_mwh": s0, "mtd0": mtd0, "fcess": tuple(fcess), "connection": connection, "dispatcher": dispatcher}
        self.cache[key] = out
        while len(self.cache) > CACHE_DAYS:
            self.cache.popitem(last=False)
        return out

    @staticmethod
    def record_0900(records: list) -> dict:
        rec = [x for x in records if int(x["t0"]) == FIRST and x["kind"] == "reveal"]
        if len(rec) != 1:
            raise RuntimeError(f"expected one 09:00 reveal record, found {len(rec)}")
        return rec[0]

    @staticmethod
    def _site_plan(r: dict, rec: dict) -> dict:
        """The factory's part of the 09:00 plan: the expected load, the meter, the caps on meter import."""
        sc = r["sc"]
        net = np.asarray(rec["net"], float)
        if sc.site is None or rec.get("load") is None:
            z = np.zeros(len(net))
            return {"lf": z, "lfe": z, "meter": -net, "caps": {}}
        lf = np.asarray(rec["load"], float)  # the plan's own load: forecast x (1 + margin)
        lfe = lf / (1 + sc.prefs.load_margin)  # the expected load
        caps = {n: np.asarray(c, float) for n, c in zip(rec["cap_names"] or (), rec["caps"] or ())}
        return {"lf": lf, "lfe": lfe, "meter": lfe - net, "caps": caps}

    # ------------------------------------------------------------------------------------------------ the 09:00 plan
    def plan(self, day, connection: str = DEFAULT_CONNECTION, dispatcher: str = DEFAULT_DISPATCHER,
             fcess: tuple = SERVICES, start_mwh: float | None = None, export_mw: float | None = None,
             import_mw: float | None = None) -> dict:
        """What was known at 09:00: the plan, the offers, the forecasts and the checks. Settings that change the
        engine's inputs (connection, dispatcher, fcess, start_mwh) replay the day; the connection limits only
        re-check the plan."""
        r = self.replay(day, connection, dispatcher, fcess, start_mwh)
        D, bat, sc = r["day"], r["bat"], r["sc"]
        P, E, eta = bat.power_mw, bat.energy_mwh, bat.eta
        factory = connection == "factory"
        exp0 = sc.site.export_limit_mw if factory else P
        imp0 = sc.site.import_limit_mw if factory else P
        export_mw = exp0 if export_mw is None else float(export_mw)
        import_mw = imp0 if import_mw is None else float(import_mw)
        rec = self.record_0900(r["records"])
        gaps: list = []
        net = np.asarray(rec["net"], float)
        soc = np.asarray(rec["soc"], float)
        f = {k: np.clip(np.asarray(rec[k], float), 0.0, None) for k in SERVICES}
        f = {k: np.where(v < 5e-7, 0.0, v) for k, v in f.items()}
        rr, cr, rl, cl = f["reg_raise"], f["cont_raise"], f["reg_lower"], f["cont_lower"]
        tau = sc.asset.limits.fcess_sustain_h
        hard = np.zeros(len(net)) if rec["hard"] is None else np.asarray(rec["hard"], float)
        floor = hard + (rr + cr) * tau / eta
        soc_start = np.r_[rec["stored"], soc[:-1]]
        site = self._site_plan(r, rec)
        # the action of each half-hour, from the plan's MW and its own economic caps on the meter (the record gives no
        # reason per half-hour): a discharge that holds the meter at a cap is a shave
        action, action_cap = [], []
        for k, x in enumerate(net):
            binding = [n for n, c in site["caps"].items() if np.isfinite(c[k]) and c[k] < 0 and abs(-x - c[k]) <= 1e-3]
            if x < -1e-3:
                action.append("charge"); action_cap.append(None)
            elif x > 1e-3 and binding:
                action.append("shave"); action_cap.append("ircr" if "ircr" in binding else binding[0])
            else:
                action.append("discharge" if x > 1e-3 else "hold"); action_cap.append(None)
        # before 09:00 the battery ran the 08:00 plan: executed, known at 09:00
        log = r["log"]
        pre_mw = (log["energy_mwh"].to_numpy()[:FIRST] / dp.DT).tolist()
        pre_soc = [float(log["soc_start"].iloc[0]), *log["soc"].to_numpy()[:FIRST].tolist()]

        wear_settled = rec["wear"] - (rec["money"] - rec["value"])
        reg_energy = rec["value"] - (rec["energy_value"] + rec["fcess_value"] - rec["wear"] + rec["stored_value"])
        value = {"energy": num(rec["energy_value"]), "regulation_energy": num(reg_energy), "fcess": num(rec["fcess_value"]),
                 "wear": num(wear_settled), "stored_end": num(rec["stored_value"]), "net": num(rec["money"])}
        if factory:
            value["network"] = None
            gaps.append({"field": "plan.value_at_forecast_aud.network", "reason": "the plan does not price the network "
                         "bill: the CMD cap steers it through a penalty that is not money; the bill is settled per "
                         "calendar month"})

        checks = self._checks(r, rec, net, soc, soc_start, f, hard, tau, export_mw, import_mw, site)
        hard_fail = [c for c in checks if c["status"] == "fail"]
        blocked = [c for c in hard_fail if c["id"].startswith("connection")]
        state = "BLOCKED" if hard_fail else "PASS"
        breaches = sorted({k for c in blocked for k in c.get("slots", [])})

        default = tuple(r["fcess"]) == SERVICES and start_mwh is None
        src = saved_rel(connection)
        out = {
            "day": str(D.date()), "weekday_label": weekday_label(D), "basis": "plan",
            "period": period(period_key(D), self._period_days(period_key(D))), "hot_season": hot_season(D),
            "connection": connection, "dispatcher": self.dispatcher(connection, dispatcher),
            "asset": self.asset_info(connection),
            "settings": {"connection": connection, "dispatcher": dispatcher, "fcess": list(r["fcess"]),
                         "start_mwh": start_mwh, "export_mw": export_mw, "import_mw": import_mw},
            "unvalidated": not default or export_mw != exp0 or import_mw != imp0,
            "start_state": {"stored_mwh": num(r["start_mwh"]),
                            "source": "the day before's stored energy at 08:00 in this dispatcher's saved backtest "
                                      f"({src}/daily.csv soc_end)" if start_mwh is None else "set in Settings"},
            "plan": {"first_interval": FIRST, "stored_at_0900_mwh": num(rec["stored"]),
                     "battery_mw": [*arr(pre_mw), *arr(net)], "soc_mwh": [*arr(pre_soc), *arr(soc)],
                     "pre_0900": "08:00-09:00 is the 08:00 plan as executed (known at 09:00)",
                     "fcess_mw": {k: pad(arr(f[k])) for k in SERVICES}, "reserve_floor_mwh": pad(arr(floor)),
                     "action": ["pre"] * FIRST + action, "action_cap": [None] * FIRST + action_cap,
                     "value_at_forecast_aud": value,
                     "source": "vpp.dispatch.run_day on_plan record at interval 2, kind 'reveal'", "status": "backtest"},
            "site": self._site_block(r, rec, site, gaps) if factory else None,
            "price": self._price(r, gaps),
            "offers_0900": self._offers(r, soc_start, gaps, site),
            "value_now": self._value_now(r, rec),
            "checks": checks,
            "state": state, "blocked_halfhours": breaches,
            "gaps": gaps,
        }
        return out

    def _site_block(self, r: dict, rec: dict, site: dict, gaps: list) -> dict:
        """The factory at 09:00: its expected load and the meter from 09:00 (08:00-09:00 as metered, known at 09:00),
        the caps on meter import (CMD, or the month's highest so far; IRCR on a flagged day) and the peak-day flag."""
        D, sc, log = r["day"], r["sc"], r["log"]
        lfe = site["lfe"]
        cap = {}
        for name, c in site["caps"].items():
            live = np.isfinite(c)
            cap[name] = pad(arr(np.where(live, c + lfe, np.nan)))  # the cap on meter import, MW
        hot = D.month in (12, 1, 2, 3)
        flags0, flags1 = set(self.aux["flags0"]), set(self.aux["flags1"])
        if not hot:
            gaps.append({"field": "site.ircr_flag_0900", "reason": "outside the Hot Season (1 Dec–31 Mar), when IRCR "
                         "peak intervals cannot fall"})
        month = D.strftime("%Y-%m")
        return {"load_fc_mw": [*arr(log["site_load"].to_numpy()[:FIRST]), *arr(lfe)],
                "meter_mw": [*arr(log["meter_mw"].to_numpy()[:FIRST]), *arr(site["meter"])],
                "cmd_cap_mw": cap.get("cmd"), "ircr_cap_mw": cap.get("ircr"),
                "import_limit_mw": sc.site.import_limit_mw, "export_limit_mw": sc.site.export_limit_mw,
                "cmd_mw": sc.route.cmd_mw, "month_max_import_mw": num(r["mtd0"].get(month)),
                "ircr_flag_0900": (D in flags0 or D in flags1) if hot else None,
                "ircr_flag_basis": {"same_day_0900": D in flags0, "day_before_0900": D in flags1} if hot else None,
                "load_basis": LOAD_BASIS,
                "pre_0900": "08:00-09:00 load and meter as metered (known at 09:00); from 09:00 the expected load "
                            "(the plan's load forecast without its margin) less the planned battery"}

    def _period_days(self, key: str) -> int:
        return sum(period_key(d["day"]) == key for d in self.days)

    def _checks(self, r, rec, net, soc, soc_start, f, hard, tau, export_mw, import_mw, site) -> list:
        sc, bat = r["sc"], r["bat"]
        P, E, eta = bat.power_mw, bat.energy_mwh, bat.eta
        rr, cr, rl, cl = f["reg_raise"], f["cont_raise"], f["reg_lower"], f["cont_lower"]
        util = sc.route.reg_util
        factory = r["connection"] == "factory"
        out = []

        def add(cid, label, ok, worst, detail, status=None, slots=(), economic=False):
            st = status or ("pass" if ok else "warn" if economic else "fail")
            out.append({"id": cid, "label": label, "status": st, "worst": worst, "detail": detail,
                        "economic": economic, "slots": [int(FIRST + k) for k in slots]})

        up = np.maximum.reduce([np.abs(net), net + rr + cr, -net + rl + cl])
        add("power", f"Battery power ≤ {P:g} MW", up.max() <= P + TOL, f"{up.max():.2f} MW",
            f"largest of |net|, net + raise, −net + lower from 09:00: {up.max():.3f} MW at {clock(FIRST + int(np.argmax(up)))}",
            slots=np.flatnonzero(up > P + TOL))
        add("soc_max", f"Stored energy 0–{E:g} MWh", soc.max() <= E + TOL and soc.min() >= -TOL, f"{soc.max():.1f} MWh",
            f"plan stored energy at the end of each half-hour: {soc.min():.2f}–{soc.max():.2f} MWh")
        gap = soc - hard
        add("soc_reserve", "Stored energy ≥ mandatory floor", gap.min() >= -TOL, f"{gap.min():.1f} MWh above",
            f"compliance floor up to {hard.max():.2f} MWh (no contract on this route)")
        kr, kl = tau / eta, tau * eta
        c1 = np.minimum(soc, soc_start) - kr * (rr + cr)
        c2 = E - (np.maximum(soc, soc_start) + kl * (rl + cl))
        add("fcess_cover", f"Grid services deliverable for {tau:g} h", c1.min() >= -TOL and c2.min() >= -TOL,
            f"{min(c1.min(), c2.min()):.2f} MWh spare",
            f"stored energy behind raise at least {c1.min():.3f} MWh; room behind lower at least {c2.min():.3f} MWh")
        planned_out = float(np.clip(net, 0, None).sum() * dp.DT + util[0] * rr.sum() * dp.DT)
        before = sc.asset.limits.cycles_per_day * E - float(rec["budget"])
        add("cycles", f"Daily discharge ≤ {sc.asset.limits.cycles_per_day:g} cycle", planned_out <= float(rec["budget"]) + TOL,
            f"{before + planned_out:.1f} MWh", f"08:00–09:00 {before:.2f} MWh + planned {planned_out:.2f} MWh ≤ {E:g} MWh")
        lf, lfe = site["lf"], site["lfe"]
        if factory:
            floor_site = np.zeros(len(net))
            for fl in rec.get("soft_floors") or ():
                floor_site = np.maximum(floor_site, np.asarray(fl, float))
            short = floor_site - soc
            add("site_reserve", "Stored energy ≥ factory reserve for tomorrow", short.max() <= TOL,
                f"{max(0.0, short.max()):.1f} MWh below",
                f"economic floor: the energy tomorrow's factory needs under CMD and IRCR (public at 09:00), up to "
                f"{floor_site.max():.2f} MWh" if floor_site.max() > 0 else "no factory reserve on this day", economic=True)
            for name, c in site["caps"].items():
                live = np.isfinite(c)
                if not live.any():
                    continue
                over = np.where(live, -net - c, -np.inf)
                cap_mw = (c + lfe)[live]
                label = (f"Meter import ≤ CMD {sc.route.cmd_mw:g} MW" if name == "cmd" else
                         f"Meter import ≤ IRCR cap {cap_mw.min():.0f} MW")
                add("cmd" if name == "cmd" else "ircr_cap", label, over.max() <= TOL, f"{max(0.0, over.max()):.2f} MW over",
                    f"cap {cap_mw.min():.2f}–{cap_mw.max():.2f} MW on the expected load; the plan's meter is at most "
                    f"{(lfe - net)[live].max():.2f} MW (an economic cap: the plan may exceed it at a penalty)",
                    slots=np.flatnonzero(over > TOL), economic=True)
        # the connection: behind the factory meter it carries the battery and the factory together
        exp_ = net + rr + cr - lf
        add("connection_export", f"{'Meter' if factory else 'Connection'} export ≤ {export_mw:g} MW",
            exp_.max() <= export_mw + TOL, f"{max(0.0, exp_.max()):.2f} MW",
            "planned discharge plus enabled raise" + (" less the factory's planned load" if factory else "") +
            f", the most the connection must carry: {exp_.max():.3f} MW; limit {export_mw:g} MW",
            slots=np.flatnonzero(exp_ > export_mw + TOL))
        imp_ = lf - net + rl + cl
        add("connection_import", f"{'Meter' if factory else 'Connection'} import ≤ {import_mw:g} MW",
            imp_.max() <= import_mw + TOL, f"{max(0.0, imp_.max()):.2f} MW",
            ("the factory's planned load plus " if factory else "") +
            f"planned charging plus enabled lower: {imp_.max():.3f} MW; limit {import_mw:g} MW",
            slots=np.flatnonzero(imp_ > import_mw + TOL))
        # only information public at 09:00: the same day with every actual price from 09:00 on x 3
        p = self.replay(r["day"], r["connection"], r["dispatcher"], r["fcess"], r["start_mwh"], perturb=True)
        rec2 = self.record_0900(p["records"])
        diff = max(float(np.max(np.abs(np.asarray(rec[k], float) - np.asarray(rec2[k], float)))) for k in ("net", "soc", *SERVICES))
        moved = float(np.max(np.abs(r["log"]["energy_mwh"].to_numpy() - p["log"]["energy_mwh"].to_numpy())))
        add("info_timing", "Only data public at 09:00", diff == 0.0 and rec["money"] == rec2["money"], f"{diff:.1e} MW",
            f"replayed with every actual price from 09:00 on × 3: the 09:00 plan moved {diff:.1e} MW (the executed day "
            f"moved up to {moved:.2f} MWh in a half-hour, so the change took effect)")
        if r["dispatcher"] == "e5":
            req = of.request(r["day"], r["forward"], "app", "app")
            errs = of.rule_errors(req, P)
            if self.schema is not None:
                errs = of.schema_errors(req, self.schema["createSubmissionJsonRequest"], self.schema) + errs
            add("offer_format", "09:00 offers pass AEMO's format and rules", not errs, f"{len(errs)} errors",
                f"{len(r['forward'])} dispatch intervals checked by vpp.v2.offers schema_errors and rule_errors; "
                "facility code and other registration fields are placeholders")
        out.append({"id": "contractual", "label": "Contracts (PPA, SCM, NCESS)", "status": "not_checked", "worst": None,
                    "detail": "no contract on this route; Zenith's real contracts are not in the model", "slots": []})
        out.append({"id": "market_eligibility", "label": "Registration and FCESS accreditation", "status": "not_checked",
                    "worst": None, "detail": "the model assumes one Scheduled facility accredited for all four FCESS; "
                    "no check exists", "slots": []})
        return out

    def _price(self, r: dict, gaps: list) -> dict:
        D = r["day"]
        fc = np.asarray(r["day_input"]["fc0900"], float)
        p10 = p90 = None
        g = self.scenarios.get(D)
        if g is not None and len(g):
            piv = g.pivot_table(index="slot", columns="member", values="energy")
            slots = piv.index.to_numpy().astype(int)
            offset = 0 if slots.min() == FIRST else FIRST
            q10, q90 = np.quantile(piv.to_numpy(), [0.1, 0.9], axis=1)
            p10, p90 = [None] * N, [None] * N
            for s_, a, b in zip(slots + offset, q10, q90):
                p10[int(s_)], p90[int(s_)] = num(a, 3), num(b, 3)
        else:
            gaps.append({"field": "price.forecast_p10, forecast_p90",
                         "reason": "the 09:00 price scenarios end on 2026-10-15; none for this day"})
        risk = self.risk.get(D)
        log = r["log"]
        return {"forecast_p50": [None] * FIRST + arr(fc[FIRST:], 3), "forecast_p10": p10, "forecast_p90": p90,
                "spike_prob_ge_200": None if risk is None else arr(risk, 4),
                "actual_before_0900": arr(log["rtp"].to_numpy()[:FIRST], 3) + [None] * (N - FIRST),
                "source": "p50 data/processed/forecast_dispatch_0900.parquet; p10/p90 the 10% and 90% of the 20 "
                          "scenarios_0900 members; spike probability spike_risk_0900"}

    def _offers(self, r: dict, soc_start: np.ndarray, gaps: list, site: dict) -> dict | None:
        if r["dispatcher"] != "e5":
            return self._offers_v1(r, gaps, site)
        if not r["forward"]:
            gaps.append({"field": "offers_0900", "reason": "no 09:00 offers were collected for this day"})
            return None
        req = of.request(r["day"], r["forward"], "app", "app")
        dis = {it["dispatchIntervalFrom"]: it for it in
               req["submission"]["variation"]["tradingDays"][0]["energy"]["facilities"][0]["dispatchIntervals"]}
        planned_price = round(of.FLOOR + 0.01, 2)
        by_hh = []
        for t in range(N):
            here = [d for d in range(t * dp.STEPS + 1, (t + 1) * dp.STEPS + 1) if d in dis]
            if t < FIRST:
                by_hh.append(None)
                continue
            acc: dict = {}
            for d in here:
                for x in dis[d]["tranches"]:
                    if x["quantity"] > 0 and x["price"] != planned_price:
                        acc.setdefault(x["price"], {})[d] = x["quantity"]
            rows = []
            for price in sorted(acc, key=lambda p: np.inf if p == "MAX" else float(p)):
                q = [acc[price].get(d, 0.0) for d in here]
                rows.append({"mw": num(np.mean(q), 4), "price_aud_per_mwh": price if price == "MAX" else num(price, 2),
                             "mw_min": num(min(q), 3), "mw_max": num(max(q), 3)})
            by_hh.append(rows)
        a = r["at9"]
        prot = [None] * FIRST
        if a and a.get("bids") is not None:
            eta, alpha, dw, k_min, ceil = a["args"]
            for t in range(FIRST, N):
                s = float(soc_start[t - FIRST])
                thr = a["bids"].thresholds(t, a["seen"], eta, alpha, dw, k_min)
                seg = np.clip(s - np.arange(len(thr)) * sv.GRID, 0.0, sv.GRID)
                prot.append(num(float(seg[thr >= ceil - 1e-9].sum()), 4))
        gaps.append({"field": "offers_0900.by_halfhour[2]", "reason": "the 09:00 submission covers the dispatch "
                     "intervals still open after the 15-minute Gate Closure: 09:15 on, so 09:00-09:30 has 3 of its 6"})
        return {"rule": "ladder", "by_halfhour": by_hh, "protected_mwh": prot,
                "ceiling_aud_per_mwh": num(of.ceiling(r["day"]), 2), "di_count": len(dis), "status": "backtest",
                "source": "vpp.v2.offers.Collector at 09:00 (the e5 replay), as the RTMS energy submission"}

    def _offers_v1(self, r: dict, gaps: list, site: dict) -> dict:
        """v1's standing offer (vpp.dispatch, offer_rule "value"): beyond the plan, the battery sells more only when
        the 5-min price reaches k_min; at the first such price in a half-hour it re-plans the rest of the day twice
        (keep the energy, sell it now) and offers at max(k_min, (alpha x value lost + wear) / MWh). That price is set
        live, so at 09:00 only the floor and the spare power are known."""
        rec = self.record_0900(r["records"])
        net = np.asarray(rec["net"], float)
        rr = np.clip(np.asarray(rec["reg_raise"], float), 0, None) + np.clip(np.asarray(rec["cont_raise"], float), 0, None)
        P, sc = r["bat"].power_mw, r["sc"]
        room = np.full(len(net), P) if sc.site is None else np.minimum(P, sc.site.export_limit_mw + site["lfe"])
        spare = np.clip(room - np.maximum(net, 0.0), 0.0, P)
        _, strat, _ = bt.strategy(bt.VALUE)
        gaps.append({"field": "offers_0900.trigger", "reason": "v1 sets each half-hour's offer price live, when the "
                     "price first reaches the floor; at 09:00 only the floor and the spare power are known"})
        return {"rule": "value", "floor_aud_per_mwh": num(strat.k_min, 2), "alpha": num(strat.alpha, 3),
                "spare_mw": pad(arr(spare, 4)), "raise_enabled_mw": pad(arr(rr, 4)), "status": "backtest",
                "source": "vpp.dispatch.value_offer_price (offer_rule 'value'), k_min and alpha of vpp.dispatch.Strategy; "
                          "spare_mw = the battery's power (behind the meter: at most the export limit plus the "
                          "expected load) less the planned discharge (the most the offer could add; it also gives up "
                          "FCESS raise headroom, which the offer must out-earn)"}

    def _value_now(self, r: dict, rec: dict) -> dict | None:
        """$ per MWh the top stored MWh is worth at 09:00 (the value table's, in the price state seen at 09:00)."""
        a = r["at9"]
        if not a or a.get("bids") is None:
            return None
        s = float(rec["stored"])
        if s < 1e-6:
            return {"stored_mwh": 0.0, "top_mwh_aud_per_mwh": None, "reason": "nothing stored at 09:00"}
        m = np.asarray(a["bids"]._values(FIRST, a["seen"]), float)
        seg = int(min(len(m) - 1, np.ceil(s / sv.GRID - 1e-9) - 1))
        return {"stored_mwh": num(s, 3), "top_mwh_aud_per_mwh": num(m[seg], 2), "segment": seg,
                "basis": "the value table's $ per MWh of the top stored MWh at the end of 09:00-09:30, in the price "
                         "state of the latest 5-min price seen at 09:00 (vpp.v2.bids.LadderBids)"}

    # ------------------------------------------------------------------------------------------------ what happened
    def outcome(self, day, connection: str = DEFAULT_CONNECTION, dispatcher: str = DEFAULT_DISPATCHER,
                fcess: tuple = SERVICES, start_mwh: float | None = None) -> dict:
        r = self.replay(day, connection, dispatcher, fcess, start_mwh)
        D, bat, log, st, summ = r["day"], r["bat"], r["log"], r["steps"], r["summary"]
        factory = connection == "factory"
        st = st.sort_values("step").reset_index(drop=True)
        base = np.maximum(st["pw_plan"].to_numpy(), 0.0)
        pw, pw_off = st["pw"].to_numpy(), st["pw_offer"].to_numpy()
        sold = st["sold"].notna().to_numpy() & (pw_off > base + 1e-9)
        extra = np.where(sold, np.maximum(0.0, np.minimum(pw, pw_off) - base), 0.0)
        cleared = extra.reshape(N, dp.STEPS).mean(axis=1)
        sold_aud = cleared * dp.DT * log["rtp"].to_numpy()
        default = tuple(r["fcess"]) == SERVICES and start_mwh is None
        name = STRATEGY[connection, dispatcher]
        saved = self.saved[connection][name]
        money = self.money[connection]
        gaps: list = []
        check = None
        if default and D in saved.index:
            check = {"replay_minus_saved_total_aud": num(summ["total"] - float(saved.loc[D, "total"]), 9),
                     "replay_minus_saved_soc_end_mwh": num(summ["soc_end"] - float(saved.loc[D, "soc_end"]), 9)}
        network, site = None, None
        if factory:
            month = D.strftime("%Y-%m")
            b = self.bills[connection]
            b = b[(b["strategy"] == name) & (b["month"].astype(str) == month)]
            if default and len(b):
                network = float(b["saving"].iloc[0] / b["days"].iloc[0])
            else:
                gaps.append({"field": "money_aud.network", "reason": "the network bill is settled per calendar month "
                             "on its highest import: with changed settings only this day is replayed, so the month's "
                             "bill is not known"})
            gaps.append({"field": "money_aud.ircr", "reason": "IRCR is settled once per Hot Season on the median "
                         "import in the 12 peak intervals; it is not split into days (Evidence shows it per year)"})
            over = log["meter_mw"].to_numpy() - log["cap_mw"].to_numpy()
            site = {"load_mw": arr(log["site_load"]), "meter_mw": arr(log["meter_mw"]), "cap_mw": arr(log["cap_mw"]),
                    "max_import_mw": num(summ["max_import_mw"]),
                    "cap_excess_halfhours": [clock(t) for t in np.flatnonzero(over > 1e-6)],
                    "network_basis": None if network is None else
                    f"the month's ({month}) RT7 bill saving against the factory without a battery, A${b['saving'].iloc[0]:.2f} "
                    f"over {b['days'].iloc[0]:g} days, per day (not this day's own); the month's highest import with the "
                    f"battery {b['peak_mw'].iloc[0]:.3f} MW against a {r['sc'].route.cmd_mw:g} MW CMD"}
        baselines = []
        for ref, short in ((PLAN_ONLY, "plan only"), (STRATEGY[connection, other(dispatcher)], other(dispatcher)),
                           (PERFECT, "prices known")):
            if ref in money and D in money.index and np.isfinite(money.loc[D, ref]):
                baselines.append({"name": short, "net_aud": num(money.loc[D, ref])})
        net = summ["total"] + (network or 0.0)
        return {
            "day": str(D.date()), "basis": "outcome", "unvalidated": not default, "connection": connection,
            "dispatcher": dispatcher,
            "battery_mw": arr(log["energy_mwh"].to_numpy() / dp.DT), "soc_mwh": arr(np.r_[log["soc_start"].iloc[0], log["soc"].to_numpy()]),
            "fcess_mw": {k: arr(log[f"{k}_mw"]) for k in SERVICES},
            "offer_cleared_mw": arr(cleared), "offer_sold_aud": arr(sold_aud, 2),
            "offer_price_live": arr(log["offer_price"].to_numpy(), 2),
            "price_actual": arr(log["rtp"], 3), "price_actual_max_5min": arr(log["max_5min_price"], 3),
            "money_aud": {"energy": num(summ["energy"]), "fcess": num(summ["fcess"]), "wear": num(summ["wear"]),
                          **({"network": num(network)} if factory else {}),
                          "net": num(net) if not factory or network is not None else num(summ["total"]),
                          "market_net": num(summ["total"])},
            "site": site,
            "discharged_mwh": num(summ["discharged_mwh"]), "replans": int(summ["replans"]),
            "baseline_same_day": baselines[0] if baselines else None, "baselines": baselines,
            "baselines_source": f"{saved_rel(connection)} (same day, prices, battery; money as this connection counts it)",
            "replay_check": check, "status": "backtest", "gaps": gaps,
            "source": f"vpp.v2.harness.route_days on route {r['sc'].route.name} from the saved start state; money = "
                      "run_day summary (energy + FCESS − wear)" +
                      (" + the month's network bill saving per day" if factory else
                       "; no network bill on this connection [assumed]"),
        }
