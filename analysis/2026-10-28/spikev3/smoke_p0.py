"""Smoke test of the step-0 runner on 6 days (R0 and the price oracles; no tables built). Not a result.
Run: .venv/bin/python analysis/2026-10-28/spikev3/smoke_p0.py"""
import sys
from dataclasses import replace
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
import numpy as np, pandas as pd  # noqa: E401,E402
from app.engine.common import HURDLE  # noqa: E402
from vpp import backtest as bt, contracts as ct  # noqa: E401,E402
from vpp.spikev3 import oracle as orc, runner as rn  # noqa: E401,E402
from vpp.v2 import backtest as vb, value as vl  # noqa: E401,E402

def main():
    prefs = replace(bt.PREFS, hurdle_per_mwh=HURDLE)
    route = ct.Route("m1", cmd_mw=vb.CMD, ircr="dynamic")
    sc = bt._sc("m1", route, (bt.VALUE,), prefs=prefs)
    days = [d for d in bt.load_days("0900") if pd.Timestamp("2026-01-12") <= d["day"] <= pd.Timestamp("2026-01-17")]
    aux = bt.q2b_aux()
    rtp = vl.prices()
    labels, c = orc.labels(rtp), orc.spike_c(rtp)
    partners = {0: orc.partners([d["day"] for d in days])}
    E5 = replace(vb.E5, record=False)
    tasks = [rn.Task(sc, E5), rn.Task(sc, replace(E5, name="O-B"), oracle="B"),
             rn.Task(sc, replace(E5, name="O-B ctrl"), oracle="B_ctrl"), rn.Task(sc, replace(E5, name="O-C-ef"), oracle="C_ef")]
    spec = {"labels": labels, "partners": partners, "c": c, "probe_days": [d["day"] for d in days]}
    daily, logs, meters, probes = rn.run(tasks, days, aux, spec)
    print("c =", round(c, 2), "| days:", [str(d["day"].date()) for d in days])
    print(daily.pivot_table(index="day", columns="strategy", values="total").round(1).to_string())
    by = {d["day"]: d for d in days}
    for p in probes:
        d = by[p["day"]]
        print(p["run"], p["day"].date(), "fc changed slots:", int((p["fc_reveal"] != d["fc0900"]).sum()),
              "spike slots:", int(orc.spike_slots(np.asarray(d["rtp"], float)).sum()), "state:", p.get("state_today"))
    app = pd.read_csv(ROOT / "analysis/2026-10-28/app_factory_h50/daily.csv", parse_dates=["day"])
    app = app[(app["strategy"] == E5.name) & app["day"].isin([d["day"] for d in days])]
    print("first day R0 vs app (only day 1 comparable, SoC start differs after):",
          float(daily[(daily.strategy == E5.name)].iloc[0]["total"]), float(app.iloc[0]["total"]))

if __name__ == "__main__":
    main()
