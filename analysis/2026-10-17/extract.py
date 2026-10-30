"""Extract every spike event, mark the top 20%, pick matched controls and cut a 5-min panel around each.

Events     every run of 30-min RTP >= $200 (gaps <= 1 interval merged) from 2023-10-01 to 2026-10-15.
           onset t0 = start of the first spike interval; peak = highest RTP; excess = sum of (RTP - 200)+ x 0.5 h.
Top 20%    top_regime: the top 20% of events by peak (ties: excess) within each regime (before / from
           2025-04-01); top_global: the same over all events. Events outside the top are "ordinary".
Controls   for each top event (either definition), N_CONTROLS reference intervals at the same 30-min slot on
           other trading days within +/- MATCH_DAYS and the same period, where that interval and the next
           2 h have no spike. Drawn at random without replacement (SEED). Their precursor windows may hold
           earlier spikes, exactly as the cases' may.
Panel      5-min rows from t0 - LOOKBACK_MIN to t0 + max(POST_MIN, event length) for every event and control:
           prices and system quantities (wem_5min), SCADA by fuel (ex post), outage state (ex post and known),
           the 30-min RTP, the 08:00 pre-dispatch run public 5 h before t0 (+20 min after its issue ID, as
           vpp.spike.protocol), and the ECMWF day-1 forecast (vpp.data.load_weather).

Run:  python extract.py   -> out/events.csv, out/windows.csv, out/panel.parquet
"""

import numpy as np
import pandas as pd

from common import (HALF, LOOKBACK_MIN, MATCH_DAYS, N_CONTROLS, OUT, POST_MIN, PROCESSED, QUIET_AFTER_MIN, SEED,
                    TOP_SHARE, pc, period_of, regime_of)
from vpp.spike.metrics import events as event_ids

W5 = ["energy", "reg_raise", "reg_lower", "cont_raise", "cont_lower", "demand_mw", "unscheduled_demand_mw", "withdrawal_mw",
      "dpv_mw", "req_energy_mw", "req_cont_raise_mw", "req_cont_lower_mw", "req_reg_raise_mw", "req_reg_lower_mw", "req_rocof"]
WX = {"wx_temp": "wx_perth_temperature_2m_previous_day1", "wx_ghi": "wx_perth_shortwave_radiation_previous_day1",
      "wx_cloud": "wx_perth_cloud_cover_previous_day1", "wx_wind_midwest": "wx_midwest_wind_speed_100m_previous_day1",
      "wx_wind_merredin": "wx_merredin_wind_speed_100m_previous_day1"}
PD = {"pd_price": "aemo_energy", "pd_req": "aemo_req_energy_mw", "pd_inservice": "aemo_in_service_injection_mw",
      "pd_reg_raise": "aemo_reg_raise", "pd_cont_raise": "aemo_cont_raise"}


def event_table(rtp: pd.Series, p5: pd.Series) -> pd.DataFrame:
    ids = event_ids(rtp)
    g = pd.DataFrame({"rtp": rtp, "ev": ids}).dropna()
    grp = g.groupby("ev")
    e = pd.DataFrame({
        "t0": grp["rtp"].apply(lambda s: s.index.min()),
        "last": grp["rtp"].apply(lambda s: s.index.max()),
        "peak": grp["rtp"].max(),
        "mean_rtp": grp["rtp"].mean(),
        "n_intervals": grp.size(),
        "n_spike": grp["rtp"].apply(lambda s: int((s >= pc.SPIKE).sum())),
        "excess": grp["rtp"].apply(lambda s: float(((s - pc.SPIKE).clip(lower=0) * 0.5).sum())),
    }).reset_index(drop=True)
    e["t_end"] = e["last"] + HALF
    e["duration_h"] = (e["t_end"] - e["t0"]).dt.total_seconds() / 3600
    e["peak5"] = [p5[a:b - pd.Timedelta(minutes=5)].max() for a, b in zip(e["t0"], e["t_end"])]
    # battery view: the best 4 half-hours (1 MW x 2 h) of the event
    e["top4_mwh_value"] = [rtp[a:b - HALF].nlargest(4).sum() * 0.5 for a, b in zip(e["t0"], e["t_end"])]
    e["trading_day"] = pc.trading_day(e["t0"])
    e["period"] = period_of(e["trading_day"])
    e["regime"] = regime_of(e["trading_day"])
    e["slot"] = ((e["t0"] - e["trading_day"] - pc.DAY_START) / HALF).astype(int)
    e["hour"] = e["t0"].dt.hour + e["t0"].dt.minute / 60
    prev_end = e["t_end"].shift()
    e["h_since_prev_event"] = (e["t0"] - prev_end).dt.total_seconds() / 3600

    def top(frame: pd.DataFrame) -> pd.Series:
        k = int(round(TOP_SHARE * len(frame)))
        order = frame.sort_values(["peak", "excess"], ascending=False).index
        rank = pd.Series(np.arange(1, len(frame) + 1), index=order).reindex(frame.index)
        return rank, rank <= k

    e["rank_regime"], e["top_regime"] = np.nan, False
    for reg, idx in e.groupby("regime").groups.items():
        rank, flag = top(e.loc[idx])
        e.loc[idx, "rank_regime"], e.loc[idx, "top_regime"] = rank, flag
    e["rank_global"], e["top_global"] = top(e)
    e["event_id"] = np.arange(len(e))
    return e


