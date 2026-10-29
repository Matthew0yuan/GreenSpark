"""spikev3 step 0 (PLAN.md section 2 and change log 1): e5 at the app's hurdle behind the factory meter (route m1),
and the same with one input replaced by its perfect-foresight version.

1. Tables, into out/: the null table (today's code, the official day-before calls) and the oracle table (the same
   with the day labels). The null table is compared array by array with the official cached one: identical, O-A's
   baseline is R0; not, R0' (e5 on the null table) is run and is O-A's baseline. The oracle table's pooled arrays
   must equal the null table's and its states the labels.
2. The runs (each in its own worker): R0, R5 (the official table read without states), [R0'], O-A, O-A ctrl seeds
   0-2, O-B, O-B ctrl seeds 0-2, O-B2, O-C-e, O-C-ef.
3. Checks 1-4 of section 2.5 plus change log 1's: R0 = app_factory_h50's e5; physics; the forecast each run's plans
   used on every day (the log's forecast column) = the injected one; timing_errors 0; O-A differs from R0 where the
   label differs from the official call (a positive control); the same days in every run; nothing official changed.

Output: out/daily.csv, out/money.csv, out/bills.csv, out/ircr.csv, out/log.parquet, out/checks.csv, out/probes.csv,
        out/constants.json, out/table_check.csv, out/v2_value_median_h50_state_null.npz, ..._oracle.npz
Run:    .venv/bin/python analysis/2026-10-28/spikev3/run_p0.py
"""

import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.engine.common import HURDLE  # noqa: E402
from vpp import backtest as bt  # noqa: E402
from vpp import contracts as ct  # noqa: E402
from vpp import dispatch as dp  # noqa: E402
from vpp import site_bill as sb  # noqa: E402
from vpp import stochastic as sto  # noqa: E402
from vpp.spikev3 import oracle as orc  # noqa: E402
from vpp.spikev3 import protocol as pr  # noqa: E402
from vpp.spikev3 import runner as rn  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import value as vl  # noqa: E402

OUT = HERE / "out"
APP = ROOT / "analysis" / "2026-10-28" / "app_factory_h50"
E5 = replace(vb.E5, record=False)
NULL_TABLE = OUT / "v2_value_median_h50_state_null.npz"
ORACLE_TABLE = OUT / "v2_value_median_h50_state_oracle.npz"
POOLED = ("level", "curve", "marg", "edges", "grid_curve", "grid_level", "grid_month", "day")
SEEDS = (0, 1, 2)
TOL = 1e-6
N_PROBE = 10
MODE = {"O-B": "B", "O-B2": "B2", "O-C-e": "C_e", "O-C-ef": "C_ef", **{f"O-B ctrl s{s}": "B_ctrl" for s in SEEDS}}


def hashes() -> dict:
    files = sorted((ROOT / "data" / "processed").glob("v2_value_*.npz")) + sorted((ROOT / "reports").glob("v2_*")) + \
        sorted(p for p in APP.iterdir() if p.is_file())
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def build_tables(params, labels: pd.Series) -> None:
    kw = params.kwargs()
    energy = kw.pop("energy_mwh")
    vl.build("median", energy, hurdle=E5.hurdle, out=NULL_TABLE, state=True, **kw)
    official = vl.regimes
    try:
        vl.regimes = lambda: labels  # the day labels in place of the day-before call: estimation and states
        vl.build("median", energy, hurdle=E5.hurdle, out=ORACLE_TABLE, state=True, **kw)
    finally:
        vl.regimes = official


