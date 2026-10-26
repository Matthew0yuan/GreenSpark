"""Run PLAN.md: rules R0-R3 on the two historical folds at budgets 15 / 30 / 45, money in both ledgers and (fold A)
both proxy labels, the checks of section 6, and the frozen rule for 2026-27.

Run from the repo root:  .venv/Scripts/python.exe analysis/2026-10-26/peak_day_money/run.py
Writes out/money.csv, out/calls.csv, out/value_curve.csv, out/r3_log.csv, out/checks.json, out/frozen_money_rule.json,
out/frozen_dp_thresholds_money.csv
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import money_rule as mr  # noqa: E402
from money_rule import pp, ps, rex  # noqa: E402
from vpp import contracts as ct  # noqa: E402
from vpp.evaluate import TRAIN_END  # noqa: E402

OUT = HERE / "out"
BUDGETS = (15, 30, 45)
FOLD = {2024: "A", 2025: "B"}
PERIOD = {2024: "热季 2024-12-01 至 2025-03-31（121 天）", 2025: "热季 2025-12-01 至 2026-03-31（121 天）"}
CUT = 60                       # perturbation check: decisions up to this season day must not see later data


def rule_calls(y: int, ev: pd.DataFrame, tr: list, tops: list, v2: np.ndarray, cost: float) -> tuple[dict, list]:
    """calls per rule key 'method|policy|budget', and R3 logs."""
    p, dl = ev["M3"].to_numpy(), ev["days_left"].to_numpy()
    calls, logs = {}, []
    for B in BUDGETS:
        calls[f"R0 existing rule|FCFS|{B}"] = pp.fcfs_calls(ev["M1_union"].to_numpy(), B)
        calls[f"R1 M3 + DP c=0.03|DP|{B}"] = pp.dp_calls(p, dl, pp.dp_thresholds(tr, "M3", B, pp.COST), B)
        calls[f"ref M3 + DP c=0 (spend all)|DP|{B}"] = pp.dp_calls(p, dl, pp.dp_thresholds(tr, "M3", B, 0.0), B)
        for q in (mr.Q,) + mr.Q_SENS:
            c_unit = cost / mr.dvbar(v2, q)
            th = pp.dp_thresholds(tr, "M3", B, c_unit)
            calls[f"R2 M3 + DP money cost q={q}|DP|{B}"] = pp.dp_calls(p, dl, th, B)
            c3, log = mr.r3_calls(tops, dl, th, B, v2, q, cost)
            calls[f"R3 M3 + money marginal q={q}|marginal|{B}"] = c3
            logs.append(log.assign(season=y, budget=B, q=q, trading_day=ev.index, p_m3=p))
    return calls, logs


def main() -> None:
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    cfg = pp.Config()
    d = pp.daily()
    mo = pp.models(d, cfg)
    m, fam, j, pol, obls, part = mr.setup()
    lab = pp.labels()
    checks, money_rows, call_rows, curve_rows, logs = {}, [], [], [], []

    for y, pools in rex.EVAL.items():
        assert all(t < y for t in pools), "training seasons must precede the evaluated season"
        ev = pp.run_season(y, pools, cfg, d=d, mo=mo)
        tr = rex.training_runs(y, cfg, d, mo)
        tops = mr.season_paths(y, pools, cfg, d, mo)
        rep = np.array([t[:, i].mean() for i, t in enumerate(tops)])
        checks[f"{FOLD[y]}_paths_reproduce_M3_maxdiff"] = float(np.nanmax(np.abs(rep - ev["M3"].to_numpy())))
        print(f"{FOLD[y]}: season and paths {time.time() - t0:.0f}s", flush=True)

        kw2, kw1 = mr.v_kw(pools, obls["L2"]), mr.v_kw(pools, obls["L1"])
        v2, v1 = mr.v_dollars(kw2, y, obls["L2"]), mr.v_dollars(kw1, y, obls["L1"])
        cost = mr.call_cost(pools, obls["L2"])
        checks[f"{FOLD[y]}_L1_value_curve_max"] = float(np.abs(v1).max())
        for q in (mr.Q,) + mr.Q_SENS:
            curve_rows.append({"fold": FOLD[y], "season": y, "train": "+".join(map(str, pools)), "q": q, "cost_per_call": cost,
                               "dVbar": mr.dvbar(v2, q), "c_unit": cost / mr.dvbar(v2, q), "rcp": ct.RCP[y + 1],
                               **{f"kw_{k}": kw2[k] for k in range(5)}, **{f"V_{k}": v2[k] for k in range(5)}})

        calls, lg = rule_calls(y, ev, tr, tops, v2, cost)
        logs += lg
        # under L1 the value curve is 0: R2 never calls (dVbar = 0) and R3 must make no call either
        l1_calls = sum(int(mr.r3_calls(tops, ev["days_left"].to_numpy(), np.zeros((46, 123)), B, v1, q, cost)[0].sum())
                       for B in BUDGETS for q in (mr.Q,) + mr.Q_SENS)
        checks[f"{FOLD[y]}_L1_R3_calls_total"] = l1_calls
        checks[f"{FOLD[y]}_L1_dVbar"] = mr.dvbar(v1, mr.Q)
        for k, c in calls.items():
            B = int(k.split("|")[2])
            checks.setdefault("budget_exceeded", 0)
            checks["budget_exceeded"] += int(c.sum() > B)
        wide = {(y, k): c for k, c in calls.items()}

        variants = [(lab[y]["primary_kind"], ps.peak_intervals(), {y: lab[y]["primary"]}, f"lab_primary")]
        if y == 2024:
            variants.append(("proxy_er", mr.er_peaks(2024), {y: lab[y]["proxy_er"]}, "lab_proxy_er"))
        for kind, peaks, oracle, col in variants:
            mon = mr.money(wide, peaks, oracle)
            money_rows.append(mon.assign(fold=FOLD[y], period=PERIOD[y], label=kind))
            labv = ev[col].to_numpy()
            iv = pd.DatetimeIndex([ps.load_market().days[i] + ps.H + pd.Timedelta(minutes=30) * s for i, s in zip(*peaks[y])])
            for k, c in calls.items():
                meth, pol_name, B = k.split("|")
                call_rows.append({"fold": FOLD[y], "season": y, "label": kind, "method": meth, "policy": pol_name, "budget": int(B),
                                  **pp.call_metrics(c, ev.index, labv, iv, int(B))})
        print(f"{FOLD[y]}: rules and money {time.time() - t0:.0f}s", flush=True)

        # check: R0 and R1 money as the study's revenue.csv
        ref = pd.read_csv(rex.OUT / "revenue.csv")
        mon = money_rows[-len(variants)] if y != 2024 else money_rows[-2]
        diffs = []
        for meth, sm, sp in (("R0 existing rule", "M1_union", "FCFS"), ("R1 M3 + DP c=0.03", "M3", "DP")):
            for B in BUDGETS:
                for ln in mr.LEDGERS.values():
                    a = mon[(mon["method"] == meth) & (mon["budget"] == B) & (mon["ledger"] == ln)]["zenith_net"].iloc[0]
                    b = ref[(ref["season"] == y) & (ref["method"] == sm) & (ref["policy"] == sp) & (ref["budget"] == B)
                            & (ref["ledger"] == ln)]["zenith_net"].iloc[0]
                    diffs.append(abs(a - b))
        checks[f"{FOLD[y]}_money_reproduces_study_maxdiff"] = float(max(diffs))

        # check: decisions up to season day CUT unchanged when every later actual and forecast moves by +500 MW
        if y == 2025:
            # public at 09:00 on day D = season day CUT: actuals to D-1, same-day forecasts to D, day-before forecasts
            # to D+1 (the run made on D for D+1); everything after that moves
            gi = pp.season_index(d, y)
            d2 = d.copy()
            D = d2.index[gi[CUT]]
            for c, after in (("y", D - pd.Timedelta(days=1)), ("er", D - pd.Timedelta(days=1)), ("f0", D), ("f1", D + pd.Timedelta(days=1))):
                later = d2.index > after
                d2.loc[later, c] = d2.loc[later, c] + 500.0
            mo2 = pp.models(d2, cfg)
            tops2 = mr.season_paths(y, pools, cfg, d2, mo2, last_dec=CUT)
            same_paths = all(np.array_equal(a, b) for a, b in zip(tops[:CUT + 1], tops2))
            th = pp.dp_thresholds(tr, "M3", 30, cost / mr.dvbar(v2, mr.Q))
            c_a, _ = mr.r3_calls(tops[:CUT + 1], ev["days_left"].to_numpy()[:CUT + 1], th, 30, v2, mr.Q, cost)
            c_b, _ = mr.r3_calls(tops2, ev["days_left"].to_numpy()[:CUT + 1], th, 30, v2, mr.Q, cost)
            checks["B_future_perturbed_paths_identical_to_cut"] = bool(same_paths)
            checks["B_future_perturbed_R3_calls_identical_to_cut"] = bool(np.array_equal(c_a, c_b))
            # positive control: perturb the day itself (an actual not yet public) -> paths must change
            d3 = d.copy()
            d3.loc[d3.index[gi[CUT]], "y"] += 500.0
            tops3 = mr.season_paths(y, pools, cfg, d3, pp.models(d3, cfg), last_dec=CUT + 2)
            checks["B_control_day_CUT_actual_moves_paths_after_publication"] = bool(not np.array_equal(tops[CUT + 2], tops3[CUT + 2]))
            checks["B_control_day_CUT_actual_leaves_own_decision"] = bool(np.array_equal(tops[CUT], tops3[CUT]))
            print(f"B: perturbation checks {time.time() - t0:.0f}s", flush=True)

    money = pd.concat(money_rows, ignore_index=True)
    # R2 / R3 decide with the ledger's own value curve: under L1 it is 0, so they make no call (checked above);
    # their L1 rows are the no-call row, not the L2 calls settled under L1
    l1 = money["method"].str.startswith(("R2", "R3")) & (money["ledger"] == mr.LEDGERS["L1"])
    money.loc[l1, ["calls", "zenith_net", "ircr_value", "pay_energy", "offset", "wholesale", "ops", "customer_net", "violations"]] = 0
    money.to_csv(OUT / "money.csv", index=False)
    pd.DataFrame(call_rows).to_csv(OUT / "calls.csv", index=False)
    pd.DataFrame(curve_rows).to_csv(OUT / "value_curve.csv", index=False)
    pd.concat(logs, ignore_index=True).to_csv(OUT / "r3_log.csv", index=False)

    # frozen for 2026-27: three seasons, DP thresholds from leave-one-season-out probabilities (as the study)
    pools = pp.SEASONS
    kw = mr.v_kw(pools, obls["L2"])
    v = mr.v_dollars(kw, 2026, obls["L2"])
    cost = mr.call_cost(pools, obls["L2"])
    loo = [pp.run_season(s, tuple(x for x in pools if x != s), cfg, d=d, mo=mo) for s in pools]
    rows = []
    for B in BUDGETS:
        c_unit = cost / mr.dvbar(v, mr.Q)
        th = pp.dp_thresholds(loo, "M3", B, c_unit)
        for nn in range(1, B + 1):
            for r in (121, 91, 61, 31, 1):
                rows.append({"budget": B, "calls_left": nn, "days_left": r, "theta": th[nn, r],
                             "lambda_aud": mr.dvbar(v, mr.Q) * max(th[nn, r] - c_unit, 0.0)})
    pd.DataFrame(rows).to_csv(OUT / "frozen_dp_thresholds_money.csv", index=False)
    frozen = {"written": time.strftime("%Y-%m-%d %H:%M"), "data_up_to": str(TRAIN_END.date()),
              "first_use": "Hot Season 2026-12-01 .. 2027-03-31 (shadow run)", "history_seasons": list(pools),
              "q": mr.Q, "kw_by_peak_days_covered": kw.tolist(), "rcp_2027_28": ct.RCP[2027],
              "value_aud_by_peak_days_covered": v.tolist(), "cost_per_call_aud": cost, "dVbar_aud": mr.dvbar(v, mr.Q),
              "c_unit": cost / mr.dvbar(v, mr.Q), "thresholds_file": "frozen_dp_thresholds_money.csv",
              "m3_model": "analysis/2026-10-24/peak_day_probability/results/frozen_model.json",
              "rule": "call when dV_i - cost_per_call >= dVbar x max(theta(calls left, days left) - c_unit, 0); dV_i from M3's "
                      "2,000 paths at 09:00 (PLAN section 3)",
              "units": "A$ per enrolled home, L2 ledger (2027 scenario, assumption), response rate 0.9"}
    (OUT / "frozen_money_rule.json").write_text(json.dumps(frozen, indent=2, ensure_ascii=False), encoding="utf-8")
    checks["runtime_s"] = round(time.time() - t0, 1)
    (OUT / "checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    print(json.dumps(checks, indent=2))

    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 400)
    show = money[(money["ledger"] == mr.LEDGERS["L2"])]
    print(show.pivot_table(index=["method", "budget"], columns=["fold", "label"], values="zenith_net").round(1).to_string())
    print(show.pivot_table(index=["method", "budget"], columns=["fold", "label"], values="calls").to_string())
    print(pd.DataFrame(curve_rows).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
