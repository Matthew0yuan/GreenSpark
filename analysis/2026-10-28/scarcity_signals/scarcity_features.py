"""Scarcity features (PLAN section 4) for every row of out/spike_table_ext.parquet.

  fx    actual FCESS raise prices (reg_raise, cont_raise) of complete trading days up to D-1 (end 08:00 D)
  pdx   jumps in AEMO's 08:00 pre-dispatch FCESS raise prices (already in the table) against actual history
  stem  the newest STEM auction public at the issue: trading day T is public at T-1 13:30 (STEM Results
        Deadline 11:30 plus the 2-hour extension AEMO may take), so at 09:00 D that is the auction for D,
        used for both D+0 and D+1. stem_*_1330 is the what-if issued at 13:30: D+1 uses the auction for D+1.

`features(r, data, ...)` works on any subset of rows; the leak audit calls it with scrambled data and with
leak=True (fx from trading day D itself, stem from the target's own auction) to show it can catch leaks.

Output: out/scarcity_features.parquet (index = row of spike_table_ext), out/stem_records.parquet
Run:    .venv/bin/python analysis/2026-10-28/scarcity_signals/scarcity_features.py
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from vpp.data import PROCESSED, RAW  # noqa: E402
from vpp.spike import protocol as pc  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
SERVICES = {"rr": "reg_raise", "cr": "cont_raise"}
STEM_FIRST = pd.Timestamp("2023-10-01")          # WEM reform: first trading day of the new market
STEM_PUBLIC = pd.Timedelta(hours=13, minutes=30)  # after the start of the calendar day before the trading day
HALF = pd.Timedelta(minutes=30)
FLOOR = 1.0                                       # $/MW/h or $/MWh, keeps ratios finite

FX = [f"fx_{s}_{k}" for s in SERVICES for k in ("max1", "step1", "slot1", "slotmax7", "jumps7", "ratio1")] + \
     ["fx_jump_both7", "fx_jump_last_h"]
PDX = [f"pdx_{s}_{k}" for s in SERVICES for k in ("step", "daymax", "ratio")]
STEM = ["stem_price", "stem_daymax", "stem_ratio", "stem_step", "stem_offer", "stem_offer_ratio"]


@dataclass
class Data:
    p5: pd.DataFrame     # 5-min reg_raise, cont_raise (interval start, naive AWST)
    p30: pd.DataFrame    # 30-min means of the same
    stem: pd.DataFrame   # auction day x slot records: tdate, slot, price, offer


def load_stem() -> pd.DataFrame:
    parts = [pd.read_csv(f) for f in sorted((RAW / "stem-summary").glob("stem-summary-*.csv"))]
    s = pd.concat(parts, ignore_index=True)
    s = pd.DataFrame({
        "tdate": pd.to_datetime(s["Trading Date"]),
        "interval_start": pd.to_datetime(s["Trading Interval"]),
        "suspended": s["Trading Suspended"].eq("Y"),
        "price": s["Clearing Price ($/MWh)"].astype(float),
        "offer": s["Offer Quantity (MWh)"].astype(float),
        "bid": s["Bid Quantity (MWh)"].astype(float),
        "cleared": s["Clearing Quantity (MWh)"].astype(float),
    })
    s["slot"] = ((s["interval_start"] - s["tdate"] - pc.DAY_START) / HALF).astype(int)
    assert s["slot"].between(0, 47).all() and not s.duplicated(["tdate", "slot"]).any()
    s.loc[s["suspended"], ["price", "offer"]] = np.nan
    s["available_at"] = s["tdate"] - pd.Timedelta(days=1) + STEM_PUBLIC
    return s[s["tdate"] >= STEM_FIRST].sort_values(["tdate", "slot"]).reset_index(drop=True)


def load() -> Data:
    p5 = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=list(SERVICES.values()))
    p30 = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=list(SERVICES.values()))
    return Data(p5, p30, load_stem())


def _grid(x: pd.Series) -> pd.DataFrame:
    """Trading day x slot (0..47) table of a 30-min series."""
    day = pc.trading_day(x.index)
    slot = ((x.index - day - pc.DAY_START) / HALF).astype(int)
    return x.groupby([day, slot]).mean().unstack()


def _at(table: pd.DataFrame, days: pd.Series, slots: pd.Series) -> np.ndarray:
    return table.stack(future_stack=True).reindex(pd.MultiIndex.from_arrays([days, slots])).to_numpy()


def _thresholds(x: pd.Series) -> pd.Series:
    """Per trading day k: 99th percentile of the service's 5-min prices on days k-28 .. k-1 (>= 14 days)."""
    day = pc.trading_day(x.index)
    pos = ((x.index - day - pc.DAY_START) / pd.Timedelta(minutes=5)).astype(int)
    m = x.groupby([day, pos]).first().unstack()
    m = m.reindex(pd.date_range(m.index.min(), m.index.max()))
    v = m.to_numpy()
    thr = np.full(len(m), np.nan)
    have = (~np.isnan(v)).any(axis=1)
    for k in range(len(m)):
        lo = max(0, k - 28)
        if have[lo:k].sum() >= 14:
            thr[k] = np.nanpercentile(v[lo:k], 99)
    return pd.Series(thr, index=m.index)


