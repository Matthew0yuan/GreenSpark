"""Run the frozen PLAN.md: two historical forward folds, online policies at fixed budgets, metrics, money in two
ledgers, sensitivities, and the model frozen on data up to 2026-06-30 for the 2026-27 Hot Season.

Folds (historical diagnostics, not formal out-of-sample evidence):
  A  history 2023-10-02 .. 2024-11-30  ->  Hot Season 2024-25 (proxy label)
  B  history 2023-10-02 .. 2025-11-30  ->  Hot Season 2025-26 (AEMO's published label)

Run:  .venv/Scripts/python.exe analysis/2026-10-24/peak_day_probability/run_experiment.py
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import peakprob as pp  # noqa: E402
from vpp import site_bill as sb  # noqa: E402
from vpp.evaluate import CUTOFF, TRAIN_END  # noqa: E402
from vpp.policy import policy_simulator as ps  # noqa: E402

OUT = HERE / "results"
EVAL = {2024: (2023,), 2025: (2023, 2024)}          # evaluated season -> earlier seasons (history pools)
FOLD = {2024: "A", 2025: "B"}
PERIOD = {2024: "热季 2024-12-01 至 2025-03-31（121 天）", 2025: "热季 2025-12-01 至 2026-03-31（121 天）"}


def training_runs(season: int, cfg: pp.Config, d, mo) -> list[pd.DataFrame]:
    """Probabilities on the evaluated season's training seasons, for the DP thresholds: each training season with
    the other training seasons as its pools, or itself when it is the only one (in-sample; thresholds only)."""
    train = EVAL[season]
    runs = []
    for s in train:
        pools = tuple(x for x in train if x != s) or (s,)
        runs.append(pp.run_season(s, pools, cfg, d=d, mo=mo))
    return runs


def evaluate(cfg: pp.Config, d, mo, label: str, costs=(pp.COST,), budgets=pp.BUDGETS, sim: bool = True):
    """Eval runs, training runs, calls and metrics for one configuration."""
    ev = {y: pp.run_season(y, EVAL[y], cfg, d=d, mo=mo, sim=sim) for y in EVAL}
    tr = {y: training_runs(y, cfg, d, mo) for y in EVAL} if sim else None
    lab = pp.labels()
    prob_rows, call_rows, calls_wide = [], [], {}
    methods_p = [m for m in pp.PROB_METHODS if sim or m not in ("M3",)]
    for y, out in ev.items():
        dates = out.index
        dl = out["days_left"].to_numpy()
        for lk in ("primary", "proxy_er"):
            labv = out[f"lab_{lk}"].to_numpy()
            kind = lab[y]["primary_kind"] if lk == "primary" else "proxy_er"
            for m in methods_p:
                for dec, col in (("same_day", m), ("day_ahead", f"{m}_da")):
                    prob_rows.append({"config": label, "fold": FOLD[y], "season": y, "period": PERIOD[y], "label": kind,
                                      "decision": dec, "method": m, **pp.prob_metrics(out[col].to_numpy(), labv)})
            iv = lab[y]["intervals"] if lk == "primary" else None
            for B in budgets:
                for m in pp.FLAG_METHODS:
                    c = pp.fcfs_calls(out[m].to_numpy(), B)
                    call_rows.append({"config": label, "fold": FOLD[y], "season": y, "label": kind, "method": m, "policy": "FCFS",
                                      "budget": B, "cost": np.nan, **pp.call_metrics(c, dates, labv, iv, B)})
                    if lk == "primary":
                        calls_wide[(y, f"{m}|FCFS|{B}")] = c
                for m in methods_p:
                    p = out[m].to_numpy()
                    if sim:
                        for cost in costs:
                            th = pp.dp_thresholds(tr[y], m, B, cost)
                            c = pp.dp_calls(p, dl, th, B)
                            call_rows.append({"config": label, "fold": FOLD[y], "season": y, "label": kind, "method": m, "policy": "DP",
                                              "budget": B, "cost": cost, **pp.call_metrics(c, dates, labv, iv, B)})
                            if lk == "primary" and cost == pp.COST:
                                calls_wide[(y, f"{m}|DP|{B}")] = c
                    c = pp.topn_calls(p, B)
                    call_rows.append({"config": label, "fold": FOLD[y], "season": y, "label": kind, "method": m,
                                      "policy": "offline top-N (reference)", "budget": B, "cost": np.nan,
                                      **pp.call_metrics(c, dates, labv, iv, B)})
            c = labv > 0
            call_rows.append({"config": label, "fold": FOLD[y], "season": y, "label": kind, "method": "oracle", "policy": "perfect foresight",
                              "budget": 4, "cost": np.nan, **pp.call_metrics(c, dates, labv, iv, 4)})
    return ev, tr, pd.DataFrame(prob_rows), pd.DataFrame(call_rows), calls_wide


def pooled_prob(ev: dict, methods, label: str) -> pd.DataFrame:
    rows = []
    both = pd.concat(ev.values())
    lab = both["lab_primary"].to_numpy()
    for m in methods:
        for dec, col in (("same_day", m), ("day_ahead", f"{m}_da")):
            r = pp.prob_metrics(both[col].to_numpy(), lab, base=both["S0"].to_numpy())   # 4 / 121 in each season
            r.pop("peak_ranks")
            r.pop("median_peak_rank")
            rows.append({"config": label, "fold": "A+B", "season": "2024-25+2025-26", "label": "primary", "decision": dec, "method": m, **r})
    return pd.DataFrame(rows)


def reliability_tables(ev: dict) -> pd.DataFrame:
    both = pd.concat(ev.values())
    lab = both["lab_primary"].to_numpy()
    out = []
    for m in pp.PROB_METHODS:
        t = pp.reliability(both[m].to_numpy(), lab)
        t.insert(0, "method", m)
        out.append(t)
    return pd.concat(out, ignore_index=True)


def peak_rank_rows(ev: dict) -> pd.DataFrame:
    """Calls needed in hindsight ordering (offline): the rank of the k-th best-ranked peak day."""
    rows = []
    for y, out in ev.items():
        lab = out["lab_primary"].to_numpy() > 0
        for m in pp.PROB_METHODS:
            r = pd.Series(out[m].to_numpy()).rank(ascending=False, method="max").to_numpy()[lab]
            r = np.sort(r)
            rows.append({"season": y, "method": m, **{f"rank_needed_for_{k}_peaks": int(r[k - 1]) for k in (1, 2, 3, 4)}})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------- the money
def money(calls_wide: dict) -> pd.DataFrame:
    """Zenith's call-related net per enrolled home per Hot Season, the existing policy model unchanged
    (vpp.policy.policy_simulator.account / ircr_value; Synergy-like reference terms; independent-call approximation)."""
    m = ps.load_market()
    fam = ps.family(2.0, 2.0, 0.2, ps.Household(), (15.0, 21.0), str(ps.LAST_DAY.date()))
    j = fam.index(ps.ircr_start(2.0))
    pol = ps.Policy(name="synergy-like reference", pay_per_kwh=0.70, event_h=2.0, standby_h=2.0, reserve_soc=0.20,
                    window=(15.0, 21.0))
    obls = {"L1 现行规则（净出口记 0）": ps.Obligations("L1", energy_to_zenith=True, capacity="floor", ops_per_customer=0.0, ops_per_event=0.05),
            "L2 净出口记负（2027 情景，假设组合下限不起作用）": ps.Obligations("L2", energy_to_zenith=True, capacity="net", ops_per_customer=0.0,
                                                               ops_per_event=0.05)}
    part = ps.Participation(availability=0.9)
    peaks = ps.peak_intervals()
    d = pp.daily()
    rows = []
    keys = sorted({k[1] for k in calls_wide})
    for y in EVAL:
        dates = d.index[pp.season_index(d, y)]
        lab = pp.labels()[y]
        cases = {k: calls_wide[(y, k)] for k in keys if (y, k) in calls_wide}
        cases["none|-|0"] = np.zeros(len(dates), bool)
        cases["oracle|perfect foresight|4"] = dates.isin(lab["primary"])
        for name, called in cases.items():
            days = m.days.get_indexer(dates[called])
            if (days < 0).any():
                raise ValueError("call outside the market days")
            calls = ps.Calls(np.asarray(days, int), np.full(len(days), j, int), np.full(len(days), "ircr", object))
            meth, pol_name, B = name.split("|")
            for ln, obl in obls.items():
                if len(days):
                    acc = ps.account(calls, fam, pol, obl, part, m, y, peaks)
                    kw = ps.ircr_value(calls, fam, obl, m, y, peaks) / (ps.ct.RCP[y + 1] / 1000)
                else:
                    acc = {"zenith_calls": 0.0, "capacity": 0.0, "pay_energy": 0.0, "offset": 0.0, "wholesale": 0.0,
                           "ops_event": 0.0, "customer_calls": 0.0, "violations": 0.0}
                    kw = 0.0
                rows.append({"season": y, "period": PERIOD[y], "ledger": ln, "method": meth, "policy": pol_name, "budget": int(B),
                             "calls": int(called.sum()), "zenith_net": acc["zenith_calls"], "ircr_value": acc["capacity"],
                             "ircr_kw_reduction_per_responding_home": kw, "pay_energy": acc["pay_energy"], "offset": acc["offset"],
                             "wholesale": acc["wholesale"], "ops": acc["ops_event"], "customer_net": acc["customer_calls"],
                             "violations": acc["violations"], "rcp_next_year": ps.ct.RCP[y + 1]})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------------- frozen version
def frozen(cfg: pp.Config, d, mo) -> dict:
    """The configuration for the 2026-27 Hot Season, from data up to 2026-06-30 only."""
    pools = pp.SEASONS
    act = d["er"].dropna()
    tol0 = sb.forecast_tolerance(d["f0"].dropna(), act, list(pools), cfg.q_tol)
    tol1 = sb.forecast_tolerance(d["f1"].dropna(), act, list(pools), cfg.q_tol)
    # ridge coefficients on every warm day up to 2026-06-30 (in operation the model refits each day)
    X, f = pp.design(d, cfg, 0)
    y = d["y"]
    warm = d.index.month.isin(pp.WARM) & (d.index <= TRAIN_END)
    ok = warm & X.notna().all(axis=1).to_numpy() & f.notna().to_numpy() & y.notna().to_numpy()
    Xa, za = X.to_numpy()[ok], (y - f).to_numpy()[ok]
    mu_x, sd = Xa.mean(axis=0), Xa.std(axis=0)
    Z = (Xa - mu_x) / sd
    beta = np.linalg.solve(Z.T @ Z + cfg.alpha * np.eye(Z.shape[1]), Z.T @ (za - za.mean()))
    # DP thresholds from leave-one-season-out probabilities of the three seasons (thresholds only)
    loo = [pp.run_season(s, tuple(x for x in pools if x != s), cfg, d=d, mo=mo) for s in pools]
    th = {}
    rows = []
    for m in pp.PROB_METHODS:
        for B in pp.BUDGETS:
            t = pp.dp_thresholds(loo, m, B, pp.COST)
            th[(m, B)] = t
            for nn in range(1, B + 1):
                for r in (121, 91, 61, 31, 1):
                    rows.append({"method": m, "budget": B, "calls_left": nn, "days_left": r, "threshold": t[nn, r]})
    pd.DataFrame(rows).to_csv(OUT / "frozen_dp_thresholds.csv", index=False)
    return {"written": time.strftime("%Y-%m-%d %H:%M"), "data_up_to": str(TRAIN_END.date()), "first_use": "Hot Season 2026-12-01 .. 2027-03-31",
            "config": asdict(cfg), "history_seasons": list(pools), "m1_tolerance_mw": {"same_day": tol0, "day_before": tol1},
            "m2_ridge_standardised": {"features": list(X.columns), "mean": mu_x.tolist(), "sd": sd.tolist(), "beta": beta.tolist(),
                                      "intercept_mw": float(za.mean()), "train_days": int(ok.sum()),
                                      "note": "target = actual daily max operational demand - forecast; refit daily in operation"},
            "dp_cost": pp.COST, "dp_thresholds_file": "frozen_dp_thresholds.csv",
            "dp_thresholds_from": "leave-one-season-out probabilities of 2023-24, 2024-25, 2025-26 (not forward; thresholds only)"}


def main() -> None:
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    cfg = pp.Config()
    d = pp.daily()
    mo = pp.models(d, cfg)
    print("formal test period Hot Season days:", int((d.loc[str(CUTOFF.date()):"2026-10-22", "season"] >= 0).sum()), flush=True)

    # ---- main
    ev, tr, prob, calls, wide = evaluate(cfg, d, mo, "main", costs=pp.COST_SWEEP)
    prob = pd.concat([prob, pooled_prob(ev, pp.PROB_METHODS, "main")], ignore_index=True)
    daily_out = pd.concat(ev.values())
    daily_out.index.name = "trading_day"
    daily_out.to_csv(OUT / "daily_probabilities.csv")
    cw = []
    for y in EVAL:
        idx = d.index[pp.season_index(d, y)]
        frame = pd.DataFrame({"season": y, "lab_primary": daily_out.loc[idx, "lab_primary"].to_numpy(),
                              "lab_proxy_er": daily_out.loc[idx, "lab_proxy_er"].to_numpy()}, index=idx)
        for (yy, k), c in wide.items():
            if yy == y:
                frame[f"call:{k}"] = c.astype(int)
        cw.append(frame)
    pd.concat(cw).rename_axis("trading_day").to_csv(OUT / "daily_calls.csv")
    prob.to_csv(OUT / "season_prob_metrics.csv", index=False)
    calls.to_csv(OUT / "season_call_metrics.csv", index=False)
    reliability_tables(ev).to_csv(OUT / "reliability.csv", index=False)
    peak_rank_rows(ev).to_csv(OUT / "peak_ranks.csv", index=False)
    print(f"main done {time.time() - t0:.0f}s", flush=True)

    # ---- money (main policy rows only)
    mon = money(wide)
    mon.to_csv(OUT / "revenue.csv", index=False)
    print(f"money done {time.time() - t0:.0f}s", flush=True)

    # ---- sensitivities (report only)
    sens_p, sens_c = [], []
    variants = {"lag2 (actual one day later)": replace(cfg, lag=2), "block 1": replace(cfg, block=1), "block 3": replace(cfg, block=3),
                "block 14": replace(cfg, block=14), "no level ratio": replace(cfg, level=False)}
    for name, c in variants.items():
        mo_c = pp.models(d, c) if c.lag != cfg.lag else mo
        e2, _, p2, c2, _ = evaluate(c, d, mo_c, name, budgets=(30,))
        sens_p.append(pd.concat([p2, pooled_prob(e2, pp.PROB_METHODS, name)]))
        sens_c.append(c2)
        print(f"{name} done {time.time() - t0:.0f}s", flush=True)
    for name, feat in (("M2-lin", "lin"), ("M2-wx", "wx")):
        c = replace(cfg, features=feat)
        mo_c = pp.models(d, c)
        e2, _, p2, c2, _ = evaluate(c, d, mo_c, name, budgets=(30,), sim=False)
        sens_p.append(pd.concat([p2, pooled_prob(e2, [m for m in pp.PROB_METHODS if m != "M3"], name)]))
        sens_c.append(c2[c2["policy"] != "DP"])
        print(f"{name} done {time.time() - t0:.0f}s", flush=True)
    pd.concat(sens_p, ignore_index=True).to_csv(OUT / "sensitivity_prob_metrics.csv", index=False)
    pd.concat(sens_c, ignore_index=True).to_csv(OUT / "sensitivity_call_metrics.csv", index=False)

    # ---- bias model diagnostics: out-of-sample errors on Hot Season days of each fold's season
    diag = []
    for y in EVAL:
        s = daily_out[daily_out["season"] == y]
        for name, pred in (("AEMO same day (f0)", s["f0"]), ("M2 same day", s["mu0"]), ("AEMO day before (f1)", s["f1"]), ("M2 day ahead", s["mu1"])):
            e = s["y"] - pred
            top = s["y"] >= s["y"].nlargest(10).iloc[-1]
            diag.append({"season": y, "period": PERIOD[y], "prediction": name, "mae_mw": float(e.abs().mean()), "bias_mw": float(e.mean()),
                         "mae_top10_days_mw": float(e[top].abs().mean()), "bias_top10_days_mw": float(e[top].mean())})
    pd.DataFrame(diag).to_csv(OUT / "bias_model_errors.csv", index=False)

    # ---- frozen for 2026-27
    fz = frozen(cfg, d, mo)
    fz["formal_test_hot_season_days"] = int((d.loc[str(CUTOFF.date()):"2026-10-22", "season"] >= 0).sum())
    (OUT / "frozen_model.json").write_text(json.dumps(fz, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- validation summary
    cm = calls[calls["policy"].isin(["FCFS", "DP"])]
    val = {"budget_exceeded_rows": int((cm["calls"] > cm["budget"]).sum()),
           "prob_out_of_range": int(sum(((daily_out[c] < 0) | (daily_out[c] > 1) | daily_out[c].isna()).sum()
                                        for c in ["S1", "M1p", "M2", "M3", "S1_da", "M1p_da", "M2_da", "M3_da"])),
           "paths_not_4_peaks": int(((daily_out["top_min"] != 4) | (daily_out["top_max"] != 4)).sum()),
           "p_final_above_p_beat_sim": int((daily_out["M3"] > daily_out["M3_beat_sim"] + 1e-12).sum()),
           "train_end_after_cutoff": int(((daily_out["mu0_train_end_ok"] != 1) | (daily_out["mu1_train_end_ok"] != 1)).sum()),
           "eval_days_missing_same_day_forecast": int(daily_out["f0"].isna().sum()),
           "eval_days_missing_day_before_forecast": int(daily_out["f1"].isna().sum()),
           "eval_days_same_day_run_older_than_0800": int((daily_out["f0_age_h"] > 1.5).sum()),
           "residual_pool_min": int(daily_out["pool_n"].min()),
           "runtime_s": round(time.time() - t0, 1)}
    (OUT / "validation_summary.json").write_text(json.dumps(val, indent=2), encoding="utf-8")
    print(json.dumps(val, indent=2))

    # ---- console summary
    main_p = prob[(prob["config"] == "main") & (prob["label"] != "proxy_er")]
    print(main_p[["fold", "season", "label", "decision", "method", "brier", "bss_vs_S0", "logloss", "sum_p", "mean_p_peak",
                  "median_peak_rank", "avg_precision"]].round(4).to_string(index=False))
    sel = calls[(calls["label"] != "proxy_er") & ((calls["cost"] == pp.COST) | calls["cost"].isna())]
    print(sel[["fold", "method", "policy", "budget", "calls", "covered_days", "covered_intervals", "median_cut", "false_calls",
               "dec_calls", "exhausted_on", "right_day_interval_outside_window"]].to_string(index=False))
    print(f"total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
