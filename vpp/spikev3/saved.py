"""The saved spikev3 model (analysis/2026-10-28/spikev3/model, written by save_model.py there): per task, a LightGBM
booster trained on every label up to 2026-06-30 and a Platt calibrator fitted on its own out-of-sample predictions of
2025-10-01 .. 2026-06-30 (the refit month 2026-10 of the walk-forward). Inputs are the rows of the point-in-time spike
table joined with vpp.spikev3.features (see MODEL.md there for the columns).
"""

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

MODEL_DIR = Path(__file__).resolve().parents[2] / "analysis" / "2026-10-28" / "spikev3" / "model"
EPS = 1e-6


def load(days_ahead: int, model_dir: Path = MODEL_DIR) -> tuple[lgb.Booster, dict]:
    spec = json.loads((model_dir / "spikev3.json").read_text())[f"d{days_ahead}"]
    return lgb.Booster(model_file=str(model_dir / spec["booster"])), spec


def predict(rows: pd.DataFrame, days_ahead: int, model_dir: Path = MODEL_DIR, calibrated: bool = True) -> np.ndarray:
    """Spike probabilities (30-minute RTP >= $200) for rows of one task."""
    booster, spec = load(days_ahead, model_dir)
    p = booster.predict(rows[spec["features"]])
    if not calibrated:
        return p
    q = np.clip(p, EPS, 1 - EPS)
    z = spec["platt"]["coef"] * np.log(q / (1 - q)) + spec["platt"]["intercept"]
    return 1 / (1 + np.exp(-z))
