"""③ what stored energy is worth, following the price level (reports/v2_plan.md, S3).

v1's value table (vpp.storage_value) fixed its prices at the level of the 365 days before the month, so when prices
rose after 2026-06-30 it fell behind. Here the table is of prices relative to a level known each morning:
  level   L_d: the median (or mean) of every half-hour's RTP over the 28 calendar days before trading day d,
          final at 08:00 on d (closed on the left)
  states  each half-hour's price less its day's level, binned as v1 (same quantiles, transitions, window of 365
          days before the month and never after TRAIN_END)
  solve   on a $5 grid of levels L: prices L + bin means; the CMD still limits charging, but the shortfall penalty
          is off (② holds what tomorrow's factory needs, vpp.v2.reserves); FCESS, IRCR and the cycle limit left out
  day     the curve and the offer values of the grid levels either side of L_d, interpolated (still non-increasing)
Per trading day it keeps L_d, the end-of-day curve ($ per MWh of each 1 MWh segment at the next 08:00), the offer
values (slot x price state x segment) and the relative bin edges, as float32 in an npz the workers load.

hurdle (round 4, reports/v2_plan.md section 11): the table charges every MWh sold its wear plus this hurdle ($/MWh),
as the plans do (bt.HURDLE, the default, is the table rounds 1-3 used). B1 builds tables at 0, 25 and 50: the trades
the table prices are made at prices already seen, so the forecast-error hurdle may be counted twice there.

Run:  python -m vpp.v2.value [--stat mean] [--energy 36] [--hurdle 0]     builds the per-day arrays (cached)
      python -m vpp.v2.value --b1                                         round 4, B1: builds and checks the tables
"""

import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from vpp import aemo_correction as ac
from vpp import backtest as bt
from vpp import dispatch as dp
from vpp import storage_value as sv
from vpp.data import PROCESSED

LEVEL_DAYS = 28   # days of past prices in the level [fixed]
GRID_STEP = 5.0   # $/MWh between solved levels [fixed]
FIRST, LAST = pd.Timestamp("2025-04-01"), pd.Timestamp("2026-10-15")  # the backtest's days


def path(stat: str = "median", energy_mwh: float = 40.0, hurdle: float = bt.HURDLE) -> Path:
    return PROCESSED / (f"v2_value_{stat}{'' if energy_mwh == 40.0 else f'_{energy_mwh:g}mwh'}"
                        f"{'' if hurdle == bt.HURDLE else f'_h{hurdle:g}'}.npz")


def prices() -> pd.DataFrame:
    """(trading day x slot) 30-min RTP."""
    return ac._by_day_slot(pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["energy"])["energy"])


def levels(table: pd.DataFrame, stat: str = "median", days: int = LEVEL_DAYS) -> pd.Series:
    """L_d for every trading day d: stat of all half-hours of the trading days in [d - days, d)."""
    idx = table.index
    x = table.to_numpy()
    out = np.full(len(idx), np.nan)
    for i, d in enumerate(idx):
        lo = idx.searchsorted(d - pd.Timedelta(days=days))
        block = x[lo:i].ravel()
        block = block[np.isfinite(block)]
        if len(block):
            out[i] = np.median(block) if stat == "median" else block.mean()
    return pd.Series(out, index=idx)


def _marginals(V: np.ndarray, ps: dict) -> tuple[np.ndarray, np.ndarray]:
    """The end-of-day curve (dow x segment) and the offer values (dow x slot x state x segment) of a solved table,
    as v1 derives them (vpp.storage_value.build)."""
    ev0 = V[:, dp.N] @ ps["pi0"]
    curve = np.clip(np.array([sv.decreasing(r) for r in np.diff(ev0, axis=1) / sv.GRID]), 0.0, None)
    ev = np.einsum("dtpc,tbc->dtbp", V[:, 1:], ps["trans"])
    marg = np.clip(np.apply_along_axis(sv.decreasing, 3, np.diff(ev, axis=3) / sv.GRID), 0.0, None)
    return curve, marg


def _solve(task: tuple) -> tuple:
    month, level, ps, energy_mwh, hurdle = task
    asset = bt.Q2B_ASSET if energy_mwh == bt.Q2B_ASSET.battery.energy_mwh else \
        replace(bt.Q2B_ASSET, battery=replace(bt.Q2B_ASSET.battery, energy_mwh=energy_mwh))
    prefs = bt.PREFS if hurdle == bt.HURDLE else replace(bt.PREFS, hurdle_per_mwh=hurdle)
    V, weeks = sv.solve({**ps, "means": level + ps["means"]}, sv.load_profiles(month), asset=asset, prefs=prefs,
                        short_penalty=0.0)
    curve, marg = _marginals(V, ps)
    return month, level, curve.astype(np.float32), marg.astype(np.float32), weeks


