"""PLAN.md checks 2-3, the choice on the kept in-sample days, and the one-time scoring (held-out days, the test).
Compares with the official files of the main checkout (read only). Run once, after run.py:
  .venv/bin/python analysis/2026-10-28/random_holdout/score.py   (from the worktree root)
Writes checks.csv, choice.csv, scores.csv here."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MAIN = ROOT.parents[2]  # the main checkout: .claude/worktrees/<this> is three levels below it
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp import evaluate as ev  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402

APRIL_END = pd.Timestamp("2025-05-01")  # the first month's models never saw a held-out day (PLAN.md check 2)
UNSEEN = pd.Timestamp("2026-10-16")
E5, V1_SA, V1_M1 = vb.E5.name, bt.VALUE, "v1"
TOL = 1e-9
checks: list[dict] = []


def record(name: str, value: float, ok: bool, note: str = "") -> None:
    checks.append({"check": name, "value": value, "pass": bool(ok), "note": note})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {value:.3g} {note}", flush=True)


def frame_diff(name: str, cols_key: list[str], date_col: str) -> None:
    """Check 2 (rows before APRIL_END identical) and check 3 (later rows differ) for one parquet in data/processed."""
    new = pd.read_parquet(ROOT / "data" / "processed" / name)
    off = pd.read_parquet(MAIN / "data" / "processed" / name)
    if new.index.name or not isinstance(new.index, pd.RangeIndex):
        new, off = new.reset_index(), off.reset_index()
    key = [c for c in cols_key if c in new.columns]
    num = [c for c in new.columns if c not in key and pd.api.types.is_float_dtype(new[c]) and c in off.columns]
    m = new.merge(off, on=key, how="outer", suffixes=("", "_off"), indicator=True)
    d = pd.to_datetime(m[date_col]) if date_col in m else pd.to_datetime(m[key[0]])
    gap = np.nanmax(np.abs(m[num].to_numpy(float) - m[[c + "_off" for c in num]].to_numpy(float)), axis=1,
                    initial=0.0)
    nan_mismatch = (m[num].isna().to_numpy() != m[[c + "_off" for c in num]].isna().to_numpy()).any(axis=1)
    early = (d < APRIL_END).to_numpy()
    record(f"check 2 {name}: largest difference before {APRIL_END.date()}",
           float(gap[early].max()) if early.any() else np.nan,
           early.any() and gap[early].max() <= TOL and not nan_mismatch[early].any() and (m["_merge"][early] == "both").all(),
           f"({int(early.sum())} rows, {len(num)} columns)")
    late = (d >= APRIL_END).to_numpy() & (m["_merge"] == "both").to_numpy()
    share = float(((gap > TOL) | nan_mismatch)[late].mean()) if late.any() else np.nan
    record(f"check 3 {name}: share of rows from {APRIL_END.date()} that changed", share, share > 0)


def table_diff(name: str) -> None:
    """Check 2/3 for a value-table npz: every per-day array, days before APRIL_END identical, later ones changed."""
    off_path = MAIN / "data" / "processed" / name
    if not off_path.exists():
        record(f"check 2 {name}", np.nan, True, "(no official file to compare: skipped, the ledgers cover it)")
        return
    new, off = np.load(ROOT / "data" / "processed" / name), np.load(off_path)
    dn, do = pd.DatetimeIndex(new["day"]), pd.DatetimeIndex(off["day"])
    common = dn.intersection(do)
    i, j = dn.get_indexer(common), do.get_indexer(common)
    early, worst, changed = common < APRIL_END, 0.0, np.zeros(len(common), bool)
    for k in new.files:
        a, b = new[k], off[k]
        if k == "day" or a.ndim == 0 or a.shape[0] != len(dn) or b.shape[0] != len(do) or not np.issubdtype(a.dtype, np.number):
            continue
        g = np.abs(a[i].astype(float) - b[j].astype(float)).reshape(len(common), -1)
        g = np.where(np.isnan(g), 0.0, g).max(axis=1)
        worst = max(worst, float(g[early].max()))
        changed |= g > TOL
    record(f"check 2 {name}: largest difference before {APRIL_END.date()}", worst, worst <= TOL,
           f"({int(early.sum())} days)")
    record(f"check 3 {name}: share of days from {APRIL_END.date()} that changed", float(changed[~early].mean()),
           changed[~early].any())


def ledger_diff(label: str, new: pd.DataFrame, off: pd.DataFrame, cols: list[str]) -> None:
    """Check 2 for the backtest: the days before APRIL_END identical (each column, both dispatchers)."""
    on = ["day", "strategy"]
    m = new[on + cols].merge(off[on + cols], on=on, suffixes=("", "_off"))
    m = m[pd.to_datetime(m["day"]) < APRIL_END]
    gap = max(float(np.abs(m[c] - m[c + "_off"]).max()) for c in cols)
    record(f"check 2 {label} ledger {', '.join(cols)}: largest difference before {APRIL_END.date()}", gap,
           len(m) > 0 and gap <= 1e-6, f"({len(m)} day-rows)")


def periods(days: pd.DatetimeIndex) -> pd.Series:
    held = ev.held_out(days)
    return pd.Series(np.select([(days < bt.CUTOFF) & ~held, (days < bt.CUTOFF) & held, days < UNSEEN],
                               ["kept in-sample", "held out", "test seen"], "test unseen"), index=days)


def main() -> None:
    frame_diff("forecast_dispatch_0900.parquet", ["ts", "issue_date", "days_ahead"], "target_date")
    frame_diff("spike_risk_0900.parquet", ["ts", "target_date", "days_ahead"], "target_date")
    for f in ("spike_risk_history_0900.parquet", "spike_risk_history_0900_d1.parquet"):
        frame_diff(f, ["ts", "target_date", "days_ahead", "issue_date"], "target_date")
    for f in sorted(p.name for p in (ROOT / "data" / "processed").glob("v2_value_median_h50_state*.npz")):
        table_diff(f)

    daily = pd.read_csv(HERE / "daily.csv", parse_dates=["day"])
    sa_off = pd.read_csv(MAIN / "analysis" / "2026-10-27" / "standalone_v2" / "daily.csv", parse_dates=["day"])
    ledger_diff("standalone", daily[daily["scenario"] == "sa"], sa_off, ["total", "soc_end"])
    led = pd.read_csv(HERE / "ledger_m1.csv", parse_dates=["day"])
    m1_off = pd.read_csv(MAIN / "reports" / "v2_test_daily.csv", parse_dates=["day"])
    ledger_diff("factory", led, m1_off[m1_off["strategy"].isin([E5, V1_M1])], ["ledger_total", "soc_end"])
    bills = pd.read_csv(HERE / "bills_m1.csv")
    bills["month"] = pd.PeriodIndex(bills["month"], freq="M")
    bills_off = pd.read_csv(MAIN / "reports" / "v2_test_bills.csv")
    bills_off["month"] = pd.PeriodIndex(bills_off["month"], freq="M")  # as q2b_bills gives it
    b = bills.merge(bills_off, on=["strategy", "month"], suffixes=("", "_off"))
    b = b[b["month"].astype(str) == "2025-04"]
    record("check 2 factory bill 2025-04 saving", float(np.abs(b["saving"] - b["saving_off"]).max()),
           len(b) == 2 and np.abs(b["saving"] - b["saving_off"]).max() <= 1e-6)
    pd.DataFrame(checks).to_csv(HERE / "checks.csv", index=False)
    if not all(c["pass"] for c in checks):
        raise SystemExit("a check failed: stop here (PLAN.md check 2), nothing scored")

    # the money per day: standalone total; factory daily ledger + the month's bill saving per day
    sa_new = daily[daily["scenario"] == "sa"].pivot_table(index="day", columns="strategy", values="total")
    sa_old = sa_off.pivot_table(index="day", columns="strategy", values="total")
    m1_new = pd.read_csv(HERE / "money_m1.csv", index_col=0, parse_dates=True)
    from vpp import stochastic as sto
    m1_old = sto.daily_money(m1_off[m1_off["strategy"].isin([E5, V1_M1])], bills_off[bills_off["strategy"].isin([E5, V1_M1])])
    modes = {"standalone": (sa_new, sa_old, V1_SA), "factory": (m1_new, m1_old, V1_M1)}

    choice, scores = [], []
    for mode, (new, old, base) in modes.items():
        new.index, old.index = pd.DatetimeIndex(new.index), pd.DatetimeIndex(old.index)
        per = periods(new.index)
        diff = new[E5] - new[base]
        kept = diff[per == "kept in-sample"]
        lo, hi = vb.month_ci(kept)
        adopt = "e5" if lo > 0 else "v1"
        choice.append({"mode": mode, "days": len(kept), "e5_minus_v1": kept.mean(), "lo95_month": lo, "hi95_month": hi,
                       "days_e5_better": int((kept > 0).sum()), "adopt": adopt})
        print(f"\n{mode}: kept in-sample {len(kept)} days, e5 - v1 {kept.mean():+.1f} A$/day, month-block 95% "
              f"[{lo:+.1f}, {hi:+.1f}] -> {adopt}", flush=True)
        od = old[E5] - old[base]
        for p in ("kept in-sample", "held out", "test seen", "test unseen"):
            sel = per == p
            g = diff[sel]
            ci = vb.month_ci(g) if g.index.to_period("M").nunique() > 1 else (np.nan, np.nan)
            row = {"mode": mode, "period": p, "days": len(g), "v1_per_day": new.loc[sel, base].mean(),
                   "e5_per_day": new.loc[sel, E5].mean(), "e5_minus_v1": g.mean(), "lo95_month": ci[0],
                   "hi95_month": ci[1], "days_e5_better": int((g > 0).sum()),
                   "official_e5_minus_v1": od.reindex(g.index).mean(),
                   "retrain_change_v1": (new.loc[sel, base] - old[base].reindex(g.index)).mean(),
                   "retrain_change_e5": (new.loc[sel, E5] - old[E5].reindex(g.index)).mean()}
            scores.append(row)
            if p == "held out":  # the Dec-Mar part of the held-out days
                hot = sel & np.isin(new.index.month, (12, 1, 2, 3))
                h = diff[hot]
                hci = vb.month_ci(h) if h.index.to_period("M").nunique() > 1 else (np.nan, np.nan)
                scores.append({**row, "period": "held out, Dec-Mar", "days": len(h), "v1_per_day": new.loc[hot, base].mean(),
                               "e5_per_day": new.loc[hot, E5].mean(), "e5_minus_v1": h.mean(), "lo95_month": hci[0],
                               "hi95_month": hci[1], "days_e5_better": int((h > 0).sum()),
                               "official_e5_minus_v1": od.reindex(h.index).mean(),
                               "retrain_change_v1": (new.loc[hot, base] - old[base].reindex(h.index)).mean(),
                               "retrain_change_e5": (new.loc[hot, E5] - old[E5].reindex(h.index)).mean()})
    pd.DataFrame(choice).to_csv(HERE / "choice.csv", index=False)
    s = pd.DataFrame(scores)
    s.to_csv(HERE / "scores.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 20):
        print("\n" + s.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
