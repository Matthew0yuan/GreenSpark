"""Shared paths, definitions and helpers for the spike-precursor study (2026-10-17).

Spike, event and period definitions are the project's (vpp.spike.protocol, vpp.evaluate):
  spike interval  30-min Reference Trading Price (RTP) >= $200/MWh
  event           runs of spike intervals, gaps of <= 1 non-spike interval merged (vpp.spike.metrics.events)
  periods         by the trading day of the event onset: train = before 2025-04-01, test = TEST_MONTHS,
                  validation = every other trading day from 2025-04-01
"Top 20%" is ranked within each market regime (old = before 2025-04-01, new = from it) by event peak RTP,
ties broken by the event's excess value; the global ranking is kept as a second definition.
All timestamps are naive AWST and label the START of an interval.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "pyproject.toml").exists())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vpp.data import PROCESSED, RAW  # noqa: E402
from vpp.spike import protocol as pc  # noqa: E402

OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

TOP_SHARE = 0.20
PRE_MIN = 300            # the precursor window: 5 hours before the onset
LOOKBACK_MIN = 600       # extracted before the onset, so lead-time features keep a full 5 h look-back
POST_MIN = 180           # extracted after the onset for controls (cases keep their whole event)
N_CONTROLS = 10          # matched controls per case
MATCH_DAYS = 28          # controls come from trading days within +/- this many days of the case
QUIET_AFTER_MIN = 120    # a control's reference interval and the next 2 h have no spike
SEED = 20261017
FIVE = pd.Timedelta(minutes=5)
HALF = pd.Timedelta(minutes=30)


def regime_of(trading_day) -> np.ndarray:
    return np.where(pd.DatetimeIndex(trading_day) < pc.EVAL_START, "old", "new")


def period_of(trading_day) -> np.ndarray:
    td = pd.DatetimeIndex(trading_day)
    return np.where(td < pc.EVAL_START, "train", pc.period_of(td))


# ---------------------------------------------------------------- facility -> fuel

COAL = {"BW1_BLUEWATERS_G2", "BW2_BLUEWATERS_G1", "COLLIE_G1", "MUJA_G5", "MUJA_G6", "MUJA_G7", "MUJA_G8"}
# combined-cycle and cogeneration units (the Kwinana HEGTs, KWINANA_GT2/GT3, are fast-start aeroderivatives that
# start and stop daily, so they count as peakers)
GAS_BASE = {"COCKBURN_CCG1", "NEWGEN_KWINANA_CCG1", "ALINTA_PNJ_U1", "ALINTA_PNJ_U2", "TIWEST_COG1", "ALCOA_WGP",
            "PPP_KCP_EG1", "SWCJV_WORSLEY_COGEN_COG1"}
OTHER = {"BIOGAS01", "HENDERSON_RENEWABLE_IG1", "RED_HILL", "ROCKINGHAM", "SOUTH_CARDUP", "TAMALA_PARK",
         "PRDSO_WALPOLE_HG1", "PHOENIX_KWINANA_WTE_G1", "KWINANA_WTE_G1", "EAST_ROCKINGHAM_WTE_G1"}


def fuel_of(code: str) -> str:
    """Fuel group of a facility code (WA names; checked against output profiles, see build_scada.py)."""
    c = code.upper()
    if c in COAL:
        return "coal"
    if "_ESR" in c or "_BESS" in c:
        return "battery"
    if "_WF" in c or c.endswith("WWF"):
        return "wind"
    if "_PV" in c or "SOLAR" in c:
        return "solar"
    if c in GAS_BASE:
        return "gas_base"
    if c in OTHER or c.endswith("_IL") or "DSP" in c:
        return "other"
    # open-cycle gas turbines and liquid-fuel peakers (Pinjar, Kemerton, Neerabup, Mungarra, Wagerup, ...)
    return "peaker"


FUELS = ["coal", "gas_base", "peaker", "wind", "solar", "battery", "other"]
