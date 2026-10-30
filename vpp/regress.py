"""Regression baselines for dispatcher changes: record what a set of backtest runs produces, then check that a
later version of the code produces exactly the same (reports/v2_plan.md, step 0).

Sets (all on short stretches of days, so a full record takes a minute or two):
  q2b-smoke  vpp.backtest.q2b_inputs(smoke=True): 30 days of 2026-01 on six 10 MW routes (m1, capacity credits,
             SCM, Western Power NCESS, PPA backup, standalone) with their strategies
  m1-paths   the same days on m1 for the code paths the v2 hooks touch: the storage-value terminal and layered
             offers ("|vt|bid"), the fixed spike reserve ("|res=20") and hourly re-planning ("|plan=roll")
  legacy30   the 1 MW study (vpp.backtest.LEGACY) on its first 30 days
Each set keeps the daily rows, the per-interval logs and, for the 10 MW sets, the float32 meters, the ledger and
the bills, under data/processed/regress/<tag>/.

Run:  python -m vpp.regress record <tag> [--set q2b-smoke,m1-paths,legacy30]
      python -m vpp.regress compare <tag a> <tag b>      exit status 1 if anything differs
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from vpp import backtest as bt
from vpp import contracts as ct
from vpp import site_bill as sb
from vpp.data import PROCESSED

OUT = PROCESSED / "regress"
SETS = ("q2b-smoke", "m1-paths", "legacy30")
M1_PATHS = (f"{bt.VALUE}|vt|bid", f"{bt.VALUE}|res=20", f"{bt.VALUE}|plan=roll")
KEYS = {"daily": ["scenario", "strategy", "day"], "ledger": ["scenario", "strategy", "day"],
        "logs": ["scenario", "strategy", "day", "slot"], "bills": ["scenario", "strategy", "month"],
        "ircr": ["scenario", "strategy"]}


def _inputs(which: str) -> tuple[list[dict], list, dict | None, float | None]:
    """(days, tasks, shared inputs, CMD the factory would nominate without a battery) of a set."""
    if which == "legacy30":
        return bt.load_days()[:30], bt.legacy_tasks(), None, None
    days, tasks, aux = bt.q2b_inputs(smoke=True)
    if which == "m1-paths":
        from vpp import storage_value as sv
        aux["storage"] = sv.build()[0]
        m1 = bt._sc("m1", ct.Route("m1", cmd_mw=9.0, ircr="dynamic"), M1_PATHS)
        tasks = bt.q2b_tasks([m1], keep=("m1",))
    return days, tasks, aux, sb.cmd_kva(bt.factory_load(days).max()) * sb.PF / 1000


def _flat(frame: pd.DataFrame) -> pd.DataFrame:
    """Columns parquet stores as they are: periods as text."""
    return frame.assign(**{c: frame[c].astype(str) for c in frame.columns if isinstance(frame[c].dtype, pd.PeriodDtype)})


def record(tag: str, which: str) -> None:
    days, tasks, aux, cmd_nb = _inputs(which)
    daily, logs, meters = bt.run_tasks(tasks, days, aux)
    out = OUT / tag
    out.mkdir(parents=True, exist_ok=True)
    parts = {"daily": daily}
    frames = [x.assign(day=d, scenario=sc, strategy=name) for (sc, name), lg in logs.items() for d, x in lg.items()]
    if frames:
        parts["logs"] = pd.concat(frames, ignore_index=True)
    if meters:
        scenarios = list({id(t[0]): t[0] for t in tasks}.values())
        parts["ledger"] = bt.q2b_ledger(daily, scenarios)
        parts["bills"], parts["ircr"] = bt.q2b_bills(meters, days, scenarios, cmd_nb)
        np.savez(out / f"{which}_meters.npz", **{f"{sc}||{name}": m for (sc, name), m in meters.items()})
    for part, frame in parts.items():
        _flat(frame).to_parquet(out / f"{which}_{part}.parquet")
    print(f"{tag} / {which}: {len(days)} days, {len(tasks)} runs, " + ", ".join(f"{p} {len(f)}" for p, f in parts.items()))


def _compare_frame(x: pd.DataFrame, y: pd.DataFrame, keys: list[str]) -> list[str]:
    """Differences between two saved frames, aligned on keys: empty if they are identical."""
    x, y = x.set_index(keys).sort_index(), y.set_index(keys).sort_index()
    if not x.index.equals(y.index):
        return [f"rows differ: {len(x)} vs {len(y)}"]
    bad = [f"only in one: {c}" for c in x.columns.symmetric_difference(y.columns)]
    for col in x.columns.intersection(y.columns):
        u, v = x[col], y[col]
        if pd.api.types.is_numeric_dtype(u) or pd.api.types.is_bool_dtype(u):
            u, v = u.astype(float).to_numpy(), v.astype(float).to_numpy()
            if not np.array_equal(u, v, equal_nan=True):
                with np.errstate(invalid="ignore"):
                    diff = np.where(np.isnan(u) & np.isnan(v), 0.0, np.abs(u - v))
                bad.append(f"{col}: max |diff| {np.nanmax(diff):.3g} in {int((diff > 0).sum())} rows")
        elif not u.astype(str).equals(v.astype(str)):
            bad.append(f"{col}: text differs")
    return bad


def compare(a: str, b: str) -> bool:
    """Print what differs between two recorded tags; True if every part of every common set is identical."""
    ok = True
    for which in SETS:
        found = sorted(p.name.removeprefix(f"{which}_") for p in (OUT / a).glob(f"{which}_*"))
        if not found:
            continue
        for name in found:
            pa, pb = OUT / a / f"{which}_{name}", OUT / b / f"{which}_{name}"
            if not pb.exists():
                print(f"{which} {name}: missing in {b}")
                ok = False
                continue
            if name.endswith(".npz"):
                za, zb = np.load(pa), np.load(pb)
                bad = [k for k in set(za.files) | set(zb.files) if k not in za.files or k not in zb.files
                       or not np.array_equal(za[k], zb[k], equal_nan=True)]
            else:
                part = name.removesuffix(".parquet")
                bad = _compare_frame(pd.read_parquet(pa), pd.read_parquet(pb), KEYS[part])
            print(f"{which} {name}: {'identical' if not bad else 'DIFFERS'}")
            for line in bad[:10]:
                print(f"    {line}")
            ok &= not bad
    print("=> ALL IDENTICAL" if ok else "=> DIFFERENCES FOUND")
    return ok


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["record"]:
        which = argv[argv.index("--set") + 1].split(",") if "--set" in argv else list(SETS)
        for w in which:
            record(argv[1], w)
    elif argv[:1] == ["compare"]:
        sys.exit(0 if compare(argv[1], argv[2]) else 1)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