# matching tiers, tried in order until a case has N_CONTROLS: (slot tolerance, +/- days)
TIERS = ((0, MATCH_DAYS), (1, MATCH_DAYS), (1, 2 * MATCH_DAYS))


def controls(e: pd.DataFrame, rtp: pd.Series) -> pd.DataFrame:
    """Matched controls (one row per case x control) for every top event of either definition.

    A control may serve several cases (standard for matched case-control sets): in late 2023 a spike at
    17:30 was the norm, so quiet evenings within +/- 4 weeks are few. Within a case controls are distinct.
    """
    spike = (rtp >= pc.SPIKE).to_numpy()
    n_after = QUIET_AFTER_MIN // 30
    # quiet[i]: interval i and the following intervals up to QUIET_AFTER_MIN from its start hold no spike
    window = np.lib.stride_tricks.sliding_window_view(np.r_[spike, np.zeros(n_after, bool)], n_after)[:len(spike)]
    quiet = ~window.any(axis=1)
    cand = pd.DataFrame({"t0": rtp.index[quiet]})
    cand["trading_day"] = pc.trading_day(cand["t0"])
    cand["slot"] = ((cand["t0"] - cand["trading_day"] - pc.DAY_START) / HALF).astype(int)
    cand["period"] = period_of(cand["trading_day"])
    # full look-back and follow-up inside the data
    lo, hi = rtp.index[0] + pd.Timedelta(minutes=LOOKBACK_MIN), rtp.index[-1] - pd.Timedelta(minutes=POST_MIN)
    cand = cand[(cand["t0"] >= lo) & (cand["t0"] <= hi)].reset_index(drop=True)
    rng = np.random.default_rng(SEED)
    rows = []
    for _, c in e[e["top_regime"] | e["top_global"]].iterrows():
        # same period (train / validation / test), so no window is both a training and a test control
        base = cand[(cand["period"] == c["period"]) & (cand["trading_day"] != c["trading_day"])]
        picked = []
        for tier, (slot_tol, days) in enumerate(TIERS):
            pool = base[((base["slot"] - c["slot"]).abs() <= slot_tol)
                        & ((base["trading_day"] - c["trading_day"]).abs() <= pd.Timedelta(days=days))]
            pool = pool[~pool.index.isin([i for i, _ in picked])]
            need = N_CONTROLS - len(picked)
            take = pool.index[rng.choice(len(pool), size=min(need, len(pool)), replace=False)] if len(pool) else []
            picked += [(i, tier) for i in take]
            if len(picked) >= N_CONTROLS:
                break
        idx, tiers = zip(*picked) if picked else ((), ())
        rows.append(cand.loc[list(idx)].assign(case_event=c["event_id"], tier=list(tiers)))
    out = pd.concat(rows, ignore_index=True)
    out["regime"] = regime_of(out["trading_day"])
    out["hour"] = out["t0"].dt.hour + out["t0"].dt.minute / 60
    out["window_id"] = "C" + out["t0"].dt.strftime("%Y%m%d%H%M")
    return out


def predispatch_runs() -> pd.DataFrame:
    pdr = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
    pdr["available_at"] = pdr["issued_at"] + pc.AVAILABILITY["predispatch"]
    return pdr


