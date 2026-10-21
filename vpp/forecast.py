"""Price forecast the dispatcher plans on, for the same trading day (days_ahead=0).

Chosen on the validation period (2025-04 .. 2025-09), see reports/findings.md:
  energy  50/50 blend of (AEMO + ML correction + weather) and (ML + weather, no AEMO inputs)
  FCESS   28-day average profile of the same slot (our FCESS models lost money against it)
Also keeps "same as yesterday" prices for the replay baseline. Walk-forward with monthly refits from
FORECAST_START, exactly as vpp.aemo_correction.

Issue time. The original forecast (OUT, "08:15") used AEMO's 08:00 run before it was public and some
weather not yet public (vpp.aemo_correction). OUT_0900 is issued at 09:00 (vpp.spike.protocol.ISSUE)
from inputs public by then; its energy forecast covers 09:00 .. 07:30 (slots 2-47), and the
dispatcher bridges 08:00-09:00 with a plan made at 08:00 (vpp.backtest). FCESS profiles and
"yesterday" prices use only complete trading days, so they are known at 08:00 and kept for all slots.

Run:  .venv/bin/python -m vpp.forecast [--legacy]
"""

import sys

import numpy as np
import pandas as pd

from vpp import aemo_correction as ac
from vpp.data import PROCESSED, PRODUCTS
from vpp.evaluate import period_of

FORECAST_START = pd.Timestamp("2025-04-01 08:00")  # first day of the validation period
FCESS = ["reg_raise", "cont_raise", "reg_lower", "cont_lower"]  # RoCoF always clears at $0
OUT = PROCESSED / "forecast_dispatch.parquet"
OUT_0900 = PROCESSED / "forecast_dispatch_0900.parquet"
ISSUE = pd.Timedelta(hours=9)  # = vpp.spike.protocol.ISSUE
ENERGY = ["energy", "energy_with_aemo", "energy_no_aemo"]


def energy_blend(frame: pd.DataFrame, days_ahead: int = 0, start: pd.Timestamp = FORECAST_START) -> pd.DataFrame:
    correction = ac.walk_forward(frame, "energy", days_ahead, weather=True, start=start)
    with_aemo = frame.loc[correction.index, "aemo_energy"] + correction
    cols = [c for c in ac.features_for("energy", weather=True) if not c.startswith("aemo")]
    no_aemo = ac.walk_forward(frame, "energy", days_ahead, start=start, cols=cols, residual=False)
    return pd.DataFrame({"energy_with_aemo": with_aemo, "energy_no_aemo": no_aemo.reindex(with_aemo.index)})


def build(days_ahead: int = 0, start: pd.Timestamp = FORECAST_START, issue: pd.Timedelta | None = None,
          frame: pd.DataFrame | None = None) -> pd.DataFrame:
    """frame: the feature frame built with the same `issue` (ac.build_frame), when the caller has it."""
    if frame is None:
        df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
        runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
        weather = pd.read_parquet(PROCESSED / "weather_30min.parquet")
        frame = ac.build_frame(df30, runs, weather, issue=issue)
    parts = energy_blend(frame, days_ahead, start)
    sub = frame.loc[parts.index]
    out = sub[["issue_date", "ts", "days_ahead", "target_date"]].assign(
        energy=(parts["energy_with_aemo"] + parts["energy_no_aemo"]) / 2, **parts,
        **{p: sub[f"prof28_{p}"] for p in FCESS},
        **{f"yday_{p}": sub[f"prev_{p}"] for p in ["energy"] + FCESS},
        **{f"actual_{p}": sub[f"actual_{p}"] for p in PRODUCTS},
    )
    if issue is not None:
        out["issue_time"] = sub["issue_time"]
        out.loc[(out["ts"] < out["issue_time"]).to_numpy(), ENERGY] = np.nan  # intervals that start before the issue
    return out.sort_values("ts").reset_index(drop=True)


def main(argv: list[str] | None = None) -> None:
    legacy = "--legacy" in (sys.argv[1:] if argv is None else argv)
    fc, out = (build(), OUT) if legacy else (build(issue=ISSUE), OUT_0900)
    fc.to_parquet(out)
    err = (fc["energy"] - fc["actual_energy"]).abs()
    yday = (fc["yday_energy"] - fc["actual_energy"]).abs()
    period = period_of(fc["target_date"])
    for name in ("validation", "test"):
        m = (period == name) & fc["energy"].notna().to_numpy()
        print(f"{name}: {fc.loc[m, 'target_date'].nunique()} days, energy MAE {err[m].mean():.2f} (yesterday {yday[m].mean():.2f})"
              + ("" if legacy else ", intervals 09:00-07:30"))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
