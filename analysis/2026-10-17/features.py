"""Window summaries at a cutoff c (minutes relative to the onset t0; c = 0 is "just before the spike").

Every feature uses the 5 hours before the cutoff, [c - 300, c), unless it is a forecast of the onset itself.
Availability of each feature (AVAIL) is what it would have been at the cutoff in real time:
  RT   visible in near-real time: 5-min energy and FCESS prices, demand, rooftop PV estimate, ESR withdrawal,
       market requirements (dispatch data; assumed published within minutes, not verified here), price
       history, and outages already submitted ("kn_")
  FC   forecasts public before the window opened: the 08:00 pre-dispatch run public 5 h before t0 ("pd_"),
       ECMWF day-1 weather ("wx_")
  EP   ex post only: SCADA output by fuel (published the next morning) and outages as finally recorded ("out_")
  CAL  calendar
"""

import numpy as np
import pandas as pd

from common import PRE_MIN, pc

LEVEL_VARS = {  # variable -> (availability, which summaries)
    "energy": ("RT", "price"),
    "reg_raise": ("RT", "fcess"), "cont_raise": ("RT", "fcess"), "reg_lower": ("RT", "fcess"), "cont_lower": ("RT", "fcess"),
    "unscheduled_demand_mw": ("RT", "full"), "underlying_mw": ("RT", "full"), "dpv_mw": ("RT", "full"),
    "withdrawal_mw": ("RT", "full"), "req_cont_raise_mw": ("RT", "full"), "req_cont_lower_mw": ("RT", "level"),
    "req_rocof": ("RT", "full"), "req_reg_raise_mw": ("RT", "level"),
    "kn_forced_mw": ("RT", "full"), "kn_total_mw": ("RT", "full"),
    "out_forced_mw": ("EP", "full"), "out_total_mw": ("EP", "full"), "out_planned_mw": ("EP", "level"),
    "out_forced_coal_mw": ("EP", "full"), "out_forced_gas_mw": ("EP", "full"), "out_n_forced": ("EP", "level"),
    "coal_mw": ("EP", "full"), "coal_units_on": ("EP", "full"), "gas_base_mw": ("EP", "full"), "peaker_mw": ("EP", "full"),
    "peaker_units_on": ("EP", "full"), "wind_mw": ("EP", "full"), "solar_mw": ("EP", "full"), "battery_mw": ("EP", "full"),
    "battery_chg_mw": ("EP", "level"), "residual_mw": ("EP", "full"), "thermal_mw": ("EP", "full"), "largest_mw": ("EP", "level"),
}
GROUP = {  # feature prefix -> plain-language group used in the report
    "energy": "能量价格", "p5": "能量价格", "reg_raise": "FCESS 价格", "cont_raise": "FCESS 价格", "reg_lower": "FCESS 价格",
    "cont_lower": "FCESS 价格", "unscheduled_demand_mw": "需求", "underlying_mw": "需求", "dpv_mw": "屋顶光伏",
    "withdrawal_mw": "电池充电（实时）", "req_cont_raise_mw": "调频需求量", "req_cont_lower_mw": "调频需求量",
    "req_rocof": "调频需求量", "req_reg_raise_mw": "调频需求量", "kn_": "停机（已公布）", "out_": "停机（事后）",
    "coal": "煤电", "gas_base": "燃气基荷", "peaker": "燃气调峰", "wind": "风电", "solar": "光伏电站",
    "battery": "电池出力", "bat_": "电池出力", "residual": "剩余需求", "thermal": "火电合计", "largest": "最大单机",
    "pd_": "预调度（5 小时前）", "surprise": "需求比预测高", "wx_": "天气预报", "hist_": "价格历史", "win_": "价格历史",
    "cal_": "日历",
}


def group_of(feature: str) -> str:
    for k in sorted(GROUP, key=len, reverse=True):
        if feature.startswith(k):
            return GROUP[k]
    return "其他"


def matrices(panel: pd.DataFrame, columns: list[str]) -> dict[str, pd.DataFrame]:
    """Each variable as a windows x rel_min table."""
    return {c: panel.pivot(index="window_id", columns="rel_min", values=c) for c in columns}


def _mean(m: pd.DataFrame, lo: int, hi: int) -> pd.Series:
    cols = [c for c in m.columns if lo <= c < hi]
    return m[cols].mean(axis=1)


def _agg(m: pd.DataFrame, lo: int, hi: int, how: str) -> pd.Series:
    cols = [c for c in m.columns if lo <= c < hi]
    return getattr(m[cols], how)(axis=1)