def jump_flags(p5: pd.DataFrame) -> pd.DataFrame:
    """Per 5-min interval and service: price > that day's threshold (NaN while the threshold is unknown).
    Strictly above: when the price sits flat for weeks the 99th percentile equals it (PLAN change log 2)."""
    out = {}
    for s, col in SERVICES.items():
        x = p5[col]
        thr = _thresholds(x).reindex(pc.trading_day(x.index)).to_numpy()
        out[s] = pd.Series(np.where(np.isnan(thr) | x.isna().to_numpy(), np.nan, (x.to_numpy() > thr).astype(float)), index=x.index)
    return pd.DataFrame(out)


def fx_features(r: pd.DataFrame, d: Data, leak: bool = False) -> pd.DataFrame:
    last = r["issue_date"] - pd.Timedelta(days=0 if leak else 1)   # last complete trading day (leak: D itself)
    out = pd.DataFrame(index=r.index)
    flags = jump_flags(d.p5)
    day5 = pc.trading_day(d.p5.index)
    for s, col in SERVICES.items():
        x5 = d.p5[col]
        dmax = x5.groupby(day5).max()
        dstep = x5.diff().groupby(day5).max()
        grid = _grid(d.p30[col])
        jc = flags[s].groupby(day5).sum(min_count=1)
        out[f"fx_{s}_max1"] = dmax.reindex(last).to_numpy()
        out[f"fx_{s}_step1"] = dstep.reindex(last).to_numpy()
        out[f"fx_{s}_slot1"] = _at(grid, last, r["slot"])
        out[f"fx_{s}_slotmax7"] = _at(grid.rolling(7, min_periods=4).max(), last, r["slot"])
        out[f"fx_{s}_jumps7"] = jc.rolling(7, min_periods=4).sum().reindex(last).to_numpy()
        base = dmax.shift(1).rolling(28, min_periods=14).median()          # days D-29 .. D-2 for last = D-1
        out[f"fx_{s}_ratio1"] = (dmax / np.maximum(base, FLOOR)).reindex(last).to_numpy()
    both = (flags["rr"] * flags["cr"]).groupby(day5).sum(min_count=1)
    out["fx_jump_both7"] = both.rolling(7, min_periods=4).sum().reindex(last).to_numpy()
    any_jump = flags.max(axis=1) == 1
    ends = (d.p5.index[any_jump] + pd.Timedelta(minutes=5)).to_numpy()
    cutoff = (last + pd.Timedelta(days=1) + pc.DAY_START).to_numpy()   # end of the last complete trading day
    k = np.searchsorted(ends, cutoff, side="right") - 1
    last_end = np.where(k >= 0, ends[np.maximum(k, 0)], np.datetime64("NaT", "ns"))
    out["fx_jump_last_h"] = (r["issue_time"].to_numpy() - last_end) / np.timedelta64(1, "h")
    return out


