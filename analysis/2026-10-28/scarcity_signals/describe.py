"""Mechanism checks, report only (PLAN section 8). In-sample trading days 2025-04-01 .. 2026-06-30 only.

M1  5-min energy spike onsets (price >= $200 after 60 minutes all below): share with an FCESS raise-price
    jump (sf.jump_flags, any service) in the 60 minutes before, against the same share at all 5-min times;
    and the other way round: after a jump, how often an onset follows within 60 minutes, against all times.
M2  at jump intervals: share where the service's requirement (req_*_mw) rose from the previous 5 minutes,
    against all intervals ("running out of power" versus "AEMO asked for more reserve").
M3  trading days by decile of the day's highest STEM price: spike-day rate (some 30-min RTP >= $200), for
    the same day's auction (D+0) and the previous day's auction (D+1).

Output: out/describe_onsets.csv, out/describe_requirement.csv, out/describe_stem_deciles.csv
Run:    .venv/bin/python analysis/2026-10-28/scarcity_signals/describe.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scarcity_features as sf  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402
from vpp.spike import protocol as pc  # noqa: E402

OUT = sf.OUT
LO, HI = pd.Timestamp("2025-04-01"), pd.Timestamp("2026-06-30")
WIN = 12                                        # 5-min intervals = 60 minutes
REQ = {"rr": "req_reg_raise_mw", "cr": "req_cont_raise_mw"}


def day_boot(flag: pd.Series, days: pd.Series, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """95% interval of a share, resampling whole trading days."""
    g = pd.DataFrame({"f": flag.astype(float).to_numpy(), "d": days.to_numpy()}).groupby("d")["f"].agg(["sum", "size"])
    idx = np.random.default_rng(seed).integers(0, len(g), (n, len(g)))
    b = g["sum"].to_numpy()[idx].sum(1) / g["size"].to_numpy()[idx].sum(1)
    return float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))


def onsets(p5: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    e = p5["energy"]
    hi = e >= pc.SPIKE
    calm = (~hi).astype(float).where(e.notna()).rolling(WIN).min().shift(1) == 1      # 60 min all below
    onset = hi & calm
    jump = (flags.max(axis=1) == 1).astype(float).where(flags.notna().all(axis=1))
    before = jump.rolling(WIN, min_periods=WIN).max().shift(1)                       # any jump in [t-60, t)
    after_onset = onset.astype(float)[::-1].rolling(WIN, min_periods=1).max()[::-1].shift(-1)  # onset in (t, t+60]
    day = pc.trading_day(e.index)
    ins = (day >= LO) & (day <= HI)
    rows = []
    sel = ins & onset.to_numpy() & before.notna().to_numpy()
    allt = ins & before.notna().to_numpy()
    for name, m in (("spike onsets", sel), ("all 5-min times", allt)):
        share = float(before[m].mean())
        lo, hi_ = day_boot(before[m] == 1, pd.Series(day[m]))
        rows.append({"measure": "jump in the 60 min before", "rows": name, "n": int(m.sum()), "share": share, "lo": lo, "hi": hi_})
    jm = ins & (jump == 1).to_numpy() & after_onset.notna().to_numpy()
    am = ins & after_onset.notna().to_numpy()
    for name, m in (("jump intervals", jm), ("all 5-min times", am)):
        share = float(after_onset[m].mean())
        lo, hi_ = day_boot(after_onset[m] == 1, pd.Series(day[m]))
        rows.append({"measure": "spike onset in the next 60 min", "rows": name, "n": int(m.sum()), "share": share, "lo": lo, "hi": hi_})
    return pd.DataFrame(rows)


def requirement(p5: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    day = pc.trading_day(p5.index)
    ins = (day >= LO) & (day <= HI)
    rows = []
    for s, col in REQ.items():
        up = (p5[col].diff() > 0).where(p5[col].diff().notna())
        j = ins & (flags[s] == 1).to_numpy() & up.notna().to_numpy()
        a = ins & up.notna().to_numpy()
        rows.append({"service": sf.SERVICES[s], "jump_intervals": int(j.sum()), "req_up_at_jumps": float(up[j].mean()),
                     "all_intervals": int(a.sum()), "req_up_all": float(up[a].mean())})
    return pd.DataFrame(rows)


def stem_deciles(stem: pd.DataFrame, rtp: pd.Series) -> pd.DataFrame:
    day = pc.trading_day(rtp.index)
    spike_day = (rtp.groupby(day).max() >= pc.SPIKE).astype(float)
    smax = stem.groupby("tdate")["price"].max()
    rows = []
    for name, lag in (("same day's auction (D+0)", 0), ("previous day's auction (D+1)", 1)):
        x = pd.DataFrame({"stem_max": smax.reindex(spike_day.index - pd.Timedelta(days=lag)).to_numpy(), "spike_day": spike_day.to_numpy()},
                         index=spike_day.index)
        x = x[(x.index >= LO) & (x.index <= HI)].dropna()
        x["decile"] = pd.qcut(x["stem_max"].rank(method="first"), 10, labels=range(1, 11))
        g = x.groupby("decile", observed=True).agg(days=("spike_day", "size"), stem_max_lo=("stem_max", "min"),
                                                   stem_max_hi=("stem_max", "max"), spike_day_rate=("spike_day", "mean"))
        rows.append(g.reset_index().assign(auction=name, spearman=x["stem_max"].corr(x["spike_day"], method="spearman")))
    return pd.concat(rows, ignore_index=True)


def main() -> None:
    p5 = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=["energy", "reg_raise", "cont_raise", *REQ.values()])
    flags = sf.jump_flags(p5)
    o = onsets(p5, flags)
    o.to_csv(OUT / "describe_onsets.csv", index=False)
    print("== M1 onsets\n" + o.round(4).to_string(index=False))
    r = requirement(p5, flags)
    r.to_csv(OUT / "describe_requirement.csv", index=False)
    print("== M2 requirement\n" + r.round(4).to_string(index=False))
    rtp = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["energy"])["energy"]
    s = stem_deciles(sf.load_stem(), rtp)
    s.to_csv(OUT / "describe_stem_deciles.csv", index=False)
    print("== M3 STEM deciles\n" + s.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
