"""Many days at once, for the Plan page's multi-day charts: what happened on each day, and the price forecast at 09:00.

The saved backtest's half-hour log holds all of it, so a year needs no replays. It does not hold each day's 09:00 plan: its
plan_net and plan_soc are each half-hour's last plan, after the day's replans (up to 10 MW from the 09:00 plan on 2026-10-22),
so no plan is shown here."""

import numpy as np

from vpp import dispatch as dp

from .common import FIRST, N, arr, num

MAX_DAYS = 366  # a year
HALFHOUR_DAYS = 31  # up to here every half-hour is sent; beyond it one row of figures a day (more half-hours than pixels)
COLUMNS = ["day", "slot", "forecast", "rtp", "energy_mwh", "soc_start", "soc", "site_load", "meter_mw", "cap_mw"]


def _hi(v: np.ndarray, nd: int = 3):
    return num(v.max(), nd) if len(v) else None


def _lo(v: np.ndarray, nd: int = 3):
    return num(v.min(), nd) if len(v) else None


def day_row(day, log, money: dict, factory: bool, known: int = N, halfhours: bool = False, band: tuple = (None, None)) -> dict:
    """One trading day. known: its half-hours already over, on a day in progress: the log runs the rest on the forecast, and
    none of that is shown. Averages run from 09:00, the hours the forecast covers; highs and lows over the whole day."""
    fc = log["forecast"].to_numpy()[FIRST:]
    rtp = log["rtp"].to_numpy()
    e = log["energy_mwh"].to_numpy()[:known]
    soc = np.r_[log["soc_start"].iloc[0], log["soc"].to_numpy()][:known + 1]
    after = rtp[FIRST:max(FIRST, known)]
    row = {"day": str(day.date()), "complete": known == N,
           "price": {"actual_mean": num(after.mean(), 2) if len(after) else None, "actual_lo": _lo(rtp[:known], 2),
                     "actual_hi": _hi(rtp[:known], 2), "forecast_mean": num(fc.mean(), 2)},
           "discharged_mwh": num(np.clip(e, 0, None).sum(), 3), "charged_mwh": num(np.clip(-e, 0, None).sum(), 3),
           "stored_mwh": {"lo": _lo(soc), "hi": _hi(soc), "end": num(soc[-1], 3)},
           "site": None, "money_aud": money, "halfhours": None}
    site = {"meter": log["meter_mw"].to_numpy()[:known], "load": log["site_load"].to_numpy()[:known],
            "cap": log["cap_mw"].to_numpy()[:known]} if factory else None
    if site:  # the cap of the day: it drops for a few half-hours on a flagged IRCR peak day, which halfhours shows
        row["site"] = {"peak_mw": _hi(site["meter"]), "load_peak_mw": _hi(site["load"]), "cap_mw": _hi(site["cap"])}
    if halfhours:
        rest = [None] * (N - known)
        row["halfhours"] = {"price_forecast": [None] * FIRST + arr(fc, 2), "price_p10": band[0], "price_p90": band[1],
                            "price_actual": arr(rtp[:known], 2) + rest, "battery_mw": arr(e / dp.DT, 3) + rest,
                            "stored_mwh": arr(soc, 3) + rest,
                            "meter_mw": arr(site["meter"], 3) + rest if site else None,
                            "load_mw": arr(site["load"], 3) + rest if site else None,
                            "cap_mw": arr(site["cap"], 3) + rest if site else None}
    return row


def totals(rows: list) -> dict:
    def add(field):
        got = [r["money_aud"][field] for r in rows if r["money_aud"][field] is not None]
        return num(sum(got), 2) if got else None
    net = add("net")
    return {**{f: add(f) for f in ("energy", "fcess", "wear", "network", "market_net", "net")},
            "per_day": num(net / len(rows), 2) if net is not None else None,
            "discharged_mwh": num(sum(r["discharged_mwh"] for r in rows), 1),
            "charged_mwh": num(sum(r["charged_mwh"] for r in rows), 1)}
