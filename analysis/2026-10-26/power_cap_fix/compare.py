"""PLAN.md P1, P2 and the reported differences: out/before_* against out/after_* (run_money.py), per scenario and
config. Money per day = the ledger's total + the month's RT7 bill saving per day (as vpp.stochastic.daily_money, but
by scenario too: the 10 MW and 8 MW runs share a config name). Writes out/compare.json and prints a summary.

Run (repo root):  .venv/bin/python analysis/2026-10-26/power_cap_fix/compare.py
"""

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
import pandas as pd  # noqa: E402

OUT = Path(__file__).parent / "out"
KEY = ["scenario", "strategy"]
PERIODS = {"样本内": ("2025-04-01", "2026-06-30"), "测试期A": ("2026-07-01", "2026-10-15"),
           "测试期B": ("2026-10-16", "2026-10-22")}
COLS = ("total", "energy", "fcess", "soc_end")


def load(tag: str) -> dict:
    led = pd.read_csv(OUT / f"{tag}_ledger.csv", parse_dates=["day"])
    bills = pd.read_csv(OUT / f"{tag}_bills.csv")
    per_day = bills.assign(bill=bills["saving"] / bills["days"])[[*KEY, "month", "bill"]]
    led["month"] = led["day"].dt.strftime("%Y-%m")
    led = led.merge(per_day, on=[*KEY, "month"], how="left")
    led["money"] = led["ledger_total"] + led["bill"]
    return {"ledger": led.set_index([*KEY, "day"]).sort_index(),
            "power": pd.read_csv(OUT / f"{tag}_power.csv", parse_dates=["day"]).set_index([*KEY, "day"]).sort_index(),
            "ircr": pd.read_csv(OUT / f"{tag}_ircr.csv").set_index(KEY)}


def main() -> None:
    b, a = load("before"), load("after")
    out, ok = {}, True
    for key in b["ledger"].index.droplevel("day").unique():
        lb, la = b["ledger"].loc[key], a["ledger"].loc[key]
        pb, pa = b["power"].loc[key], a["power"].loc[key]
        P = float(pb["power_mw"].iloc[0])
        bug_days = pb.index[pb["half_hours_over"] > 0]
        first_bug = bug_days.min() if len(bug_days) else None
        diff = (la[list(COLS)] - lb[list(COLS)]).abs()
        changed = diff.index[(diff > 1e-9).any(axis=1)]
        pre = diff[diff.index < first_bug] if first_bug is not None else diff
        r = {"power_mw": P,
             "before_days_over": int(len(bug_days)), "before_max_mw": float(pb["max_mw"].max()),
             "after_days_over": int((pa["half_hours_over"] > 0).sum()), "after_max_mw": float(pa["max_mw"].max()),
             "first_bug_day": None if first_bug is None else str(first_bug.date()),
             "bug_days": [str(d.date()) for d in bug_days],
             "p2_days_before_first_bug": int(len(pre)), "p2_max_abs_diff": float(pre.max().max()) if len(pre) else 0.0,
             "days_changed": int(len(changed)), "first_changed": None if not len(changed) else str(changed.min().date()),
             "ircr_mw": [float(b["ircr"].loc[key, "ircr_mw"]), float(a["ircr"].loc[key, "ircr_mw"])],
             "ircr_saving_per_year": [float(b["ircr"].loc[key, "saving_per_year"]), float(a["ircr"].loc[key, "saving_per_year"])],
             "periods": {}}
        r["P1"] = r["after_days_over"] == 0 and r["after_max_mw"] <= P + 1e-6 and r["before_days_over"] >= (3 if P == 10 else 1)
        r["P2"] = r["p2_max_abs_diff"] <= 1e-9
        for name, (lo, hi) in PERIODS.items():
            m = (lb.index >= lo) & (lb.index <= hi)
            n = int(m.sum())
            d = {c: float((la.loc[m, c] - lb.loc[m, c]).sum()) for c in ("ledger_total", "bill", "money")}
            r["periods"][name] = {"days": n, "days_changed": int(((lb.index >= lo) & (lb.index <= hi) & lb.index.isin(changed)).sum()),
                                  **{f"d_{c}_total": v for c, v in d.items()},
                                  **{f"d_{c}_per_day": v / n if n else 0.0 for c, v in d.items()},
                                  "money_before_per_day": float(lb.loc[m, "money"].mean()),
                                  "money_after_per_day": float(la.loc[m, "money"].mean())}
        ok &= r["P1"] and r["P2"]
        out[" | ".join(key)] = r
    (OUT / "compare.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    for k, r in out.items():
        print(f"\n{k}: P1 {'PASS' if r['P1'] else 'FAIL'} (before {r['before_days_over']} days, max {r['before_max_mw']:.3f} MW; "
              f"after {r['after_days_over']} days, max {r['after_max_mw']:.3f} MW); P2 {'PASS' if r['P2'] else 'FAIL'} "
              f"({r['p2_days_before_first_bug']} days before {r['first_bug_day']}, max diff {r['p2_max_abs_diff']:.2e})")
        print(f"  days changed {r['days_changed']} from {r['first_changed']}; bug days {r['bug_days']}; "
              f"IRCR MW {r['ircr_mw']}, saving/yr {[round(x) for x in r['ircr_saving_per_year']]}")
        for p, x in r["periods"].items():
            print(f"  {p}: {x['days']} days, {x['days_changed']} changed; $/day market {x['d_ledger_total_per_day']:+.2f}, "
                  f"bill {x['d_bill_per_day']:+.2f}, money {x['d_money_per_day']:+.2f} (before {x['money_before_per_day']:.1f})")
    print("\nALL PASS" if ok else "\nSOME FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
