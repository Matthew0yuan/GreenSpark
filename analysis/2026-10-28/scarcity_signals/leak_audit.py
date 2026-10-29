"""Leak audit of the scarcity features (PLAN section 6).

A1  every STEM record used (09:00 and 13:30 versions) was public by the issue time (T-1 13:30 rule).
A2  scramble the future: for 30 random issue days D, replace actual 5-min / 30-min FCESS prices of trading
    days >= D and STEM auctions of trading days >= D+1 (>= D+2 for the 13:30 version) by random numbers;
    the features of that issue must not change at all.
P1  planted leak: fx from trading day D itself and stem from the target's own auction at 09:00
    (features(..., leak=True)); A2's scramble must change them on every audited day.

Output: out/leak_audit.csv (one row per day and check), printed summary with PASS / FAIL
Run:    .venv/bin/python analysis/2026-10-28/scarcity_signals/leak_audit.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scarcity_features as sf  # noqa: E402

OUT = sf.OUT
N_DAYS = 30
CHECK_0900 = sf.FX + sf.PDX + sf.STEM
CHECK_1330 = [f"{c}_1330" for c in sf.STEM]


def scramble(d: sf.Data, day: pd.Timestamp, stem_from: pd.Timestamp, rng: np.random.Generator) -> sf.Data:
    cut = day + sf.pc.DAY_START
    p5, p30, st = d.p5.copy(), d.p30.copy(), d.stem.copy()
    for p in (p5, p30):
        m = p.index >= cut
        p.loc[m] = rng.uniform(0, 3000, (m.sum(), p.shape[1]))
    m = st["tdate"] >= stem_from
    st.loc[m, "price"] = rng.uniform(-1000, 1000, m.sum())
    st.loc[m, "offer"] = rng.uniform(1000, 4000, m.sum())
    return sf.Data(p5, p30, st)


def changed(a: pd.DataFrame, b: pd.DataFrame, cols: list[str]) -> pd.Series:
    x, y = a[cols].to_numpy(float), b[cols].to_numpy(float)
    diff = ~((x == y) | (np.isnan(x) & np.isnan(y)))
    return pd.Series(diff.sum(axis=0), index=cols)


def main() -> None:
    table = pd.read_parquet(OUT / "spike_table_ext.parquet")
    d = sf.load()
    f = pd.read_parquet(OUT / "scarcity_features.parquet")
    rows = [{"check": "A1", "day": "", "group": "stem 09:00", "n": len(f), "changed": int((~f["stem_public_ok"]).sum())},
            {"check": "A1", "day": "", "group": "stem 13:30", "n": len(f), "changed": int((~f["stem_public_ok_1330"]).sum())}]
    days = pd.DatetimeIndex(sorted(table["issue_date"].unique()))
    days = days[days >= pd.Timestamp("2023-12-01")]
    rng = np.random.default_rng(0)
    pick = pd.DatetimeIndex(sorted(rng.choice(days, N_DAYS, replace=False)))
    for day in pick:
        r = table[table["issue_date"] == day]
        one = pd.Timedelta(days=1)
        base = sf.features(r, d)
        s9 = sf.features(r, scramble(d, day, day + one, rng))
        s13 = sf.features(r, scramble(d, day, day + 2 * one, rng))
        c9, c13 = changed(base, s9, CHECK_0900), changed(base, s13, CHECK_1330)
        for g, cols in (("fx", sf.FX), ("pdx", sf.PDX), ("stem", sf.STEM)):
            rows.append({"check": "A2", "day": day.date(), "group": g, "n": len(r), "changed": int(c9[cols].sum())})
        rows.append({"check": "A2", "day": day.date(), "group": "stem_1330", "n": len(r), "changed": int(c13.sum())})
        leak_base = sf.features(r, d, leak=True)
        leak_s = sf.features(r, scramble(d, day, day + one, rng), leak=True)
        cl = changed(leak_base, leak_s, CHECK_0900)
        rows.append({"check": "P1", "day": day.date(), "group": "fx", "n": len(r), "changed": int(cl[sf.FX].sum())})
        d1 = r["days_ahead"] == 1
        cl1 = changed(leak_base[d1.to_numpy()], leak_s[d1.to_numpy()], sf.STEM)
        rows.append({"check": "P1", "day": day.date(), "group": "stem (D+1 rows)", "n": int(d1.sum()), "changed": int(cl1.sum())})
        print(f"{day.date()}: A2 changed {int(c9.sum())} + {int(c13.sum())}, P1 caught fx {int(cl[sf.FX].sum())}, stem {int(cl1.sum())}", flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "leak_audit.csv", index=False)
    a = out[out["check"].isin(["A1", "A2"])]
    p = out[out["check"] == "P1"]
    caught = p.groupby("group")["changed"].apply(lambda s: int((s > 0).sum()))
    ok = (a["changed"] == 0).all() and (caught == N_DAYS).all()
    print(f"A1/A2: {int((a['changed'] == 0).sum())} of {len(a)} checks unchanged; "
          f"P1 days caught: {caught.to_dict()} of {N_DAYS}")
    print("RESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
