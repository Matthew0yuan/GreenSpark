"""Figures for the spike-precursor study (static PNG, light surface).

  fig_event_study_<regime>.png  median 5-min paths from 5 h before to 2 h after the onset: top-20% events,
                                ordinary events, matched controls (top-20% interquartile band shaded)
  fig_matched_auc.png           matched AUC with 95% intervals per period for the most consistent features
  fig_lead_time.png             model matched AUC (fit on train, actionable features) by minutes before onset

Palette: the dataviz reference slots 1-3 (validated all-pairs, light), controls in muted ink.
Run:  python figures.py   -> out/fig_*.png
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from common import OUT  # noqa: E402

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
MUTED, INK, INK2, GRID, AXIS, SURFACE = "#898781", "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7", "#fcfcfb"
plt.rcParams.update({
    "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"], "axes.unicode_minus": False,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "axes.edgecolor": AXIS, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 9, "axes.titlesize": 10, "axes.titlecolor": INK,
    "lines.linewidth": 2, "lines.solid_capstyle": "round", "lines.solid_joinstyle": "round",
})

LABELS = {
    "energy_last30": "能量价格：开始前 30 分钟均值", "energy_max5h": "能量价格：5 小时内最高", "energy_min5h": "能量价格：5 小时内最低",
    "energy_d1h": "能量价格：最近 1 小时变化", "energy_d5h": "能量价格：5 小时变化", "p5_mean5h": "能量价格：5 小时均值",
    "p5_n150_5h": "5 小时内 ≥$150 的 5 分钟区间数", "p5_n300_5h": "5 小时内 ≥$300 的 5 分钟区间数", "p5_std5h": "能量价格：5 小时波动",
    "reg_raise_last30": "调频上调价格：开始前 30 分钟", "reg_raise_max5h": "调频上调价格：5 小时内最高",
    "cont_raise_last30": "应急上调价格：开始前 30 分钟", "cont_raise_max5h": "应急上调价格：5 小时内最高",
    "reg_lower_last30": "调频下调价格：开始前 30 分钟", "cont_lower_last30": "应急下调价格：开始前 30 分钟",
    "unscheduled_demand_mw_last30": "需求：开始前 30 分钟", "unscheduled_demand_mw_max5h": "需求：5 小时内最高",
    "unscheduled_demand_mw_min5h": "需求：5 小时内最低", "unscheduled_demand_mw_d1h": "需求：最近 1 小时爬坡",
    "underlying_mw_last30": "含屋顶光伏的总需求：开始前 30 分钟", "underlying_mw_max5h": "含屋顶光伏的总需求：5 小时内最高",
    "underlying_mw_d1h": "含屋顶光伏的总需求：最近 1 小时爬坡", "dpv_mw_max5h": "屋顶光伏：5 小时内最高",
    "residual_mw_last30": "剩余需求（需求−风−光）：开始前 30 分钟 *", "residual_mw_d1h": "剩余需求：最近 1 小时爬坡 *",
    "residual_mw_max5h": "剩余需求：5 小时内最高 *", "residual_mw_min5h": "剩余需求：5 小时内最低 *",
    "thermal_mw_last30": "火电出力合计：开始前 30 分钟 *", "thermal_mw_max5h": "火电出力合计：5 小时内最高 *",
    "thermal_mw_min5h": "火电出力合计：5 小时内最低 *", "gas_base_mw_last30": "燃气基荷出力：开始前 30 分钟 *",
    "gas_base_mw_max5h": "燃气基荷出力：5 小时内最高 *", "gas_base_mw_min5h": "燃气基荷出力：5 小时内最低 *",
    "largest_mw_last30": "最大单机出力 *", "battery_mw_last30": "电池净放电：开始前 30 分钟 *",
    "battery_mw_max5h": "电池净放电：5 小时内最高 *", "battery_mw_d5h": "电池净放电：5 小时变化 *",
    "battery_mw_d1h": "电池净放电：最近 1 小时变化 *", "bat_dis_mwh_5h": "电池 5 小时内已放电量 *",
    "pd_price_t0": "预调度（5 小时前）对开始时段的预测价", "pd_price_max_next2h": "预调度：开始后 2 小时最高预测价",
    "pd_price_max_prev5h": "预调度：开始前 5 小时最高预测价", "pd_req_t0": "预调度：开始时段的预测需求量",
    "pd_headroom_t0": "预调度：开始时段的在运余量", "hist_spk_5to24h": "5–24 小时前的尖峰区间数",
    "hist_rtp_max_24h": "过去 24 小时最高 RTP", "hist_h_since_spike": "距上一次尖峰的小时数", "win_spk_n": "5 小时内的尖峰区间数",
    "req_rocof_last30": "RoCoF 需求量：开始前 30 分钟", "req_rocof_max5h": "RoCoF 需求量：5 小时内最高",
    "peaker_mw_last30": "燃气调峰出力：开始前 30 分钟 *", "peaker_mw_max5h": "燃气调峰出力：5 小时内最高 *",
    "wind_mw_last30": "风电出力：开始前 30 分钟 *", "wind_mw_max5h": "风电出力：5 小时内最高 *",
    "coal_mw_d5h": "煤电出力：5 小时变化 *", "wx_wind_midwest_t0": "中西部风速预报",
}


def label(f: str) -> str:
    return LABELS.get(f, f)


def event_study() -> None:
    panel = pd.read_parquet(OUT / "panel.parquet", columns=["window_id", "rel_min", "energy", "reg_raise", "unscheduled_demand_mw",
                                                             "residual_mw", "wind_mw", "battery_mw", "dpv_mw", "out_forced_mw"])
    win = pd.read_csv(OUT / "windows.csv").set_index("window_id")
    matched = pd.read_csv(OUT / "matched.csv")
    panel = panel[(panel["rel_min"] >= -300) & (panel["rel_min"] < 120)]
    vars_ = [("energy", "5 分钟能量价格（$/MWh）"), ("reg_raise", "调频上调价格 > 0 的窗口占比（%）"),
             ("unscheduled_demand_mw", "需求（MW）"), ("residual_mw", "剩余需求：需求−风−光（MW）*"),
             ("wind_mw", "风电出力（MW）*"), ("battery_mw", "电池净放电（MW，负 = 充电）*"),
             ("dpv_mw", "屋顶光伏估计（MW）"), ("out_forced_mw", "强迫停机损失容量（MW，事后）*")]
    for regime, title in (("old", "2025-04 之前（训练期）：143 个前 20% 尖峰（峰值 ≥ $726）"),
                          ("new", "2025-04 起（验证期 + 测试期）：60 个前 20% 尖峰（峰值 ≥ $329）")):
        w = win[win["regime"] == regime]
        top = w.index[(w["kind"] == "top") & w["top_regime"]]
        ordinary = w.index[(w["kind"] == "ordinary") | ((w["kind"] == "top") & ~w["top_regime"])]
        ctl = matched.loc[matched["top_regime"] & matched["case_window"].isin(top), "control_window"].unique()
        groups = [("前 20% 尖峰", top, BLUE), ("其余 80% 尖峰", ordinary, ORANGE), ("同一时刻的对照（每个尖峰 10 个）", ctl, MUTED)]
        fig, axes = plt.subplots(2, 4, figsize=(15, 6.9), sharex=True)
        for ax, (v, name) in zip(axes.flat, vars_):
            for gname, ids, color in groups:
                p = panel[panel["window_id"].isin(ids)].groupby("rel_min")[v]
                if v == "reg_raise":   # the median is 0 almost everywhere; the share above 0 carries the signal
                    med = p.apply(lambda s: (s > 0).mean() * 100)
                else:
                    med = p.median()
                ax.plot(med.index / 60, med.to_numpy(), color=color, lw=2 if color != MUTED else 1.6, label=gname, zorder=3 if color == BLUE else 2)
                if color == BLUE and v != "reg_raise":
                    ax.fill_between(med.index / 60, p.quantile(0.25).to_numpy(), p.quantile(0.75).to_numpy(), color=BLUE, alpha=0.10, lw=0)
            ax.axvline(0, color=AXIS, lw=1)
            ax.set_title(name, loc="left")
            ax.set_xticks([-5, -4, -3, -2, -1, 0, 1, 2])
        handles, labels_ = axes[0, 0].get_legend_handles_labels()
        fig.suptitle(title + "。线 = 中位数（调频上调价格画的是 > 0 的窗口占比），蓝色阴影 = 前 20% 尖峰的四分位区间；* = 事后数据",
                     x=0.01, y=0.985, ha="left", fontsize=11, color=INK)
        fig.legend(handles, labels_, loc="upper left", bbox_to_anchor=(0.01, 0.95), ncol=3, frameon=False, fontsize=9)
        fig.supxlabel("相对尖峰开始的小时数（0 = 第一个 RTP ≥ $200 的 30 分钟区间开始）", fontsize=9, color=INK2)
        fig.tight_layout(rect=(0, 0, 1, 0.91))
        fig.savefig(OUT / f"fig_event_study_{regime}.png", dpi=150)
        plt.close(fig)


def forest() -> None:
    u = pd.read_csv(OUT / "univariate.csv")
    w = u.pivot(index="feature", columns="period", values="matched_auc")
    keep = u.drop_duplicates("feature").set_index("feature")
    ok = keep.index[keep["sig_all3"].fillna(False).astype(bool)]
    strength = (w.loc[ok] - 0.5).abs().min(axis=1).sort_values(ascending=False)
    # one feature per underlying variable, the strongest summary of it, then the top 22
    seen, chosen = set(), []
    for f in strength.index:
        base = f.rsplit("_", 1)[0] if not f.startswith(("p5_", "hist_", "win_", "pd_", "bat_")) else f
        if base in seen:
            continue
        seen.add(base)
        chosen.append(f)
    chosen = chosen[:22][::-1]
    fig, ax = plt.subplots(figsize=(10, 9))
    off = {"train": 0.22, "validation": 0.0, "test": -0.22}
    colors = {"train": BLUE, "validation": ORANGE, "test": AQUA}
    names = {"train": "训练期（2025-04 前，143 个）", "validation": "验证期（37 个）", "test": "测试期（23 个）"}
    for period in ("train", "validation", "test"):
        s = u[u["period"] == period].set_index("feature").reindex(chosen)
        y = np.arange(len(chosen)) + off[period]
        ax.hlines(y, s["lo"], s["hi"], color=colors[period], lw=2)
        ax.plot(s["matched_auc"], y, "o", ms=6, color=colors[period], mec=SURFACE, mew=1.5, label=names[period])
    ax.axvline(0.5, color=AXIS, lw=1)
    ax.set_yticks(np.arange(len(chosen)))
    ax.set_yticklabels([label(f) for f in chosen], color=INK2)
    ax.set_xlim(0.15, 1.0)
    ax.set_xlabel("配对 AUC：前 20% 尖峰的值高于自己 10 个同时刻对照的概率（0.5 = 没有差别，<0.5 = 尖峰前更低）")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=3, frameon=False)
    fig.suptitle("三期都显著且方向一致的前兆特征（每个变量取最强的一种汇总，95% 区间）。* = 事后数据", x=0.01, ha="left",
                 fontsize=10, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(OUT / "fig_matched_auc.png", dpi=150)
    plt.close(fig)


def lead() -> None:
    lt = pd.read_csv(OUT / "lead_time.csv")
    lt = lt[lt["fit"] == "T"]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for period, color, name in (("validation", BLUE, "验证期（37 个尖峰）"), ("test", ORANGE, "测试期（23 个尖峰）")):
        s = lt[lt["period"] == period].sort_values("cutoff_min")
        x = -s["cutoff_min"] / 60
        ax.fill_between(x, s["lo"], s["hi"], color=color, alpha=0.10, lw=0)
        ax.plot(x, s["matched_auc"], "-o", color=color, ms=5, mec=SURFACE, mew=1.5, label=name)
    ax.axhline(0.5, color=AXIS, lw=1)
    ax.set_xlim(5.2, -0.2)
    ax.set_ylim(0.45, 1.0)
    ax.set_xlabel("提前量：只用尖峰开始前多少小时为止可见的信息")
    ax.set_ylabel("配对 AUC")
    ax.legend(loc="lower right", frameon=False)
    ax.set_title("LightGBM（训练期拟合，只用实时可见和提前已知的特征）区分前 20% 尖峰与同时刻对照的能力，阴影 = 95% 区间", loc="left", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "fig_lead_time.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    event_study()
    forest()
    lead()
    print("figures written")
