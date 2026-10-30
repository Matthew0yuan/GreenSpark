"""Runs the Gate Closure configuration (e5 with offers locked 15 minutes ahead: vpp.v2.backtest.G5B) over every day with
vpp.v2.offers.Collector, and writes (PLAN.md):
  out/daily.csv         the days' results (V0b against reports/v2_g5_daily.csv)
  out/steps.parquet     one row per 5-min step: the dispatcher's decisions and the offers' MW at the step's price (V2, V4)
  out/validation.csv    per day and request (Gate Closure offers, 09:00 offers): AEMO schema and rule errors (V1a, V1b)
  out/errors_sample.txt the first errors, if any
  out/start_state.pkl   each day's stored energy and months' highest import at its start (V3 replays a day)
  out/tranches_test.csv the Gate Closure offers' tranches over the test period
  out/rtms_<day>_gate.json, out/rtms_<day>_0900.json   the requests of KEEP days

Run (repo root):  .venv/bin/python analysis/2026-10-26/market_offer/run_offers.py [--days N]
"""

import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from vpp import backtest as bt  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import harness as hs  # noqa: E402
from vpp.v2 import offers as of  # noqa: E402

OUT = Path(__file__).parent / "out"
SCHEMA = ROOT / "data/raw/aemo_rtms/rtms_openapi.json"
KEEP = {pd.Timestamp("2026-02-02"), pd.Timestamp("2026-10-20")}  # an IRCR peak day with a spike; a recent test day
TEST_FROM = pd.Timestamp("2026-07-01")


def check(req: dict, root: dict, P: float) -> list[str]:
    return of.schema_errors(req, root["createSubmissionJsonRequest"], root) + of.rule_errors(req, P)


def main(argv: list[str]) -> None:
    OUT.mkdir(exist_ok=True)
    days = bt.load_days("0900")
    if "--days" in argv:
        days = days[:int(argv[argv.index("--days") + 1])]
    bt._init_worker(days, bt.q2b_aux())
    sc, cfg = vb.m1(), vb.G5B
    bat = hs.scenario(sc, cfg).asset.battery
    root = json.loads(SCHEMA.read_text())["components"]["schemas"]
    col = of.Collector(bat)
    months = {d["day"]: d["slot_month"] for d in days}
    soc, mtd, start, daily, valid, sample, tranches = 0.0, {}, {}, [], [], [], []
    t0 = time.time()
    for day, _, summary, log, _ in hs.route_days(sc, cfg, on_step=col, on_day=col.on_day):
        start[day] = (soc, dict(mtd))
        soc = summary["soc_end"]
        for m, x in zip(months[day], log["meter_mw"].to_numpy()):
            mtd[m] = max(mtd.get(m, 0.0), x)
        daily.append({"day": day, **{k: summary[k] for k in ("energy", "fcess", "wear", "total", "soc_end",
                                                             "discharged_mwh", "triggers", "replans")}})
        reqs = {"gate": of.request(day, col.submitted.pop(day), f"{of.ASSUMED['facilityCode']}-{day.date()}-gate",
                                   "Offers as locked at each DI's Gate Closure (a log: one row per DI)"),
                "0900": of.request(day, col.forward.pop(day), f"{of.ASSUMED['facilityCode']}-{day.date()}-0900",
                                   "Offers at 09:00 from the 09:00 plan, for the DIs still open")}
        col.leaked.pop(day, None)
        for name, req in reqs.items():
            errs = check(req, root, bat.power_mw)
            valid.append({"day": day, "request": name, "errors": len(errs),
                          "dis": len(req["submission"]["variation"]["tradingDays"][0]["energy"]["facilities"][0]
                                     ["dispatchIntervals"])})
            sample += [f"{day.date()} {name}: {e}" for e in errs[:3]]
            if day in KEEP:
                (OUT / f"rtms_{day.date()}_{name}.json").write_text(json.dumps(req, indent=1))
        if day >= TEST_FROM:
            for it in reqs["gate"]["submission"]["variation"]["tradingDays"][0]["energy"]["facilities"][0][
                    "dispatchIntervals"]:
                tranches += [{"day": day.date(), "di": it["dispatchIntervalFrom"], "tranche": x["tranche"],
                              "mw": x["quantity"], "price": x["price"]} for x in it["tranches"]]
        if len(daily) % 50 == 0:
            print(f"{day.date()}: {len(daily)} days, {time.time() - t0:.0f} s", flush=True)
    pd.DataFrame(daily).to_csv(OUT / "daily.csv", index=False)
    pd.DataFrame(col.steps).to_parquet(OUT / "steps.parquet")
    pd.DataFrame(valid).to_csv(OUT / "validation.csv", index=False)
    (OUT / "errors_sample.txt").write_text("\n".join(sample[:200]) + "\n")
    pd.DataFrame(tranches).to_csv(OUT / "tranches_test.csv", index=False)
    with open(OUT / "start_state.pkl", "wb") as f:
        pickle.dump(start, f)
    v = pd.DataFrame(valid)
    print(f"{len(daily)} days in {time.time() - t0:.0f} s; requests with errors: "
          f"{int((v['errors'] > 0).sum())} of {len(v)}")


if __name__ == "__main__":
    main(sys.argv[1:])
