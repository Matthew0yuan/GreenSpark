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

Run:  python -m vpp.v2.value [--stat mean] [--energy 36]     builds the per-day arrays (cached)
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


def path(stat: str = "median", energy_mwh: float = 40.0) -> Path:
    return PROCESSED / f"v2_value_{stat}{'' if energy_mwh == 40.0 else f'_{energy_mwh:g}mwh'}.npz"


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
    month, level, ps, energy_mwh = task
    asset = bt.Q2B_ASSET if energy_mwh == bt.Q2B_ASSET.battery.energy_mwh else \
        replace(bt.Q2B_ASSET, battery=replace(bt.Q2B_ASSET.battery, energy_mwh=energy_mwh))
    V, weeks = sv.solve({**ps, "means": level + ps["means"]}, sv.load_profiles(month), asset=asset, short_penalty=0.0)
    curve, marg = _marginals(V, ps)
    return month, level, curve.astype(np.float32), marg.astype(np.float32), weeks


def build(stat: str = "median", energy_mwh: float = 40.0, rebuild: bool = False) -> Path:
    """Per-day arrays for every backtest day (cached at path(stat, energy_mwh))."""
    out = path(stat, energy_mwh)
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
        tasks += [(month, float(g), ps, energy_mwh) for g in grid]
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
    np.savez_compressed(out, day=np.array(rows["day"]), level=np.array(rows["level"]),
                        curve=np.array(rows["curve"], np.float32), marg=np.array(rows["marg"], np.float32),
                        edges=np.array(rows["edges"], np.float32),
                        weeks=np.array([w for _, _, w in solved.values()]), n_tables=len(solved))
    return out


def load(p) -> dict:
    """The per-day arrays, keyed by trading day."""
    z = np.load(p)
    days = pd.to_datetime(z["day"].astype(str))
    return {"index": {d: i for i, d in enumerate(days)}, "level": z["level"], "curve": z["curve"], "marg": z["marg"],
            "edges": z["edges"]}


def terminal(inputs: dict, day: pd.Timestamp, q_mwh: float, shift: bool = True) -> np.ndarray:
    """The plan's end-of-day value: the day's curve, shifted past the q MWh ① and ② already need at 08:00."""
    c = inputs["curve"][inputs["index"][pd.Timestamp(day).normalize()]].astype(float)
    q = int(np.clip(round(q_mwh), 0, len(c))) if shift else 0
    return np.r_[np.full(q, c[0]), c[:len(c) - q]]


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    stat = argv[argv.index("--stat") + 1] if "--stat" in argv else "median"
    energy = float(argv[argv.index("--energy") + 1]) if "--energy" in argv else 40.0
    p = build(stat, energy, "--rebuild" in argv)
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
