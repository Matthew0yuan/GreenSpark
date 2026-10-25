"""Data definitions and coverage before PLAN.md is frozen (no method is scored here).

Checks: which columns hold the actual and forecast quantities, units and interval conventions, the trading-day
boundary, coverage of the 09:00 inputs on Hot Season days, and whether the proxy peak days agree with AEMO's
published ones. Prints only; writes inspect_data.txt next to this file through print redirection is not allowed,
so the output is copied into PLAN.md by hand.

Run:  .venv/Scripts/python.exe analysis/2026-10-24/peak_day_probability/inspect_data.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from vpp import site_bill as sb  # noqa: E402
from vpp.data import PROCESSED  # noqa: E402

H = pd.Timedelta(hours=8)


def main() -> None:
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 40)
    df = pd.read_parquet(PROCESSED / "wem_30min.parquet", columns=["demand_mw", "req_energy_mw", "dpv_mw", "filled"])
    print("wem_30min:", df.index[0], "->", df.index[-1], "rows", len(df))
    print("index step:", pd.Series(df.index).diff().value_counts().head(3).to_dict())
    day = (df.index - H).normalize()
    seas = sb.hot_season(day)
    for y in (2023, 2024, 2025):
        s = df[seas == y]
        print(f"season {y}: trading days {pd.Index(day[seas == y]).nunique()}, first interval {s.index[0]}, last {s.index[-1]},"
              f" NaN demand {s['demand_mw'].isna().sum()}, NaN req {s['req_energy_mw'].isna().sum()}, filled {int(s['filled'].sum())}")
    od = df["demand_mw"].groupby(day).max()
    er = df["req_energy_mw"].groupby(day).max()
    od_slot = df["demand_mw"].groupby(day).idxmax()
    d = pd.DataFrame({"od": od, "er": er})
    d["season"] = sb.hot_season(d.index)
    hs = d[d["season"] >= 0]
    print("\nHot Season daily max (MW): operational demand vs actual energy requirement")
    print(hs.groupby("season").agg(od_mean=("od", "mean"), od_max=("od", "max"), er_mean=("er", "mean"), er_max=("er", "max")).round(0))
    print("corr(od, er) daily max, Hot Seasons:", round(hs["od"].corr(hs["er"]), 4),
          " mean er - od:", round((hs["er"] - hs["od"]).mean(), 1), " sd:", round((hs["er"] - hs["od"]).std(), 1))
    # interval level: is ER = OD + something?
    x = df[(seas >= 0)].dropna(subset=["demand_mw", "req_energy_mw"])
    print("interval level er - od, Hot Seasons: mean", round((x["req_energy_mw"] - x["demand_mw"]).mean(), 1),
          "p05", round((x["req_energy_mw"] - x["demand_mw"]).quantile(0.05), 1),
          "p95", round((x["req_energy_mw"] - x["demand_mw"]).quantile(0.95), 1))
    hours = pd.DatetimeIndex(od_slot[hs.index]).hour + pd.DatetimeIndex(od_slot[hs.index]).minute / 60
    print("clock time of the daily OD maximum on Hot Season days:", pd.Series(hours).describe().round(2).to_dict())

    print("\nTop-4 days per season: by operational demand (proxy), by actual energy requirement, AEMO published")
    for y in (2023, 2024, 2025):
        g = hs[hs["season"] == y]
        top_od = sorted(g["od"].nlargest(4).index.date)
        top_er = sorted(g["er"].nlargest(4).index.date)
        off = sorted({(t - H).normalize().date() for t in sb.ircr_intervals(df["demand_mw"], y)}) if y in sb.OFFICIAL_PEAK_INTERVALS else None
        print(f"  {y}: OD {[str(v) for v in top_od]}  ER {[str(v) for v in top_er]}  official {[str(v) for v in off] if off else 'n/a'}")
        print(f"        OD 4th / 5th / 8th highest: {g['od'].nlargest(8).round(0).tolist()}")
        if y in sb.OFFICIAL_PEAK_INTERVALS:
            prox = df["demand_mw"][(df.index >= f"{y}-12-01 08:00") & (df.index < f"{y + 1}-04-01 08:00")]
            dd = (prox.index - H).normalize()
            top_days = prox.groupby(dd).max().nlargest(4).index
            pi = sorted(np.concatenate([prox[dd == t].nlargest(3).index.to_numpy() for t in top_days]))
            offi = sorted(pd.DatetimeIndex(sb.OFFICIAL_PEAK_INTERVALS[y]).to_numpy())
            print("        proxy 12 intervals == official:", [str(pd.Timestamp(a)) for a in pi] == [str(pd.Timestamp(b)) for b in offi])

    t = pd.read_parquet(PROCESSED / "spike_table.parquet")
    print("\nspike_table columns (subset):", [c for c in t.columns if c.startswith(("pd_", "wx_", "src_", "st_ref", "issue", "target", "days", "lead"))])
    print("issue dates", t["issue_date"].min(), "->", t["issue_date"].max())
    for da in (0, 1):
        u = t[t["days_ahead"] == da]
        per_day = u.groupby("target_date").agg(n=("target_time", "size"), fmax=("pd_day_req_max", "max"),
                                               nan_req=("pd_req", lambda s: s.isna().sum()), age=("pd_age_h", "max"),
                                               late=("src_pd_available_at", lambda s: 0), tmax=("wx_tmax_day", "max"),
                                               wx_age=("wx_age_h", "max"), first=("target_time", "min"), issue=("issue_time", "first"))
        late = (pd.to_datetime(u["src_pd_available_at"]) > u["issue_time"]).sum()
        ps_ = sb.hot_season(per_day.index)
        hsd = per_day[ps_ >= 0]
        print(f"days_ahead {da}: target days {len(per_day)}; Hot Season target days {len(hsd)}; rows per day {hsd['n'].value_counts().to_dict()};"
              f" days with NaN daily max forecast {int(hsd['fmax'].isna().sum())}; days with some NaN pd_req {int((hsd['nan_req'] > 0).sum())};"
              f" rows with pre-dispatch available after issue {late}")
        print(f"   pd_age_h (hours from run label to issue) on Hot Season days: {hsd['age'].describe().round(2).to_dict()}")
        print(f"   days where the run used is older than the same-day 08:00 run (age > 1.5 h + 24 h*da): {int((hsd['age'] > 1.5 + 24 * da).sum())}")
        print(f"   weather tmax NaN days {int(hsd['tmax'].isna().sum())}; wx_age_h max {hsd['wx_age'].max()}; first target {hsd['first'].min()}; issue {hsd['issue'].iloc[0]}")
        for y in (2023, 2024, 2025):
            print(f"   season {y}: target days with forecast {int((~per_day.loc[ps_ == y, 'fmax'].isna()).sum())} of {int((ps_ == y).sum())}")
    # forecast vs actual energy requirement, interval level (definition and units, not a score of any method)
    u = t[(t["days_ahead"] == 0)].copy()
    u["act_er"] = df["req_energy_mw"].reindex(u["target_time"]).to_numpy()
    u["act_od"] = df["demand_mw"].reindex(u["target_time"]).to_numpy()
    u = u[sb.hot_season(u["target_date"]) >= 0].dropna(subset=["pd_req", "act_er"])
    print("\ninterval level, same-day run, Hot Seasons: corr(pd_req, actual ER)", round(u["pd_req"].corr(u["act_er"]), 4),
          " corr(pd_req, actual OD)", round(u["pd_req"].corr(u["act_od"]), 4),
          " mean actual ER - pd_req", round((u["act_er"] - u["pd_req"]).mean(), 1), " mean actual OD - pd_req", round((u["act_od"] - u["pd_req"]).mean(), 1))
    print("pd_req unit check: median", round(u["pd_req"].median(), 0), "MW; actual ER median", round(u["act_er"].median(), 0), "MW")
    print("target_time minutes:", sorted(u["target_time"].dt.minute.unique()))
    # raw pre-dispatch: do the 08:00 runs cover the whole trading day?
    pdr = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet", columns=["issue_date", "issued_at", "ts", "days_ahead"])
    n = pdr.groupby("issue_date").size()
    print("pre-dispatch runs", len(n), "intervals per run", n.value_counts().head(4).to_dict(),
          "issued_at clock (h) distribution", (pdr.drop_duplicates("issue_date")["issued_at"].dt.hour + pdr.drop_duplicates("issue_date")["issued_at"].dt.minute / 60).round(2).value_counts().head(5).to_dict())


if __name__ == "__main__":
    main()
