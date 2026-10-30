"""Checks of PLAN.md section 9: information cut-offs, reproducibility, budgets, ranges, boundaries, missing data.

Run:  .venv/Scripts/python.exe analysis/2026-10-24/peak_day_probability/test_peakprob.py
"""

import sys
import traceback
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import peakprob as pp  # noqa: E402
from vpp import site_bill as sb  # noqa: E402

CFG = pp.Config(n_paths=400)
PROBS = ["M1p", "M2", "S1", "M3", "M1p_da", "M2_da", "S1_da", "M3_da", "S0"]


def perturb_after(d: pd.DataFrame, cut: pd.Timestamp, rng) -> pd.DataFrame:
    """Change everything not yet public at 09:00 on `cut`: actuals from cut on, same-day forecasts after cut,
    day-before forecasts after cut + 1, weather after cut."""
    e = d.copy()
    noise = lambda m: rng.normal(0, 300, m.sum())  # noqa: E731
    for col, first in (("y", cut), ("er", cut), ("f0", cut + pd.Timedelta(days=1)), ("f1", cut + pd.Timedelta(days=2)),
                       ("wx_tmax0", cut + pd.Timedelta(days=1)), ("wx_t18", cut + pd.Timedelta(days=1))):
        m = (e.index >= first) & e[col].notna().to_numpy()
        e.loc[m, col] = e.loc[m, col] + (noise(m) if col not in ("wx_tmax0", "wx_t18") else rng.normal(0, 5, m.sum()))
    return e


def test_trading_day_and_positions():
    ts = pd.date_range("2024-01-01 08:00", "2024-01-03 07:30", freq="30min")
    s = pd.Series(1.0, index=ts)
    s[pd.Timestamp("2024-01-02 07:30")] = 9.0          # last interval of trading day 2024-01-01
    s[pd.Timestamp("2024-01-02 08:00")] = 5.0          # first interval of trading day 2024-01-02
    m = sb.daily_max(s)
    assert m[pd.Timestamp("2024-01-01")] == 9.0 and m[pd.Timestamp("2024-01-02")] == 5.0
    pos = pp.season_position(pd.DatetimeIndex(["2023-11-30", "2023-12-01", "2024-03-31", "2025-03-31", "2024-07-01"]))
    assert list(pos) == [-1, 0, 121, 120, -153], pos
    d = pp.daily()
    assert [len(pp.season_index(d, y)) for y in pp.SEASONS] == [122, 121, 121]
    assert (d.loc["2026-07-01":"2026-10-22", "season"] == -1).all()          # the formal test has no Hot Season day


def test_labels():
    lab = pp.labels()
    d = pp.daily()
    for y in pp.SEASONS:
        for k in ("primary", "proxy_od", "proxy_er"):
            assert len(lab[y][k]) == 4 and (sb.hot_season(lab[y][k]) == y).all()
        assert len(lab[y]["intervals"]) == 12
        assert set((lab[y]["intervals"] - pp.H).normalize()) == set(lab[y]["primary"])
    assert list(lab[2025]["official"]) == list(lab[2025]["proxy_od"])
    assert lab[2025]["primary_kind"] == "official" and lab[2024]["primary_kind"] == "proxy_od"
    assert pp.kth_known(np.array([1.0, 2.0, 3.0]), 3) == -np.inf
    assert pp.kth_known(np.array([5.0, 1.0, 4.0, 3.0, 2.0, 9.0]), 5) == 2.0     # 6th value not yet known
    _ = d


