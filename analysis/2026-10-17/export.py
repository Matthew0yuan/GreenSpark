"""The extracted spikes and precursors in plain files.

  top20_spikes.csv            the 203 top-20% events (ranked within each regime), one row each
  top20_precursor_5min.csv    every 5-min interval from 5 h before each of them to its end (rel_min < 0 is the
                              precursor, in_event marks the spike itself)
  window_features.csv         the cutoff-0 features of every window (top-20% events, other events, matched
                              controls), with availability tags in window_features_availability.csv

Run:  python export.py
"""

import pandas as pd

from analyze import Study
from common import OUT, PRE_MIN

COLS_5MIN = ["energy", "rtp", "reg_raise", "reg_lower", "cont_raise", "cont_lower", "unscheduled_demand_mw", "demand_mw",
             "underlying_mw", "dpv_mw", "withdrawal_mw", "req_energy_mw", "req_cont_raise_mw", "req_cont_lower_mw", "req_rocof",
             "coal_mw", "gas_base_mw", "peaker_mw", "wind_mw", "solar_mw", "battery_mw", "battery_dis_mw", "battery_chg_mw",
             "residual_mw", "thermal_mw", "coal_units_on", "peaker_units_on", "largest_mw",
             "out_total_mw", "out_forced_mw", "out_planned_mw", "out_forced_coal_mw", "out_forced_gas_mw", "kn_total_mw", "kn_forced_mw",
             "pd_price", "pd_req", "pd_headroom", "wx_temp", "wx_ghi", "wx_cloud", "wx_wind_midwest", "wx_wind_merredin"]


def main() -> None:
    e = pd.read_csv(OUT / "events.csv", parse_dates=["t0", "last", "t_end", "trading_day"])
    top = e[e["top_regime"]].sort_values("t0")
    top[["event_id", "t0", "t_end", "duration_h", "n_spike", "peak", "peak5", "mean_rtp", "excess", "top4_mwh_value", "period",
         "regime", "rank_regime", "top_global", "hour", "h_since_prev_event"]].to_csv(OUT / "top20_spikes.csv", index=False)

    panel = pd.read_parquet(OUT / "panel.parquet")
    ids = "E" + top["event_id"].astype(str)
    p = panel[panel["window_id"].isin(ids) & (panel["rel_min"] >= -PRE_MIN)]
    p = p[(p["rel_min"] < 0) | p["in_event"]]
    p = p.assign(event_id=p["window_id"].str[1:].astype(int))[["event_id", "ts", "rel_min", "in_event"] + COLS_5MIN]
    p.sort_values(["event_id", "rel_min"]).to_csv(OUT / "top20_precursor_5min.csv", index=False)

    st = Study()
    F, avail = st.features(0)
    w = st.win[["kind", "event_id", "t0", "period", "regime", "top_regime", "top_global"]]
    F = w.join(F)
    first_case = st.sets.groupby("control_window")["case_window"].first()
    F["matched_to"] = first_case.reindex(F.index)
    F.to_csv(OUT / "window_features.csv")
    pd.Series(avail, name="availability").rename_axis("feature").to_csv(OUT / "window_features_availability.csv")
    print(f"{len(top)} events; {len(p):,} 5-min rows ({p['event_id'].nunique()} events); {len(F):,} windows x {F.shape[1]} columns")


if __name__ == "__main__":
    main()