def summarize(mats: dict[str, pd.DataFrame], win: pd.DataFrame, rtp: pd.Series, esroi: pd.Series, cutoff: int = 0) -> tuple[pd.DataFrame, dict]:
    """(features per window, availability per feature) at the cutoff."""
    c, lo = cutoff, cutoff - PRE_MIN
    f, avail = {}, {}

    def put(name, values, a):
        f[name], avail[name] = values, a

    for v, (a, kind) in LEVEL_VARS.items():
        m = mats[v]
        last = _mean(m, c - 30, c)
        put(f"{v}_last30", last, a)
        if kind in ("full", "price"):
            put(f"{v}_d1h", last - _mean(m, c - 90, c - 60), a)
            put(f"{v}_d5h", last - _mean(m, lo, lo + 30), a)
            put(f"{v}_max5h", _agg(m, lo, c, "max"), a)
            put(f"{v}_min5h", _agg(m, lo, c, "min"), a)
        if kind == "fcess":
            put(f"{v}_max5h", _agg(m, lo, c, "max"), a)
    p = mats["energy"]
    cols = [x for x in p.columns if lo <= x < c]
    put("p5_mean5h", p[cols].mean(axis=1), "RT")
    put("p5_std5h", p[cols].std(axis=1), "RT")
    put("p5_n150_5h", (p[cols] >= 150).sum(axis=1).astype(float), "RT")
    put("p5_n300_5h", (p[cols] >= 300).sum(axis=1).astype(float), "RT")
    bd = mats["battery_dis_mw"]
    put("bat_dis_mwh_5h", bd[[x for x in bd.columns if lo <= x < c]].sum(axis=1) * 5 / 60, "EP")
    # actual energy requirement against the pre-dispatch forecast of the same intervals
    put("surprise_req_last30", _mean(mats["req_energy_mw"], c - 30, c) - _mean(mats["pd_req"], c - 30, c), "RT")
    put("surprise_req_mean5h", _mean(mats["req_energy_mw"], lo, c) - _mean(mats["pd_req"], lo, c), "RT")

    # forecasts of the onset itself (the run public 5 h before t0; weather forecast a day ahead)
    for v in ("pd_price", "pd_headroom", "pd_req", "pd_reg_raise", "pd_cont_raise"):
        put(f"{v}_t0", _mean(mats[v], 0, 30), "FC")
    put("pd_price_max_next2h", _agg(mats["pd_price"], 0, 120, "max"), "FC")
    put("pd_headroom_min_next2h", _agg(mats["pd_headroom"], 0, 120, "min"), "FC")
    put("pd_req_ramp5h", _mean(mats["pd_req"], 0, 30) - _mean(mats["pd_req"], -300, -270), "FC")
    put("pd_price_max_prev5h", _agg(mats["pd_price"], -300, 0, "max"), "FC")
    for v in ("wx_temp", "wx_ghi", "wx_cloud", "wx_wind_midwest", "wx_wind_merredin"):
        put(f"{v}_t0", _mean(mats[v], 0, 30), "FC")
    put("wx_wind_d5h", (_mean(mats["wx_wind_midwest"], 0, 30) + _mean(mats["wx_wind_merredin"], 0, 30)) / 2
        - (_mean(mats["wx_wind_midwest"], -300, -270) + _mean(mats["wx_wind_merredin"], -300, -270)) / 2, "FC")
    put("wx_temp_max_win", _agg(mats["wx_temp"], -600, 180, "max"), "FC")

    # price history before the cutoff (30-min RTP of intervals finished by then)
    t0 = pd.DatetimeIndex(win["t0"])
    tc = t0 + pd.Timedelta(minutes=c)
    spike_end = rtp.index[rtp >= pc.SPIKE] + pd.Timedelta(minutes=30)
    se = spike_end.to_numpy()
    cnt = lambda a, b: np.searchsorted(se, b.to_numpy(), side="right") - np.searchsorted(se, a.to_numpy(), side="right")  # noqa: E731
    put("win_spk_n", cnt(tc - pd.Timedelta(hours=5), tc).astype(float), "RT")
    put("hist_spk_5to24h", cnt(tc - pd.Timedelta(hours=24), tc - pd.Timedelta(hours=5)).astype(float), "RT")
    put("hist_spk_7d", cnt(tc - pd.Timedelta(days=7), tc).astype(float), "RT")
    k = np.searchsorted(se, tc.to_numpy(), side="right") - 1
    since = np.where(k >= 0, (tc.to_numpy() - se[np.maximum(k, 0)]) / np.timedelta64(1, "h"), np.nan)
    put("hist_h_since_spike", np.minimum(since, 24 * 7), "RT")
    cum = rtp.rolling("24h").max()  # max of intervals starting in (t - 24h, t]; shift so only finished ones count
    put("hist_rtp_max_24h", cum.reindex(tc - pd.Timedelta(minutes=30)).to_numpy(), "RT")
    put("hist_rtp_same_slot_yday", rtp.reindex(t0 - pd.Timedelta(days=1)).to_numpy(), "RT")

    put("cal_dow", t0.dayofweek.to_numpy().astype(float), "CAL")
    put("cal_weekend", (t0.dayofweek >= 5).astype(float), "CAL")
    put("cal_month_sin", np.sin(2 * np.pi * t0.month.to_numpy() / 12), "CAL")
    put("cal_month_cos", np.cos(2 * np.pi * t0.month.to_numpy() / 12), "CAL")
    put("cal_esroi", esroi.reindex(t0.floor("30min")).astype(float).to_numpy(), "CAL")
    put("cal_hour", win["hour"].to_numpy(), "CAL")

    out = pd.DataFrame({k: (v.reindex(win["window_id"]).to_numpy() if isinstance(v, pd.Series) else np.asarray(v))
                        for k, v in f.items()}, index=win["window_id"])
    # windows without a complete look-back (the first hours of the data) have no precursor
    e = mats["energy"]
    has = e[lo].reindex(win["window_id"]).notna().to_numpy() if lo in e.columns else np.zeros(len(out), bool)
    out.loc[~has] = np.nan
    return out, avail
