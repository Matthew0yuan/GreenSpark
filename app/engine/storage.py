"""One trading day of Zenith's storage network: 10 MW / 40 MWh on its own connection, registered Scheduled, all four
FCESS (route `sa`, analysis/2026-10-27/standalone_v2). Replays the adopted dispatcher from the day's saved start
state and splits the result into what was known at 09:00 (plan, offers, forecasts, checks) and what happened
(outcome), which the API serves separately.

Ported from analysis/2026-10-26/ui_redesign/export/site_days_replay.py and site_days_export.py; the factory blocks
(meter, CMD, IRCR, network bill) are gone because this asset has no site."""

import json
from collections import OrderedDict
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp import dispatch as dp
from vpp import storage_value as sv
from vpp.data import PROCESSED
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs
from vpp.v2 import offers as of
from vpp.v2.config import V1

from .common import FIRST, N, ROOT, STANDALONE, arr, clock, hot_season, num, pad, period, period_key, weekday_label

TOL = 1e-6
SERVICES = tuple(dp.FCESS)  # reg_raise, cont_raise, reg_lower, cont_lower
SCHEMA = ROOT / "data" / "raw" / "aemo_rtms" / "rtms_openapi.json"
CACHE_DAYS = 24


class Storage:
    """Loads the market days once (~5 s) and replays days on request (~1.5-4 s each, cached)."""

    def __init__(self):
        self.days = bt.load_days("0900")
        self.aux = bt.q2b_aux()
        self.index = {d["day"]: i for i, d in enumerate(self.days)}
        self.adopted = (STANDALONE / "adopted.txt").read_text(encoding="utf-8").strip()
        self.is_e5 = self.adopted == vb.E5.name
        self.baseline = bt.VALUE if self.is_e5 else vb.E5.name
        daily = pd.read_csv(STANDALONE / "daily.csv", parse_dates=["day"])
        refs = STANDALONE / "refs_daily.csv"  # the 09:00 plan alone ("forecast") and prices known ahead ("perfect")
        if refs.exists():
            daily = pd.concat([daily, pd.read_csv(refs, parse_dates=["day"])], ignore_index=True)
        self.saved = {name: g.set_index("day").sort_index() for name, g in daily.groupby("strategy")}
        self.cache: OrderedDict = OrderedDict()
        self.schema = json.loads(SCHEMA.read_text())["components"]["schemas"] if SCHEMA.exists() else None
        sc = pd.read_parquet(PROCESSED / "scenarios_0900.parquet", columns=["day", "member", "slot", "energy"])
        sc["day"] = pd.to_datetime(sc["day"])
        self.scenarios = {d: g for d, g in sc.groupby("day")}
        risk = pd.read_parquet(PROCESSED / "spike_risk_0900.parquet", columns=["ts", "target_date", "days_ahead", "p_spike"])
        risk = risk[risk["days_ahead"] == 0]
        self.risk = {pd.Timestamp(d): g.sort_values("ts")["p_spike"].to_numpy() for d, g in risk.groupby("target_date")}

    # ------------------------------------------------------------------------------------------------ meta
    def meta(self) -> dict:
        days = [d["day"] for d in self.days]
        return {"first_day": str(days[0].date()), "last_day": str(days[-1].date()), "days": len(days),
                "dispatcher": self.dispatcher(), "baseline": self.baseline_info(),
                "asset": self.asset_info()}

    def dispatcher(self) -> dict:
        return {"name": self.adopted, "short": "e5" if self.is_e5 else "v1",
                "chosen_by": "analysis/2026-10-27/standalone_v2/PLAN.md: in-sample uplift over v1, month-block 95% "
                             "interval above 0"}

    def baseline_info(self) -> dict:
        return {"name": self.baseline, "short": "v1" if self.is_e5 else "e5"}

    def asset_info(self) -> dict:
        bat = bt.Q2B_ASSET.battery
        return {"power_mw": bat.power_mw, "energy_mwh": bat.energy_mwh, "round_trip": bat.round_trip,
                "wear_aud_per_mwh": bt.Q2B_ASSET.wear_per_mwh, "registration": "scheduled", "metering": "standalone",
                "facilities": 1, "fcess": list(SERVICES), "status": "assumed"}

    # ------------------------------------------------------------------------------------------------ replay
    def _scenario(self, fcess: tuple):
        sc = bt._sc("sa", ct.Route("sa", metering="standalone", fcess=fcess), (bt.VALUE,))
        if sc is None:
            raise ValueError(f"route refused: {bt.REFUSED}")
        return sc

    def _cfg(self):
        return replace(vb.E5 if self.is_e5 else V1, record=True)

    def start_mwh(self, day: pd.Timestamp) -> float:
        g = self.saved[self.adopted]
        before = g[g.index < day]
        return float(before["soc_end"].iloc[-1]) if len(before) else 0.0

    def replay(self, day, fcess: tuple = SERVICES, start_mwh: float | None = None, perturb: bool = False) -> dict:
        day = pd.Timestamp(day)
        key = (day, tuple(fcess), None if start_mwh is None else round(float(start_mwh), 6), perturb)
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
        sc, cfg = self._scenario(tuple(fcess)), self._cfg()
        bat = hs.scenario(sc, cfg).asset.battery
        col = of.Collector(bat)
        steps: list = []
        seen: dict = {}

        def on_step(d, x):
            x = dict(x)
            if x["load_act"] is None:
                x["load_act"] = 0.0
            if self.is_e5:
                col(d, x)
            en = x["enabled"] or {}
            steps.append({"step": x["step"], "t": x["t"], "s": x["s"], "pw_plan": x["pw_plan"], "sold": x["sold"],
                          "pw_offer": x["pw_offer"], "pw": x["pw"], "price": x["price"],
                          **{f"en_{k}": float(v) for k, v in en.items()}})

        def on_day(d, kw, summary, log):
            seen["kw"] = kw
            if self.is_e5:  # a standalone connection: export and import rooms are the battery's power, no load
                P = bat.power_mw
                col.on_day(d, {**kw, "load_fc": np.zeros(N), "site": SimpleNamespace(export_limit_mw=P, import_limit_mw=P)},
                           summary, log)

        s0 = self.start_mwh(day) if start_mwh is None else float(start_mwh)
        _, terms, summary, log, recs = next(hs.route_days(sc, cfg, s0, {}, until=day, on_day=on_day, on_step=on_step))
        out = {"day": day, "sc": sc, "bat": bat, "summary": summary, "log": log, "records": recs,
               "steps": pd.DataFrame(steps), "forward": col.forward.get(day, {}), "at9": col._at9, "day_input": sub[0],
               "start_mwh": s0, "fcess": tuple(fcess)}
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

    # ------------------------------------------------------------------------------------------------ the 09:00 plan
    def plan(self, day, fcess: tuple = SERVICES, start_mwh: float | None = None, export_mw: float | None = None,
             import_mw: float | None = None) -> dict:
        """What was known at 09:00: the plan, the offers, the forecasts and the checks. Settings that change the
        engine's inputs (fcess, start_mwh) replay the day; the connection limits only re-check the plan."""
        r = self.replay(day, fcess, start_mwh)
        D, bat, sc = r["day"], r["bat"], r["sc"]
        P, E, eta = bat.power_mw, bat.energy_mwh, bat.eta
        export_mw = P if export_mw is None else float(export_mw)
        import_mw = P if import_mw is None else float(import_mw)
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
        action = ["charge" if x < -1e-3 else "discharge" if x > 1e-3 else "hold" for x in net]
        # before 09:00 the battery ran the 08:00 plan: executed, known at 09:00
        log = r["log"]
        pre_mw = (log["energy_mwh"].to_numpy()[:FIRST] / dp.DT).tolist()
        pre_soc = [float(log["soc_start"].iloc[0]), *log["soc"].to_numpy()[:FIRST].tolist()]

        wear_settled = rec["wear"] - (rec["money"] - rec["value"])
        reg_energy = rec["value"] - (rec["energy_value"] + rec["fcess_value"] - rec["wear"] + rec["stored_value"])
        value = {"energy": num(rec["energy_value"]), "regulation_energy": num(reg_energy), "fcess": num(rec["fcess_value"]),
                 "wear": num(wear_settled), "stored_end": num(rec["stored_value"]), "net": num(rec["money"])}

        checks = self._checks(r, rec, net, soc, soc_start, f, hard, tau, export_mw, import_mw)
        hard_fail = [c for c in checks if c["status"] == "fail"]
        blocked = [c for c in hard_fail if c["id"].startswith("connection")]
        state = "BLOCKED" if hard_fail else "PASS"
        breaches = sorted({k for c in blocked for k in c.get("slots", [])})

        default = tuple(r["fcess"]) == SERVICES and start_mwh is None
        out = {
            "day": str(D.date()), "weekday_label": weekday_label(D), "basis": "plan",
            "period": period(period_key(D), self._period_days(period_key(D))), "hot_season": hot_season(D),
            "dispatcher": self.dispatcher(), "asset": self.asset_info(),
            "settings": {"fcess": list(r["fcess"]), "start_mwh": start_mwh, "export_mw": export_mw, "import_mw": import_mw},
            "unvalidated": not default or export_mw != P or import_mw != P,
            "start_state": {"stored_mwh": num(r["start_mwh"]),
                            "source": "the day before's stored energy at 08:00 in the adopted dispatcher's backtest "
                                      "(analysis/2026-10-27/standalone_v2/daily.csv soc_end)" if start_mwh is None
                            else "set in Settings"},
            "plan": {"first_interval": FIRST, "stored_at_0900_mwh": num(rec["stored"]),
                     "battery_mw": [*arr(pre_mw), *arr(net)], "soc_mwh": [*arr(pre_soc), *arr(soc)],
                     "pre_0900": "08:00-09:00 is the 08:00 plan as executed (known at 09:00)",
                     "fcess_mw": {k: pad(arr(f[k])) for k in SERVICES}, "reserve_floor_mwh": pad(arr(floor)),
                     "action": ["pre"] * FIRST + action, "value_at_forecast_aud": value,
                     "source": "vpp.dispatch.run_day on_plan record at interval 2, kind 'reveal'", "status": "backtest"},
            "price": self._price(r, gaps),
            "offers_0900": self._offers(r, soc_start, gaps),
            "value_now": self._value_now(r, rec),
            "checks": checks,
            "state": state, "blocked_halfhours": breaches,
            "gaps": gaps,
        }
        return out

    def _period_days(self, key: str) -> int:
        return sum(period_key(d["day"]) == key for d in self.days)

    def _checks(self, r, rec, net, soc, soc_start, f, hard, tau, export_mw, import_mw) -> list:
        sc, bat = r["sc"], r["bat"]
        P, E, eta = bat.power_mw, bat.energy_mwh, bat.eta
        rr, cr, rl, cl = f["reg_raise"], f["cont_raise"], f["reg_lower"], f["cont_lower"]
        util = sc.route.reg_util
        out = []

        def add(cid, label, ok, worst, detail, status=None, slots=()):
            out.append({"id": cid, "label": label, "status": status or ("pass" if ok else "fail"), "worst": worst,
                        "detail": detail, "slots": [int(FIRST + k) for k in slots]})

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
        exp_ = net + rr + cr
        add("connection_export", f"Connection export ≤ {export_mw:g} MW", exp_.max() <= export_mw + TOL,
            f"{max(0.0, exp_.max()):.2f} MW", f"planned discharge plus enabled raise, the most the connection must carry: "
            f"{exp_.max():.3f} MW; limit {export_mw:g} MW", slots=np.flatnonzero(exp_ > export_mw + TOL))
        imp_ = -net + rl + cl
        add("connection_import", f"Connection import ≤ {import_mw:g} MW", imp_.max() <= import_mw + TOL,
            f"{max(0.0, imp_.max()):.2f} MW", f"planned charging plus enabled lower: {imp_.max():.3f} MW; limit "
            f"{import_mw:g} MW", slots=np.flatnonzero(imp_ > import_mw + TOL))
        # only information public at 09:00: the same day with every actual price from 09:00 on x 3
        p = self.replay(r["day"], r["fcess"], r["start_mwh"], perturb=True)
        rec2 = self.record_0900(p["records"])
        diff = max(float(np.max(np.abs(np.asarray(rec[k], float) - np.asarray(rec2[k], float)))) for k in ("net", "soc", *SERVICES))
        moved = float(np.max(np.abs(r["log"]["energy_mwh"].to_numpy() - p["log"]["energy_mwh"].to_numpy())))
        add("info_timing", "Only data public at 09:00", diff == 0.0 and rec["money"] == rec2["money"], f"{diff:.1e} MW",
            f"replayed with every actual price from 09:00 on × 3: the 09:00 plan moved {diff:.1e} MW (the executed day "
            f"moved up to {moved:.2f} MWh in a half-hour, so the change took effect)")
        if self.is_e5:
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

    def _offers(self, r: dict, soc_start: np.ndarray, gaps: list) -> dict | None:
        if not self.is_e5:
            return self._offers_v1(r, gaps)
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

    def _offers_v1(self, r: dict, gaps: list) -> dict:
        """v1's standing offer (vpp.dispatch, offer_rule "value"): beyond the plan, the battery sells more only when
        the 5-min price reaches k_min; at the first such price in a half-hour it re-plans the rest of the day twice
        (keep the energy, sell it now) and offers at max(k_min, (alpha x value lost + wear) / MWh). That price is set
        live, so at 09:00 only the floor and the spare power are known."""
        rec = self.record_0900(r["records"])
        net = np.asarray(rec["net"], float)
        rr = np.clip(np.asarray(rec["reg_raise"], float), 0, None) + np.clip(np.asarray(rec["cont_raise"], float), 0, None)
        P = r["bat"].power_mw
        spare = np.clip(P - np.maximum(net, 0.0), 0.0, P)
        _, strat, _ = bt.strategy(bt.VALUE)
        gaps.append({"field": "offers_0900.trigger", "reason": "v1 sets each half-hour's offer price live, when the "
                     "price first reaches the floor; at 09:00 only the floor and the spare power are known"})
        return {"rule": "value", "floor_aud_per_mwh": num(strat.k_min, 2), "alpha": num(strat.alpha, 3),
                "spare_mw": pad(arr(spare, 4)), "raise_enabled_mw": pad(arr(rr, 4)), "status": "backtest",
                "source": "vpp.dispatch.value_offer_price (offer_rule 'value'), k_min and alpha of vpp.dispatch.Strategy; "
                          "spare_mw = battery power − planned discharge (the most the offer could add; it also gives "
                          "up FCESS raise headroom, which the offer must out-earn)"}

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
    def outcome(self, day, fcess: tuple = SERVICES, start_mwh: float | None = None) -> dict:
        r = self.replay(day, fcess, start_mwh)
        D, bat, log, st, summ = r["day"], r["bat"], r["log"], r["steps"], r["summary"]
        st = st.sort_values("step").reset_index(drop=True)
        base = np.maximum(st["pw_plan"].to_numpy(), 0.0)
        pw, pw_off = st["pw"].to_numpy(), st["pw_offer"].to_numpy()
        sold = st["sold"].notna().to_numpy() & (pw_off > base + 1e-9)
        extra = np.where(sold, np.maximum(0.0, np.minimum(pw, pw_off) - base), 0.0)
        cleared = extra.reshape(N, dp.STEPS).mean(axis=1)
        sold_aud = cleared * dp.DT * log["rtp"].to_numpy()
        default = tuple(r["fcess"]) == SERVICES and start_mwh is None
        saved = self.saved[self.adopted]
        check = None
        if default and D in saved.index:
            check = {"replay_minus_saved_total_aud": num(summ["total"] - float(saved.loc[D, "total"]), 9),
                     "replay_minus_saved_soc_end_mwh": num(summ["soc_end"] - float(saved.loc[D, "soc_end"]), 9)}
        baselines = []
        for name, short in (("forecast", "plan only"), (self.baseline, self.baseline_info()["short"]), ("perfect", "prices known")):
            b = self.saved.get(name)
            if b is not None and D in b.index:
                baselines.append({"name": short, "net_aud": num(b.loc[D, "total"])})
        baseline = baselines[0] if baselines else None
        return {
            "day": str(D.date()), "basis": "outcome", "unvalidated": not default,
            "battery_mw": arr(log["energy_mwh"].to_numpy() / dp.DT), "soc_mwh": arr(np.r_[log["soc_start"].iloc[0], log["soc"].to_numpy()]),
            "fcess_mw": {k: arr(log[f"{k}_mw"]) for k in SERVICES},
            "offer_cleared_mw": arr(cleared), "offer_sold_aud": arr(sold_aud, 2),
            "offer_price_live": arr(log["offer_price"].to_numpy(), 2),
            "price_actual": arr(log["rtp"], 3), "price_actual_max_5min": arr(log["max_5min_price"], 3),
            "money_aud": {"energy": num(summ["energy"]), "fcess": num(summ["fcess"]), "wear": num(summ["wear"]),
                          "net": num(summ["total"])},
            "discharged_mwh": num(summ["discharged_mwh"]), "replans": int(summ["replans"]),
            "baseline_same_day": baseline, "baselines": baselines,
            "baselines_source": "analysis/2026-10-27/standalone_v2 daily.csv and refs_daily.csv (same day, prices, battery)",
            "replay_check": check, "status": "backtest",
            "source": "vpp.v2.harness.route_days on route sa from the saved start state; money = run_day summary "
                      "(energy + FCESS − wear; no network bill on this connection [assumed])",
        }
