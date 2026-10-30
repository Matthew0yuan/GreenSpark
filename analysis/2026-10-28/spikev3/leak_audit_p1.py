"""Leak audit of spikev3's new feature groups (PLAN.md section 3.5). For 30 random issue days D (seed 0):
A  scramble what was not public at 09:00 on D; the group's features for issue D must not change at all:
     G1 STEM auctions of trading days >= D+1;  G2 pre-dispatch headroom of issues >= D+1;
     G4 outage snapshots of days > D;          G5 weather vintages public after 09:00 on D
P  a planted leak per group must change under the same scramble on every audited day:
     G1 the target's own auction (D+1 for the D+1 task);  G2 a centred 28-issue window (13 issues ahead);
     G4 the next day's snapshot;                           G5 weather taken as public at initialisation (no delay)
G0 (price_extra) comes from the spike table, whose sources the spike study audited (vpp.spike.report audit).

Output: out/p1_leak_audit.csv; prints PASS / FAIL
Run:    .venv/bin/python analysis/2026-10-28/spikev3/leak_audit_p1.py
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

from vpp.spike import outages as og  # noqa: E402
from vpp.spike import protocol as pc  # noqa: E402
from vpp.spikev3 import features as fx  # noqa: E402

OUT = HERE / "out"
SCARCITY = ROOT / "analysis" / "2026-10-28" / "scarcity_signals" / "out"
N_DAYS = 30


def changed(a: pd.DataFrame, b: pd.DataFrame, cols) -> int:
    x, y = a[cols].to_numpy(float), b[cols].to_numpy(float)
    return int((~((x == y) | (np.isnan(x) & np.isnan(y)))).sum())


def g2_centred(r: pd.DataFrame) -> pd.DataFrame:
    """The planted G2 leak: the normal level from a window centred on the issue (13 issues after it)."""
    out = pd.DataFrame(index=r.index, columns=["hr_norm"], dtype=float)
    for d in sorted(r["days_ahead"].unique()):
        part = r[r["days_ahead"] == d]
        g = part.pivot_table(index="issue_date", columns="slot", values="pd_headroom", aggfunc="first")
        g = g.reindex(pd.date_range(g.index.min(), g.index.max()))
        med = g.rolling(fx.NORM_DAYS, min_periods=fx.NORM_MIN, center=True).median()
        key = pd.MultiIndex.from_arrays([part["issue_date"], part["slot"]])
        out.loc[part.index, "hr_norm"] = part["pd_headroom"].to_numpy() / \
            np.maximum(med.stack(future_stack=True).reindex(key).to_numpy(), fx.HR_FLOOR)
    return out


def main() -> None:
    table = pd.read_parquet(SCARCITY / "spike_table_ext.parquet")
    stem = fx.load_stem()
    wx = pd.read_parquet(SCARCITY / "pit" / "weather.parquet")
    supply = fx.Supply()
    days = pd.DatetimeIndex(sorted(table["issue_date"].unique()))
    days = days[(days >= pd.Timestamp("2024-04-01")) & (days <= days.max() - pd.Timedelta(days=2))]
    rng = np.random.default_rng(0)
    pick = pd.DatetimeIndex(sorted(rng.choice(days, N_DAYS, replace=False)))
    rows = []
    for day in pick:
        r = table[table["issue_date"] == day]
        issue = day + pc.ISSUE

        # G1
        st = stem.copy()
        m = st["tdate"] >= day + pd.Timedelta(days=1)
        st.loc[m, ["price", "offer", "bid"]] = rng.uniform(-1000, 1000, (m.sum(), 3))
        a, b = fx.g1_stem(r, stem), fx.g1_stem(r, st)
        pa, pb = fx.g1_stem(r, stem, auction=r["target_date"]), fx.g1_stem(r, st, auction=r["target_date"])
        rows += [{"day": day.date(), "group": "G1", "check": "A", "changed": changed(a, b, fx.G1)},
                 {"day": day.date(), "group": "G1", "check": "P", "changed": changed(pa, pb, fx.G1)}]

        # G2 (needs the issues around D)
        near = table[(table["issue_date"] >= day - pd.Timedelta(days=40)) & (table["issue_date"] <= day + pd.Timedelta(days=40))]
        sc = near.copy()
        m = sc["issue_date"] >= day + pd.Timedelta(days=1)
        sc.loc[m, "pd_headroom"] = rng.uniform(-2000, 2000, m.sum())
        sc.loc[m, "pd_day_headroom_min"] = rng.uniform(-2000, 2000, m.sum())
        idx = r.index
        a, b = fx.g2_norm(near).loc[idx], fx.g2_norm(sc).loc[idx]
        pa, pb = g2_centred(near).loc[idx], g2_centred(sc).loc[idx]
        rows += [{"day": day.date(), "group": "G2", "check": "A", "changed": changed(a, b, fx.G2)},
                 {"day": day.date(), "group": "G2", "check": "P", "changed": changed(pa, pb, ["hr_norm"])}]

        # G4
        def scrambled(d, D=day):
            s = og.load_snapshot(d)
            if s is None or d <= D:
                return s
            s = s.copy()
            s["rac"] = rng.uniform(0, 500, len(s))
            return s
        a, b = fx.g4_rsi(r, supply), fx.g4_rsi(r, supply, snapshot=scrambled)
        nxt = lambda d: og.load_snapshot(d + pd.Timedelta(days=1))  # noqa: E731
        nxt_s = lambda d: scrambled(d + pd.Timedelta(days=1))  # noqa: E731
        pa, pb = fx.g4_rsi(r, supply, snapshot=nxt), fx.g4_rsi(r, supply, snapshot=nxt_s)
        rows += [{"day": day.date(), "group": "G4", "check": "A", "changed": changed(a, b, fx.G4)},
                 {"day": day.date(), "group": "G4", "check": "P", "changed": changed(pa, pb, fx.G4)}]

        # G5
        w = wx.copy()
        late = (w["evidence_at"] + pc.AVAILABILITY["weather"]) > issue
        w.loc[late, "value"] = rng.uniform(0, 30, late.sum())
        a, b = fx.g5_wind(r, wx), fx.g5_wind(r, w)
        no_lag = {**pc.AVAILABILITY, "weather": pd.Timedelta(0)}
        pa, pb = fx.g5_wind(r, wx, no_lag), fx.g5_wind(r, w, no_lag)
        rows += [{"day": day.date(), "group": "G5", "check": "A", "changed": changed(a, b, fx.G5)},
                 {"day": day.date(), "group": "G5", "check": "P", "changed": changed(pa, pb, fx.G5)}]
        print(day.date(), {k["group"] + k["check"]: k["changed"] for k in rows[-8:]}, flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "p1_leak_audit.csv", index=False)
    a = out[out["check"] == "A"]
    p = out[out["check"] == "P"].groupby("group")["changed"].apply(lambda s: int((s > 0).sum()))
    ok = (a["changed"] == 0).all() and (p == N_DAYS).all()
    print(f"A: {int((a['changed'] == 0).sum())} of {len(a)} unchanged; P days caught: {p.to_dict()} of {N_DAYS}")
    print("RESULT:", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
