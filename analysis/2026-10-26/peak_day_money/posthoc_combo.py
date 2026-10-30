"""Added after the results (RESULTS.md addendum; not part of PLAN's judgement): the stricter peak-day rule (tolerance
q = 0.75, the policy study's rule) and R3, alone and combined, at a 30-call budget.

  strict    the existing rule's flags with the q = 0.75 tolerance from earlier seasons only, first come first served
  or        call when the strict rule flags or R3 says call; R3's paths see every call already made
  and       call only when both say call

Run from the repo root:  .venv/Scripts/python.exe analysis/2026-10-26/peak_day_money/posthoc_combo.py
Writes out/posthoc_combo.csv
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import money_rule as mr  # noqa: E402
from money_rule import pp, ps, rex, sb  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402

OUT = HERE / "out"
BUDGET = 30


def combined(tops, days_left, theta, budget, v, q, cost, flags, mode: str) -> np.ndarray:
    """R3's test (as mr.r3_calls) joined with the strict rule's flag; the paths count every call made so far."""
    dvb = mr.dvbar(v, q)
    c_unit = cost / dvb
    D = mr.marginal_table(v, q)
    called = np.zeros(len(tops), bool)
    left = budget
    for i, top in enumerate(tops):
        if left <= 0:
            break
        K = top[:, called[:i].nonzero()[0]].sum(axis=1) if called[:i].any() else np.zeros(top.shape[0], int)
        mm = top[:, i + 1:].sum(axis=1)
        dv = float((top[:, i] * D[K, mm]).mean())
        lam = dvb * max(theta[left, int(min(days_left[i], theta.shape[1] - 1))] - c_unit, 0.0)
        r3 = dv - cost >= lam
        go = (r3 or flags[i]) if mode == "or" else (r3 and flags[i])
        if go:
            called[i] = True
            left -= 1
    return called


def main() -> None:
    cfg = pp.Config()
    d = pp.daily()
    mo = pp.models(d, cfg)
    _, _, _, _, obls, _ = mr.setup()
    lab = pp.labels()
    actual = sb.daily_max(pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["req_energy_mw"])["req_energy_mw"])
    p0, p1 = sb.peak_day_predictor(0), sb.peak_day_predictor(1)
    rows = []
    for y, pools in rex.EVAL.items():
        ev = pp.run_season(y, pools, cfg, d=d, mo=mo)
        tr = rex.training_runs(y, cfg, d, mo)
        tops = mr.season_paths(y, pools, cfg, d, mo)
        v2 = mr.v_dollars(mr.v_kw(pools, obls["L2"]), y, obls["L2"])
        cost = mr.call_cost(pools, obls["L2"])
        th = pp.dp_thresholds(tr, "M3", BUDGET, cost / mr.dvbar(v2, mr.Q))
        dl = ev["days_left"].to_numpy()
        dates = ev.index
        f = {}
        for q_tol in (0.9, 0.75):
            t0 = sb.forecast_tolerance(p0, actual, list(pools), q_tol)
            t1 = sb.forecast_tolerance(p1, actual, list(pools), q_tol)
            f[q_tol] = dates.isin(sb.dynamic_flags(p0, actual, t0, 1).union(sb.dynamic_flags(p1, actual, t1, 2)))
        calls = {
            "existing rule (q=0.9)": pp.fcfs_calls(f[0.9].astype(float), BUDGET),
            "strict rule (q=0.75)": pp.fcfs_calls(f[0.75].astype(float), BUDGET),
            "R3": mr.r3_calls(tops, dl, th, BUDGET, v2, mr.Q, cost)[0],
            "strict or R3": combined(tops, dl, th, BUDGET, v2, mr.Q, cost, f[0.75], "or"),
            "strict and R3": combined(tops, dl, th, BUDGET, v2, mr.Q, cost, f[0.75], "and"),
        }
        assert np.array_equal(calls["existing rule (q=0.9)"], ev["M1_union"].to_numpy() > 0), "existing rule must match the study"
        wide = {(y, f"{k}|-|{BUDGET}"): c for k, c in calls.items()}
        variants = [(lab[y]["primary_kind"], ps.peak_intervals(), lab[y]["primary"])]
        if y == 2024:
            variants.append(("proxy_er", mr.er_peaks(2024), lab[y]["proxy_er"]))
        for kind, peaks, pk in variants:
            mon = mr.money(wide, peaks, {y: pk})
            for k, c in calls.items():
                for led in mon["ledger"].unique():
                    r = mon[(mon["method"] == k) & (mon["ledger"] == led)].iloc[0]
                    rows.append({"season": y, "label": kind, "ledger": led[:2], "rule": k, "calls": int(c.sum()),
                                 "covered_days": int(np.isin(dates[c], pk).sum()),
                                 "missed": " ".join(str(x.date()) for x in pk if x not in set(dates[c])),
                                 "zenith_net": float(r["zenith_net"]), "ircr_value": float(r["ircr_value"]),
                                 "customer_net": float(r["customer_net"])})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "posthoc_combo.csv", index=False)
    pd.set_option("display.width", 220)
    print(out[out["ledger"] == "L2"].round(1).to_string(index=False))

    # 2023-24 (peaks all in February): do the flag rules run out of calls first come first served? The policy study's
    # flags (tolerance from the other two seasons: no earlier season exists, so not forward)
    m = ps.load_market()
    pflags, _ = ps.ircr_flags(m)
    sel = sb.hot_season(m.days) == 2023
    dates23 = m.days[sel]
    pk = set(lab[2023]["primary"])
    rows23 = []
    for q_tol in (0.75, 0.9):
        f23 = pflags[q_tol][sel].astype(float)
        for B in (30, 45):
            c = pp.fcfs_calls(f23, B)
            rows23.append({"season": 2023, "rule": f"q={q_tol} flags, first come first served", "budget": B, "flagged": int(f23.sum()),
                           "calls": int(c.sum()), "last_call": str(dates23[c].max().date()), "covered_days": len(pk & set(dates23[c])),
                           "peak_days": " ".join(sorted(str(p.date()) for p in pk))})
    r23 = pd.DataFrame(rows23)
    r23.to_csv(OUT / "posthoc_2023_budget.csv", index=False)
    print(r23.to_string(index=False))


if __name__ == "__main__":
    main()
