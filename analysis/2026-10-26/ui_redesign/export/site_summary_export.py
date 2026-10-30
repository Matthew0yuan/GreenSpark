"""data/site_summary.json (DATA_CONTRACT.md section 2): the site battery (10 MW / 40 MWh behind the factory meter,
route m1, CMD 9 MW), adopted config e5 = "rw50+state t5+roll" against the frozen v1 baseline, same days and prices.

Inputs (all read-only):
  export/site_summary_replay_*            the replay with the code as it is now, after the 2026-10-26 power-cap fix
                                          (site_summary_replay.py: v1 and e5 through the backtest's own pipeline)
  analysis/2026-10-26/power_cap_fix/out/  after_* (the fix's own rerun of e5) and before_* (e5, old code)
  reports/v2_test_daily.csv, _bills.csv, _ircr.csv, _result.csv, data/processed/v2_test_log.parquet
                                          the published test run (old code; `python -m vpp.v2.backtest --step test`)
  data/processed/forecast_dispatch_0900.parquet  the strict 09:00 price forecast (vpp.forecast.OUT_0900)

Every figure the UI shows is cross-checked against the published reports first (the script stops if a check fails):
the published money reproduces reports/v2_test_result.csv, the replay's e5 reproduces power_cap_fix/out/after_money,
the published log's power excesses reproduce power_cap_fix/out/before_power.

Run (repo root):  .venv/Scripts/python.exe analysis/2026-10-26/ui_redesign/export/site_summary_export.py
"""

import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp.stochastic import block_ci, daily_money  # noqa: E402
from vpp.v2.backtest import month_ci  # noqa: E402

OUT = HERE.parent / "data" / "site_summary.json"
REP = lambda p: str(Path(p).resolve().relative_to(ROOT)).replace("\\", "/")  # noqa: E731
PCF = ROOT / "analysis/2026-10-26/power_cap_fix/out"
E5, V1 = "rw50+state t5+roll", "v1"
P_MW, E_MWH, IMPORT_MW, EXPORT_MW, CMD_MW, DT, TOL = 10.0, 40.0, 12.0, 10.0, 9.0, 0.5, 1e-6
SEGMENTS = (("in-sample", "2025-04-01", "2026-06-30", 456), ("test-seen", "2026-07-01", "2026-10-15", 107),
            ("test-unseen", "2026-10-16", None, None))
CHECKS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(f"{'PASS' if ok else 'FAIL'}  {name}{': ' + detail if detail else ''}")
    print(CHECKS[-1], flush=True)
    if not ok:
        raise SystemExit(f"check failed: {name}")


def periods(days: pd.DatetimeIndex) -> list[dict]:
    out = []
    for label, a, b, n in SEGMENTS:
        sel = days[(days >= a) & ((days <= b) if b else True)]
        if n is not None:
            check(f"{label} has {n} days", len(sel) == n, f"{len(sel)}")
        out.append({"label": label, "from": sel.min().strftime("%Y-%m-%d"), "to": sel.max().strftime("%Y-%m-%d"),
                    "days": int(len(sel))})
    return out


def in_period(idx, p: dict) -> np.ndarray:
    idx = pd.DatetimeIndex(idx)
    return np.asarray((idx >= p["from"]) & (idx <= p["to"]))


def r(x, nd=2):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)