def compare_tables(official: Path, labels: pd.Series) -> tuple[bool, pd.DataFrame]:
    """(null table == official, rows of the table checks)."""
    a, n, o = np.load(official), np.load(NULL_TABLE), np.load(ORACLE_TABLE)
    rows = []
    for k in sorted(set(a.files) | set(n.files)):
        same = k in a.files and k in n.files and a[k].shape == n[k].shape and \
            (np.array_equal(a[k], n[k], equal_nan=True) if a[k].dtype.kind in "fc" else np.array_equal(a[k], n[k]))
        rows.append({"check": "null = official", "array": k, "same": bool(same)})
    for k in POOLED:
        same = n[k].shape == o[k].shape and (np.array_equal(n[k], o[k], equal_nan=True) if n[k].dtype.kind in "fc"
                                             else np.array_equal(n[k], o[k]))
        rows.append({"check": "oracle pooled = null pooled", "array": k, "same": bool(same)})
    days = pd.DatetimeIndex(pd.to_datetime(o["day"].astype(str)))
    lab = labels.copy()
    lab.index = pd.DatetimeIndex(lab.index).normalize()
    for k, shift in (("state_today", 0), ("state_tomorrow", 1)):
        want = lab.reindex(days + pd.Timedelta(days=shift)).to_numpy(float)
        rows.append({"check": "oracle states = labels", "array": k, "same": bool(np.array_equal(o[k], want, equal_nan=True))})
    rows.append({"check": "oracle marg_state differs from null (positive control)", "array": "marg_state",
                 "same": not np.array_equal(o["marg_state"], n["marg_state"])})
    df = pd.DataFrame(rows)
    return bool(df.loc[df["check"] == "null = official", "same"].all()), df


