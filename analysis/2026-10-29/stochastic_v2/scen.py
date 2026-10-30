"""The extended scenarios (build_scenarios.py) attached to the backtest days, as vpp.stochastic.attach does with the
old file."""

from pathlib import Path

from vpp import scenarios as sn
from vpp import stochastic as sto

FILE = Path(__file__).resolve().parent / "scenarios_0900.parquet"


def attach(days: list[dict]) -> int:
    """Give each day its scenarios from FILE; returns the days without any."""
    old, sn.OUT = sn.OUT, FILE
    try:
        return sto.attach(days)
    finally:
        sn.OUT = old
