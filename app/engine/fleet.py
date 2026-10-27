"""Customer batteries (VPP): the 09:00 call decision, the operator's own calls, and who gets what.

Uses vpp/policy/policy_simulator.py read-only, with the saved terms and fitted rules of reports/policy/
policy_backtest.csv (scenario S2b: Zenith as the customers' retailer under the assumed 2027 capacity rule; S3 is not in
the product, ROUND6.md 2.4). Ported from analysis/2026-10-26/ui_redesign/export/fleet_export.py.

The operator decides each day at 09:00. A day the operator has not decided follows the model's rule, given the calls
already used; the effective calls of the contract year are valued with the model's own accounts (ps.account), so
"your calls" and "model calls" are settled the same way. The customers are the policy model's synthetic households
(13.5 kWh batteries): the real customers are contestable businesses, so the money is a magnitude only."""

import numpy as np
import pandas as pd

from vpp import site_bill as sb
from vpp.data import PROCESSED
from vpp.policy import policy_simulator as ps

from .common import ROOT, clock, hot_season, num, weekday_label

POL = ROOT / "reports" / "policy"
OBL = ps.Obligations("S2b retailer, 2027 IRCR", capacity="net")
CENTRAL = ps.Participation()
A = CENTRAL.availability
CASES = {"ref": "B reference", "custom": "C optimised"}
NAMES = {"ref": "Reference terms", "custom": "Model-suggested offer"}
TRAIN_SEASONS = [2023, 2024]  # the 2025-26 tolerance is learnt on the two seasons before it (chronological)


def policy_from(row: pd.Series, **over) -> ps.Policy:
    kw = dict(name=row["policy_name"], comp_type=row["policy_comp_type"], pay_per_kwh=float(row["policy_pay_per_kwh"]),
              pay_per_event=float(row["policy_pay_per_event"]), annual_fee=float(row["policy_annual_fee"]),
              max_events=int(row["policy_max_events"]), event_h=float(row["policy_event_h"]),
              standby_h=float(row["policy_standby_h"]), max_control_h=float(row["policy_max_control_h"]),
              reserve_soc=float(row["policy_reserve_soc"]), notice_h=float(row["policy_notice_h"]),
              window=tuple(float(v) for v in str(row["policy_window"]).strip("()").split(",")),
              max_events_per_day=int(row["policy_max_events_per_day"]),
              offset_rate=float(row["policy_offset_rate"]), min_usable_kwh=float(row["policy_min_usable_kwh"]))
    kw.update(over)
    return ps.Policy(**kw)


def rule_from(row: pd.Series) -> ps.CallRule:
    return ps.CallRule(kind=row["rule_kind"], theta=float(row["rule_theta"]), ircr_q=float(row["rule_ircr_q"]),
                       reserve_for_ircr=bool(row["rule_reserve_for_ircr"]))


class Offer:
    """One offer's terms, fitted rule, enrolment and candidate calls (the family of every possible activation)."""

    def __init__(self, fl: "Fleet", oid: str, pol: ps.Policy, rule: ps.CallRule, fit: ps.Fit):
        self.id, self.pol, self.rule, self.fit = oid, pol, rule, fit
        self.key = (pol.event_h, pol.standby_h, pol.reserve_soc, ps.Household(), tuple(pol.window), str(ps.LAST_DAY.date()))
        self.fam = ps.family(*self.key)
        e_starts = np.flatnonzero(self.fam.starts + 2 * pol.event_h <= 2 * (pol.window[1] - 8))
        self.scores = ps.energy_scores(self.key, pol, OBL, fl.fc, fl.m, e_starts)
        self.enrolled = CENTRAL.eligible * CENTRAL.share(fit.cust)
        self.ircr_j = self.fam.index(ps.ircr_start(pol.event_h))


