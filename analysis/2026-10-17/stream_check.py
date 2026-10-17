"""The tree rules applied to every 30-min interval, not only to matched sets: how often do they fire, and how
often is a spike onset behind them?

For every 30-min interval start t (outside spike events, since an onset cannot happen inside one) the rule
inputs are computed from the 5-min series before t, exactly as features.py does at cutoff 0:
  energy_last30     mean 5-min energy price in [t - 30 min, t)
  reg_raise_last30  mean 5-min regulation-raise price in [t - 30 min, t)
  p5_n150_5h        5-min intervals with energy >= $150 in [t - 5 h, t)
  hist_h_since_spike hours since the end of the last 30-min interval with RTP >= $200
Targets: t is the onset of a top-20% event (top_regime) / of any spike event; any spike interval starts in
[t, t + 2 h).

Run:  python stream_check.py   -> out/stream_check.csv
"""

import numpy as np
import pandas as pd

from common import OUT, PROCESSED, pc, period_of

RULES = {  # from out/rules.txt (the leaf holding most cases in each tree)
    "T 叶子 12：最后30分钟均价>$94.4 且 调频上调价>0 且 距上次尖峰>0.75h":
        lambda d: (d["energy_last30"] > 94.42) & (d["reg_raise_last30"] > 0.005833) & (d["hist_h_since_spike"] > 0.75),
    "V 叶子 12：最后30分钟均价>$118.4 且 5小时内≥$150的5分钟区间>15个":
        lambda d: (d["energy_last30"] > 118.4) & (d["p5_n150_5h"] > 15.5),
    "只看价格：最后30分钟均价>$94.4":
        lambda d: d["energy_last30"] > 94.42,
}


def main() -> None:
    w5 = pd.read_parquet(PROCESSED / "wem_5min.parquet", columns=["energy", "reg_raise"])
    rtp = pd.read_parquet(PROCESSED / "wem_30min.parquet")["energy"]
    ev = pd.read_csv(OUT / "events.csv", parse_dates=["t0", "last"])
    # value at t = statistic over the 5-min intervals before t (the rolling window ends at t - 5 min)
    last30 = w5.rolling(6).mean().shift(1)
    n150 = (w5["energy"] >= 150).astype(float).rolling(60).sum().shift(1)
    d = pd.DataFrame({"energy_last30": last30["energy"], "reg_raise_last30": last30["reg_raise"], "p5_n150_5h": n150}).reindex(rtp.index)
    spike_end = (rtp.index[rtp >= pc.SPIKE] + pd.Timedelta(minutes=30)).to_numpy()
    k = np.searchsorted(spike_end, rtp.index.to_numpy(), side="right") - 1
    d["hist_h_since_spike"] = np.where(k >= 0, (rtp.index.to_numpy() - spike_end[np.maximum(k, 0)]) / np.timedelta64(1, "h"), np.inf)

    inside = pd.Series(False, index=rtp.index)
    for a, b in zip(ev["t0"], ev["last"]):
        inside[a:b] = True
    onset_any = pd.Series(False, index=rtp.index)
    onset_any[ev["t0"]] = True
    onset_top = pd.Series(False, index=rtp.index)
    onset_top[ev.loc[ev["top_regime"], "t0"]] = True
    spike = (rtp >= pc.SPIKE).astype(int)
    soon = spike[::-1].rolling(4, min_periods=1).max()[::-1].astype(bool)      # a spike interval in [t, t + 2 h)

    d = d[(~inside | onset_any) & d["energy_last30"].notna()].copy()
    d["period"] = period_of(pc.trading_day(d.index))
    d["onset_top"], d["onset_any"], d["soon"] = onset_top.reindex(d.index), onset_any.reindex(d.index), soon.reindex(d.index)
    rows = []
    for name, rule in RULES.items():
        flag = rule(d)
        for period, g in d.groupby("period"):
            f = flag[g.index]
            rows.append({"rule": name, "period": period, "intervals": len(g), "flagged": int(f.sum()), "flag_share": f.mean(),
                         "top_onsets": int(g["onset_top"].sum()), "top_caught": int((f & g["onset_top"]).sum()),
                         "top_recall": (f & g["onset_top"]).sum() / max(1, g["onset_top"].sum()),
                         "precision_top_onset": (f & g["onset_top"]).sum() / max(1, f.sum()),
                         "precision_any_onset": (f & g["onset_any"]).sum() / max(1, f.sum()),
                         "precision_spike_within_2h": (f & g["soon"]).sum() / max(1, f.sum()),
                         "base_top_onset": g["onset_top"].mean(), "base_any_onset": g["onset_any"].mean(), "base_spike_within_2h": g["soon"].mean()})
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "stream_check.csv", index=False)
    pd.set_option("display.width", 250)
    print(out.round(4).to_string())


if __name__ == "__main__":
    main()