def build(stat: str = "median", energy_mwh: float = 40.0, rebuild: bool = False, hurdle: float = bt.HURDLE,
          out: Path | None = None) -> Path:
    """Per-day arrays for every backtest day (cached at path(stat, energy_mwh, hurdle), or written to out)."""
    if out is None:
        out = path(stat, energy_mwh, hurdle)
        if out.exists() and not rebuild:
            return out
    table = prices()
    lv = levels(table, stat)
    days = lv.index[(lv.index >= FIRST) & (lv.index <= LAST)]
    tasks, states = [], {}
    for month in sv.MONTHS:
        in_month = days[(days >= month.start_time) & (days <= month.end_time)]
        if not len(in_month):
            continue
        ps = sv.price_states(table, month, level=lv)
        states[month] = ps
        lo, hi = lv[in_month].min(), lv[in_month].max()
        grid = np.arange(np.floor(lo / GRID_STEP) * GRID_STEP, np.ceil(hi / GRID_STEP) * GRID_STEP + 1e-9, GRID_STEP)
        if len(grid) == 1:
            grid = np.r_[grid, grid + GRID_STEP]
        tasks += [(month, float(g), ps, energy_mwh, hurdle) for g in grid]
    with ProcessPoolExecutor() as pool:
        solved = {(m, g): (c, mg, w) for m, g, c, mg, w in pool.map(_solve, tasks)}
    rows = {"day": [], "level": [], "curve": [], "marg": [], "edges": []}
    for d in days:
        month = d.to_period("M")
        grid = sorted(g for m, g in solved if m == month)
        L = float(lv[d])
        k = min(max(np.searchsorted(grid, L, side="right") - 1, 0), len(grid) - 2)
        g0, g1 = grid[k], grid[k + 1]
        w = min(max((L - g0) / (g1 - g0), 0.0), 1.0)
        (c0, m0, _), (c1, m1, _) = solved[month, g0], solved[month, g1]
        dow = d.dayofweek
        rows["day"].append(str(d.date()))
        rows["level"].append(L)
        rows["curve"].append((1 - w) * c0[dow] + w * c1[dow])
        rows["marg"].append((1 - w) * m0[dow] + w * m1[dow])
        rows["edges"].append(states[month]["edges"])
    keys = sorted(solved)
    np.savez_compressed(out, day=np.array(rows["day"]), level=np.array(rows["level"]),
                        curve=np.array(rows["curve"], np.float32), marg=np.array(rows["marg"], np.float32),
                        edges=np.array(rows["edges"], np.float32),
                        weeks=np.array([w for _, _, w in solved.values()]), n_tables=len(solved),
                        grid_month=np.array([str(m) for m, _ in keys]), grid_level=np.array([g for _, g in keys]),
                        grid_curve=np.array([solved[k][0] for k in keys], np.float32))
    return out


def load(p) -> dict:
    """The per-day arrays, keyed by trading day, and each month's end-of-day curves on its level grid."""
    z = np.load(p)
    days = pd.to_datetime(z["day"].astype(str))
    grid: dict = {}
    for m, g, c in zip(z["grid_month"].astype(str), z["grid_level"], z["grid_curve"]):
        grid.setdefault(m, []).append((float(g), c))
    return {"index": {d: i for i, d in enumerate(days)}, "level": z["level"], "curve": z["curve"], "marg": z["marg"],
            "edges": z["edges"], "grid": {m: sorted(v, key=lambda x: x[0]) for m, v in grid.items()}}


def curve_at(inputs: dict, month: str, dow: int, level: float) -> np.ndarray:
    """The end-of-day curve of a trading day starting on weekday dow, from month's table at a price level
    (interpolated on the month's grid, the nearest end beyond it)."""
    g = inputs["grid"][month]
    levels = np.array([x for x, _ in g])
    k = int(np.clip(np.searchsorted(levels, level, side="right") - 1, 0, len(levels) - 2))
    w = float(np.clip((level - levels[k]) / (levels[k + 1] - levels[k]), 0.0, 1.0))
    return (1 - w) * g[k][1][dow].astype(float) + w * g[k + 1][1][dow].astype(float)


def shifted(c: np.ndarray, q_mwh: float, shift: bool = True) -> np.ndarray:
    """A curve shifted past the q MWh already needed (the first q segments take the first segment's value)."""
    q = int(np.clip(round(q_mwh), 0, len(c))) if shift else 0
    return np.r_[np.full(q, c[0]), c[:len(c) - q]]


def terminal(inputs: dict, day: pd.Timestamp, q_mwh: float, shift: bool = True) -> np.ndarray:
    """The plan's end-of-day value: the day's curve, shifted past the q MWh ① and ② already need at 08:00."""
    return shifted(inputs["curve"][inputs["index"][pd.Timestamp(day).normalize()]].astype(float), q_mwh, shift)