class Fleet:
    """Loads the market, forecasts and both offers once (~30 s); each request after that is well under a second."""

    def __init__(self):
        self.cache: dict = {}
        self.m, self.fc = ps.load_market(), ps.load_forecasts()
        self.years = ps.contract_years(self.m)
        self.peaks = ps.peak_intervals()
        self.saved = pd.read_csv(POL / "policy_backtest.csv")
        self.offers = {}
        for oid, case in CASES.items():
            row = self._saved(case, "in-sample")
            pol, rule = policy_from(row), rule_from(row)
            fit = ps.fit_rule(pol, OBL, CENTRAL, rules=[rule])
            if abs(fit.zen - row["fit_zen"]) > 1e-6:
                raise RuntimeError(f"{oid}: fitted rule does not reproduce the saved study ({fit.zen} vs {row['fit_zen']})")
            self.offers[oid] = Offer(self, oid, pol, rule, fit)
        self.fy = np.where(self.m.days.month >= 7, self.m.days.year, self.m.days.year - 1)
        self.season = ps.season_of(self.m)
        self._season_tables()
        self._req = self._demand_forecasts()
        df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["req_energy_mw"])
        self.req_actual = df30["req_energy_mw"]
        # self-check: with no operator decision the contract year equals the saved study
        for oid, case in CASES.items():
            got = self.value(oid, 2025, {})["parties_aud"]["zenith"]
            want = float(self._saved(case, "in-sample")["zenith_profit"])
            if abs(got - want) > 0.01:
                raise RuntimeError(f"{oid}: replayed Zenith profit {got} differs from the saved {want}")

    def _saved(self, case: str, period: str) -> pd.Series:
        x = self.saved[(self.saved["scenario"] == "S2b") & (self.saved["case"] == case) & (self.saved["period"] == period)]
        if len(x) != 1:
            raise KeyError((case, period))
        return x.iloc[0]

    # ------------------------------------------------------------------------------------------------ forecasts
    def _season_tables(self):
        """The peak-day rule's forecast and threshold for every 2025-26 Hot Season day, per tolerance quantile, as
        vpp.site_bill.dynamic_flags computes them (fleet_export.season_table), checked against the model's flags."""
        actual = sb.daily_max(pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["req_energy_mw"])["req_energy_mw"])
        self.actual_daily_max = actual
        p0, p1 = sb.peak_day_predictor(0), sb.peak_day_predictor(1)
        self.tables = {}
        for q in sorted({o.rule.ircr_q for o in self.offers.values()}):
            t = []
            for lag, pred in ((1, p0), (2, p1)):
                tol = sb.forecast_tolerance(pred, actual, TRAIN_SEASONS, q)
                df = pd.concat({"f": pred, "a": actual}, axis=1)
                df = df[sb.hot_season(df.index) == 2025]
                rows = []
                for i, d in enumerate(df.index):
                    known = df["a"].iloc[:max(0, i - (lag - 1))].dropna()
                    kth = known.nlargest(4).iloc[-1] if len(known) >= 4 else -np.inf
                    f = df.loc[d, "f"]
                    rows.append({"day": d, "f": f, "kth": kth, "thr": kth - tol, "flag": bool(pd.notna(f) and f >= kth - tol)})
                t.append(pd.DataFrame(rows).set_index("day"))
            self.tables[q] = t
            hs_idx = np.flatnonzero(self.season == 2025)
            mine = np.array([bool(t[0]["flag"].get(self.m.days[i], False) or t[1]["flag"].get(self.m.days[i], False))
                             for i in hs_idx])
            if not (mine == self.fc.flags[q][hs_idx]).all():
                raise RuntimeError(f"peak-day flag replay differs from the model's flags (q={q})")

    def _demand_forecasts(self) -> dict:
        """AEMO's half-hourly energy requirement forecast issued at 09:00 for the same day (pre-dispatch), MW."""
        s = pd.read_parquet(PROCESSED / "spike_table.parquet", columns=["issue_time", "target_date", "slot", "pd_req", "days_ahead"])
        s = s[(s["days_ahead"] == 0) & (s["issue_time"].dt.hour == 9)]
        return {pd.Timestamp(d): g.set_index("slot")["pd_req"] for d, g in s.groupby("target_date")}

    # ------------------------------------------------------------------------------------------------ the calls
    def _i(self, day) -> int:
        d = pd.Timestamp(day)
        if d not in self.m.days:
            raise KeyError(f"no customer-battery data for {d.date()}")
        return int(self.m.days.get_loc(d))

    def contract_year(self, day) -> int:
        return int(self.fy[self._i(day)])

    def _model_call(self, o: Offer, i: int, days_from: np.ndarray, left: int):
        """The rule's 09:00 decision on day i given the calls left: (start index, kind) or None."""
        if left <= 0:
            return None
        energy_on = OBL.energy_to_zenith and int(self.fy[i]) >= 2025  # 09:00 forecasts exist from 2025-04-01
        c = ps.select_calls(days_from, o.fam, o.pol, o.rule, self.fc, o.scores, True, energy_on, self.m, cap=left)
        if len(c.day) and int(c.day[0]) == i:
            return int(c.start[0]), str(c.kind[0])
        return None

    def effective(self, oid: str, y: int, decisions: dict) -> list[dict]:
        """Every day of contract year y in order: the model's call given the calls used so far, the operator's
        decision if any, and the call that counts. decisions: {"YYYY-MM-DD": {"call": bool, "start": "HH:MM"}}."""
        key = (oid, y, tuple(sorted((k, v["call"], v.get("start")) for k, v in decisions.items())))
        if key in self.cache:
            return self.cache[key]
        o = self.offers[oid]
        days = self.years[y]
        used, out = 0, []
        for n, i in enumerate(days):
            left = o.pol.max_events - used
            model = self._model_call(o, int(i), days[n:], left)
            ds = str(self.m.days[i].date())
            op = decisions.get(ds)
            if op is not None:
                j = self.start_index(o, op.get("start")) if op["call"] else None
                eff = (j, "ircr" if self.season[i] >= 0 else "energy") if op["call"] and left > 0 else None
            else:
                eff = model
            out.append({"i": int(i), "day": ds, "left_before": left, "model": model, "operator": op, "call": eff})
            used += int(eff is not None)
        self.cache[key] = out
        return out

    def start_index(self, o: Offer, start: str | None) -> int:
        if not start:
            return o.ircr_j
        h, m_ = (int(x) for x in start.split(":"))
        slot = int(round(((h - 8) % 24) * 2 + m_ / 30))
        hit = np.flatnonzero(o.fam.starts == slot)
        if not len(hit):
            raise ValueError(f"{start} is not an allowed event start ({self.start_options(o)})")
        return int(hit[0])

    def start_options(self, o: Offer) -> list[str]:
        return [clock(int(s)) for s in o.fam.starts]

    def _calls(self, rows: list[dict]) -> ps.Calls:
        c = [(r["i"], r["call"][0], r["call"][1]) for r in rows if r["call"] is not None]
        return ps.Calls(np.array([x[0] for x in c], int), np.array([x[1] for x in c], int), np.array([x[2] for x in c], object))

    # ------------------------------------------------------------------------------------------------ one day
    def call_day(self, oid: str, day, decisions: dict, hindsight: bool = False) -> dict:
        o = self.offers[oid]
        D = pd.Timestamp(day)
        i = self._i(D)
        y = int(self.fy[i])
        rows = self.effective(oid, y, decisions)
        row = next(r for r in rows if r["i"] == i)
        hs_y = int(self.season[i])
        q = o.rule.ircr_q
        fc = None
        if hs_y == 2025:
            t0, t1 = self.tables[q]
            g = lambda t, k: t[k].get(D, np.nan)  # noqa: E731
            fc = {"peak_mw": num(g(t0, "f"), 1), "threshold_mw": num(g(t0, "thr"), 1),
                  "dayahead_mw": num(g(t1, "f"), 1), "dayahead_threshold_mw": num(g(t1, "thr"), 1),
                  "fourth_highest_mw": num(g(t0, "kth"), 1), "margin_mw": num(g(t0, "kth") - g(t0, "thr"), 1),
                  "flagged": bool(self.fc.flags[q][i]),
                  "routes": [n for n, t in (("same_day", t0), ("day_ahead", t1)) if bool(t["flag"].get(D, False))],
                  "first_days_auto": bool(np.isinf(g(t0, "kth")) or np.isinf(g(t1, "kth")))}
        curve = self._req.get(D)
        demand = None
        if curve is not None:
            demand = [None] * 48
            for s, v in curve.items():
                if 0 <= int(s) < 48:
                    demand[int(s)] = num(v, 1)
        model = row["model"]
        reason = ("cap reached" if row["left_before"] <= 0 else "peak-day flag" if model and model[1] == "ircr"
                  else "price margin" if model else "outside Hot Season" if hs_y < 0 else "not flagged")
        eff = row["call"]
        pay = self._payment(o, i, eff[0] if eff else (model[0] if model else o.ircr_j), hindsight=False)
        used_before = o.pol.max_events - row["left_before"]
        hs_left = int(((self.season == hs_y) & (np.arange(len(self.season)) > i)).sum()) if hs_y >= 0 else 0
        out = {
            "day": str(D.date()), "weekday_label": weekday_label(D), "offer": oid, "offer_name": NAMES[oid],
            "contract_year": f"{y}-{(y + 1) % 100:02d}", "hot_season": hot_season(D) if hs_y >= 0 else None,
            "in_sample": D < pd.Timestamp("2026-07-01"),
            "forecast": fc, "demand_forecast_mw": demand,
            "model": {"call": model is not None, "kind": model[1] if model else None, "reason": reason,
                      **self._window(o, model[0] if model else None)},
            "operator": row["operator"],
            "decision": {"call": eff is not None, "source": "operator" if row["operator"] is not None else "model",
                         **self._window(o, eff[0] if eff else None)},
            "calls": {"used_before": int(used_before), "used_with_today": int(used_before + (eff is not None)),
                      "cap": int(o.pol.max_events), "hot_season_days_left": hs_left},
            "payment": pay, "enrolled": num(o.enrolled, 1), "responding": num(o.enrolled * A, 1),
            "start_options": self.start_options(o), "event_h": o.pol.event_h, "standby_h": o.pol.standby_h,
            "reserve_pct": num(100 * o.pol.reserve_soc, 0),
            "terms": {"pay_c_per_kwh": num(100 * o.pol.pay_per_kwh, 2), "pay_aud_per_event": num(o.pol.pay_per_event, 2),
                      "max_calls": int(o.pol.max_events),
                      "status": "researched" if oid == "ref" else "assumed"},
        }
        if hindsight:
            act = self.req_actual[(self.req_actual.index >= D + pd.Timedelta(hours=8)) &
                                  (self.req_actual.index < D + pd.Timedelta(hours=32))]
            peak_days = {pd.Timestamp(t[:10]) for t in sb.OFFICIAL_PEAK_INTERVALS.get(2025, [])}
            out["hindsight"] = {"actual_peak_mw": num(self.actual_daily_max.get(D, np.nan), 1),
                                "demand_actual_mw": [num(v, 1) for v in act.to_numpy()[:48]],
                                "official_peak_day": D in peak_days,
                                "peak_intervals": [t[11:] for t in sb.OFFICIAL_PEAK_INTERVALS.get(2025, []) if t.startswith(str(D.date()))],
                                "payment_settled": self._payment(o, i, eff[0], hindsight=True) if eff else None}
        return out

    def _window(self, o: Offer, j) -> dict:
        if j is None:
            return {"start": None, "end": None, "standby_from": None}
        s = int(o.fam.starts[j])
        return {"start": clock(s), "end": clock(s + int(round(2 * o.pol.event_h))),
                "standby_from": clock(s - int(round(2 * o.pol.standby_h)))}

    def _payment(self, o: Offer, i: int, j: int, hindsight: bool) -> dict:
        """Fleet payment for one call: per event + per kWh of billed export + standby offset credit, x responding.
        At 09:00 the billed kWh are not known: the expected payment uses the mean of the offer's calls in contract year
        2025-26 (in-sample); afterwards (hindsight) the day's own."""
        fam, pol = o.fam, o.pol
        if hindsight:
            kwh, sb_kwh = float(fam.paid_kwh[j, i]), float(fam.standby_kwh[j, i])
        else:
            c = self._calls(self.effective(o.id, 2025, {}))
            kwh = float(fam.paid_kwh[c.start, c.day].mean()) if len(c.day) else float(pol.nominal_kwh(ps.Household()))
            sb_kwh = float(fam.standby_kwh[c.start, c.day].mean()) if len(c.day) else 0.0
        per = pol.pay_per_event + pol.pay_per_kwh * kwh + pol.offset_rate * sb_kwh
        n = o.enrolled * A
        return {"fleet_aud": num(per * n, 2), "per_customer_aud": num(per, 3), "billed_kwh_per_customer": num(kwh, 2),
                "basis": "settled on the day" if hindsight else "expected: mean billed export of the offer's calls in "
                         "contract year 2025-26 (training period)", "transfer": True}

    # ------------------------------------------------------------------------------------------------ the season
    def season_days(self, oid: str, decisions: dict, through=None, hindsight: bool = False) -> dict:
        """The 2025-26 Hot Season, day by day: forecast, threshold, flag, the model's and the operator's calls. With
        through, days after it are cut (as known at 09:00 that day) unless hindsight."""
        o = self.offers[oid]
        rows = {r["i"]: r for r in self.effective(oid, 2025, decisions)}
        q = o.rule.ircr_q
        t0, t1 = self.tables[q]
        peak_days = {pd.Timestamp(t[:10]) for t in sb.OFFICIAL_PEAK_INTERVALS.get(2025, [])}
        out = []
        for i in np.flatnonzero(self.season == 2025):
            D = self.m.days[i]
            known = through is None or D <= pd.Timestamp(through) or hindsight
            r = rows[int(i)]
            if not known:  # after the day in view: nothing about it is known at 09:00, not even the rule's decision
                out.append({"day": str(D.date()), "forecast_mw": None, "threshold_mw": None, "flagged": False,
                            "model_call": False, "operator": None, "called": False, "actual_peak_mw": None,
                            "official_peak_day": None, "known": False})
                continue
            out.append({"day": str(D.date()), "forecast_mw": num(t0["f"].get(D, np.nan), 1),
                        "threshold_mw": num(t0["thr"].get(D, np.nan), 1), "flagged": bool(self.fc.flags[q][i]),
                        "model_call": r["model"] is not None, "operator": r["operator"], "called": r["call"] is not None,
                        "actual_peak_mw": num(self.actual_daily_max.get(D, np.nan), 1) if (hindsight or (through is not None and D < pd.Timestamp(through))) else None,
                        "official_peak_day": (D in peak_days) if hindsight else None, "known": known})
        return {"season": "2025-26", "from": "2025-12-01", "to": "2026-03-31", "days": out,
                "official_peak_days": sorted(str(d.date()) for d in peak_days) if hindsight else None}

    # ------------------------------------------------------------------------------------------------ the money
    def value(self, oid: str, y: int, decisions: dict, pol: ps.Policy | None = None) -> dict:
        """Contract year y, settled as it happened with the effective calls: money by party and the ledger lines,
        as ps.evaluate_policy does (annual items pro rata), fleet totals."""
        o = self.offers[oid]
        pol = pol or o.pol
        rows = self.effective(oid, y, decisions)
        calls = self._calls(rows)
        season = y if y in ps.SEASONS else None
        acc = ps.account(calls, o.fam, pol, OBL, CENTRAL, self.m, season, self.peaks if season is not None else None)
        n, share = o.enrolled, len(self.years[y]) / 365.0
        fee, ops = pol.annual_fee * share, OBL.ops_per_customer * share
        fixed = OBL.fixed_cost * share if n > 0 else 0.0
        zen = n * (acc["zenith_calls"] - fee - ops) - fixed
        cust = n * (acc["customer_calls"] + fee)
        retailer = n * acc["retailer"]
        system = n * (acc["system"] - ops) - fixed
        transfers = n * (acc["pay_energy"] + acc["pay_event"] + acc["offset"] + fee)
        cap_full = (A * ps.ircr_value(calls, o.fam, OBL, self.m, season, self.peaks) if season is not None and len(calls.day)
                    else 0.0)
        items = [("wholesale", "Wholesale value of the change in net export", "value_created", "WEM settlement", "Zenith", n * acc["wholesale"]),
                 ("capacity", "Capacity charge (IRCR) saved", "value_created", "capacity cost saved", "Zenith", n * cap_full),
                 ("pay_energy", "Payment per kWh exported in events", "transfer", "Zenith", "customers", n * acc["pay_energy"]),
                 ("pay_event", "Payment per event", "transfer", "Zenith", "customers", n * acc["pay_event"]),
                 ("annual_fee", "Annual fee", "transfer", "Zenith", "customers", n * fee),
                 ("offset", "Standby import credit", "transfer", "Zenith", "customers", n * acc["offset"]),
                 ("bill", "Customers' retail bill change", "transfer", "customers", "retailer (Zenith's retail arm)", n * acc["bill"]),
                 ("ops_event", "Operating cost per event", "cost", "Zenith", None, n * acc["ops_event"]),
                 ("ops_customer", "Operating cost per customer", "cost", "Zenith", None, n * ops),
                 ("wear", "Customer battery wear", "cost", "customers", None, n * acc["wear"]),
                 ("hassle", "Customer inconvenience", "cost", "customers", None, n * acc["hassle"])]
        peak = ps.peak_days_covered(calls, self.m, season, self.peaks) if season is not None else None
        n_days = len(self.years[y])
        return {"offer": oid, "offer_name": NAMES[oid], "contract_year": f"{y}-{(y + 1) % 100:02d}",
                "period": {"kind": "contract_year", "start": str(self.m.days[self.years[y][0]].date()),
                           "end": str(self.m.days[self.years[y][-1]].date()), "days": n_days,
                           "used_for_selection": y <= 2025},
                "calls": int(len(calls.day)), "operator_days": len(decisions),
                "customer_limit_breaches": int(acc["violations"]),
                "peak_days_covered": peak, "peak_days": len(set(self.peaks[season][0].tolist())) if season is not None else None,
                "enrolled": num(n, 1), "responding": num(n * A, 1),
                "parties_aud": {"zenith": num(zen, 2), "customers": num(cust, 2), "retailer": num(retailer, 2),
                                "value_created": num(system, 2)},
                "transfers_aud": num(transfers, 2),
                "per_customer_aud": {"zenith": num(zen / n, 2) if n else None, "customers": num(cust / n, 2) if n else None},
                "ledger": [{"item": k, "label": lab, "kind": kind, "payer": payer, "receiver": recv, "aud": num(v, 2)}
                           for k, lab, kind, payer, recv, v in items],
                "status": "backtest",
                "source": "vpp/policy/policy_simulator.py account() with the effective calls; terms and rules from "
                          "reports/policy/policy_backtest.csv (S2b)"}

    def summary(self, decisions_by_offer: dict) -> dict:
        """Value page: both offers in contract year 2025-26 (model calls, and the operator's where they differ), the
        study's expected year and its season-by-season tests."""
        out = {"offers": {}, "scenario": "S2b", "scenario_note": "Zenith as retailer of contestable customers under "
               "the assumed 2027 capacity rule (net export counts negative); today's rule (S2a) gives no business case"}
        loso = pd.read_csv(POL / "policy_loso.csv")
        for oid, case in CASES.items():
            o = self.offers[oid]
            dec = decisions_by_offer.get(oid, {})
            model = self.value(oid, 2025, {})
            mine = self.value(oid, 2025, dec) if dec else None
            row = self._saved(case, "in-sample")
            h = loso[(loso["scenario"] == "S2b") & (loso["case"].str.startswith(case[0]))]
            h = h[["held_out", "chronological", "held_out_profit", "covered_held_out"]]
            out["offers"][oid] = {
                "name": NAMES[oid], "terms": {"pay_c_per_kwh": num(100 * o.pol.pay_per_kwh, 2),
                                              "pay_aud_per_event": num(o.pol.pay_per_event, 4),
                                              "annual_fee_aud": num(o.pol.annual_fee, 2), "max_calls": int(o.pol.max_events),
                                              "event_h": o.pol.event_h, "reserve_pct": num(100 * o.pol.reserve_soc, 0)},
                "status": "researched" if oid == "ref" else "assumed",
                "status_note": ("Synergy Battery Rewards V3: a public reference, not a Zenith tariff" if oid == "ref"
                                else "the policy study's suggestion: not a contract or an approved tariff"),
                "model_calls": model, "your_calls": mine,
                "expected_year": {"zenith_aud": num(o.enrolled * float(row["fit_zen"]), 2),
                                  "customers_aud": num(o.enrolled * float(row["fit_cust"]), 2),
                                  "zenith_risk_adjusted_aud": num(o.enrolled * float(row["fit_zen_risk"]), 2),
                                  "basis": "contract year 2025-26 energy part + mean of the 2023-24..2025-26 Hot Season "
                                           "capacity parts (the study's objective, training data)"},
                "holdouts": [{k: (v.item() if hasattr(v, "item") else v) for k, v in r.items()} for _, r in h.iterrows()],
            }
        out["difference_custom_minus_ref_aud"] = num(out["offers"]["custom"]["model_calls"]["parties_aud"]["zenith"]
                                                     - out["offers"]["ref"]["model_calls"]["parties_aud"]["zenith"], 2)
        return out

    def evaluate_terms(self, oid: str, pay_c_per_kwh=None, pay_aud_per_event=None, annual_fee_aud=None,
                       max_calls=None) -> dict:
        """New payment terms on the same physical design: the rule refitted on the training data and the contract year
        settled (ps.evaluate_policy). Terms that change the battery's run (event length, reserve) are not live."""
        o = self.offers[oid]
        row = self._saved(CASES[oid], "in-sample")
        over = {}
        if pay_c_per_kwh is not None:
            over["pay_per_kwh"] = float(pay_c_per_kwh) / 100
        if pay_aud_per_event is not None:
            over["pay_per_event"] = float(pay_aud_per_event)
        if annual_fee_aud is not None:
            over["annual_fee"] = float(annual_fee_aud)
        if max_calls is not None:
            over["max_events"] = int(max_calls)
        pol = policy_from(row, **over)
        e = ps.evaluate_policy(pol, market_obligations=OBL, participation_scenario=CENTRAL, period="in-sample")
        return {"offer": oid, "terms": {k: num(v, 4) for k, v in over.items()}, "contract_year": "2025-26",
                "enrolled": num(e.enrolled, 1), "calls": int(e.activations),
                "parties_aud": {k: num(v, 2) for k, v in e.parties.items()},
                "peak_days_covered": {str(k): int(v) for k, v in e.peak_days_covered.items()},
                "feasible": bool(e.feasible), "unvalidated": True,
                "note": "terms changed in the app: the call rule is refitted on the training data; not tested on other "
                        "seasons", "status": "backtest"}
