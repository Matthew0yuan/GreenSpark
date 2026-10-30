"""Self-checks T1-T5 (PLAN.md section 9). Writes out/selfcheck.csv. Says nothing about forecast skill.

T1  scrambled future: labels of target days after the month's training end + 9,999 -> the month's predictions
    must not change (a history month, an in-sample month, a test month on the frozen fit)
T2  planted leak: the month's own days put into its training, then T1 -> the predictions must change
T3  M1 recomputed by a plain loop for 20 random in-sample days -> 0 mismatches
T4  B0 = max of the 30-min pd_req_mw = the daily column -> 0 mismatches
T5  recall: perfect ranking -> 1, reversed -> 0

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_forecast/selfcheck.py
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import forecast as fc  # noqa: E402

TOL = 1e-6


def scrambled(d: pd.DataFrame, h: pd.DataFrame, after: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    d2, h2 = d.copy(), h.copy()
    d2.loc[d2.index > after, "margin_min_1621_mw"] += 9999.0
    m = h2["day"] > after
    h2.loc[m, "lab_residual_mw"] += 9999.0
    h2.loc[m, "margin_min_1621_mw"] += 9999.0
    return d2, h2


def changed(a: pd.DataFrame, b: pd.DataFrame) -> tuple[int, int]:
    cols = ["m1_mw", "m2_mw"]
    a, b = a[cols].sort_index(), b[cols].reindex(a.index)
    diff = ((a - b).abs() > TOL) | (a.isna() != b.isna())
    return int(diff.to_numpy().sum()), int(a.notna().to_numpy().sum())


def main() -> None:
    d, h = fc.load()
    rows = []
    for month in ("2024-07", "2025-12", "2026-08"):
        m = pd.Period(month, "M")
        fm = m if m <= fc.FROZEN_MONTH else fc.FROZEN_MONTH
        days = d.index[(d.index >= m.start_time) & (d.index <= m.end_time)]
        te = fc.month_train_end(fm)
        base = fc.predict(days, d, h, fc.fit(d, h, te))
        d2, h2 = scrambled(d, h, te)
        scr = fc.predict(days, d2, h2, fc.fit(d2, h2, te))
        n_ch, n_val = changed(base, scr)
        rows.append({"check": "T1", "month": month, "fit_month": str(fm), "train_last_day": str(te.date()),
                     "changed": n_ch, "values": n_val, "pass": n_ch == 0 and n_val > 0})
        # T2: the month's own days in training (cap lifted so the frozen test fit can see the test month too)
        le = m.end_time.normalize()
        leak = fc.predict(days, d, h, fc.fit(d, h, le, cap=False))
        leak2 = fc.predict(days, d2, h2, fc.fit(d2, h2, le, cap=False))
        n_ch2, n_val2 = changed(leak, leak2)
        rows.append({"check": "T2", "month": month, "fit_month": str(m), "train_last_day": str(le.date()),
                     "changed": n_ch2, "values": n_val2, "pass": n_ch2 > 0})

    # T3: plain-loop M1
    p, _ = fc.walk_forward(d, h)
    rng = np.random.default_rng(7)
    ins = d.index[(d["period"] == "in_sample").to_numpy()]
    pick = rng.choice(ins[p["m1_mw"].reindex(ins).notna().to_numpy()], 20, replace=False)
    bad = 0
    for day in pd.DatetimeIndex(pick):
        m = day.to_period("M")
        conv = fc.fit(d, h, fc.month_train_end(m))["conv"]
        best, best_slot = None, None
        for _, r in h[h["day"] == day].iterrows():
            v1, v2, g = r["wx_wind_midwest_kmh"], r["wx_wind_merredin_kmh"], r["wx_ghi_perth_wm2"]
            wind = conv[1] * v1 + conv[2] * v1 * v1 + conv[3] * v2 + conv[4] * v2 * v2
            solar = conv[5] * g
            res = r["pd_req_mw"] - conv[0] - wind - solar
            val = r["thermal_cap_mw"] - r["out_thermal_mw"] + r["bat_cap_mw"] - res
            if best is None or val < best:
                best, best_slot = val, r["slot"]
        if abs(best - p.loc[day, "m1_mw"]) > 1e-3 or best_slot != p.loc[day, "t_star"]:
            bad += 1
    rows.append({"check": "T3", "month": "in_sample", "changed": bad, "values": 20, "pass": bad == 0})

    # T4: B0
    mx = h.groupby("day")["pd_req_mw"].max().where(h.groupby("day")["pd_req_mw"].count() == fc.N_SLOTS)
    col = d["pd_req_max_1621_mw"]
    mism = int((((mx - col).abs() > 1e-6) | (mx.isna() != col.isna())).sum())
    rows.append({"check": "T4", "month": "all", "changed": mism, "values": int(col.notna().sum()), "pass": mism == 0})

    # T5: recall function
    x = d[d["period"] == "in_sample"].dropna(subset=["margin_min_1621_mw"]).copy()
    k = fc.kk(len(x))
    act = fc.top_flags(x["margin_min_1621_mw"], k)
    perfect = fc.top_flags(x["margin_min_1621_mw"], k)
    reverse = fc.top_flags(-x["margin_min_1621_mw"], k)
    month = x.index.to_period("M").astype(str)
    r = fc.boot_recall_diff(perfect, reverse, act, pd.Series(month, index=x.index))
    ok = r["recall_a"] == 1.0 and r["recall_b"] == 0.0 and r["lo"] == 1.0
    rows.append({"check": "T5", "month": "in_sample", "changed": int(not ok), "values": k, "pass": bool(ok),
                 "note": f"perfect {r['recall_a']}, reversed {r['recall_b']}, interval [{r['lo']}, {r['hi']}]"})

    out = pd.DataFrame(rows)
    fc.OUT.mkdir(exist_ok=True)
    out.to_csv(fc.OUT / "selfcheck.csv", index=False)
    print(out.to_string(index=False))
    print("ALL PASS" if out["pass"].all() else "SOME FAIL")


if __name__ == "__main__":
    main()