def test_future_invariance():
    d = pp.daily()
    cut = pd.Timestamp("2026-01-15")
    base = pp.run_season(2025, (2023, 2024), CFG, d=d)
    e = perturb_after(d, cut, np.random.default_rng(1))
    alt = pp.run_season(2025, (2023, 2024), CFG, d=e)
    same_day = ["M1p", "M2", "S1", "M3", "M1_same", "M1_union", "M2_flag", "mu0", "K4_od", "K4_er", "T_s1", "ratio"]
    day_ahead = ["M1p_da", "M2_da", "S1_da", "M3_da", "M1_da", "mu1"]
    b0, a0 = base.loc[:cut], alt.loc[:cut]
    b1, a1 = base.loc[:cut + pd.Timedelta(days=1)], alt.loc[:cut + pd.Timedelta(days=1)]
    for c in same_day:
        assert np.allclose(b0[c].to_numpy(), a0[c].to_numpy(), equal_nan=True, atol=0, rtol=0), c
    for c in day_ahead:
        assert np.allclose(b1[c].to_numpy(), a1[c].to_numpy(), equal_nan=True, atol=0, rtol=0), c
    # later days do change (the perturbation reached the model)
    assert not np.allclose(base.loc[cut + pd.Timedelta(days=2):, "mu0"], alt.loc[cut + pd.Timedelta(days=2):, "mu0"])
    # positive control: changing the actual of cut - 1 (public at 09:00 on cut) changes the decision on cut
    f = d.copy()
    f.loc[cut - pd.Timedelta(days=1), "y"] += 400.0
    pos = pp.run_season(2025, (2023, 2024), CFG, d=f)
    assert not np.allclose(base.loc[cut:cut, ["mu0", "M3"]].to_numpy(), pos.loc[cut:cut, ["mu0", "M3"]].to_numpy())


def test_training_truncation():
    d = pp.daily()
    for ahead in (0, 1):
        pr = pp.predictions(d, CFG, ahead)
        decision = pr.index - pd.Timedelta(days=ahead)
        te = pr["train_end"]
        ok = te.isna() | (te <= decision - pd.Timedelta(days=CFG.lag))
        assert ok.all(), pr[~ok].head()
    out = pp.run_season(2024, (2023,), CFG, d=d)
    assert (out["mu0_train_end_ok"] == 1).all() and (out["mu1_train_end_ok"] == 1).all()


def test_seed_and_ranges():
    d = pp.daily()
    a = pp.run_season(2024, (2023,), CFG, d=d)
    b = pp.run_season(2024, (2023,), CFG, d=d)
    assert np.array_equal(a["M3"].to_numpy(), b["M3"].to_numpy()) and np.array_equal(a["M3_da"].to_numpy(), b["M3_da"].to_numpy())
    c = pp.run_season(2024, (2023,), replace(CFG, seed=7), d=d)
    assert not np.array_equal(a["M3"].to_numpy(), c["M3"].to_numpy())          # the seed is used
    for col in PROBS:
        v = a[col].to_numpy()
        assert not np.isnan(v).any() and (v >= 0).all() and (v <= 1).all(), col
    assert (a["top_min"] == 4).all() and (a["top_max"] == 4).all()             # every path has exactly 4 peak days
    assert (a["M3"] <= a["M3_beat_sim"] + 1e-12).all()                          # final top 4 implies beating the known 4th


def test_missing_forecast():
    d = pp.daily()
    day = pd.Timestamp("2026-01-21")                                            # an official peak day
    e = d.copy()
    e.loc[day, ["f0", "f1"]] = np.nan
    out = pp.run_season(2025, (2023, 2024), CFG, d=e)
    r = out.loc[day]
    assert r["M2"] == 1.0 and r["S1"] == 1.0 and r["M2_flag"] == 1.0 and r["M1p"] == 1.0
    assert r["M3"] > 0                                                          # climatological, not zero
    assert r["M1_same"] == 0.0     # the existing rule leaves a day without a forecast unflagged (recorded finding)
    f = d.copy()
    f.loc[day, "f0"] = np.nan                                                   # only the same-day run missing
    out2 = pp.run_season(2025, (2023, 2024), CFG, d=f)
    assert not np.isnan(out2.loc[day, "mu0"])                                   # falls back to the day-before run


def test_policies():
    rng = np.random.default_rng(3)
    train = [pd.DataFrame({"p": rng.beta(0.3, 6, 121), "days_left": np.arange(121, 0, -1)}) for _ in range(2)]
    for B in pp.BUDGETS:
        th = pp.dp_thresholds(train, "p", B, pp.COST)
        diff = np.diff(th[1:, 1:], axis=0)                                      # more calls left: lower or equal threshold
        assert (diff <= 1e-12).all()
        p = rng.beta(0.3, 3, 121)
        assert pp.dp_calls(p, np.arange(121, 0, -1), th, B).sum() <= B
        assert pp.fcfs_calls((p > 0.05).astype(float), B).sum() <= B
        assert pp.topn_calls(p, B).sum() == B
    th0 = pp.dp_thresholds(train, "p", 5, 0.0)
    assert np.isclose(th0[5, 1], 0.0)                                           # last day, no cost: any p is worth it


def main() -> None:
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"PASS {t.__name__}", flush=True)
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
