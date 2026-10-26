"""Report-only diagnostics after forecast.py (no change to any method or criterion):
  - per month: actual tightest days, hits by B0 / M1 / M2 (why the month-block interval is wide);
  - the bootstrap distribution of the main difference (share of resamples at or below 0);
  - M1's error split at its tightest half-hour t*: unregistered outages, residual-demand forecast error,
    battery look-back difference, and the gap from t* to the label's own lowest half-hour.
Writes out/by_month.csv, out/error_split.csv, out/diag.json.

Run from the repo root:  .venv/bin/python analysis/2026-10-26/tightness_forecast/diag.py
"""

import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import forecast as fc  # noqa: E402


def main() -> None:
    x = pd.read_csv(fc.OUT / "daily_predictions.csv", parse_dates=["day"], index_col="day")
    for c in [c for c in x.columns if c.startswith(("top_", "mtop_"))] + ["spike", "lrc", "ircr_peak"]:
        x[c] = x[c].map({True: True, False: False, "True": True, "False": False})
    xi = x[x["period"] == "in_sample"].copy()
    out = {}

    # per month
    g = xi.groupby("month")
    bm = pd.DataFrame({"days": g.size(), "actual_top": g["top_actual"].sum(),
                       "hits_B0": g.apply(lambda t: int((t.top_B0 & t.top_actual).sum())),
                       "hits_M1": g.apply(lambda t: int((t.top_M1 & t.top_actual).sum())),
                       "hits_M2": g.apply(lambda t: int((t.top_M2 & t.top_actual).sum())),
                       "pred_top_B0": g["top_B0"].sum(), "pred_top_M1": g["top_M1"].sum(),
                       "bat_mean": g["bat"].mean(), "margin_median": g["margin_min_1621_mw"].median()})
    bm.to_csv(fc.OUT / "by_month.csv")
    out["same_hit_days_M1_M2"] = bool(((xi.top_M1 & xi.top_actual) == (xi.top_M2 & xi.top_actual)).all())
    out["same_top_days_M1_M2"] = int((xi.top_M1 & xi.top_M2).sum())

    # bootstrap distribution of the main difference (same resamples as forecast.boot_recall_diff)
    arr = np.array([[(t.top_M1 & t.top_actual).sum(), (t.top_B0 & t.top_actual).sum(), t.top_actual.sum()]
                    for _, t in xi.groupby("month")], float)
    rng = np.random.default_rng(fc.SEED)
    bs = []
    for _ in range(fc.N_BOOT):
        s = arr[rng.integers(0, len(arr), len(arr))].sum(0)
        if s[2]:
            bs.append((s[0] - s[1]) / s[2])
    bs = np.array(bs)
    out["boot_M1_share_le0"] = float((bs <= 0).mean())
    out["boot_M1_q"] = np.percentile(bs, [2.5, 5, 10, 50]).round(4).tolist()

    # M1 error split at t* (30-min labels)
    h = pd.read_parquet(fc.INP / "inputs_d1_30min.parquet")[["day", "slot", "lab_margin_mw", "lab_residual_mw",
                                                              "lab_outage_mw", "lab_battery_mw"]]
    xs = x.dropna(subset=["m1_mw", "margin_min_1621_mw"]).reset_index()
    j = xs.merge(h, left_on=["day", "t_star"], right_on=["day", "slot"], how="left").set_index("day")
    j["e_outage"] = j["lab_outage_mw"] - j["out_t"]            # unregistered (forced / late) outages
    j["e_residual"] = j["lab_residual_mw"] - j["res_t"]        # residual demand forecast error
    j["e_battery"] = j["bat"] - j["lab_battery_mw"]            # shift(2) vs the label's shift(1)
    j["e_timing"] = j["lab_margin_mw"] - j["margin_min_1621_mw"]  # the label's lowest half-hour is not t*
    j["e_total"] = j["m1_mw"] - j["margin_min_1621_mw"]
    j["check"] = j["e_outage"] + j["e_residual"] + j["e_battery"] + j["e_timing"] - j["e_total"]
    out["error_split_max_abs_check"] = float(j["check"].abs().max())
    cols = ["e_total", "e_outage", "e_residual", "e_battery", "e_timing"]
    ji = j[j["period"] == "in_sample"]
    groups = {"样本内全部": ji, "样本内实际最紧 46 天": ji[ji["top_actual"]],
              "样本内实际最紧、M1 漏掉的": ji[ji["top_actual"] & ~ji["top_M1"]],
              "样本内实际最紧、M1 抓到的": ji[ji["top_actual"] & ji["top_M1"]],
              "样本内 M1 误报（预测最紧、实际不是）": ji[ji["top_M1"] & ~ji["top_actual"]],
              "样本内 IRCR 高峰日": ji[ji["ircr_peak"]], "样本内 LRC 日": ji[ji["lrc"]]}
    rows = []
    for name, t in groups.items():
        rows.append({"group": name, "days": len(t), **t[cols].mean().round(1).to_dict(),
                     "spike_share": float(t["spike"].mean()) if len(t) else np.nan})
    es = pd.DataFrame(rows)
    es.to_csv(fc.OUT / "error_split.csv", index=False)
    ev = ji[ji["lrc"] | ji["ircr_peak"]][["t_star", "margin_min_time", "m1_mw", "margin_min_1621_mw"] + cols]
    ev.round(1).to_csv(fc.OUT / "error_split_events.csv")
    hist_days = pd.read_csv(fc.OUT / "history_lrc_outage_lowwind.csv", parse_dates=["day"])["day"]
    test_lrc = j[(j["period"].isin(["test_seen", "test_unseen"])) & j["lrc"]].index
    other = j[j.index.isin(hist_days) | j.index.isin(test_lrc)][["period", "t_star", "margin_min_time", "m1_mw",
                                                                  "margin_min_1621_mw", "out_t", "lab_outage_mw"]
                                                                 + cols]
    other.round(1).to_csv(fc.OUT / "error_split_lrc_other.csv")
    print(other.round(1).to_string())
    miss = ji[ji["top_actual"] & ~ji["top_M1"]][["margin_min_1621_mw", "m1_mw", "pct_M1", "pd_req_max_1621_mw",
                                                   "spike", "ircr_peak", "lrc"] + cols]
    miss.round(3).to_csv(fc.OUT / "missed_by_M1.csv")

    with open(fc.OUT / "diag.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    pd.set_option("display.width", 250)
    print(bm.round(1).to_string())
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(es.to_string(index=False))
    print(ev.round(1).to_string())
    print(miss.round(3).to_string())


if __name__ == "__main__":
    main()