def main() -> None:
    w5 = pd.read_parquet(PROCESSED / "wem_5min.parquet")[W5]
    w5["underlying_mw"] = w5["unscheduled_demand_mw"] + w5["dpv_mw"]
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    rtp = df30["energy"]
    scada = pd.read_parquet(OUT / "scada_5min.parquet")
    outages = pd.read_parquet(OUT / "outages_30min.parquet")
    wx = pd.read_parquet(PROCESSED / "weather_30min.parquet")[list(WX.values())].rename(columns={v: k for k, v in WX.items()})
    pdr = predispatch_runs()

    e = event_table(rtp, w5["energy"])
    ctl = controls(e, rtp)
    e.to_csv(OUT / "events.csv", index=False)
    e["window_id"] = "E" + e["event_id"].astype(str)
    matched = ctl.merge(e[["event_id", "window_id", "top_regime", "top_global"]], left_on="case_event", right_on="event_id",
                        suffixes=("", "_case"))
    matched = matched.rename(columns={"window_id": "control_window", "window_id_case": "case_window"})
    matched[["case_event", "case_window", "control_window", "tier", "top_regime", "top_global"]].to_csv(OUT / "matched.csv", index=False)

    cols = ["window_id", "kind", "event_id", "t0", "t_end", "trading_day", "slot", "hour", "period", "regime", "top_regime", "top_global"]
    ev_win = e.assign(kind=np.where(e["top_regime"] | e["top_global"], "top", "ordinary"))[cols]
    flags = matched.groupby("control_window")[["top_regime", "top_global"]].any()
    ctl_win = (ctl.drop_duplicates("window_id").set_index("window_id").drop(columns=["case_event", "tier"])
               .join(flags).reset_index().assign(kind="control", event_id=-1, t_end=lambda d: d["t0"] + HALF))[cols]
    win = pd.concat([ev_win, ctl_win], ignore_index=True)

    # the 08:00 run public 5 h before the onset
    start = (win["t0"] - pd.Timedelta(minutes=300)).astype("datetime64[ns]")
    runs = pdr.drop_duplicates("issue_date")[["issue_date", "available_at"]].sort_values("available_at")
    runs["available_at"] = runs["available_at"].astype("datetime64[ns]")
    pick = pd.merge_asof(pd.DataFrame({"start": start, "i": np.arange(len(win))}).sort_values("start"), runs,
                         left_on="start", right_on="available_at", direction="backward").set_index("i").sort_index()
    win["pd_issue_date"], win["pd_available_at"] = pick["issue_date"].to_numpy(), pick["available_at"].to_numpy()
    win["pd_age_h"] = (win["t0"] - win["pd_available_at"]).dt.total_seconds() / 3600
    win.to_csv(OUT / "windows.csv", index=False)

    # ---- the 5-min panel
    w5 = w5.join(scada.drop(columns=["n_reporting"]), how="left")
    w5["residual_mw"] = w5["unscheduled_demand_mw"] - w5["wind_mw"] - w5["solar_mw"]
    w5["thermal_mw"] = w5["coal_mw"] + w5["gas_base_mw"] + w5["peaker_mw"]
    grid5 = w5.index
    w5 = w5.join(outages.reindex(grid5, method="ffill"))
    w5["rtp"] = rtp.reindex(grid5, method="ffill")
    w5 = w5.join(wx.reindex(grid5, method="ffill"))
    pd_by_run = {d: g.set_index("ts") for d, g in pdr.groupby("issue_date")}
    parts = []
    for _, w in win.iterrows():
        post = max(POST_MIN, int((w["t_end"] - w["t0"]).total_seconds() // 60))
        lo, hi = w["t0"] - pd.Timedelta(minutes=LOOKBACK_MIN), w["t0"] + pd.Timedelta(minutes=post)
        p = w5.loc[lo:hi - pd.Timedelta(minutes=5)].copy()
        run = pd_by_run.get(w["pd_issue_date"])
        if run is not None:
            fc = run[list(PD.values())].rename(columns={v: k for k, v in PD.items()})
            p = p.join(fc.reindex(p.index.floor("30min")).set_index(p.index))
        p["window_id"] = w["window_id"]
        p["rel_min"] = ((p.index - w["t0"]).total_seconds() // 60).astype(int)
        p["in_event"] = (p.index >= w["t0"]) & (p.index < w["t_end"])
        parts.append(p)
    panel = pd.concat(parts)
    panel["pd_headroom"] = panel["pd_inservice"] - panel["pd_req"]
    panel.index.name = "ts"
    panel = panel.reset_index()
    panel.to_parquet(OUT / "panel.parquet", index=False)

    print(f"events {len(e)}: " + ", ".join(f"{r} {n}" for r, n in e["regime"].value_counts().items()))
    for d in ("top_regime", "top_global"):
        t = e[e[d]]
        print(f"{d}: {len(t)} events, peak >= {t['peak'].min():.0f}; by period {t['period'].value_counts().to_dict()}")
    per_case = ctl.groupby("case_event").size()
    print(f"controls: {len(ctl)} case-control pairs, {ctl['window_id'].nunique()} distinct control windows, "
          f"{ctl['case_event'].nunique()} cases; per case min {per_case.min()}, cases with < {N_CONTROLS}: {(per_case < N_CONTROLS).sum()}; "
          f"tiers {ctl['tier'].value_counts().sort_index().to_dict()}")
    print(f"panel {len(panel):,} rows x {panel.shape[1]} columns, {panel['window_id'].nunique()} windows")
    print("missing share in the panel (top columns):")
    print(panel.isna().mean().sort_values(ascending=False).head(12).round(3).to_string())


if __name__ == "__main__":
    main()
