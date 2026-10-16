"""Price forecast the dispatcher plans on, issued at 08:15 for the same trading day (days_ahead=0).

Chosen on the validation period (2025-04 .. 2025-09), see reports/findings.md:
  energy  50/50 blend of (AEMO + ML correction + weather) and (ML + weather, no AEMO inputs)
  FCESS   28-day average profile of the same slot (our FCESS models lost money against it)
Also keeps "same as yesterday" prices for the replay baseline. Walk-forward with monthly refits from
FORECAST_START, exactly as vpp.aemo_correction.

Run:  .venv/bin/python -m vpp.forecast
"""

import pandas as pd

from vpp import aemo_correction as ac
from vpp.data import PROCESSED, PRODUCTS
from vpp.evaluate import period_of

FORECAST_START = pd.Timestamp("2025-04-01 08:00")  # first day of the validation period
FCESS = ["reg_raise", "cont_raise", "reg_lower", "cont_lower"]  # RoCoF always clears at $0
OUT = PROCESSED / "forecast_dispatch.parquet"


def energy_blend(frame: pd.DataFrame, days_ahead: int = 0, start: pd.Timestamp = FORECAST_START) -> pd.DataFrame:
    correction = ac.walk_forward(frame, "energy", days_ahead, weather=True, start=start)
    with_aemo = frame.loc[correction.index, "aemo_energy"] + correction
    cols = [c for c in ac.features_for("energy", weather=True) if not c.startswith("aemo")]
    no_aemo = ac.walk_forward(frame, "energy", days_ahead, start=start, cols=cols, residual=False)
    return pd.DataFrame({"energy_with_aemo": with_aemo, "energy_no_aemo": no_aemo.reindex(with_aemo.index)})


def build(days_ahead: int = 0, start: pd.Timestamp = FORECAST_START) -> pd.DataFrame:
    df30 = pd.read_parquet(PROCESSED / "wem_30min.parquet")
    runs = pd.read_parquet(PROCESSED / "aemo_predispatch.parquet")
    weather = pd.read_parquet(PROCESSED / "weather_30min.parquet")
    frame = ac.build_frame(df30, runs, weather)
    parts = energy_blend(frame, days_ahead, start)
    sub = frame.loc[parts.index]
    out = sub[["issue_date", "ts", "days_ahead", "target_date"]].assign(
        energy=(parts["energy_with_aemo"] + parts["energy_no_aemo"]) / 2, **parts,
        **{p: sub[f"prof28_{p}"] for p in FCESS},
        **{f"yday_{p}": sub[f"prev_{p}"] for p in ["energy"] + FCESS},
        **{f"actual_{p}": sub[f"actual_{p}"] for p in PRODUCTS},
    )
    return out.sort_values("ts").reset_index(drop=True)


def main() -> None:
    fc = build()
    fc.to_parquet(OUT)
    err = (fc["energy"] - fc["actual_energy"]).abs()
    period = period_of(fc["target_date"])
    for name in ("validation", "test"):
        m = period == name
        print(f"{name}: {fc.loc[m, 'target_date'].nunique()} days, energy MAE {err[m].mean():.2f} "
              f"(yesterday {(fc.loc[m, 'yday_energy'] - fc.loc[m, 'actual_energy']).abs().mean():.2f})")
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
