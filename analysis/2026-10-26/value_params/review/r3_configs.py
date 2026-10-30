"""Reviewer, CHANGED code: does the harness's new guard refuse a configuration that existed before?
For every configuration in vpp.v2.backtest (each STEPS entry's configs and SENS) that uses a value table, on m1 as
route_days sees it (hs.scenario first, so usable_mwh applies):
  - the table name the new harness looks up, against the old harness's key (level stat, the dispatcher's MWh, hurdle,
    state, regime) put through the old name formula;
  - where that table is at hand (the scratchpad's tables/ built with the changed code, else the main workspace's
    data/processed/, built with the original code, read only), hs.value_inputs runs its check (vl.check_table);
  - one day of route_days for e5, G5B, the 36 MWh usable-energy sensitivity and the mean-level one.
Writes review/r3_configs.json.
Run (worktree root):  /Users/matthew.yuan/Desktop/HGS/.venv/bin/python analysis/2026-10-26/value_params/review/r3_configs.py
"""

import json

import _rc

_rc.use("new")
from vpp import backtest as bt  # noqa: E402
from vpp.v2 import backtest as vb  # noqa: E402
from vpp.v2 import harness as hs  # noqa: E402
from vpp.v2 import value as vl  # noqa: E402

_path = vl.path


def old_name(stat, energy_mwh, hurdle, state, regime) -> str:
    return (f"v2_value_{stat}{'' if energy_mwh == 40.0 else f'_{energy_mwh:g}mwh'}"
            f"{'' if hurdle == bt.HURDLE else f'_h{hurdle:g}'}{'_state' if state else ''}"
            f"{'_regime' if regime else ''}.npz")


def at_hand(*a, **k):
    name = _path(*a, **k).name
    for d in (_rc.NEW_TABLES, _rc.MAIN / "data/processed"):
        if (d / name).exists():
            return d / name
    return _rc.NEW_TABLES / name  # missing: value_inputs raises FileNotFoundError


def main() -> None:
    vl.path = at_hand
    configs = {}
    for spec in vb.STEPS.values():
        for c in spec.get("configs", ()):
            configs.setdefault(c.name, c)
    for c, _ in vb.SENS:
        configs.setdefault(c.name, c)
    sc0 = vb.m1()
    out = {"configs": {}}
    for name, cfg in configs.items():
        if not (cfg.value == "relative" or cfg.ladder):
            continue
        sc = hs.scenario(sc0, cfg)
        hurdle = bt.HURDLE if cfg.hurdle is None else cfg.hurdle
        old = old_name(cfg.level_stat, sc.asset.battery.energy_mwh, hurdle, cfg.state, cfg.regime)
        params = hs.table_params(sc.asset, sc.site, sc.route.cmd_mw)
        new = _path(cfg.level_stat, hurdle=hurdle, state=cfg.state, regime=cfg.regime, **params.kwargs()).name
        row = {"old": old, "new": new, "same_name": old == new}
        hs.VALUE_INPUTS.clear()
        try:
            hs.value_inputs(cfg, sc.asset, sc.site, sc.route.cmd_mw)
            row["guard"] = "accepted"
        except FileNotFoundError as e:
            row["guard"] = f"table not at hand: {e}"[:160]
        except ValueError as e:
            row["guard"] = f"REFUSED: {e}"
        out["configs"][name] = row
        print(f"{name}: same name {row['same_name']} ({new}); {row['guard'][:110]}")
    days = bt.load_days("0900")[:2]
    bt._init_worker(days, bt.q2b_aux())
    out["one_day"] = {}
    for name in (vb.E5.name, vb.G5B.name, "rel+ladder 36 MWh", *(n for n in configs if "mean" in n)):
        if name not in configs:
            out["one_day"][name] = "not a configuration"
            continue
        hs.VALUE_INPUTS.clear()
        try:
            day, _, s, _, _ = next(hs.route_days(sc0, configs[name], until=days[0]["day"]))
            out["one_day"][name] = {"day": str(day.date()), "total": s["total"]}
        except Exception as e:  # noqa: BLE001  (the error is the result)
            out["one_day"][name] = f"{type(e).__name__}: {e}"
        print(name, out["one_day"][name])
    (_rc.HERE / "r3_configs.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
