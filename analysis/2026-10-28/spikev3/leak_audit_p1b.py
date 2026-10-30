"""Leak audit of spikev3 step 1b's new features (PLAN.md section 5.5). For 30 random issue days D (seed 0), both tasks:
B  scramble labels of target days >= M' - 2 days (M' = D's month) and the 13 signals of issues after D: the month's
   thresholds and weights and the stress of D's rows must not change. Planted leak: the fitting window runs to the
   end of M' (its own labels); its parameters must change on every audited day.
C  scramble labels of target days >= D and spikev3's predictions issued on or after D: sur_28 and sur_60 of D's rows
   must not change. Planted leak: the window D-W+1 .. D; it must change on every audited day.
A uses step-1 features audited in leak_audit_p1.py.

Output: out/p1b_leak_audit.csv; prints PASS / FAIL
Run:    .venv/bin/python analysis/2026-10-28/spikev3/leak_audit_p1b.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp.spike import protocol as pc  # noqa: E402
from vpp.spikev3 import combos as cb  # noqa: E402

sys.path.insert(0, str(HERE))
from run_p1b import load  # noqa: E402

OUT = HERE / "out"
N_DAYS = 30
SUR = [f"sur_{w}" for w in cb.WINDOWS]


def same(a: np.ndarray, b: np.ndarray) -> int:
    """Number of differing values (NaN equal to NaN)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    return int((~((a == b) | (np.isnan(a) & np.isnan(b)))).sum())


def params_diff(p: dict, q: dict) -> int:
    return int(p != q)


def main() -> None:
    data, pred, _ = load()
    v3 = pred[pred["model"] == "spikev3"]
    days = pd.DatetimeIndex(sorted(data["issue_date"].unique()))
    days = days[(days >= pd.Timestamp("2024-12-01")) & (days <= days.max() - pd.Timedelta(days=2))]
    rng = np.random.default_rng(0)
    pick = pd.DatetimeIndex(sorted(rng.choice(days, N_DAYS, replace=False)))
    rows = []
    for d in (0, 1):
        part = data[data["days_ahead"] == d]
        base = v3[v3["days_ahead"] == d]
        sur_ok = cb.surprise(part, base)
        sur_leak = cb.surprise(part, base, shift=0)
        for day in pick:
            month = day.to_period("M").to_timestamp()
            on = part["issue_date"] == day

            # B
            sc = part.copy()
            lab = sc["target_date"] >= month - pd.Timedelta(days=pc.EMBARGO_DAYS)
            sc.loc[lab, "spike"] = rng.integers(0, 2, lab.sum()).astype(float)
            late = sc["issue_date"] > day
            for s in cb.SIGNALS:
                sc.loc[late, s] = rng.uniform(-1e3, 1e3, late.sum())
            p, q = cb.fit_stress(part, month), cb.fit_stress(sc, month)
            a = same(cb.apply_stress(part[on], p), cb.apply_stress(sc[on], q)) + params_diff(p, q)
            end = month + pd.offsets.MonthBegin(1)
            pp, pq = cb.fit_stress(part, month, window_end=end), cb.fit_stress(sc, month, window_end=end)
            rows += [{"days_ahead": d, "day": day.date(), "feature": "B", "check": "A", "changed": a, "signals": len(p)},
                     {"days_ahead": d, "day": day.date(), "feature": "B", "check": "P", "changed": params_diff(pp, pq),
                      "signals": len(pp)}]

            # C
            bs = base.copy()
            m = bs["target_date"] >= day
            bs.loc[m, "spike"] = rng.integers(0, 2, m.sum()).astype(float)
            m = bs["issue_date"] >= day
            bs.loc[m, "p_cal"] = rng.uniform(0, 1, m.sum())
            a = same(sur_ok[on][SUR].to_numpy(), cb.surprise(part[on], bs)[SUR].to_numpy())
            pl = same(sur_leak[on][SUR].to_numpy(), cb.surprise(part[on], bs, shift=0)[SUR].to_numpy())
            rows += [{"days_ahead": d, "day": day.date(), "feature": "C", "check": "A", "changed": a,
                      "signals": int(sur_ok[on][SUR].notna().all(axis=1).sum())},
                     {"days_ahead": d, "day": day.date(), "feature": "C", "check": "P", "changed": pl, "signals": np.nan}]
            print(d, day.date(), {r["feature"] + r["check"]: r["changed"] for r in rows[-4:]}, flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "p1b_leak_audit.csv", index=False)
    a = out[out["check"] == "A"]
    p = out[out["check"] == "P"].groupby(["feature", "days_ahead"])["changed"].apply(lambda s: int((s > 0).sum()))
    ok = (a["changed"] == 0).all() and (p == N_DAYS).all()
    print(f"A: {int((a['changed'] == 0).sum())} of {len(a)} unchanged; P days caught: {p.to_dict()} of {N_DAYS}")
    print("RESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
