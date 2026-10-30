"""Builds every table in out/, runs the checks of PLAN.md section 5 and writes NUMBERS.csv.

Run (from the repository root):  .venv/bin/python analysis/2026-10-26/customer_kwh_value/run.py
Writes only inside this folder: out/*.csv, out/checks.txt, NUMBERS.csv.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import csv  # noqa: E402
import time  # noqa: E402
from dataclasses import replace  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import kwh_value as kv  # noqa: E402
from agents.check import check_numbers  # noqa: E402
from vpp import contracts as ct  # noqa: E402
from vpp.evaluate import TRAIN_END  # noqa: E402
from vpp.policy import policy_simulator as ps  # noqa: E402

OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
C = 100.0                     # A$/kWh -> c/kWh
TOL_KWH, TOL_SAME, TOL_CALL, TOL_FLEET = 1e-6, 1e-9, 1e-6, 0.01
LINES: list[str] = []
RESULTS: dict = {}


def say(msg: str = "") -> None:
    print(msg, flush=True)
    LINES.append(msg)


def verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


# ------------------------------------------------------------------------------------------------ check 1
def check_rerun(p: kv.Parts, fam: ps.Family) -> bool:
    assert (p.starts == fam.starts).all()
    diffs = {
        "d_net": np.abs(p.d_net - fam.d_net).max(),
        "w_actual": np.abs(p.w - fam.w_actual).max(),
        "paid_kwh": np.abs(p.q - fam.paid_kwh).max(),
        "standby_kwh": np.abs(p.standby_kwh - fam.standby_kwh).max(),
        "d_import_kwh": np.abs(p.d_imp_total - fam.d_import_kwh).max(),
        "d_feedin_event": np.abs(p.debs["event"] - fam.d_feedin_event).max(),
        "d_feedin_other": np.abs(p.debs["standby"] + p.debs["after"] - fam.d_feedin_other).max(),
        "d_through_kwh": np.abs(p.d_dis - fam.d_through_kwh).max(),
        "delivered_kwh": np.abs(p.delivered - fam.delivered_kwh).max(),
        "gap_kwh": np.abs(p.gap - fam.gap_kwh).max(),
        "net_peak": np.abs(p.net_peak - fam.net_peak).max(),
        "baseline re-step": p.base_err,
    }
    ok = all(v <= TOL_SAME for v in diffs.values())
    say(f"[1] 重算一致（我按阶段重跑 vs family()，最大绝对差，≤ {TOL_SAME:g}）：{verdict(ok)}")
    for k, v in diffs.items():
        say(f"      {k:18s} {v:.3e}")
    RESULTS["rerun_max"] = max(diffs.values())
    return ok


# ------------------------------------------------------------------------------------------------ check 2
def check_balance(p: kv.Parts, m: ps.Market) -> tuple[bool, pd.DataFrame]:
    r = kv.balances(p)
    rows = []
    for j, s in enumerate(p.starts):
        rows.append({"window": kv.window_label(s), "days": m.n_days,
                     "max_abs_event_residual_kwh": float(np.abs(r["event"][j]).max()),
                     "max_abs_horizon_residual_kwh": float(np.abs(r["horizon"][j]).max()),
                     **{f"mean_event_{k}_kwh": float(p.ev[k][j].mean()) for k in
                        ("avail", "pv", "home", "export", "loss", "retained")},
                     "mean_Q_kwh": float(p.q[j].mean()), "mean_dN_kwh": float(p.dn[j].mean()),
                     "mean_extra_import_kwh": float(p.d_imp_total[j].mean()),
                     "mean_extra_export_kwh": float(p.d_exp_total[j].mean()),
                     "mean_extra_loss_kwh": float(p.d_loss[j].mean()), "mean_gap_kwh": float(p.gap[j].mean())})
    df = pd.DataFrame(rows)
    ev_max = float(np.abs(r["event"]).max())
    hz_max = float(np.abs(r["horizon"]).max())
    ok = ev_max <= TOL_KWH and hz_max <= TOL_KWH
    say(f"[2] 电量收支闭合（每天、每窗口，残差 ≤ {TOL_KWH:g} kWh）：{verdict(ok)}  "
        f"事件内最大 {ev_max:.2e}，跟踪期增量最大 {hz_max:.2e}；共 {r['event'].size} 次候选调用")
    RESULTS["balance_event_max"], RESULTS["balance_horizon_max"] = ev_max, hz_max
    return ok, df


# ------------------------------------------------------------------------------------------------ check 3
CASES = [("B", "S1"), ("B", "S2a"), ("B", "S2b"), ("B", "S3"), ("C-S2b", "S2b"), ("C-S3", "S3")]
PERIODS = ("in-sample", "test", "FY2023", "FY2024")


def reconcile_case(p, fam, m, pol_name, sc, period, saved, bug: str | None = None) -> dict:
    pol, obl = kv.POLICIES[pol_name], kv.SCENARIOS[sc]
    fit = kv.fit_of(pol_name, sc)
    u = kv.actual_items(p, pol, obl)
    calls, y, season, days = kv.calls_for(pol, obl, period, fit)
    peaks = ps.peak_intervals() if obl.capacity != "none" else None
    acc = ps.account(calls, fam, pol, obl, kv.PART, m, season, peaks)
    j, d = calls.start, calls.day
    q = p.q[j, d]
    a = kv.AVAIL
    pz0 = u["p_z0"][j, d]
    if bug == "w_no_div1000":           # planted: the wholesale value left in $/MWh x kWh (no /1000)
        pz0 = pz0 + (u["v_energy"][j, d] * 1000 - u["v_energy"][j, d]) * float(obl.energy_to_zenith)
    if bug == "denominator_dN":         # planted: market value per kWh of ΔN, multiplied back by Q
        pz0 = pz0 + (p.w[j, d] / p.dn[j, d] - u["v_energy"][j, d]) * float(obl.energy_to_zenith)
    if bug == "per_enrolled_as_responding":
        a = 1.0
    with np.errstate(divide="ignore", invalid="ignore"):
        pay_c = (pol.pay_per_kwh * q + pol.pay_per_event) / q
    zen = a * q * (pz0 - pay_c)
    cust = a * q * (pay_c - u["p_c"][j, d])
    # a NaN or inf (e.g. a division by ΔN close to 0) counts as a mismatch
    dz = float(np.nan_to_num(np.abs(zen - acc["per_call_zenith"]), nan=np.inf).max()) if len(d) else 0.0
    dc = float(np.nan_to_num(np.abs(cust - acc["per_call_customer"]), nan=np.inf).max()) if len(d) else 0.0
    cap_mine, kw, v_ircr, q_ircr = 0.0, 0.0, 0.0, 0.0
    if season is not None and obl.capacity != "none":
        kw, val = kv.my_ircr(p, d, j, obl, season, peaks)
        q_ircr = float(q[calls.kind == "ircr"].sum())
        v_ircr = obl.capacity_share * val / q_ircr if q_ircr > 0 else 0.0
        cap_mine = a * v_ircr * q_ircr if q_ircr > 0 else a * obl.capacity_share * val
    dcap = abs(cap_mine - acc["capacity"])
    e = ps.evaluate_policy(pol, market_obligations=obl, period=period, fit=fit)
    share = len(days) / 365.0
    zen_pc = float(zen.sum()) + cap_mine - (pol.annual_fee + obl.ops_per_customer) * share
    cust_pc = float(cust.sum()) + pol.annual_fee * share
    fixed = obl.fixed_cost * share if e.enrolled > 0 else 0.0
    fleet_z = e.enrolled * zen_pc - fixed
    case = "B reference" if pol_name == "B" else "C optimised"
    row = saved[(saved["scenario"] == sc) & (saved["case"] == case) & (saved["period"] == period)]
    d_saved_z = float(abs(fleet_z - row["zenith_profit"].iloc[0])) if len(row) else np.nan
    d_saved_c = float(abs(cust_pc - row["customer_benefit"].iloc[0])) if len(row) else np.nan
    rule_saved_ok = np.nan
    if len(row):
        rule_saved_ok = bool(row["rule_ircr_q"].iloc[0] == fit.rule.ircr_q
                             and float(row["rule_theta"].iloc[0]) == fit.rule.theta
                             and abs(row["policy_pay_per_kwh"].iloc[0] - pol.pay_per_kwh) < 1e-9
                             and abs(row["policy_pay_per_event"].iloc[0] - pol.pay_per_event) < 1e-9
                             and int(row["policy_max_events"].iloc[0]) == pol.max_events)
    return {"policy": pol_name, "scenario": sc, "period": period, "rule_theta": fit.rule.theta,
            "rule_ircr_q": fit.rule.ircr_q, "calls": len(d), "ircr_calls": int((calls.kind == "ircr").sum()),
            "Q_sum_kwh_per_responding": float(q.sum()), "ircr_kw": kw, "v_ircr_c_per_kwh": C * v_ircr,
            "max_abs_diff_per_call_zenith_A$": dz, "max_abs_diff_per_call_customer_A$": dc,
            "abs_diff_capacity_A$_per_enrolled": dcap,
            "abs_diff_vs_evaluate_policy_zenith_A$_per_enrolled": abs(zen_pc - e.zenith_per_customer),
            "abs_diff_vs_evaluate_policy_customer_A$_per_enrolled": abs(cust_pc - e.customer_benefit),
            "abs_diff_vs_saved_backtest_zenith_A$_fleet": d_saved_z,
            "abs_diff_vs_saved_backtest_customer_A$_per_enrolled": d_saved_c,
            "saved_rule_and_terms_match": rule_saved_ok,
            "zenith_A$_per_enrolled": zen_pc, "customer_A$_per_enrolled": cust_pc, "enrolled": e.enrolled,
            "zenith_A$_fleet": fleet_z}


def check_reconcile(p, fam, m) -> tuple[bool, pd.DataFrame, dict]:
    saved = pd.read_csv(ROOT / "reports" / "policy" / "policy_backtest.csv")
    rows = []
    for pol_name, sc in CASES:
        for period in PERIODS:
            if period.startswith("FY") and kv.SCENARIOS[sc].capacity == "none":
                continue
            rows.append(reconcile_case(p, fam, m, pol_name, sc, period, saved))
    df = pd.DataFrame(rows)
    per_call = max(df["max_abs_diff_per_call_zenith_A$"].max(), df["max_abs_diff_per_call_customer_A$"].max())
    cap = df["abs_diff_capacity_A$_per_enrolled"].max()
    ev = max(df["abs_diff_vs_evaluate_policy_zenith_A$_per_enrolled"].max(),
             df["abs_diff_vs_evaluate_policy_customer_A$_per_enrolled"].max())
    sv = np.nanmax(df["abs_diff_vs_saved_backtest_zenith_A$_fleet"].to_numpy())
    svc = np.nanmax(df["abs_diff_vs_saved_backtest_customer_A$_per_enrolled"].to_numpy())
    rules_ok = bool(df["saved_rule_and_terms_match"].dropna().astype(bool).all())
    ok = per_call <= TOL_CALL and cap <= TOL_CALL and ev <= TOL_FLEET and sv <= TOL_FLEET and svc <= TOL_FLEET \
        and rules_ok
    say(f"[3] 对账（{len(df)} 组：方案 × 情景 × 时间段，{int(df['calls'].sum())} 次调用）：{verdict(ok)}")
    say(f"      每次调用最大差 {per_call:.2e} A$（≤ {TOL_CALL:g}）；每季容量最大差 {cap:.2e} A$（≤ {TOL_CALL:g}）")
    say(f"      和 evaluate_policy 最大差 {ev:.2e} A$/报名户；和保存的 policy_backtest.csv 最大差 Zenith {sv:.2e} A$（全客户群）、"
        f"客户 {svc:.2e} A$/报名户（≤ {TOL_FLEET}）；保存的规则和条款一致：{rules_ok}")
    # planted bugs: the same comparison must fail
    bugs = {}
    for bug in ("w_no_div1000", "denominator_dN", "per_enrolled_as_responding"):
        r = reconcile_case(p, fam, m, "B", "S2b", "in-sample", saved, bug=bug)
        caught = max(r["max_abs_diff_per_call_zenith_A$"], r["max_abs_diff_per_call_customer_A$"]) > TOL_CALL
        bugs[bug] = (caught, max(r["max_abs_diff_per_call_zenith_A$"], r["max_abs_diff_per_call_customer_A$"]))
    RESULTS.update(recon_per_call=per_call, recon_cap=cap, recon_eval=ev, recon_saved=sv, recon_saved_c=svc,
                   bugs=bugs)
    return ok, df, bugs


# ------------------------------------------------------------------------------------------------ check 4
def check_scores(p, mm, m, fc) -> bool:
    pol, obl = kv.POLICIES["B"], kv.SCENARIOS["S1"]
    x = kv.exante(p, mm, fc, m, pol, obl)
    worst = 0.0
    for j, s in enumerate(p.starts):
        if s + 2 * kv.EVENT_H > 2 * (pol.window[1] - 8):
            continue
        sc = ps.energy_scores(kv.FAM_KEY, pol, obl, fc, m, np.array([j]))
        mine = x["w_main"][j] - (pol.pay_per_kwh * x["q"][j] + pol.pay_per_event + pol.offset_rate * x["standby"][j]
                                 + obl.ops_per_event)
        has = fc.has_fc
        worst = max(worst, float(np.abs(mine[has] - sc.best[has]).max()))
        assert np.isneginf(sc.best[~has]).all()
    ok = worst <= TOL_SAME
    say(f"[4] 09:00 事前毛利 = 现有 energy_scores 单窗口打分（不含次日缺口项）：{verdict(ok)}  最大差 {worst:.2e} A$")
    RESULTS["scores_max"] = worst
    return ok


# ------------------------------------------------------------------------------------------------ check 5
def check_lookahead(p, mm, m, fc) -> bool:
    rng = np.random.default_rng(7)
    pol, obl = kv.POLICIES["C-S2b"], kv.SCENARIOS["S2b"]
    base = kv.exante(p, mm, fc, m, pol, obl)
    # (i) actual prices from 09:00 on, every day, scrambled (fitted constants slot_price and uplift kept)
    rtp = m.rtp.copy()
    idx = np.arange(len(rtp))
    after = (idx % ps.N) >= ps.DECISION_SLOT
    rtp[after] = rtp[after] * rng.uniform(0.0, 3.0, after.sum())
    m2 = replace(m, rtp=rtp)
    x2 = kv.exante(p, mm, fc, m2, pol, obl)
    same_price = all(np.array_equal(base[k], x2[k], equal_nan=True) for k in base)
    w_new = (p.d_net * rtp[np.arange(m.n_days)[:, None] * ps.N + np.arange(ps.HORIZON)[None, :]][None]).sum(axis=2) / 1000
    post_changed = not np.allclose(w_new + p.w_phase["gap"], p.w)
    # (ii) household physics from 2026-07-01 on scrambled
    test = (m.days > TRAIN_END)
    p3 = kv.stress_copy(p, test, rng)
    mm3 = kv.monthly_means(p3, m)
    x3 = kv.exante(p3, mm3, fc, m, pol, obl)
    same_phys = all(np.array_equal(base[k], x3[k], equal_nan=True) for k in base)
    post3 = kv.actual_items(p3, pol, obl)
    post_changed3 = not np.allclose(np.nan_to_num(post3["p_c"][:, test]),
                                    np.nan_to_num(kv.actual_items(p, pol, obl)["p_c"][:, test]))
    ok = same_price and post_changed and same_phys and post_changed3
    say(f"[5] 不偷看：09:00 以后实际价格乱改 → 事前值不变 {same_price}、事后值变了 {post_changed}；"
        f"2026-07-01 以后家庭物理量加噪 → 事前值不变 {same_phys}、事后值变了 {post_changed3}：{verdict(ok)}")
    return ok


# ------------------------------------------------------------------------------------------------ tables
def allocations(p) -> tuple[dict, pd.DataFrame]:
    allocs, rows = {}, []
    for sc in ("S2a", "S2b", "S3"):
        for method in ("C", "B", "peak4"):
            for s in p.starts:
                group = []
                for y in ps.SEASONS:
                    a = kv.allocation(p, sc, method, y, int(s))
                    allocs[(sc, method, y, int(s))] = a
                    group.append(a)
                    rows.append({"scenario": sc, "method": method, "season": f"{y}-{str(y + 1)[2:]}",
                                 "window": kv.window_label(s), "calls": a.n_calls, "Q_sum_kwh": a.q_sum,
                                 "Q_mean_kwh_per_call": a.q_sum / a.n_calls if a.n_calls else np.nan,
                                 "ircr_kw_reduction": a.kw, "rcp_A$_per_MW_yr": a.rcp,
                                 "value_full_A$": a.value_full, "capacity_share": kv.SCENARIOS[sc].capacity_share,
                                 "value_zenith_A$": a.value_zenith, "zenith_c_per_kwh": C * a.per_kwh,
                                 "zenith_A$_per_call": a.value_zenith / a.n_calls if a.n_calls else np.nan,
                                 "zenith_c_per_kwh_at_2027_28_rcp": C * a.per_kwh_2027})
                qs = sum(a.q_sum for a in group)
                n = sum(a.n_calls for a in group)
                rows.append({"scenario": sc, "method": method, "season": "三季合并", "window": kv.window_label(s),
                             "calls": n, "Q_sum_kwh": qs, "Q_mean_kwh_per_call": qs / n if n else np.nan,
                             "ircr_kw_reduction": np.nan, "rcp_A$_per_MW_yr": np.nan,
                             "value_full_A$": sum(a.value_full for a in group),
                             "capacity_share": kv.SCENARIOS[sc].capacity_share,
                             "value_zenith_A$": sum(a.value_zenith for a in group),
                             "zenith_c_per_kwh": C * kv.pooled_per_kwh(group),
                             "zenith_A$_per_call": sum(a.value_zenith for a in group) / n if n else np.nan,
                             "zenith_c_per_kwh_at_2027_28_rcp":
                                 C * sum(a.per_kwh_2027 * a.q_sum for a in group) / qs if qs else 0.0})
    return allocs, pd.DataFrame(rows)


def daily(p, mm, m, fc, allocs) -> pd.DataFrame:
    labels = kv.period_label(m.days)
    sel = np.flatnonzero(labels != "")
    season = ps.season_of(m)
    frames = []
    for sc, obl in kv.SCENARIOS.items():
        pol = kv.POLICIES[kv.C_OF[sc] or "B"]
        x = kv.exante(p, mm, fc, m, pol, obl)
        u = kv.actual_items(p, pol, obl)
        cap = obl.capacity != "none"
        if cap:
            fit = kv.fit_of(kv.C_OF[sc], "S2b" if sc == "S2a" else sc)
            flags = fc.flags[fit.rule.ircr_q]
        else:
            flags = np.zeros(m.n_days, bool)
        for j, s in enumerate(p.starts):
            s = int(s)
            pooled = kv.pooled_per_kwh([allocs[(sc, "C", y, s)] for y in ps.SEASONS]) if cap else 0.0
            v_ex = np.where(flags, pooled, 0.0)
            called = np.zeros(m.n_days, bool)
            v_act = np.zeros(m.n_days)
            if cap:
                for y in ps.SEASONS:
                    a = allocs[(sc, "C", y, s)]
                    called[list(a.days)] = True
                    v_act[list(a.days)] = a.per_kwh
            pz_ex = x["p_z0"][j] + v_ex
            pz_act = u["p_z0"][j] + v_act
            frames.append(pd.DataFrame({
                "day": m.days[sel].strftime("%Y-%m-%d"), "period": labels[sel], "month": m.days[sel].strftime("%Y-%m"),
                "scenario": sc, "window": kv.window_label(s), "energy_to_zenith": obl.energy_to_zenith,
                "hot_season": season[sel] >= 0, "flagged": flags[sel], "has_0900_forecast": fc.has_fc[sel],
                "E_Q_kwh": x["q"][j, sel], "E_dN_kwh": x["dn"][j, sel],
                "a_self_use_c": C * x["v_self"][j, sel], "event_debs_c": C * x["debs_c"][j, sel],
                "wear_c": C * x["wear_c"][j, sel], "hassle_c": C * x["hassle_c"][j, sel],
                "d_customer_min_c": C * x["p_c"][j, sel],
                "b_energy_0900_c": C * x["v_energy"][j, sel], "c_ircr_0900_c": C * v_ex[sel],
                "offset_c": C * x["offset_c"][j, sel], "ops_c": C * x["ops_c"][j, sel],
                "d_zenith_max_0900_c": C * pz_ex[sel],
                "surplus_0900_c": C * (pz_ex[sel] - x["p_c"][j, sel]),
                "Q_actual_kwh": p.q[j, sel], "dN_actual_kwh": p.dn[j, sel],
                "W_actual_A$": p.w[j, sel],
                "a_self_use_actual_c": C * u["v_self"][j, sel], "d_customer_min_actual_c": C * u["p_c"][j, sel],
                "b_energy_actual_c": C * u["v_energy"][j, sel], "called_by_C_rule": called[sel],
                "c_ircr_actual_c": C * v_act[sel], "d_zenith_max_actual_c": C * pz_act[sel],
            }))
    return pd.concat(frames, ignore_index=True)


def wmean(v, w):
    w = np.asarray(w, float)
    return float((np.asarray(v, float) * w).sum() / w.sum()) if w.sum() > 0 else np.nan


def monthly(dd: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (period, month, sc, win), g in dd.groupby(["period", "month", "scenario", "window"], sort=True):
        fcd = g[g["has_0900_forecast"]]
        fl, un = fcd[fcd["flagged"]], fcd[~fcd["flagged"]]
        cl = g[g["called_by_C_rule"]]
        r = {"period": period, "month": month, "scenario": sc, "window": win, "days": len(g),
             "days_with_0900_forecast": len(fcd), "flagged_days": int(g["flagged"].sum()), "called_days_C": len(cl),
             "E_Q_kwh": g["E_Q_kwh"].mean(), "E_dN_kwh": g["E_dN_kwh"].mean()}
        for k in ("a_self_use_c", "event_debs_c", "wear_c", "hassle_c", "d_customer_min_c", "offset_c", "ops_c"):
            r[k] = g[k].mean()
        r["b_energy_0900_c"] = fcd["b_energy_0900_c"].mean()
        r["b_energy_0900_flagged_c"] = fl["b_energy_0900_c"].mean() if len(fl) else np.nan
        r["c_ircr_0900_flagged_c"] = fl["c_ircr_0900_c"].mean() if len(fl) else np.nan
        r["d_zenith_max_0900_unflagged_c"] = un["d_zenith_max_0900_c"].mean() if len(un) else np.nan
        r["d_zenith_max_0900_flagged_c"] = fl["d_zenith_max_0900_c"].mean() if len(fl) else np.nan
        r["d_zenith_max_0900_best_day_c"] = fcd["d_zenith_max_0900_c"].max() if len(fcd) else np.nan
        r["days_zenith_max_above_customer_min_0900"] = int((fcd["surplus_0900_c"] > 0).sum())
        r["Q_actual_kwh"] = g["Q_actual_kwh"].mean()
        r["b_energy_actual_c"] = wmean(g["b_energy_actual_c"], g["Q_actual_kwh"])
        r["a_self_use_actual_c"] = wmean(g["a_self_use_actual_c"], g["Q_actual_kwh"])
        r["d_customer_min_actual_c"] = wmean(g["d_customer_min_actual_c"], g["Q_actual_kwh"])
        r["d_zenith_max_actual_called_c"] = wmean(cl["d_zenith_max_actual_c"], cl["Q_actual_kwh"]) if len(cl) else np.nan
        for name, rate in (("70c_ref", kv.REF_70C), ("85c_model", kv.MODEL_85C)):
            r[f"{name}_covers_customer"] = bool(C * rate >= r["d_customer_min_c"])
            r[f"{name}_within_zenith_flagged"] = (bool(C * rate <= r["d_zenith_max_0900_flagged_c"])
                                                  if len(fl) else np.nan)
            r[f"{name}_within_zenith_unflagged"] = (bool(C * rate <= r["d_zenith_max_0900_unflagged_c"])
                                                    if len(un) else np.nan)
        rows.append(r)
    return pd.DataFrame(rows)


def annual(p, fam, m, allocs) -> pd.DataFrame:
    rows = []
    for pol_name, sc in CASES:
        pol, obl = kv.POLICIES[pol_name], kv.SCENARIOS[sc]
        fit = kv.fit_of(pol_name, sc)
        u = kv.actual_items(p, pol, obl)
        for period in PERIODS:
            if period.startswith("FY") and obl.capacity == "none":
                continue
            calls, y, season, days = kv.calls_for(pol, obl, period, fit)
            j, d = calls.start, calls.day
            q = p.q[j, d]
            share_days = len(days) / 365.0
            cap_resp = 0.0
            if season is not None and obl.capacity != "none":
                _, val = kv.my_ircr(p, d, j, obl, season, ps.peak_intervals())
                cap_resp = obl.capacity_share * val
            annual_resp = (pol.annual_fee + obl.ops_per_customer) * share_days / kv.AVAIL
            qs = float(q.sum())
            w_z = float(p.w[j, d].sum()) * float(obl.energy_to_zenith)
            offs = float((u["offset_c"][j, d] * q).sum())
            ops = obl.ops_per_event * len(d)
            cust_cost = float((u["p_c"][j, d] * q).sum())
            pay = float((pol.pay_per_kwh * q + pol.pay_per_event).sum())
            label = {"in-sample": "合同年2025-26（样本内，365天）", "test": "测试期 2026-07-01至10-22（114天）",
                     "FY2023": "合同年2023-24（数据从2023-10-01起，274天；只算热季调用）",
                     "FY2024": "合同年2024-25（365天；只算热季调用）"}[period]
            r = {"policy": pol_name, "scenario": sc, "period": label, "calls": len(d), "Q_sum_kwh_per_responding": qs,
                 "W_to_zenith_A$": w_z, "ircr_to_zenith_A$_per_responding": cap_resp, "offset_A$": offs, "ops_event_A$": ops,
                 "annual_costs_A$_per_responding": annual_resp, "customer_costs_net_of_offset_A$": cust_cost,
                 "payment_A$": pay}
            if qs > 0:
                r["zenith_max_c_per_kwh_year"] = C * (w_z + cap_resp - offs - ops - annual_resp) / qs
                r["customer_min_c_per_kwh_year"] = C * cust_cost / qs
                r["paid_c_per_billed_kwh"] = C * pay / qs
                r["zenith_A$_per_enrolled_check"] = kv.AVAIL * qs * (r["zenith_max_c_per_kwh_year"] - r["paid_c_per_billed_kwh"]) / C
                r["customer_A$_per_enrolled_check"] = kv.AVAIL * qs * (r["paid_c_per_billed_kwh"] - r["customer_min_c_per_kwh_year"]) / C \
                    + pol.annual_fee * share_days
            else:
                r.update({"zenith_max_c_per_kwh_year": np.nan, "customer_min_c_per_kwh_year": np.nan,
                          "paid_c_per_billed_kwh": np.nan, "zenith_A$_per_enrolled_check": -annual_resp * kv.AVAIL,
                          "customer_A$_per_enrolled_check": pol.annual_fee * share_days})
            e = ps.evaluate_policy(pol, market_obligations=obl, period=period, fit=fit)
            r["zenith_A$_per_enrolled_evaluate_policy"] = e.zenith_per_customer
            r["customer_A$_per_enrolled_evaluate_policy"] = e.customer_benefit
            rows.append(r)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------ NUMBERS.csv
NUM: list[dict] = []


def add(name, value, unit, period, source, status, formula="", check="", note=""):
    NUM.append({"name": name, "value": repr(float(value)), "unit": unit, "formula": formula, "period": period,
                "source": source, "status": status, "check": check, "note": note})


def numbers(p, mm, m, fc, mon, alloc_df, allocs, ann, bal_df, rec_df):
    P_IN = "样本内 合同年2025-26（2025-07-01至2026-06-30，365天）"
    P_T1 = "测试期 已看过 2026-07-01至2026-10-15（107天）"
    P_T2 = "测试期 未看过 2026-10-16至2026-10-22（7天）"
    SRC = "analysis/2026-10-26/customer_kwh_value/run.py"
    WIN = kv.window_label(kv.DEFAULT_START)
    per_call = "A$/(户[响应]*次)"
    kwh_call = "kWh/(户[响应]*次)"
    add("零售电价A1", C * ps.RETAIL, "c/kWh", "2026-07-01起，模型用于所有年份", "vpp/policy/policy_simulator.py RETAIL；Synergy A1",
        "调研", note="POLICY_ASSUMPTIONS.md 标 VERIFIED；本任务没有重读原文")
    add("DEBS峰时上网电价", C * ps.DEBS_PEAK, "c/kWh", "现行", "policy_simulator.py DEBS_PEAK", "调研", note="15:00-21:00")
    add("磨损成本", C * kv.PART.deg_per_kwh, "c/kWh", "全部", "policy_simulator.py Participation", "假设")
    add("麻烦费", kv.PART.hassle_per_event, per_call, "全部", "policy_simulator.py Participation", "假设")
    add("每次运营费", kv.SCENARIOS["S2b"].ops_per_event, per_call, "全部", "policy_simulator.py Obligations", "假设")
    add("参考条款70c", C * kv.REF_70C, "c/kWh", "Battery Rewards V3（2026-05-06生效）", "POLICY_ASSUMPTIONS.md 表2 第11行 V3 PD 4",
        "调研", note="Synergy 参考条款，不是 Zenith 已批准的 tariff；本任务没有重读原文")
    add("模型建议85c", C * kv.MODEL_85C, "c/kWh", "样本内选择", "reports/policy/RECOMMENDATION.md", "推算",
        note="模型建议，不是合同")
    add("名义每次出口电量", kv.POLICIES["C-S2b"].nominal_kwh(kv.HH), kwh_call, "设计值", "Policy.nominal_kwh：13.5×0.8×√0.9",
        "推算")
    add("C_S2b按次付款", kv.POLICIES["C-S2b"].pay_per_event, per_call, "模型建议", "policy_optimizer.make_policy", "推算",
        formula="{模型建议85c} * {名义每次出口电量}", note="模型建议，不是合同")

    # default window, in-sample, S2b: customer side and Zenith side, flagged days and other days
    dd_in = mon[(mon["period"].str.startswith("样本内")) & (mon["window"] == WIN)]
    j = p.index(kv.DEFAULT_START)
    # in-sample flagged days (Hot Season), S2b; means over those days of the 09:00 values
    flagged = RESULTS["flag_days_in"]
    g = RESULTS["flag_means"]
    add("热季旗标日数_S2b", len(flagged), "天", P_IN + " 中的热季旗标日", SRC, "回测", note="C 的规则 q=0.75")
    add("旗标日_期望计费出口", g["E_Q_kwh"], kwh_call, P_IN + " 旗标日均值", SRC, "回测",
        note="同月份、2026-06-30以前日子的平均，09:00可知")
    add("旗标日_期望净出口变化ΔN", g["E_dN_kwh"], kwh_call, P_IN + " 旗标日均值", SRC, "回测", note="市场价值用的电量，不是计费电量")
    add("旗标日_a客户自用价值", g["a_self_use_c"], "c/kWh", P_IN + " 旗标日均值", SRC, "回测", note="每kWh计费净出口")
    add("旗标日_事件出口DEBS", g["event_debs_c"], "c/kWh", P_IN + " 旗标日均值", SRC, "回测")
    add("旗标日_磨损", g["wear_c"], "c/kWh", P_IN + " 旗标日均值", SRC, "回测")
    add("旗标日_麻烦费每kWh", g["hassle_c"], "c/kWh", P_IN + " 旗标日均值", SRC, "回测")
    add("旗标日_d客户最少收", g["d_customer_min_c"], "c/kWh", P_IN + " 旗标日均值", SRC, "推算",
        formula="{旗标日_a客户自用价值} - {旗标日_事件出口DEBS} + {旗标日_磨损} + {旗标日_麻烦费每kWh}")
    add("旗标日_b市场价值09点", g["b_energy_0900_c"], "c/kWh", P_IN + " 旗标日均值", SRC, "回测",
        note="09:00预测价+尖峰概率×uplift；相对不调用的增量金额÷计费电量")
    add("旗标日_提前充电补贴", g["offset_c"], "c/kWh", P_IN + " 旗标日均值", SRC, "回测")
    add("旗标日_运营费每kWh", g["ops_c"], "c/kWh", P_IN + " 旗标日均值", SRC, "回测")
    for sc in ("S2b", "S3"):
        pooled = C * kv.pooled_per_kwh([allocs[(sc, "C", y, kv.DEFAULT_START)] for y in ps.SEASONS])
        add(f"{sc}_c_IRCR每kWh_三季合并", pooled, "c/kWh", "热季2023-24、2024-25、2025-26（样本内）", SRC, "回测",
            note="平均摊销，不是单次调用的边际价值")
    add("S2b_旗标日_d_Zenith最多付", g["S2b_pz"], "c/kWh", P_IN + " 旗标日均值", SRC, "推算",
        formula="{旗标日_b市场价值09点} + {S2b_c_IRCR每kWh_三季合并} - {旗标日_提前充电补贴} - {旗标日_运营费每kWh}")
    add("S3_旗标日_d_Zenith最多付", g["S3_pz"], "c/kWh", P_IN + " 旗标日均值", SRC, "推算",
        formula="{S3_c_IRCR每kWh_三季合并} - {旗标日_提前充电补贴} - {旗标日_运营费每kWh}", note="S3 energy 归 Synergy")
    add("S2b_旗标日_余量", g["S2b_pz"] - g["d_customer_min_c"], "c/kWh", P_IN + " 旗标日均值", SRC, "推算",
        formula="{S2b_旗标日_d_Zenith最多付} - {旗标日_d客户最少收}")
    add("S3_旗标日_余量", g["S3_pz"] - g["d_customer_min_c"], "c/kWh", P_IN + " 旗标日均值", SRC, "推算",
        formula="{S3_旗标日_d_Zenith最多付} - {旗标日_d客户最少收}")
    u = RESULTS["unflag_means"]
    add("非旗标日_b市场价值09点", u["b_energy_0900_c"], "c/kWh", P_IN + " 非旗标日均值（有预测的日子）", SRC, "回测")
    add("非旗标日_提前充电补贴", u["offset_c"], "c/kWh", P_IN + " 非旗标日均值", SRC, "回测")
    add("非旗标日_运营费每kWh", u["ops_c"], "c/kWh", P_IN + " 非旗标日均值", SRC, "回测")
    add("S1_非旗标日_d_Zenith最多付", u["S1_pz"], "c/kWh", P_IN + " 非旗标日均值", SRC, "推算",
        formula="{非旗标日_b市场价值09点} - {非旗标日_提前充电补贴} - {非旗标日_运营费每kWh}")
    add("非旗标日_d客户最少收", u["d_customer_min_c"], "c/kWh", P_IN + " 非旗标日均值", SRC, "回测")
    add("样本内_Zenith最多付高于客户最少收的天数_S1", RESULTS["S1_days_pos"], "天", P_IN, SRC, "回测",
        note="09:00事前值，默认窗口")

    # seasons, S2b, C rule
    for y in ps.SEASONS:
        a = allocs[("S2b", "C", y, kv.DEFAULT_START)]
        tag = f"{y}-{str(y + 1)[2:]}"
        per = f"热季{tag}" + ("（样本内）" if y == 2025 else "（样本内，当时无09:00价格预测）")
        add(f"S2b_{tag}_调用次数", a.n_calls, "次", per, SRC, "回测", note="C 的规则")
        add(f"S2b_{tag}_计费出口合计", a.q_sum, "kWh/户[响应]", per, SRC, "回测")
        add(f"S2b_{tag}_IRCR降幅", a.kw, "kW/户[响应]", per, SRC, "回测", note="12个Peak SWIS区间净进口中位数之差")
        add(f"RCP_{tag}", a.rcp, "A$/(MW*年)", f"容量年{y + 1}-{str(y + 2)[2:]}（as-if 惯例）", "vpp/contracts.py RCP", "调研")
        add(f"一季按一年计_{tag}", 1.0, "年", per, "as-if：一季对应一个容量年", "假设")
        add(f"S2b_{tag}_IRCR价值", a.value_zenith, "A$/户[响应]", per, SRC, "推算",
            formula=f"{{S2b_{tag}_IRCR降幅}} * {{RCP_{tag}}} * {{一季按一年计_{tag}}}")
        add(f"S2b_{tag}_c_IRCR每kWh", C * a.per_kwh, "c/kWh", per, SRC, "推算",
            formula=f"{{S2b_{tag}_IRCR价值}} / {{S2b_{tag}_计费出口合计}}")
        add(f"S2b_{tag}_IRCR每次", a.value_zenith / a.n_calls, per_call.replace("户[响应]*次", "户[响应]*次"), per, SRC, "推算",
            formula=f"{{S2b_{tag}_IRCR价值}} / {{S2b_{tag}_调用次数}}")
        p4 = allocs[("S2b", "peak4", y, kv.DEFAULT_START)]
        add(f"S2b_{tag}_c_IRCR每kWh_只调4个峰值日", C * p4.per_kwh, "c/kWh", per, SRC, "回测", note="事后才知道峰值日，做不到")
    add("S2b_三季合并_c_IRCR每kWh_验算", C * kv.pooled_per_kwh([allocs[("S2b", "C", y, kv.DEFAULT_START)] for y in ps.SEASONS]),
        "c/kWh", "热季2023-24至2025-26（样本内）", SRC, "推算",
        formula="({S2b_2023-24_IRCR价值} + {S2b_2024-25_IRCR价值} + {S2b_2025-26_IRCR价值}) / "
                "({S2b_2023-24_计费出口合计} + {S2b_2024-25_计费出口合计} + {S2b_2025-26_计费出口合计})")
    add("S3容量分成", kv.SCENARIOS["S3"].capacity_share, "1", "假设", "policy_optimizer.SCENARIOS", "假设")
    add("S3_三季合并_c_IRCR每kWh_验算", C * kv.pooled_per_kwh([allocs[("S3", "C", y, kv.DEFAULT_START)] for y in ps.SEASONS]),
        "c/kWh", "热季2023-24至2025-26（样本内）", SRC, "推算",
        note="S3 的调用日来自 C-S3 的规则，和 S2b 的相同时才等于 0.5 倍 S2b")
    add("RCP_2027-28", ct.RCP[2027], "A$/(MW*年)", "容量年2027-28", "vpp/contracts.py RCP", "调研")

    # annual (contract-year) break-even, C policies
    for pol_name, sc in (("C-S2b", "S2b"), ("C-S3", "S3"), ("B", "S2b")):
        r = ann[(ann["policy"] == pol_name) & (ann["scenario"] == sc) & (ann["period"].str.startswith("合同年2025-26"))].iloc[0]
        t = f"{pol_name}_{sc}_全年"
        unit_t = "A$/户[响应]"
        add(f"{t}_计费出口合计", r["Q_sum_kwh_per_responding"], "kWh/户[响应]", P_IN, SRC, "回测")
        add(f"{t}_市场价值归Zenith", r["W_to_zenith_A$"], unit_t, P_IN, SRC, "回测")
        add(f"{t}_IRCR归Zenith", r["ircr_to_zenith_A$_per_responding"], unit_t, P_IN + "（热季2025-26）", SRC, "回测")
        add(f"{t}_提前充电补贴", r["offset_A$"], unit_t, P_IN, SRC, "回测")
        add(f"{t}_每次运营费合计", r["ops_event_A$"], unit_t, P_IN, SRC, "回测")
        add(f"{t}_年度运营费折每响应户", r["annual_costs_A$_per_responding"], unit_t, P_IN, SRC, "推算",
            note="$25/报名户/年 ÷ 响应率0.9")
        add(f"{t}_客户成本扣offset", r["customer_costs_net_of_offset_A$"], unit_t, P_IN, SRC, "回测")
        add(f"{t}_Zenith最多付", r["zenith_max_c_per_kwh_year"], "c/kWh", P_IN, SRC, "推算",
            formula=f"({{{t}_市场价值归Zenith}} + {{{t}_IRCR归Zenith}} - {{{t}_提前充电补贴}} - {{{t}_每次运营费合计}} "
                    f"- {{{t}_年度运营费折每响应户}}) / {{{t}_计费出口合计}}")
        add(f"{t}_客户最少收", r["customer_min_c_per_kwh_year"], "c/kWh", P_IN, SRC, "推算",
            formula=f"{{{t}_客户成本扣offset}} / {{{t}_计费出口合计}}")
        add(f"{t}_实付每计费kWh", r["paid_c_per_billed_kwh"], "c/kWh", P_IN, SRC, "回测",
            note="C-S2b 按次付 $8.7089，折成每实际计费 kWh")
    # C-S2b effective rate per billed kWh
    rr = ann[(ann["policy"] == "C-S2b") & (ann["period"].str.startswith("合同年2025-26"))].iloc[0]
    add("C_S2b_样本内调用次数", rr["calls"], "次", P_IN, SRC, "回测")
    add("C_S2b_样本内平均计费出口", rr["Q_sum_kwh_per_responding"] / rr["calls"], kwh_call, P_IN, SRC, "推算",
        formula="{C-S2b_S2b_全年_计费出口合计} / {C_S2b_样本内调用次数}")
    add("C_S2b_按次付款折每计费kWh", C * kv.POLICIES["C-S2b"].pay_per_event / (rr["Q_sum_kwh_per_responding"] / rr["calls"]),
        "c/kWh", P_IN, SRC, "推算", formula="{C_S2b按次付款} / {C_S2b_样本内平均计费出口}")

    # energy balance, default window, all days
    b = bal_df[bal_df["window"] == WIN].iloc[0]
    PB = "2023-10-01至2026-10-22 全部日子（1118天）每天一次候选调用的均值"
    for k, zh in (("avail", "事件开始reserve以上存电"), ("pv", "事件内PV"), ("home", "事件内家里用掉"),
                  ("export", "事件内电表出口"), ("loss", "事件内放电损耗"), ("retained", "事件结束reserve以上留存")):
        add(f"平衡_{zh}", b[f"mean_event_{k}_kwh"], kwh_call, PB, SRC, "回测")
    add("平衡_可用电量", b["mean_event_avail_kwh"] + b["mean_event_pv_kwh"], kwh_call, PB, SRC, "推算",
        formula="{平衡_事件内家里用掉} + {平衡_事件内电表出口} + {平衡_事件内放电损耗} + {平衡_事件结束reserve以上留存}")
    add("平衡_事件内最大残差", RESULTS["balance_event_max"], "kWh/(户[响应]*次)", "全部日子、全部窗口", SRC, "回测",
        check="<= 0.000001")
    add("平衡_跟踪期增量最大残差", RESULTS["balance_horizon_max"], "kWh/(户[响应]*次)", "全部日子、全部窗口", SRC, "回测",
        check="<= 0.000001")
    add("平衡_跟踪期多进口", b["mean_extra_import_kwh"], kwh_call, PB, SRC, "回测")
    add("平衡_跟踪期多出口", b["mean_extra_export_kwh"], kwh_call, PB, SRC, "回测")
    add("平衡_跟踪期多损耗", b["mean_extra_loss_kwh"], kwh_call, PB, SRC, "回测")
    add("平衡_次日15点缺口", b["mean_gap_kwh"], kwh_call, PB, SRC, "回测", note="负数=比不调用少")
    add("平衡_跟踪期净多进口", b["mean_extra_import_kwh"] - b["mean_extra_export_kwh"], kwh_call, PB, SRC, "推算",
        formula="{平衡_跟踪期多损耗} + {平衡_次日15点缺口}")
    # reconciliation
    add("对账_每次调用最大差", RESULTS["recon_per_call"], "A$/(户[报名]*次)", "样本内、测试期、热季2023-24、2024-25", SRC, "回测",
        check="<= 0.000001")
    add("对账_每季容量最大差", RESULTS["recon_cap"], "A$/户[报名]", "热季2023-24、2024-25、2025-26", SRC, "回测",
        check="<= 0.000001")
    add("对账_和保存回测最大差", RESULTS["recon_saved"], "A$", "样本内、测试期、热季2023-24、2024-25", SRC, "回测", check="<= 0.01")
    # test period
    for lab, per in ((P_T1, "测试期 已看过"), (P_T2, "测试期 未看过")):
        t = mon[(mon["period"].str.startswith(per)) & (mon["window"] == WIN) & (mon["scenario"] == "S1")]
        w = t["days_with_0900_forecast"]
        tag = "已看过" if "已看过" in per else "未看过"
        add(f"测试期{tag}_b市场价值09点", wmean(t["b_energy_0900_c"], w), "c/kWh", lab, SRC, "回测")
        add(f"测试期{tag}_b市场价值实际", wmean(t["b_energy_actual_c"], t["days"] * t["Q_actual_kwh"]), "c/kWh", lab, SRC, "回测")
        add(f"测试期{tag}_d客户最少收", wmean(t["d_customer_min_c"], t["days"]), "c/kWh", lab, SRC, "回测")
        add(f"测试期{tag}_S1_Zenith最多付", wmean(t["d_zenith_max_0900_unflagged_c"], w), "c/kWh", lab, SRC, "回测",
            note="测试期没有热季，IRCR 为 0，无法检验")
        add(f"测试期{tag}_Zenith最多付高于客户最少收的天数", int(t["days_zenith_max_above_customer_min_0900"].sum()), "天", lab,
            SRC, "回测")
    with (HERE / "NUMBERS.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=["name", "value", "unit", "formula", "period", "source", "status", "check", "note"])
        wr.writeheader()
        wr.writerows(NUM)


# ------------------------------------------------------------------------------------------------ main
def main() -> int:
    t0 = time.time()
    m, fc = ps.load_market(), ps.load_forecasts()
    fam = ps.family(*kv.FAM_KEY)
    p = kv.decompose()
    mm = kv.monthly_means(p, m)
    say(f"数据：{m.days[0].date()} 至 {m.days[-1].date()}，{m.n_days} 天；窗口 {[kv.window_label(s) for s in p.starts]}")
    ok = {}
    ok["1 重算一致"] = check_rerun(p, fam)
    ok["2 电量收支闭合"], bal = check_balance(p, m)
    ok["3 对账"], rec, bugs = check_reconcile(p, fam, m)
    ok["4 09:00与energy_scores一致"] = check_scores(p, mm, m, fc)
    ok["5 不偷看"] = check_lookahead(p, mm, m, fc)
    caught = all(v[0] for v in bugs.values())
    say(f"[6] 埋错检查（对账必须报错）：{verdict(caught)}  " +
        "；".join(f"{k} 差 {v[1]:.3g} A$ → {'抓到' if v[0] else '没抓到'}" for k, v in bugs.items()))
    ok["6 埋错能抓到"] = caught
    bal.to_csv(OUT / "energy_balance.csv", index=False)
    rec.to_csv(OUT / "reconciliation.csv", index=False)

    allocs, alloc_df = allocations(p)
    alloc_df.to_csv(OUT / "season_ircr.csv", index=False)
    dd = daily(p, mm, m, fc, allocs)
    dd.round(6).to_csv(OUT / "daily.csv", index=False)
    mon = monthly(dd)
    mon.round(4).to_csv(OUT / "monthly.csv", index=False)
    ann = annual(p, fam, m, allocs)
    ann.to_csv(OUT / "annual_breakeven.csv", index=False)
    d_ann = max((ann["zenith_A$_per_enrolled_check"] - ann["zenith_A$_per_enrolled_evaluate_policy"]).abs().max(),
                (ann["customer_A$_per_enrolled_check"] - ann["customer_A$_per_enrolled_evaluate_policy"]).abs().max())
    say(f"[3b] 全年口径乘回去 = evaluate_policy（每报名户）：{verdict(d_ann <= TOL_CALL)}  最大差 {d_ann:.2e} A$")
    ok["3b 全年口径对账"] = d_ann <= TOL_CALL

    # summaries for NUMBERS.csv and RESULTS.md
    WIN = kv.window_label(kv.DEFAULT_START)
    ins = dd[(dd["period"].str.startswith("样本内")) & (dd["window"] == WIN)]
    s2b = ins[ins["scenario"] == "S2b"]
    fl = s2b[s2b["flagged"] & s2b["has_0900_forecast"]]
    RESULTS["flag_days_in"] = fl["day"].tolist()
    g = {k: fl[k].mean() for k in ("E_Q_kwh", "E_dN_kwh", "a_self_use_c", "event_debs_c", "wear_c", "hassle_c",
                                    "d_customer_min_c", "b_energy_0900_c", "offset_c", "ops_c")}
    g["S2b_pz"] = fl["d_zenith_max_0900_c"].mean()
    s3 = ins[(ins["scenario"] == "S3") & ins["day"].isin(fl["day"])]
    g["S3_pz"] = s3["d_zenith_max_0900_c"].mean()
    RESULTS["flag_means"] = g
    s1 = ins[(ins["scenario"] == "S1") & ins["has_0900_forecast"]]
    un = s1[~s1["day"].isin(fl["day"])]
    RESULTS["unflag_means"] = {"b_energy_0900_c": un["b_energy_0900_c"].mean(), "offset_c": un["offset_c"].mean(),
                               "ops_c": un["ops_c"].mean(), "S1_pz": un["d_zenith_max_0900_c"].mean(),
                               "d_customer_min_c": un["d_customer_min_c"].mean()}
    RESULTS["S1_days_pos"] = int((s1["surplus_0900_c"] > 0).sum())
    numbers(p, mm, m, fc, mon, alloc_df, allocs, ann, bal, rec)
    res = check_numbers(HERE / "NUMBERS.csv")
    nfail = sum(1 for lvl, _ in res if lvl == "FAIL")
    for lvl, msg in res:
        say(f"      {lvl} {msg}")
    say(f"[7] NUMBERS.csv 按单位复算（agents.check.check_numbers）：{verdict(nfail == 0)}")
    ok["7 单位复算"] = nfail == 0
    say("")
    say("总结：" + "；".join(f"{k} {verdict(v)}" for k, v in ok.items()))
    say(f"用时 {time.time() - t0:.0f} 秒")
    (OUT / "checks.txt").write_text("\n".join(LINES) + "\n", encoding="utf-8")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
