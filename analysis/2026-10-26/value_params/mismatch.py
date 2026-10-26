"""PLAN.md, report item 8: what happens when the table does not match the battery, after the change (the original
code's run is out/pre_silent.json: an 8 MW battery ran G5B on the 10 MW table without a word).

Each case runs G5B through the first backtest day (hs.route_days), the value tables looked up in a given directory
(vl.path's directory moved there, the name still path()'s), and records the error or the day's result:
  a  8 MW battery, only the 10 MW tables at hand (out/ref/, as in pre_silent.py)  -> FileNotFoundError + command
  b  the 10 MW table checked for the 8 MW battery (vl.check_table)                 -> ValueError (no record)
  c  planted: the 8 MW table's record says 10 MW (out/planted/)                     -> ValueError
  d  positive control: 8 MW battery, its own table (out/tables/)                    -> runs
  e  import limit 15 MW (>= CMD): the default table; 8 MW (< CMD): a table of its own
  f  planted: a 40-segment table at the 36 MWh name, no record (out/planted/)       -> ValueError (segments)
  g  a route without a CMD, a scenario without a site                               -> ValueError
Writes out/mismatch.json.

Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/mismatch.py
"""

import json
import os
from dataclasses import replace

import _common

from vpp import backtest as bt
from vpp.v2 import backtest as vb
from vpp.v2 import harness as hs
from vpp.v2 import value as vl

OUT = _common.OUT
_path = vl.path
where = {"dir": OUT / "tables"}


def redirected(*a, **k):
    return where["dir"] / _path(*a, **k).name


def with_battery(sc, **kw):
    return replace(sc, asset=replace(sc.asset, battery=replace(sc.asset.battery, **kw)))


def one_day(sc, directory, first_day) -> dict:
    where["dir"] = directory
    hs.VALUE_INPUTS.clear()
    try:
        day, _, s, _, _ = next(hs.route_days(sc, vb.G5B, until=first_day))
        return {"error": None, "day": str(day.date()), "total": s["total"], "soc_end": s["soc_end"]}
    except Exception as e:  # noqa: BLE001  (the error is the result)
        return {"error": f"{type(e).__name__}: {e}"}


def main() -> None:
    vl.path = redirected
    days = bt.load_days("0900")[:3]
    bt._init_worker(days, bt.q2b_aux())
    first = days[0]["day"]
    sc = vb.m1()
    sc8 = with_battery(sc, power_mw=8.0)
    res = {}
    res["a 8 MW, only the 10 MW tables (out/ref)"] = one_day(sc8, OUT / "ref", first)
    params8 = hs.table_params(sc8.asset, sc8.site, sc8.route.cmd_mw)
    ref_b = OUT / "ref" / "v2_value_median_h50_state.npz"
    try:
        vl.check_table(ref_b, vl.load(ref_b), params8, "median", 50.0, True, False)
        res["b the 10 MW table checked for 8 MW"] = {"error": None}
    except ValueError as e:
        res["b the 10 MW table checked for 8 MW"] = {"error": f"ValueError: {e}"}
    # c: the real 8 MW table, its record planted to say 10 MW
    planted = OUT / "planted"
    planted.mkdir(exist_ok=True)
    real8 = OUT / "tables" / _path(hurdle=50.0, state=True, power_mw=8.0).name
    link = planted / real8.name
    if not link.exists():
        os.symlink(real8, link)
    rec = json.loads(real8.with_suffix(".json").read_text())
    rec["params"]["power_mw"] = 10.0
    link.with_suffix(".json").write_text(json.dumps(rec, indent=1))
    res["c planted: the 8 MW table's record says 10 MW"] = one_day(sc8, planted, first)
    res["d positive control: 8 MW, its own table"] = one_day(sc8, OUT / "tables", first)
    res["d' positive control: 10 MW, the default table"] = one_day(sc, OUT / "tables", first)
    res["e import 15 MW (>= CMD)"] = one_day(replace(sc, site=replace(sc.site, import_limit_mw=15.0)), OUT / "tables", first)
    res["e' import 8 MW (< CMD)"] = one_day(replace(sc, site=replace(sc.site, import_limit_mw=8.0)), OUT / "tables", first)
    # f: a 40-segment table at the 36 MWh name, without a record (the old segment check, still there)
    link36 = planted / _path(hurdle=50.0, state=True, energy_mwh=36.0).name
    if not link36.exists():
        os.symlink(OUT / "ref" / "v2_value_median_h50_state.npz", link36)
    res["f planted: 40 segments at the 36 MWh name"] = one_day(with_battery(sc, energy_mwh=36.0), planted, first)
    res["g a route without a CMD"] = one_day(replace(sc, route=replace(sc.route, cmd_mw=None)), OUT / "tables", first)
    res["g' a scenario without a site"] = one_day(replace(sc, site=None), OUT / "tables", first)
    pre = json.loads((OUT / "pre_silent.json").read_text())
    out = {"before (out/pre_silent.json)": {"error": pre["error"], "tables_looked_up": pre["tables_looked_up"],
                                             "days": pre["days"]}, "after": res}
    (OUT / "mismatch.json").write_text(json.dumps(out, indent=1))
    for k, v in res.items():
        print(f"{k}: {v.get('error') or 'ran: ' + json.dumps(v)}")


if __name__ == "__main__":
    main()
