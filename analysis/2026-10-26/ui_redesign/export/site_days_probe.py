"""Probe for the site-day export: what the replay gives (record kinds, the 09:00 record, the day's money against the
fixed code's ledger, the 09:00 offers against the saved G5B file). Prints only.

Run (repo root):  .venv/Scripts/python.exe analysis/2026-10-26/ui_redesign/export/site_days_probe.py
"""

import sys

sys.dont_write_bytecode = True
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import json  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import site_days_replay as rp  # noqa: E402
from vpp import backtest as bt  # noqa: E402
from vpp.v2 import offers as of  # noqa: E402


def main():
    days = bt.load_days("0900")
    aux = bt.q2b_aux()
    led = pd.read_csv(rp.FIX / "out/after_ledger.csv", parse_dates=["day"])
    led = led[(led["scenario"] == "m1") & (led["strategy"] == rp.E5_NAME)].set_index("day")
    print("flags0 2026-02-02:", pd.Timestamp("2026-02-02") in set(aux["flags0"]),
          "flags1:", pd.Timestamp("2026-02-02") in set(aux["flags1"]), "peaks:", aux["ircr_peaks"].get(pd.Timestamp("2026-02-02")))
    for day in ("2026-02-02", "2026-10-20"):
        r = rp.replay(day, days, aux)
        s = r["summary"]
        row = led.loc[pd.Timestamp(day)]
        print(f"\n=== {day} start {r['start']}; mtd0 {r['mtd0']}")
        print("total", s["total"], "fixed ledger", row["total"], "diff", s["total"] - row["total"],
              "soc_end diff", s["soc_end"] - row["soc_end"])
        print("summary keys", sorted(s))
        print("records", [(int(x["t0"]), x["kind"]) for x in r["records"]][:8], len(r["records"]))
        rec = [x for x in r["records"] if int(x["t0"]) == 2]
        print("t0==2 records", [x["kind"] for x in rec])
        x = next(x for x in r["records"] if int(x["t0"]) == 2 and x["kind"] == "reveal")
        print("stored", x["stored"], "budget", x["budget"], "scalars",
              {k: x[k] for k in ("energy_value", "fcess_value", "wear", "stored_value", "value", "money", "penalty",
                                 "shortfall")})
        print("hard max", float(np.max(x["hard"])), "soft", x["soft_names"], x["soft_values"],
              "caps", x["cap_names"], x["cap_kinds"], x["cap_penalties"], x["cap_compliance"])
        print("net", np.round(x["net"], 3))
        print("soc", np.round(x["soc"], 2))
        for k in ("reg_raise", "cont_raise", "reg_lower", "cont_lower"):
            print(k, np.round(x[k], 2))
        print("prices", np.round(x["prices"], 1))
        print("fc0900", np.round(r["day_input"]["fc0900"][2:], 1))
        print("load", np.round(x["load"], 2))
        for nm, c in zip(x["cap_names"], x["caps"]):
            print("cap", nm, np.round(c, 2))
        for nm, f in zip(x["soft_names"], x["soft_floors"]):
            print("soft floor", nm, np.round(f, 2))
        print("context", {k: x[k] for k in x if k in ("config", "carry_mwh", "level", "curve_shift_mwh",
                                                       "curve_shift_early_mwh", "state_today", "state_tomorrow",
                                                       "site_ircr_mwh", "site_total_mwh")})
        bids = r["kw"]["bids"]
        print("protect", np.round(bids.protect, 2), "value", bids.value)
        print("log cols", list(r["log"].columns))
        print("steps cols", list(r["steps"].columns), len(r["steps"]))
        # 09:00 offers: e5's (this replay) against G5B's saved file
        saved = json.loads((rp.FIX / f"out_offers/rtms_{day}_0900.json").read_text())
        sdi = {it["dispatchIntervalFrom"]: it for it in
               saved["submission"]["variation"]["tradingDays"][0]["energy"]["facilities"][0]["dispatchIntervals"]}
        mine = of.request(r["day"], r["forward"], "x", "x")
        mdi = {it["dispatchIntervalFrom"]: it for it in
               mine["submission"]["variation"]["tradingDays"][0]["energy"]["facilities"][0]["dispatchIntervals"]}
        same = sum(sdi[k]["tranches"] == mdi.get(k, {}).get("tranches") for k in sdi)
        print(f"0900 offers: saved G5B DIs {len(sdi)} ({min(sdi)}..{max(sdi)}), e5 DIs {len(mdi)}; identical tranches {same}")
        diff = [k for k in sdi if sdi[k]["tranches"] != mdi.get(k, {}).get("tranches")]
        print("first differing DIs", diff[:10])
        if diff:
            k = diff[0]
            print("saved", sdi[k]["tranches"])
            print("e5   ", mdi[k]["tranches"])
        for k in (16, 19, 60, 112, 113, 200):
            if k in mdi:
                print("DI", k, [(t["quantity"], t["price"]) for t in mdi[k]["tranches"]])


if __name__ == "__main__":
    main()
