"""Reviewer, CHANGED code: edge cases of the names, the digest, the rebuild command and the guard (no table built,
nothing written outside review/).
  1  path() names: defaults and the old option combinations as the old formula; import >= CMD shares the default's
     name; CMD below the import limit or import below the CMD gets a digest; ints and floats give the same digest
  2  command(): parsed back through the CLI flags it gives the same key (so the printed rebuild command builds the
     table the harness asks for), for several parameter sets
  3  check_table on the reviewer's tables: a record with another hurdle or state is refused; a 36 MWh table without
     a record at the 36 MWh name is taken; a record copied from another table is refused
  4  parameters out of range raise ValueError
Writes review/r4_edge.json.
Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/review/r4_edge.py
"""

import json
import shutil

import _rc

_rc.use("new")
from vpp import backtest as bt  # noqa: E402
from vpp.v2 import value as vl  # noqa: E402

PLANT = _rc.SCR / "planted"


def old_name(stat="median", energy_mwh=40.0, hurdle=bt.HURDLE, state=False, regime=False) -> str:
    return (f"v2_value_{stat}{'' if energy_mwh == 40.0 else f'_{energy_mwh:g}mwh'}"
            f"{'' if hurdle == bt.HURDLE else f'_h{hurdle:g}'}{'_state' if state else ''}"
            f"{'_regime' if regime else ''}.npz")


def parse(cmd: str) -> vl.TableParams:
    a = cmd.split()[3:]
    return vl.TableParams(**{vl.CLI[f]: float(v) for f, v in zip(a, a[1:]) if f in vl.CLI})


def refused(fn) -> str:
    try:
        fn()
        return "accepted"
    except ValueError as e:
        return f"ValueError: {str(e)[:120]}"


def main() -> None:
    out = {}
    names = {}
    for kw in (dict(), dict(stat="mean"), dict(energy_mwh=36.0), dict(hurdle=0.0), dict(hurdle=25.0), dict(hurdle=50.0),
               dict(hurdle=50.0, state=True), dict(hurdle=50.0, state=True, regime=True),
               dict(energy_mwh=36.0, hurdle=50.0, state=True), dict(stat="mean", energy_mwh=30.0, hurdle=75.0)):
        names[json.dumps(kw)] = vl.path(**kw).name == old_name(**kw)
    names["import 15 == default"] = vl.path(import_limit_mw=15.0).name == old_name()
    names["import 9 == default"] = vl.path(import_limit_mw=9.0).name == old_name()
    names["import 8.99 has digest"] = "_p" in vl.path(import_limit_mw=8.99).name
    names["CMD 8 has digest"] = "_p" in vl.path(cmd_mw=8.0).name
    names["CMD 8 import 8 == CMD 8 import 12"] = vl.path(cmd_mw=8.0, import_limit_mw=8.0).name == vl.path(cmd_mw=8.0).name
    names["int 8 == float 8.0"] = vl.TableParams(power_mw=8).digest() == vl.TableParams(power_mw=8.0).digest()
    names["energy 36 + power 8 keeps _36mwh and adds digest"] = vl.path(energy_mwh=36.0, power_mw=8.0).name
    out["names"] = names
    cmds = {}
    for p in (vl.TableParams(power_mw=8.0), vl.TableParams(cmd_mw=10.0), vl.TableParams(cmd_mw=13.0),
              vl.TableParams(import_limit_mw=8.0), vl.TableParams(cmd_mw=10.0, import_limit_mw=9.5),
              vl.TableParams(cmd_mw=7.0, import_limit_mw=12.0), vl.TableParams(load_scale=12.0, round_trip=0.9),
              vl.TableParams(power_mw=7.5, energy_mwh=30.0, round_trip=0.9, wear_per_mwh=35.0, cmd_mw=10.5,
                             import_limit_mw=9.5, load_scale=11.0)):
        c = vl.command(p, "median", 50.0, True)
        cmds[c] = parse(c).key() == p.key()
    out["command_round_trip"] = cmds
    # 3: the guard against planted records
    PLANT.mkdir(exist_ok=True)
    src8 = _rc.NEW_TABLES / vl.path(hurdle=50.0, state=True, power_mw=8.0).name
    p8 = vl.TableParams(power_mw=8.0)
    inputs8 = vl.load(src8)
    g = {}
    g["8 MW own record"] = refused(lambda: vl.check_table(src8, inputs8, p8, "median", 50.0, True, False))
    g["8 MW record, asked hurdle 75"] = refused(lambda: vl.check_table(src8, inputs8, p8, "median", bt.HURDLE, True, False))
    g["8 MW record, asked state False"] = refused(lambda: vl.check_table(src8, inputs8, p8, "median", 50.0, False, False))
    g["8 MW record, asked rt 0.88"] = refused(lambda: vl.check_table(src8, inputs8, vl.TableParams(power_mw=8.0, round_trip=0.88),
                                                                      "median", 50.0, True, False))
    # a CMD 10 table carrying a copy of the 8 MW record
    srcc = _rc.NEW_TABLES / vl.path(hurdle=50.0, state=True, cmd_mw=10.0).name
    fake = PLANT / srcc.name
    if not fake.exists():
        fake.symlink_to(srcc)
    shutil.copyfile(src8.with_suffix(".json"), fake.with_suffix(".json"))
    g["CMD 10 table with the 8 MW record, asked CMD 10"] = refused(
        lambda: vl.check_table(fake, vl.load(fake), vl.TableParams(cmd_mw=10.0), "median", 50.0, True, False))
    # a 36 MWh table without its record at the 36 MWh name (as the original code built them)
    src36 = _rc.NEW_TABLES / vl.path(hurdle=50.0, state=True, energy_mwh=36.0).name
    bare = PLANT / src36.name
    if not bare.exists():
        bare.symlink_to(src36)
    if bare.with_suffix(".json").exists():
        bare.with_suffix(".json").unlink()
    g["36 MWh table, no record"] = refused(
        lambda: vl.check_table(bare, vl.load(bare), vl.TableParams(energy_mwh=36.0), "median", 50.0, True, False))
    g["36 MWh table, no record, asked 36 MWh at 8 MW"] = refused(
        lambda: vl.check_table(bare, vl.load(bare), vl.TableParams(energy_mwh=36.0, power_mw=8.0), "median", 50.0, True, False))
    out["guard"] = g
    bad = {}
    for kw in (dict(power_mw=0.0), dict(round_trip=1.2), dict(round_trip=0.0), dict(wear_per_mwh=-1.0), dict(cmd_mw=0.0),
               dict(energy_mwh=37.5), dict(energy_mwh=0.5), dict(load_scale=-1.0)):
        bad[json.dumps(kw)] = refused(lambda kw=kw: vl.TableParams(**kw))
    out["out_of_range"] = bad
    (_rc.HERE / "r4_edge.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
