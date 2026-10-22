"""Decision records (reports/v2_plan.md, NFR-05): every plan a day adopts and what it was made from, plus the day's
v2 context (② and the value curve). Rows hold float32 arrays, so a backtest's workers can return them with their
results; a Recorder is made per day inside the worker (run_day's on_plan)."""

import numpy as np
import pandas as pd

from vpp import dispatch as dp

PLAN_ARRAYS = ("net", "soc", *dp.FCESS)
SCALARS = ("energy_value", "fcess_value", "wear", "stored_value", "value", "money", "penalty", "shortfall")


def _f32(x) -> np.ndarray:
    return np.asarray(x, dtype=np.float32).ravel()


class Recorder:
    """One day's adopted plans, as rows. context: the day's v2 inputs (the same for every row of the day)."""

    def __init__(self, day, context: dict | None = None):
        self.day, self.context, self.rows = day, dict(context or {}), []

    def __call__(self, t0: int, kind: str, plan: dict, ctx: dict) -> None:
        row = {"day": self.day, "t0": t0, "kind": kind, "stored": float(ctx["stored"]),
               "budget": np.nan if ctx.get("budget") is None else float(ctx["budget"]),
               **{k: float(plan.get(k, np.nan)) for k in SCALARS},
               **{k: _f32(plan[k]) for k in PLAN_ARRAYS if k in plan},
               "prices": _f32(ctx["prices"]), "terminal": _f32(ctx["terminal"]),
               "load": None if ctx.get("load") is None else _f32(ctx["load"]),
               "hard": None if ctx.get("hard") is None else _f32(ctx["hard"]),
               "soft_names": [n for n, _, _ in ctx["soft"]], "soft_values": [float(v) for _, _, v in ctx["soft"]],
               "soft_floors": [_f32(f).tolist() for _, f, _ in ctx["soft"]],
               "cap_names": [c[0] for c in ctx["caps"]], "cap_kinds": [c[2] for c in ctx["caps"]],
               "cap_penalties": [float(c[3]) for c in ctx["caps"]],
               "cap_compliance": [bool(c[4]) for c in ctx["caps"]], "caps": [_f32(c[1]).tolist() for c in ctx["caps"]],
               "fcess_prices": None if ctx.get("fcess") is None else {k: _f32(v) for k, v in ctx["fcess"].items()}}
        self.rows.append({**row, **self.context})


def frame(rows: list[dict]) -> pd.DataFrame:
    """Records as a table (one row per adopted plan), ready for parquet."""
    return pd.DataFrame(rows)