def main() -> None:
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    before = hashes()
    prefs = replace(bt.PREFS, hurdle_per_mwh=HURDLE)
    route = ct.Route("m1", cmd_mw=vb.CMD, ircr="dynamic")
    sc = bt._sc("m1", route, (bt.VALUE,), prefs=prefs)
    assert sc is not None, bt.REFUSED
    days = bt.load_days("0900")
    aux = bt.q2b_aux()

    rtp = vl.prices()
    labels = orc.labels(rtp)
    c = orc.spike_c(rtp)
    day_list = pd.DatetimeIndex([d["day"] for d in days])
    partners = {s: orc.partners(day_list, s) for s in SEEDS}
    fixed = sum(int(sum(k == v for k, v in p.items())) for p in partners.values())
    spiky = [d["day"] for d in days if orc.spike_slots(np.asarray(d["rtp"], float)).any()]
    probe_days = sorted(pd.DatetimeIndex(np.random.default_rng(pr.SEED).choice(spiky, N_PROBE, replace=False)))
    lab_in = labels[(labels.index >= pr.IN_START) & (labels.index <= pr.IN_END)].dropna()
    early = int(sum((np.asarray(d["rtp"][:pr.FIRST_SLOT]) >= pr.SPIKE).sum() for d in days
                    if pr.IN_START <= d["day"] <= pr.IN_END))
    constants = {"c_spike_price": c, "label_share_in_sample": float(lab_in.mean()), "label_days_in_sample": int(len(lab_in)),
                 "spike_halfhours_0800_0900_in_sample": early, "partner_fixed_points": fixed,
                 "days": len(days), "first_day": str(day_list.min().date()), "last_day": str(day_list.max().date()),
                 "probe_days": [str(d.date()) for d in probe_days], "hurdle_plan": HURDLE, "hurdle_table": E5.hurdle}
    (OUT / "constants.json").write_text(json.dumps(constants, indent=1) + "\n")
    print(f"c = {c:.2f} $/MWh; in-sample label share {constants['label_share_in_sample']:.3f}; "
          f"partner fixed points {fixed}", flush=True)

    _, params, _ = rn.table_key(sc, E5)
    official = vl.path("median", hurdle=E5.hurdle, state=True, **params.kwargs())
    t1 = time.time()
    build_tables(params, labels)
    null_same, tchk = compare_tables(official, labels)
    tchk.to_csv(OUT / "table_check.csv", index=False)
    print(f"tables built in {time.time() - t1:.0f} s; null table = official: {null_same}", flush=True)

    tasks = [
        rn.Task(sc, E5),                                                           # R0: the official name
        rn.Task(sc, replace(E5, name="R5 no-state", state=False), table=str(official)),
        rn.Task(sc, replace(E5, name="O-A"), oracle="A", table=str(ORACLE_TABLE)),
        *(rn.Task(sc, replace(E5, name=f"O-A ctrl s{s}"), oracle="A_ctrl", table=str(ORACLE_TABLE), seed=s) for s in SEEDS),
        rn.Task(sc, replace(E5, name="O-B"), oracle="B"),
        *(rn.Task(sc, replace(E5, name=f"O-B ctrl s{s}"), oracle="B_ctrl", seed=s) for s in SEEDS),
        rn.Task(sc, replace(E5, name="O-B2"), oracle="B2"),
        rn.Task(sc, replace(E5, name="O-C-e"), oracle="C_e"),
        rn.Task(sc, replace(E5, name="O-C-ef"), oracle="C_ef"),
    ]
    if not null_same:
        tasks.insert(1, rn.Task(sc, replace(E5, name="R0' null table"), table=str(NULL_TABLE)))
    spec = {"labels": labels, "partners": partners, "c": c, "probe_days": list(probe_days)}
    t1 = time.time()
    daily, logs, meters, probes = rn.run(tasks, days, aux, spec)
    print(f"{len(tasks)} runs x {len(days)} days in {time.time() - t1:.0f} s", flush=True)

    ledger = bt.q2b_ledger(daily, [sc])
    cmd_nb = sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000
    bills, ircr = bt.q2b_bills(meters, days, [sc], cmd_nb)
    money = sto.daily_money(ledger, bills)
    ledger.to_csv(OUT / "daily.csv", index=False)
    bills.to_csv(OUT / "bills.csv", index=False)
    ircr.to_csv(OUT / "ircr.csv", index=False)
    money.to_csv(OUT / "money.csv")
    log = pd.concat([x.assign(day=d, strategy=name) for (_, name), lg in logs.items() for d, x in lg.items()],
                    ignore_index=True)
    log.to_parquet(OUT / "log.parquet")

    checks = []

    def add(check, run, value, ok, note=""):
        checks.append({"check": check, "run": run, "value": value, "pass": bool(ok), "note": note})

    for chk, g in tchk.groupby("check", sort=False):
        bad = g.loc[~g["same"], "array"].tolist()
        ok = not bad if chk != "null = official" else True  # a different null table only adds R0'
        add(f"0 tables: {chk}", "tables", len(bad), ok, ", ".join(bad) if bad else "")

    # 1. R0 reproduces the app's saved e5
    app = pd.read_csv(APP / "daily.csv", parse_dates=["day"])
    app = app[app["strategy"] == E5.name]
    r0 = ledger[ledger["strategy"] == E5.name].assign(day=lambda x: pd.to_datetime(x["day"]))
    m = r0.merge(app, on="day", suffixes=("", "_app"))
    gap = max(float((m[k] - m[k + "_app"]).abs().max()) for k in ("ledger_total", "soc_end"))
    add("1 R0 = app_factory_h50 e5", E5.name, gap, len(m) == len(r0) == len(app) and gap <= TOL, f"{len(m)} days")

    # 2. physics per run, timing errors, same days
    eta = bt.Q2B_ASSET.battery.eta
    P, E = bt.Q2B_ASSET.battery.power_mw, bt.Q2B_ASSET.battery.energy_mwh
    by = log.groupby(["strategy", "day"])
    dd = daily.set_index(["strategy", "day"])
    load = (by["site_load"].sum() * dp.DT).reindex(dd.index)
    flows = ((dd["import_mwh"] - load + dd["self_mwh"]) * eta - (dd["self_mwh"] + dd["export_mwh"]) / eta
             + dd["reg_in_mwh"] * eta - dd["reg_out_mwh"] / eta)
    err = (dd["soc_end"] - by["soc_start"].first().reindex(dd.index) - flows).abs().groupby(level=0).max()
    base_days = list(daily.loc[daily["strategy"] == E5.name, "day"])
    by_day = {d["day"]: d for d in days}
    for name in daily["strategy"].unique():
        lg = log[log["strategy"] == name]
        dn = daily[daily["strategy"] == name]
        add("2a energy balance (MWh)", name, float(err[name]), err[name] < TOL)
        add("2b half-hours over 10 MW", name, int((np.abs(lg["energy_mwh"]) / dp.DT > P + TOL).sum()),
            (np.abs(lg["energy_mwh"]) / dp.DT <= P + TOL).all())
        add("2c stored energy outside 0-40 MWh", name, int(((lg["soc"] < -TOL) | (lg["soc"] > E + TOL)).sum()),
            ((lg["soc"] >= -TOL) & (lg["soc"] <= E + TOL)).all())
        add("2d meter over the month's CMD cap (half-hours, reported)", name,
            int((lg["meter_mw"] > lg["cap_mw"] + TOL).sum()), True, "reported only")
        add("2e timing errors", name, int(dn["timing_errors"].sum()), dn["timing_errors"].sum() == 0)
        add("2f same days in the same order", name, len(dn), list(dn["day"]) == base_days)
        # the forecast the plans used on every day: the injected 09:00 one from slot 2, the bridge before
        mode = MODE.get(name)
        part = partners[int(name[-1])] if mode == "B_ctrl" else None
        bad = 0
        for day, g in lg.groupby("day", sort=False):
            d = by_day[day]
            want = d["fc0900"] if mode is None else orc.inject(d, mode, c, by_day[part[day]] if part else None)["fc0900"]
            exp = np.r_[np.asarray(d["prices"]["bridge"][0][:pr.FIRST_SLOT], float), np.asarray(want, float)[pr.FIRST_SLOT:]]
            bad += int(not np.array_equal(g["forecast"].to_numpy(float), exp))
        add("3a plans' forecast = injected, every day", name, bad, bad == 0, "days that differ")

    # 3b. probe days: the reveal, the FCESS prices, the states
    official_flags = vl.regimes()
    planning = lambda x: np.maximum(np.asarray(x, float), 0) + dp.OFFER_EPS  # noqa: E731
    prows = []
    for p in probes:
        d, run = by_day[p["day"]], p["run"]
        mode = MODE.get(run)
        part = partners[int(run[-1])] if mode == "B_ctrl" else None
        want = d if mode is None else orc.inject(d, mode, c, by_day[part[d["day"]]] if part else None)
        ok_fc = p["fc_reveal"] is not None and np.array_equal(p["fc_reveal"], np.asarray(want["fc0900"], float))
        want_f = want["prices"]["bridge"][1]
        ok_f = all(np.array_equal(p["fc_f"][k], np.asarray(want_f[k], float)) for k in want_f)
        if mode == "C_ef":
            ok_f = ok_f and all(np.array_equal(p["fc_f"][k], planning(d["act_f"][k])) for k in d["act_f"])
        nxt = d["day"] + pd.Timedelta(days=1)
        if run.startswith("O-A"):
            src = (lambda x, q=partners[int(run[-1])]: q.get(x, x)) if "ctrl" in run else (lambda x: x)
            want_s = (labels.get(src(d["day"]), np.nan), labels.get(src(nxt), np.nan))
        elif run == "R5 no-state":
            want_s = (np.nan, np.nan)
        else:
            want_s = (official_flags.get(d["day"], np.nan), official_flags.get(nxt, np.nan))
        got_s = (p.get("state_today", np.nan), p.get("state_tomorrow", np.nan))
        ok_s = all((a == b) or (np.isnan(a) and np.isnan(b)) for a, b in zip(got_s, want_s))
        prows.append({"run": run, "day": d["day"].date(), "forecast_ok": ok_fc, "fcess_ok": ok_f, "states_ok": ok_s,
                      "spike_slots": int(orc.spike_slots(np.asarray(d["rtp"], float)).sum()),
                      "changed_slots": int((np.asarray(want["fc0900"]) != np.asarray(d["fc0900"])).sum())})
    prows = pd.DataFrame(prows)
    prows.to_csv(OUT / "probes.csv", index=False)
    for run, g in prows.groupby("run"):
        add("3b injection on probe days", run, int(len(g)), bool(g[["forecast_ok", "fcess_ok", "states_ok"]].all().all()),
            f"forecast slots changed: {int(g['changed_slots'].sum())}")

    # 3c. positive control: O-A differs from R0 on days whose label differs from the official call
    tot = daily.pivot_table(index="day", columns="strategy", values="total")
    differ = [dday for dday in tot.index if not ((labels.get(dday, np.nan) == official_flags.get(dday, np.nan)) or
                                                 (np.isnan(labels.get(dday, np.nan)) and np.isnan(official_flags.get(dday, np.nan))))]
    moved = int((tot.loc[differ, "O-A"] - tot.loc[differ, E5.name]).abs().gt(1e-6).sum())
    add("3c O-A moves on days where label != official call", "O-A", moved, moved > 0, f"of {len(differ)} such days")

    # 4. nothing official changed
    after = hashes()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    add("4 official files unchanged", "all", len(changed), not changed, "; ".join(changed[:5]))

    ch = pd.DataFrame(checks)
    ch.to_csv(OUT / "checks.csv", index=False)
    with pd.option_context("display.width", 220, "display.max_colwidth", 70, "display.max_rows", 300):
        print(ch.to_string(index=False))
    print("RESULT:", "ALL CHECKS PASS" if ch["pass"].all() else "SOME CHECKS FAIL", f"({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