B1_HURDLES = (0.0, 25.0, 50.0)  # round 4, B1 [fixed beforehand, reports/v2_plan.md section 11.3]


def b1() -> bool:
    """Round 4, B1 (no decision changes): the tables at hurdles 0, 25 and 50, checked (the default hurdle rebuilt
    bit for bit; every curve and offer value non-increasing in stored energy and not below 0; training windows before
    the month and not after TRAIN_END) and reported: the mean 08:00 curve at 5/10/20/30 MWh before and after the
    cutoff, with the buy limit 0.922 x value / 1.2 - h and the sell threshold max(150, 1.2 x value / 0.922 + 30 + h)."""
    from vpp import evaluate as ev
    ok = True
    check = PROCESSED / "v2_value_median_b1check.npz"
    build("median", 40.0, hurdle=bt.HURDLE, out=check)
    a, b = np.load(path()), np.load(check)
    diff = [k for k in a.files if not np.array_equal(a[k], b[k])]
    worst = max((float(np.max(np.abs(a[k].astype(float) - b[k].astype(float)))) for k in diff
                 if a[k].dtype.kind == "f" and a[k].shape == b[k].shape), default=0.0)
    print(f"{'PASS' if not diff else 'FAIL'}  hurdle {bt.HURDLE:g} rebuilt bit for bit: "
          f"{'all ' + str(len(a.files)) + ' arrays equal' if not diff else f'differ {diff}, largest {worst:.3g}'}")
    ok &= not diff
    a.close(), b.close()
    check.unlink()
    table = prices()
    for month in sv.MONTHS:
        end = min(month.start_time, ev.CUTOFF)
        used = table.index[(table.index >= end - sv.WINDOW) & (table.index < end) & (table.index <= ev.TRAIN_END)]
        ok &= bool(used.max() < month.start_time and used.max() <= ev.TRAIN_END)
    print(f"{'PASS' if ok else 'FAIL'}  training windows before each month and not after {ev.TRAIN_END.date()}")
    rows = []
    for h in (*B1_HURDLES, bt.HURDLE):
        z = load(build("median", 40.0, hurdle=h))
        flat = (np.diff(z["curve"], axis=1) <= 1e-6).all() and (np.diff(z["marg"], axis=3) <= 1e-6).all()
        pos = (z["curve"] >= 0).all() and (z["marg"] >= 0).all()
        print(f"{'PASS' if flat and pos else 'FAIL'}  hurdle {h:g}: curves and offer values non-increasing and >= 0")
        ok &= bool(flat and pos)
        days = pd.DatetimeIndex(list(z["index"]))
        for name, m in (("before cutoff", days < ev.CUTOFF), ("after cutoff", days >= ev.CUTOFF)):
            c = z["curve"][m].astype(float).mean(axis=0)
            for s in (5, 10, 20, 30):
                rows.append({"hurdle": h, "period": name, "stored_mwh": s, "value": c[s],
                             "buy_limit": 0.922 * c[s] / 1.2 - h, "sell_threshold": max(150.0, 1.2 * c[s] / 0.922 + 30 + h)})
    rep = pd.DataFrame(rows)
    rep.to_csv(bt.REPORTS / "v2_b1_values.csv", index=False)
    pd.set_option("display.width", 200)
    print(rep.pivot_table(index=["period", "stored_mwh"], columns="hurdle",
                          values=["value", "buy_limit", "sell_threshold"]).round(0).to_string())
    return ok


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if "--b1" in argv:
        sys.exit(0 if b1() else 1)
    stat = argv[argv.index("--stat") + 1] if "--stat" in argv else "median"
    energy = float(argv[argv.index("--energy") + 1]) if "--energy" in argv else 40.0
    hurdle = float(argv[argv.index("--hurdle") + 1]) if "--hurdle" in argv else bt.HURDLE
    p = build(stat, energy, "--rebuild" in argv, hurdle)
    z = load(p)
    lv = pd.Series(z["level"], index=list(z["index"]))
    print(f"{p.name}: {len(lv)} days; level ${lv.min():.0f}-{lv.max():.0f}/MWh (median {lv.median():.0f})")
    for d in ("2025-07-02", "2026-01-07", "2026-08-05"):
        i = z["index"][pd.Timestamp(d)]
        c = z["curve"][i]
        print(f"  {d} (L ${z['level'][i]:.0f}): 08:00 value $/MWh at 0/5/10/20/30/39 MWh: "
              + " / ".join(f"{c[s]:.0f}" for s in (0, 5, 10, 20, 30, len(c) - 1)))


if __name__ == "__main__":
    main()