def main() -> None:
    # ---------- money: published (old code), the fix's rerun of e5, the replay (current code) ----------
    pub_led = pd.read_csv(ROOT / "reports/v2_test_daily.csv", parse_dates=["day"])
    pub_bills = pd.read_csv(ROOT / "reports/v2_test_bills.csv", parse_dates=["month"])
    pub_bills["month"] = pub_bills["month"].dt.to_period("M")
    pub = daily_money(pub_led, pub_bills)
    res = pd.read_csv(ROOT / "reports/v2_test_result.csv")
    rep_led = pd.read_csv(HERE / "site_summary_replay_ledger.csv", parse_dates=["day"])
    rep_bills = pd.read_csv(HERE / "site_summary_replay_bills.csv", parse_dates=["month"])
    rep_bills["month"] = rep_bills["month"].dt.to_period("M")
    rep = pd.read_csv(HERE / "site_summary_replay_money.csv", index_col=0, parse_dates=True)
    fix = pd.read_csv(PCF / "after_money.csv", index_col=0, parse_dates=True)
    check("replay money = daily_money(replay ledger, replay bills)",
          float((daily_money(rep_led, rep_bills)[[V1, E5]] - rep[[V1, E5]]).abs().max().max()) < 1e-9)

    days = pub.index
    check("same 570 days in published, fix rerun and replay", len(days) == 570 and days.equals(rep.index) and days.equals(fix.index))
    P = periods(days)

    # published money reproduces reports/v2_test_result.csv (per_day of e5, and e5 - v1)
    labels = {"in-sample": "validation", "test-seen": "test to 2026-10-15", "test-unseen": "test from 2026-10-16"}
    for p in P:
        row = res[(res["period"] == labels[p["label"]]) & (res["config"] == E5) & (res["base"] == V1)].iloc[0]
        m = in_period(days, p)
        d1 = abs(pub.loc[m, E5].mean() - row["per_day"])
        d2 = abs((pub.loc[m, E5] - pub.loc[m, V1]).mean() - row["vs_base"])
        check(f"published money reproduces v2_test_result.csv, {p['label']}", d1 < 1e-6 and d2 < 1e-6,
              f"e5 {row['per_day']:.2f}/day, e5 - v1 {row['vs_base']:.2f}/day")
    # the replay's e5 = the fix's own rerun (after_money), day for day
    gap = float((rep[E5] - fix[E5]).abs().max())
    check("replay e5 = power_cap_fix/out/after_money.csv e5", gap < 1e-6, f"largest daily difference {gap:.2g}")
    # where the current code differs from the published outputs
    changed = {}
    for name in (V1, E5):
        dd = (rep[name] - pub[name]).abs()
        changed[name] = [d.strftime("%Y-%m-%d") for d in dd.index[dd > 1e-6]]
        print(f"  {name}: days where current code differs from published: {changed[name]}")
    check("e5 differs from published only on the 3 days power_cap_fix names",
          changed[E5] == ["2025-12-05", "2025-12-08", "2026-02-02"], str(changed[E5]))

    base, opt = rep[V1], rep[E5]
    diff = opt - base

    # ---------- q2b ----------
    q2b_periods = []
    for p in P:
        m = in_period(days, p)
        d = diff[m]
        lo_hi = None
        ci_note = None
        if p["label"] == "in-sample":
            lo, hi = month_ci(d)
            row = res[(res["period"] == "validation") & (res["config"] == E5) & (res["base"] == V1)].iloc[0]
            lo_p, hi_p = month_ci(pub.loc[m, E5] - pub.loc[m, V1])
            check("month_ci on published money reproduces the published in-sample interval",
                  abs(lo_p - row["vs_base_lo"]) < 1e-6 and abs(hi_p - row["vs_base_hi"]) < 1e-6,
                  f"[{row['vs_base_lo']:.1f}, {row['vs_base_hi']:.1f}]")
            lo_hi = [r(lo, 1), r(hi, 1)]
            b7 = block_ci(d.to_numpy())
            ci_note = (f"month-block bootstrap (vpp.v2.backtest.month_ci: whole calendar months resampled, 4000 draws, "
                       f"seed 0) on the current-code daily money; published (old code) [{row['vs_base_lo']:.1f}, "
                       f"{row['vs_base_hi']:.1f}]. 7-day moving-block interval (vpp.stochastic.block_ci) "
                       f"[{b7[0]:.1f}, {b7[1]:.1f}]")
        else:
            ci_note = ("null: the repo reports intervals only for the combined 114-day test (see reference_test_combined); "
                       + ("findings.md section 27: 'only 7 days, no interval'" if p["label"] == "test-unseen"
                          else "it lists this segment's mean without an interval (reports/v2_test_result.csv)"))
        worst = d.idxmin()
        q2b_periods.append({
            "period": p,
            "baseline_aud_per_day": r(base[m].mean()),
            "optimizer_aud_per_day": r(opt[m].mean()),
            "uplift_aud_per_day": r(d.mean()),
            "uplift_pct": r(100 * d.mean() / base[m].mean(), 1),
            "ci95_aud_per_day": lo_hi,
            "ci95_note": ci_note,
            "p10_daily_uplift_aud": r(np.percentile(d.to_numpy(), 10)),
            "share_days_below_baseline": r(float((d < 0).mean()), 4),
            "days_below_baseline": int((d < 0).sum()),
            "worst_day": {"day": worst.strftime("%Y-%m-%d"), "uplift_aud": r(d.min())},
            "market_uplift_aud_per_day": r((rep_led.pivot_table(index="day", columns="strategy", values="ledger_total")
                                            .pipe(lambda x: x[E5] - x[V1]))[m].mean()),
            "published_old_code": {
                "baseline_aud_per_day": r(pub.loc[m, V1].mean()), "optimizer_aud_per_day": r(pub.loc[m, E5].mean()),
                "uplift_aud_per_day": r((pub.loc[m, E5] - pub.loc[m, V1]).mean())},
        })
    # the combined test, as the repo reports it (both segments mixed; reference only)
    mt = np.asarray(days >= "2026-07-01")
    rowt = res[(res["period"] == "test") & (res["config"] == E5) & (res["base"] == V1)].iloc[0]
    lo_t, hi_t = month_ci(diff[mt])
    lo_tp, hi_tp = month_ci(pub.loc[mt, E5] - pub.loc[mt, V1])
    check("month_ci on published money reproduces the published 114-day test interval",
          abs(lo_tp - rowt["vs_base_lo"]) < 1e-6 and abs(hi_tp - rowt["vs_base_hi"]) < 1e-6,
          f"[{rowt['vs_base_lo']:.1f}, {rowt['vs_base_hi']:.1f}]")
    ref_test = {
        "period": {"label": "test (seen + unseen combined, as the repo reports it)", "from": "2026-07-01",
                   "to": P[2]["to"], "days": int(mt.sum())},
        "uplift_aud_per_day": r(diff[mt].mean()),
        "ci95_aud_per_day": [r(lo_t, 1), r(hi_t, 1)],
        "ci95_7day_block_aud_per_day": [r(x, 1) for x in block_ci(diff[mt].to_numpy())],
        "published_old_code": {"uplift_aud_per_day": r(rowt["vs_base"]),
                               "ci95_aud_per_day": [r(rowt["vs_base_lo"], 1), r(rowt["vs_base_hi"], 1)],
                               "ci95_7day_block_aud_per_day": [r(rowt["vs_base_7d_lo"], 1), r(rowt["vs_base_7d_hi"], 1)]},
        "note": "Mixes the seen and unseen segments; not for display as a segment. It is the only test-period interval "
                "the repo reports (reports/v2_test_result.csv row test / rw50+state t5+roll / v1; findings.md section 27).",
    }

    # ---------- q2a ----------
    fc = pd.read_parquet(ROOT / "data/processed/forecast_dispatch_0900.parquet")
    fc = fc[fc["energy"].notna() & fc["yday_energy"].notna() & fc["actual_energy"].notna()]
    err = (fc["energy"] - fc["actual_energy"]).abs()
    yerr = (fc["yday_energy"] - fc["actual_energy"]).abs()
    q2a_periods = []
    for p in P:
        m = in_period(fc["target_date"], p)
        nd = fc.loc[m, "target_date"].nunique()
        check(f"q2a {p['label']}: forecast covers every day", nd == p["days"], f"{nd} days")
        mo, ba = err[m].mean(), yerr[m].mean()
        q2a_periods.append({"period": p, "model": r(mo), "baseline": r(ba), "change_pct": r(100 * (mo - ba) / ba, 1),
                            "intervals": int(m.sum())})
    seen = q2a_periods[1]
    check("q2a test-seen reproduces findings.md:754 (MAE 19.66, yesterday 28.79)",
          abs(seen["model"] - 19.66) < 0.006 and abs(seen["baseline"] - 28.79) < 0.006,
          f"{seen['model']} / {seen['baseline']}")
    check("q2a in-sample model reproduces findings.md:879 (point MAE 20.12)", abs(q2a_periods[0]["model"] - 20.12) < 0.006,
          f"{q2a_periods[0]['model']}")

    # ---------- hard limits (half-hour logs) ----------
    log = pd.read_parquet(HERE / "site_summary_replay_log.parquet")
    plog = pd.read_parquet(ROOT / "data/processed/v2_test_log.parquet")
    short = rep_led.set_index(["strategy", "day"])["site_shortfall_mw"]

    def counts(lg: pd.DataFrame, name: str, p: dict, shortfall=None) -> dict:
        x = lg[(lg["strategy"] == name) & in_period(lg["day"], p)]
        mw = np.abs(x["energy_mwh"].to_numpy()) / DT
        imp = x["meter_mw"].to_numpy() > IMPORT_MW + TOL
        out = {"halfhours": int(len(x)),
               "power": int((mw > P_MW + TOL).sum()),
               "power_days": int(x.loc[mw > P_MW + TOL, "day"].nunique()),
               "power_day_list": sorted(x.loc[mw > P_MW + TOL, "day"].dt.strftime("%Y-%m-%d").unique().tolist()),
               "cmd_over_10kw": int((x["meter_mw"] > CMD_MW + 0.01).sum()),
               "cmd_over_100kw": int((x["meter_mw"] > CMD_MW + 0.1).sum()),
               "max_battery_mw": float(mw.max()),
               "soc": int(((x["soc"] < -TOL) | (x["soc"] > E_MWH + TOL)).sum()),
               "meter_import": int(imp.sum()),
               "meter_export": int((-x["meter_mw"] > EXPORT_MW + TOL).sum()),
               "cmd_exceeded_halfhours": int((x["meter_mw"] > CMD_MW + TOL).sum()),
               "cmd_exceeded_days": int(x.loc[x["meter_mw"] > CMD_MW + TOL, "day"].nunique()),
               "max_meter_import_mw": float(x["meter_mw"].max()),
               "max_meter_export_mw": float(max(0.0, -x["meter_mw"].min()))}
        if shortfall is not None:
            key = pd.MultiIndex.from_frame(x[["strategy", "day"]])
            out["meter_import_unrecorded"] = int((imp & ~(shortfall.reindex(key).to_numpy() > 0)).sum())
        return out

    # the published log reproduces the fix's "before" power count (3 e5 days over 10 MW, all in-sample)
    bp = pd.read_csv(PCF / "before_power.csv", parse_dates=["day"])
    bp = bp[(bp["scenario"] == "m1") & (bp["strategy"] == E5)]
    pub_e5 = [counts(plog, E5, p) for p in P]
    check("published log: e5 power excess days = power_cap_fix before_power",
          [c["power_days"] for c in pub_e5] == [int((bp.loc[in_period(bp["day"], p), "half_hours_over"] > 0).sum()) for p in P],
          str([c["power_days"] for c in pub_e5]))
    ap = pd.read_csv(PCF / "after_power.csv", parse_dates=["day"])
    ap = ap[(ap["scenario"] == "m1") & (ap["strategy"] == E5)]
    rep_e5 = [counts(log, E5, p, short) for p in P]
    check("replay log: e5 power excess = power_cap_fix after_power (0)",
          [c["power"] for c in rep_e5] == [int(ap.loc[in_period(ap["day"], p), "half_hours_over"].sum()) for p in P],
          str([c["power"] for c in rep_e5]))
    rep_v1 = [counts(log, V1, p, short) for p in P]
    pub_v1 = [counts(plog, V1, p) for p in P]
    # monthly bills: months whose highest half-hour meter is above the CMD, and their excess charge (ENUC)
    eb = rep_bills[rep_bills["strategy"] == E5].set_index("month")
    hl_periods = []
    for p, c, cp, cv, cvp in zip(P, rep_e5, pub_e5, rep_v1, pub_v1):
        months = eb[in_period(eb.index.to_timestamp(), p)]
        hl_periods.append({
            "period": p,
            "power": c["power"], "soc": c["soc"], "meter_import": c["meter_import"], "meter_export": c["meter_export"],
            "cmd_exceeded_halfhours": c["cmd_exceeded_halfhours"],
            "cmd_exceeded_days": c["cmd_exceeded_days"],
            "cmd_exceeded_halfhours_by_more_than_0_01_mw": c["cmd_over_10kw"],
            "cmd_exceeded_halfhours_by_more_than_0_1_mw": c["cmd_over_100kw"],
            "max_battery_mw": r(c["max_battery_mw"], 3), "max_meter_import_mw": r(c["max_meter_import_mw"], 3),
            "max_meter_export_mw": r(c["max_meter_export_mw"], 3), "halfhours_checked": c["halfhours"],
            "published_old_code": {"power": cp["power"], "power_days": cp["power_days"], "soc": cp["soc"],
                                   "meter_import": cp["meter_import"], "meter_export": cp["meter_export"],
                                   "cmd_exceeded_halfhours": cp["cmd_exceeded_halfhours"],
                                   "max_battery_mw": r(cp["max_battery_mw"], 3)},
            "baseline_v1": {"power": cv["power"], "soc": cv["soc"], "meter_import": cv["meter_import"],
                            "meter_export": cv["meter_export"], "cmd_exceeded_halfhours": cv["cmd_exceeded_halfhours"],
                            "published_old_code_power": cvp["power"], "published_old_code_power_days": cvp["power_day_list"]},
            "note": (f"Counts of half-hours (e5, current code) over: battery power {P_MW:g} MW (|energy_mwh| / 0.5 h, "
                     f"vpp/checks.py I11), stored energy outside 0-{E_MWH:g} MWh (I3), meter import {IMPORT_MW:g} MW and "
                     f"export {EXPORT_MW:g} MW (I1). CMD {CMD_MW:g} MW is an economic limit priced in the RT7 bill "
                     f"(excess network use charge on the month's highest half-hour), not a hard limit; the dispatcher's "
                     f"cap is max(CMD, the month-to-date peak) (vpp/v2/harness.py:207), so once a month has gone above "
                     f"the CMD, later half-hours up to that peak add no charge. Meter above the CMD in "
                     f"{c['cmd_exceeded_halfhours']} half-hours on {c['cmd_exceeded_days']} days "
                     f"({c['cmd_over_10kw']} by more than 0.01 MW, {c['cmd_over_100kw']} by more than 0.1 MW)"
                     + (f"; months whose bill peak is above the CMD: {', '.join(f'{k} {v:.3f} MW' for k, v in months['peak_mw'][months['peak_mw'] > CMD_MW + TOL].items()) or 'none'}"
                        f", excess charge in those months A${months['enuc'][months['peak_mw'] > CMD_MW + TOL].sum():,.0f} (in the money above)"
                        if p["label"] != "test-unseen" else
                        "; the October bill is shared with the seen segment (10-01..10-15), so this segment's charge is not separable")),
        })
        if p["label"] == "test-unseen":
            oct_ = eb.loc[pd.Period("2026-10", "M")]
            hl_periods[-1]["note"] += f" (October 2026 bill peak {oct_['peak_mw']:.3f} MW, excess charge A${oct_['enuc']:,.0f})"

    # ---------- own value and IRCR ----------
    led_e5 = rep_led[rep_led["strategy"] == E5].set_index("day")["ledger_total"]
    ov = []
    for p in P:
        m = in_period(days, p)
        ov.append({"period": p, "market_plus_bill_aud_per_day": r(opt[m].mean(), 1),
                   "market_aud_per_day": r(led_e5[m].mean(), 1), "bill_saving_aud_per_day": r((opt - led_e5)[m].mean(), 1)})
    for p, want in zip(ov, (4668.9, 2101.4, 1805.4)):
        before = pub.loc[in_period(days, p["period"]), E5].mean()
        check(f"own value {p['period']['label']}: published (old code) {before:.1f} = power_cap_fix RESULTS.md {want}",
              abs(before - want) < 0.05, f"current code {p['market_plus_bill_aud_per_day']}")
    ir = pd.read_csv(HERE / "site_summary_replay_ircr.csv").set_index("strategy")
    ir_pub = pd.read_csv(ROOT / "reports/v2_test_ircr.csv").set_index("strategy")
    ir_fix = pd.read_csv(PCF / "after_ircr.csv")
    ir_fix = ir_fix[(ir_fix["scenario"] == "m1") & (ir_fix["strategy"] == E5)].iloc[0]
    sv = float(ir.loc[E5, "saving_per_year"])
    check("IRCR saving: replay = published = power_cap_fix after_ircr",
          abs(sv - ir_pub.loc[E5, "saving_per_year"]) < 1e-6 and abs(sv - ir_fix["saving_per_year"]) < 1e-6, f"{sv:,.2f}")

    # ---------- write ----------
    src_rep = "analysis/2026-10-26/ui_redesign/export/site_summary_replay.py"
    summary = {
        "generated": "2026-10-26",
        "site": {"battery": {"power_mw": P_MW, "energy_mwh": E_MWH, "round_trip": 0.85},
                 "limits": {"import_mw": IMPORT_MW, "export_mw": EXPORT_MW, "cmd_mw": CMD_MW},
                 "route": "m1 (behind the factory meter)", "config": "e5 = rw50+state t5+roll (adopted, reports/v2_plan.md section 12.9)",
                 "baseline": "v1, frozen at commit 90515d3 (reports/v2_plan.md header)"},
        "q2b": {
            "periods": q2b_periods,
            "reference_test_combined": ref_test,
            "status": "backtest",
            "derived": {
                "uplift_pct": "100 * uplift_aud_per_day / baseline_aud_per_day",
                "p10_daily_uplift_aud": "numpy.percentile(daily uplift, 10), linear interpolation; daily uplift = e5 money - v1 money",
                "share_days_below_baseline": "(days with daily uplift < 0) / days, a fraction 0-1",
                "worst_day": "day of the smallest daily uplift",
                "market_uplift_aud_per_day": "mean of (e5 ledger_total - v1 ledger_total): market part only, no bill",
                "ci95_aud_per_day": "in-sample only: the repo's own interval method (vpp.v2.backtest.month_ci) re-run on "
                                    "the current-code daily uplift; the published value (old code) is in ci95_note",
            },
            "money_definition": "A day's money = its ledger (energy + FCESS - wear; capacity, refunds, contracts are 0 on m1) "
                                "+ its calendar month's RT7 bill saving against the factory without a battery, per day "
                                "(vpp.stochastic.daily_money). IRCR is not in it (see own_value).",
            "source": f"{src_rep} -> export/site_summary_replay_money.csv (current code, columns v1 and {E5}); "
                      "published (old code): reports/v2_test_daily.csv + reports/v2_test_bills.csv via "
                      "vpp.stochastic.daily_money, reproducing reports/v2_test_result.csv; interval: vpp.v2.backtest.month_ci",
            "note": f"Current code differs from the published outputs on: e5 {changed[E5]}, v1 {changed[V1]} (both were "
                    "over 10 MW there before the 2026-10-26 fix). On those days v1 gains more than e5, so the in-sample "
                    "uplift is a little lower than the published reports (see published_old_code); the reports and "
                    "findings.md section 27 still carry the old values. The test's "
                    "seen / unseen split is reported by the repo for the means (reports/v2_test_result.csv rows 'test to "
                    "2026-10-15' and 'test from 2026-10-16'); p10, share and worst day are computed here from the daily money. "
                    "Bill savings are monthly and spread over the month's days, so the unseen segment shares October's bill "
                    "with the seen one. 2026-10-22 has no 'tomorrow' yet and changes at the next data refresh (findings.md 27.5).",
        },
        "q2a": {
            "metric": "MAE, A$/MWh (energy price, half-hours 09:00-07:30 of each trading day)",
            "baseline": "yesterday's price (same half-hour of the previous trading day, yday_energy)",
            "forecast": "strict 09:00 same-day forecast the dispatcher uses: 50/50 blend of (AEMO pre-dispatch + ML correction "
                        "+ weather) and (ML + weather, no AEMO), walk-forward monthly refits, trained on data to 2026-06-30 "
                        "at most (vpp/forecast.py)",
            "periods": q2a_periods,
            "status": "derived",
            "derived": {"model": "mean |energy - actual_energy|", "baseline": "mean |yday_energy - actual_energy|",
                        "change_pct": "100 * (model - baseline) / baseline (negative = smaller error than the baseline)"},
            "source": "data/processed/forecast_dispatch_0900.parquet columns energy, yday_energy, actual_energy, target_date; "
                      "the same computation as vpp/forecast.py main(), split by period. Reproduces findings.md:754 "
                      "(test 07-01..10-15: 19.66 vs 28.79) and findings.md:879 (in-sample point MAE 20.12).",
            "note": "The repo reports the in-sample model MAE and the test-seen pair; the in-sample baseline and the "
                    "test-unseen pair are computed here. Not an accuracy: a lower MAE is a smaller average error in A$/MWh.",
        },
        "hard_limits": {
            "periods": hl_periods,
            "power_cap_note": "Published results predate the 2026-10-26 fix (analysis/2026-10-26/power_cap_fix): e5 was over "
                              f"10 MW on {pub_e5[0]['power_days']} in-sample days (2025-12-05, 2025-12-08, 2026-02-02, "
                              f"17:00-17:30, up to {pub_e5[0]['max_battery_mw']:.2f} MW; {pub_e5[0]['power']} half-hours); "
                              "fixed code: 0 on every day. Money effect on e5 in-sample +A$0.02/day, test 0. The v1 baseline "
                              f"was also over 10 MW in the published outputs ({pub_v1[0]['power']} half-hours on "
                              f"{', '.join(pub_v1[0]['power_day_list'])}); with the fixed code it is 0 and v1 earns "
                              f"+A${(base - pub[V1])[in_period(days, P[0])].mean():.2f}/day more in-sample, so the in-sample "
                              f"uplift is {diff[in_period(days, P[0])].mean():.2f} (published "
                              f"{(pub[E5] - pub[V1])[in_period(days, P[0])].mean():.2f}). Test periods unchanged.",
            "status": "derived",
            "derived": {"power": f"count of half-hours with |energy_mwh| / 0.5 h > {P_MW:g} MW + 1e-6",
                        "soc": f"count of half-hours with soc < -1e-6 or > {E_MWH:g} MWh + 1e-6",
                        "meter_import": f"count of half-hours with meter_mw > {IMPORT_MW:g} MW + 1e-6",
                        "meter_export": f"count of half-hours with -meter_mw > {EXPORT_MW:g} MW + 1e-6",
                        "cmd_exceeded_halfhours": f"count of half-hours with meter_mw > {CMD_MW:g} MW + 1e-6"},
            "source": f"{src_rep} -> export/site_summary_replay_log.parquet (current code; conditions as vpp/v2/checks.py "
                      "execution() I1, I3 and vpp/checks.py I11, tolerance 1e-6), run here on the half-hour log, not by "
                      "vpp/checks.py itself; published: data/processed/v2_test_log.parquet; month peaks and excess charge: "
                      "export/site_summary_replay_bills.csv columns peak_mw, enuc",
        },
        "own_value": {
            "periods": ov,
            "ircr_saving_aud_per_year": r(sv),
            "ircr_season": "2025-26",
            "ircr": {
                "period": {"label": "in-sample", "from": "2025-12-01", "to": "2026-03-31", "season": "Hot Season 2025-26",
                           "sets": "the factory's IRCR for capacity year 2026-27"},
                "ircr_mw": r(float(ir.loc[E5, "ircr_mw"]), 3), "ircr_no_battery_mw": r(float(ir.loc[E5, "ircr_no_battery_mw"]), 3),
                "peak_intervals_covered": int(ir.loc[E5, "intervals_covered"]), "peak_intervals": 12,
                "peak_days_covered": int(ir.loc[E5, "peak_days_covered"]), "peak_days": 4,
                "formula": "(ircr_no_battery_mw - ircr_mw) x 216,091.78 A$/(MW*yr) (vpp/contracts.py RCP[2026], 2026-27) "
                           "x 1.5693 (vpp/site_bill.py IRCR_RATIO, AEMO TDL x Total ratio for 2026-10)",
                "note": "Current (old) IRCR definition: 4 peak days x 3 half-hours = 12 Peak SWIS Trading Intervals "
                        "(AEMO's published 2025-26 intervals, vpp/site_bill.py OFFICIAL_PEAK_INTERVALS). In-sample only; no "
                        "out-of-sample test until the 2026-27 Hot Season (2026-12-01..2027-03-31). Not in the per-day money.",
            },
            "status": "backtest",
            "derived": {"market_plus_bill_aud_per_day": "mean daily money (ledger + monthly bill saving per day)",
                        "bill_saving_aud_per_day": "market_plus_bill - market"},
            "source": f"{src_rep} -> export/site_summary_replay_money.csv, _ledger.csv, _ircr.csv; equals "
                      "analysis/2026-10-26/power_cap_fix/out/after_money.csv and after_ircr.csv; published (old code) "
                      "values in power_cap_fix/RESULTS.md section 2",
        },
        "gaps": [],
        "sources": {
            "adopted_config_and_baseline": "reports/v2_plan.md (header; sections 12.9, 14, 15.7)",
            "published_results": "reports/findings.md section 27; reports/v2_test_result.csv",
            "power_cap_fix": "analysis/2026-10-26/power_cap_fix/RESULTS.md, out/compare.json, out/after_*.csv",
            "replay": src_rep,
            "export": REP(__file__),
            "reviews_checked": "No number used here is reported as wrong or unverified in analysis/2026-10-26/"
                               "{value_params,customer_kwh_value,tightness_forecast,tightness_inputs}/REVIEW.md or "
                               "analysis/2026-10-25/mvp_review/REVIEW_*.md. value_params/REVIEW.md issue 4 (power over "
                               "the rating; 8 MW Hot Season IRCR overstated) is the bug the 2026-10-26 fix removed; "
                               "no 8 MW figure is used.",
            "checks_run": CHECKS,
        },
    }
    gaps = summary["gaps"]
    for p in q2b_periods:
        if p["ci95_aud_per_day"] is None:
            gaps.append({"field": f"q2b.periods[{p['period']['label']}].ci95_aud_per_day", "reason": p["ci95_note"]})
    gaps += [
        {"field": "own_value.ircr (test-seen, test-unseen)",
         "reason": "No Hot Season after the cutoff; IRCR cannot be scored out of sample until 2026-27 (findings.md 16.6)."},
        {"field": "hard_limits (contractual, market eligibility)",
         "reason": "No check exists in the repo for these; not part of the counts."},
        {"field": "hard_limits (check runner)",
         "reason": "vpp/checks.py and vpp.v2.checks were not run on the current-code replay (they read the published "
                   "files and would rewrite nothing, but are wired to reports/ and data/processed/); the counts apply "
                   "their conditions (I1, I3, I11) to the replay's half-hour log here."},
        {"field": "q2b, reports/findings.md section 27 and reports/v2_test_result.csv",
         "reason": "The published in-sample uplift (+408.2/day [205.7, 601.5]) predates the power-cap fix; with the "
                   "current code it is +406.9 [204.8, 600.2]. The reports have not been regenerated (power_cap_fix/"
                   "RESULTS.md section 4); this file uses the current-code values."},
        {"field": "q2b, own_value bill part",
         "reason": "The bill saving rests on a synthetic factory load (findings.md 27.5); no real load or retail contract verified."},
    ]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {REP(OUT)}")


if __name__ == "__main__":
    main()