def pdx_features(r: pd.DataFrame, d: Data) -> pd.DataFrame:
    """Needs every row of an issue (D+0 and D+1) for the step across targets."""
    out = pd.DataFrame(index=r.index)
    last = r["issue_date"] - pd.Timedelta(days=1)
    order = r.sort_values(["issue_date", "target_time"])
    for s, col in SERVICES.items():
        f = order[f"aemo_{col}"]
        gap = order.groupby("issue_date")["target_time"].diff() == HALF
        out[f"pdx_{s}_step"] = (f - f.groupby(order["issue_date"]).shift(1)).where(gap).reindex(r.index)
        out[f"pdx_{s}_daymax"] = r.groupby(["issue_date", "target_date"])[f"aemo_{col}"].transform("max")
        med = _grid(d.p30[col]).rolling(28, min_periods=14).median()
        out[f"pdx_{s}_ratio"] = r[f"aemo_{col}"].to_numpy() / np.maximum(_at(med, last, r["slot"]), FLOOR)
    return out


def stem_features(r: pd.DataFrame, d: Data, mode: str = "0900", leak: bool = False) -> pd.DataFrame:
    """mode '0900': the auction for the issue day D. mode '1330' (or leak=True at 09:00): the target's own
    trading day, i.e. D+1 for the D+1 task."""
    auction = r["target_date"] if (mode == "1330" or leak) else r["issue_date"]
    issue = r["issue_date"] + (pd.Timedelta(hours=13, minutes=30) if mode == "1330" else pc.ISSUE)
    st = d.stem
    price = st.pivot(index="tdate", columns="slot", values="price")
    offer = st.pivot(index="tdate", columns="slot", values="offer")
    full = pd.date_range(price.index.min(), price.index.max())
    price, offer = price.reindex(full), offer.reindex(full)
    out = pd.DataFrame(index=r.index)
    out["stem_price"] = _at(price, auction, r["slot"])
    out["stem_daymax"] = price.max(axis=1).reindex(auction).to_numpy()
    out["stem_ratio"] = out["stem_price"] / np.maximum(_at(price.shift(1).rolling(28, min_periods=14).median(), auction, r["slot"]), FLOOR)
    flat = price.stack(future_stack=True)                         # (day, slot) in time order
    out["stem_step"] = _at((flat - flat.shift(1)).unstack(), auction, r["slot"])
    out["stem_offer"] = _at(offer, auction, r["slot"])
    out["stem_offer_ratio"] = out["stem_offer"] / _at(offer.shift(1).rolling(28, min_periods=14).median(), auction, r["slot"])
    public = auction - pd.Timedelta(days=1) + STEM_PUBLIC
    out["stem_auction"] = auction.to_numpy()
    out["stem_public_ok"] = (public <= issue).to_numpy()
    return out


def features(r: pd.DataFrame, d: Data, leak: bool = False) -> pd.DataFrame:
    s9 = stem_features(r, d, "0900", leak)
    s13 = stem_features(r, d, "1330").drop(columns=["stem_auction"]).add_suffix("_1330")
    return pd.concat([fx_features(r, d, leak), pdx_features(r, d), s9, s13], axis=1)


def main() -> None:
    table = pd.read_parquet(OUT / "spike_table_ext.parquet")
    d = load()
    d.stem.to_parquet(OUT / "stem_records.parquet", index=False)
    f = features(table, d)
    assert f["stem_public_ok"].all() and f["stem_public_ok_1330"].all(), "a STEM record used before it was public"
    f.to_parquet(OUT / "scarcity_features.parquet")
    nan = f[FX + PDX + STEM].isna().groupby(table["issue_date"] >= pc.EVAL_START).mean().T
    print(f"{len(f):,} rows x {f.shape[1]} columns; share missing (False = before 2025-04-01, True = from 2025-04-01):")
    print(nan.round(3).to_string())


if __name__ == "__main__":
    main()
