"""Raw files -> point-in-time record tables, one parquet per source in data/pit/.

Every record keeps: source, source_file, record_id, version, run_at, evidence_at, available_at,
fetched_at, target_time, scenario, facility_or_service, variable, value, unit (times naive AWST).
available_at = evidence_at + vpp.spike.protocol.AVAILABILITY[source] (the features can redo it with the
DELAYED lags); the evidence per source:

predispatch  AEMO Reference Pre-Dispatch, the run labelled 08:00 of each trading day (one run per day;
             4 days where it is missing from the archive use a later run). version = dispatchDataIssueID.
             Evidence: issue ID; the public copy appeared 20 min after it on every run listed 2026-10-16.
stpasa       ST PASA daily report (Reference/High/Low[/AvailableCapacity] demand, 7-14 days; shortfall
             flags or MW). Content is "as forecast at 11AM" on the report day. Evidence: the later of the
             listing time and the newest ZIP member time (some 2023 files were re-uploaded weeks later,
             which makes them unavailable until then: conservative, never earlier).
weather      Open-Meteo Previous Runs, ECMWF IFS 0.25. `_previous_dayN` at valid time v comes from the run
             initialised at floor6h(v - N x 24h) (matched against Single Runs on 2026-10-09..11: every unique
             match had lead 24-27h for N=1 and 48-51h for N=2). Evidence: initialisation time; Open-Meteo
             served the 2026-10-16 06Z run 7.35h after initialisation.
outages      vpp.spike.outages reads the daily snapshot extracts directly (scripts/download_outages.py).

Run:  python -m vpp.spike.sources
"""

import csv
import io
import json
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from vpp.data import DATA, PROCESSED, RAW
from vpp.spike.protocol import AVAILABILITY

PIT = DATA / "pit"
COLUMNS = ["source", "source_file", "record_id", "version", "run_at", "evidence_at", "available_at", "fetched_at",
           "target_time", "scenario", "facility_or_service", "variable", "value", "unit"]


def _frame(rows: list[dict] | pd.DataFrame, **const) -> pd.DataFrame:
    df = pd.DataFrame(rows).assign(**const)
    for c in COLUMNS:
        if c not in df:
            df[c] = pd.NA
    return df[COLUMNS + [c for c in df.columns if c not in COLUMNS]]


# ---------------------------------------------------------------- pre-dispatch (08:00 run, already on disk)

PD_VARIABLES = {  # raw column -> (variable, scenario/facility, unit)
    "energy": ("price", "energy", "$/MWh"), "reg_raise": ("price", "reg_raise", "$/MW/h"),
    "reg_lower": ("price", "reg_lower", "$/MW/h"), "cont_raise": ("price", "cont_raise", "$/MW/h"),
    "cont_lower": ("price", "cont_lower", "$/MW/h"), "rocof": ("price", "rocof", "$/MWs/h"),
    "req_energy_mw": ("requirement", "energy", "MW"), "demand_level_mw": ("demand_level", "system", "MW"),
    "dpv_level_mw": ("dpv_level", "system", "MW"), "in_service_injection_mw": ("in_service_injection", "system", "MW"),
    "available_injection_mw": ("available_injection", "system", "MW"),
    "dispatch_injection_mw": ("dispatch_injection", "system", "MW"),
    "dispatch_withdrawal_mw": ("dispatch_withdrawal", "system", "MW"),
    "energy_deficit_mw": ("energy_deficit", "system", "MW"),
}


def predispatch() -> pd.DataFrame:
    rows = []
    for f in sorted((RAW / "predispatch-0800").glob("*.csv")):
        d = pd.read_csv(f, dtype={"run": str, "issued": str})
        d["source_file"] = f"DispatchSolutionPreDispatch_{f.stem}.zip:ReferencePre-DispatchSolution_{d['run'].iloc[0]}.json"
        d["fetched_at"] = pd.Timestamp(f.stat().st_mtime, unit="s").tz_localize("UTC").tz_convert("Australia/Perth").tz_localize(None)
        rows.append(d)
    d = pd.concat(rows, ignore_index=True)
    d["target_time"] = pd.to_datetime(d["interval"], utc=True).dt.tz_convert("Australia/Perth").dt.tz_localize(None)
    d["run_at"] = pd.to_datetime(d["run"], format="%Y%m%d%H%M")
    d["issued_at"] = pd.to_datetime(d["issued"], format="%Y%m%d%H%M%S")
    long = d.melt(id_vars=["source_file", "fetched_at", "target_time", "run", "run_at", "issued", "issued_at"],
                  value_vars=list(PD_VARIABLES), var_name="raw", value_name="value").dropna(subset=["value"])
    meta = pd.DataFrame(PD_VARIABLES, index=["variable", "facility_or_service", "unit"]).T
    long = long.join(meta, on="raw")
    long = long.drop(columns="raw").rename(columns={"run": "record_id", "issued": "version", "issued_at": "evidence_at"})
    return _frame(long.assign(available_at=long["evidence_at"] + AVAILABILITY["predispatch"]), source="predispatch",
                  scenario="Reference")


# ---------------------------------------------------------------- ST PASA

MARKETS = {  # names in the files -> ours
    "energy": "energy", "Energy": "energy", "Energy Shortage": "energy",
    "regulationRaise": "reg_raise", "RegulationRaise": "reg_raise", "Regulation Raise Shortage": "reg_raise",
    "regulationLower": "reg_lower", "RegulationLower": "reg_lower", "Regulation Lower Shortage": "reg_lower",
    "contingencyRaise": "cont_raise", "ContingencyRaise": "cont_raise", "Contingency Raise Shortage": "cont_raise",
    "contingencyLower": "cont_lower", "ContingencyLower": "cont_lower", "Contingency Lower Shortage": "cont_lower",
    "rocof": "rocof", "Rocof": "rocof", "RoCoF Shortage": "rocof",
}
SCENARIOS = {"Reference": "Reference", "ForecastHigh": "High", "ForecastLow": "Low", "AvailableCapacity": "AvailableCapacity",
             "Reference Load Scenario": "Reference", "High Load Scenario": "High", "Low Load Scenario": "Low",
             "Available Capacity Load Scenario": "AvailableCapacity"}
DEMAND_COLS = {"forecastOperationalDemandReference": "Reference", "ForecastOperationalDemandReference": "Reference",
               "forecastOperationalDemandHigh": "High", "forecastOperationalDemandLow": "Low",
               "forecastOperationalDemandAvailableCapacity": "AvailableCapacity",
               "Forecast Reference": "Reference", "Forecast High": "High", "Forecast Low": "Low"}


def _interval(s) -> pd.Timestamp:
    """'16-10-2026 08:00', '01-07-2024 07:30:00', '15/03/2024 08:00', '2026-10-19T06:30:00+08:00' -> AWST."""
    s = str(s).strip()
    if "T" in s:
        return pd.Timestamp(s).tz_convert("Australia/Perth").tz_localize(None)
    return pd.to_datetime(s.replace("/", "-"), format="%d-%m-%Y %H:%M" + (":%S" if s.count(":") == 2 else ""))


def _table(z: zipfile.ZipFile, name: str) -> list[list]:
    """Rows of a CSV or the first sheet of an xlsx member, as lists of strings."""
    raw = z.read(name)
    if name.endswith(".xlsx"):
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True).worksheets[0]
        return [["" if v is None else (v.strftime("%d/%m/%Y %H:%M") if hasattr(v, "strftime") else str(v)) for v in r]
                for r in ws.iter_rows(values_only=True)]
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig", "replace"))))


def _demand_from_table(rows: list[list]) -> list[dict]:
    head = rows[0]
    cols = {i: DEMAND_COLS[h.strip()] for i, h in enumerate(head) if h.strip() in DEMAND_COLS}
    if not cols or head[0].strip() != "Dispatch Interval":
        raise ValueError(f"unknown demand layout: {head[:5]}")
    out = []
    for r in rows[1:]:
        if not r or not re.match(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", r[0].strip()):
            continue
        t = _interval(r[0])
        out += [{"target_time": t, "scenario": sc, "value": float(r[i])} for i, sc in cols.items() if r[i].strip()]
    return out


def _flags_from_table(rows: list[list]) -> tuple[list[dict], list[tuple]]:
    """Old layout: 'Detail view - <scenario>' blocks, a market header row, a 'Limit 1/Limit 2' row, then the
    flagged intervals as date-times in those columns. Returns (flag records, (scenario, market) covered)."""
    r0 = next(i for i, r in enumerate(rows) if any(str(c).startswith("Detail view") for c in r))
    scen = {j: SCENARIOS[str(c).replace("Detail view - ", "").strip()] for j, c in enumerate(rows[r0]) if str(c).startswith("Detail view")}
    rm = next(i for i in range(r0 + 1, r0 + 6) if any(str(c).strip() in MARKETS for c in rows[i]))
    rl = rm + 1
    colmap = {}
    for j, c in enumerate(rows[rl]):
        lim = str(c).strip()
        if lim not in ("Limit 1", "Limit 2"):
            continue
        market_col = j if str(rows[rm][j]).strip() in MARKETS else j - 1
        s = max((k for k in scen if k <= j), default=None)
        if s is None or str(rows[rm][market_col]).strip() not in MARKETS:
            raise ValueError(f"cannot place column {j}")
        colmap[j] = (scen[s], MARKETS[str(rows[rm][market_col]).strip()], lim[-1])
    out = []
    for r in rows[rl + 1:]:
        for j, (sc, mk, lim) in colmap.items():
            if j < len(r) and re.match(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", str(r[j]).strip()):
                out.append({"target_time": _interval(r[j]), "scenario": sc, "facility_or_service": mk,
                            "variable": f"short_flag_l{lim}", "value": 1.0})
    return out, sorted({(sc, mk) for sc, mk, _ in colmap.values()})


def _flags_v1(j: list) -> tuple[list[dict], list[tuple]]:
    """2024-06-23 .. 2026-06-24: [{scenario, markets: {market: null | {..Count, dispatchInterval: {limit_1: [..]}}}}]."""
    out, covered = [], []
    for item in j:
        sc = SCENARIOS[item["scenario"]]
        for mk, v in item["markets"].items():
            covered.append((sc, MARKETS[mk]))
            for lim, times in ((v or {}).get("dispatchInterval") or {}).items():
                out += [{"target_time": _interval(t), "scenario": sc, "facility_or_service": MARKETS[mk],
                         "variable": f"short_flag_l{lim[-1]}", "value": 1.0} for t in times]
    return out, covered


def _flags_v2(j: list) -> tuple[list[dict], list[tuple], dict]:
    """2026-06-25 ..: summary rows {ShortfallIntervalsScenario, Markets: {market: count}} and detail rows
    {ActualShortfallScenario, Markets: {DispatchInterval, <Market>Shortfall MW, ...}} (Reference only)."""
    out, covered, counts = [], [], {}
    for item in j:
        if "ShortfallIntervalsScenario" in item:
            sc = SCENARIOS[item["ShortfallIntervalsScenario"]]
            counts.update({(sc, MARKETS[k]): v for k, v in item["Markets"].items()})
        else:
            sc, m = SCENARIOS[item["ActualShortfallScenario"]], item["Markets"]
            t = _interval(m["DispatchInterval"])
            for k, v in m.items():
                if k.endswith("Shortfall") and v is not None:
                    mk = MARKETS[k[:-len("Shortfall")]]
                    out.append({"target_time": t, "scenario": sc, "facility_or_service": mk, "variable": "short_mw", "value": float(v)})
                    if v > 0:
                        out.append({"target_time": t, "scenario": sc, "facility_or_service": mk, "variable": "short_flag_any", "value": 1.0})
    detail_scen = {r["scenario"] for r in out}
    covered = [k for k in counts if k[0] in detail_scen or counts[k] == 0]
    return out, covered, counts


def stpasa() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(records, per-report audit). Flag records are positives only; the audit's `covered` lists the
    (scenario, market) pairs a report covers, so a missing flag inside its horizon means 0."""
    man = pd.read_csv(RAW / "st-pasa" / "manifest.csv", parse_dates=["listed_at", "member_newest", "fetched_at"])
    man = man[man["name"].str.lower().str.endswith(".zip")]
    man["report_day"] = pd.to_datetime(man["name"].str.extract(r"(\d{4}-\d{2}-\d{2})")[0], errors="coerce")
    alt = man["report_day"].isna()  # "ST-PASA Report 23-10-2023.zip"
    man.loc[alt, "report_day"] = pd.to_datetime(man.loc[alt, "name"].str.extract(r"(\d{2}-\d{2}-\d{4})")[0], format="%d-%m-%Y")
    records, audit = [], []
    for _, m in man.sort_values(["report_day", "listed_at"]).iterrows():
        evidence = max(m["listed_at"], m["member_newest"]) if pd.notna(m["member_newest"]) else m["listed_at"]
        base = {"source_file": m["name"], "record_id": f"{m['report_day']:%Y-%m-%d}", "version": m["sha256"][:12],
                "run_at": m["report_day"] + pd.Timedelta(hours=11), "evidence_at": evidence,
                "available_at": evidence + AVAILABILITY["stpasa"], "fetched_at": m["fetched_at"]}
        a = {**base, "report_day": m["report_day"], "layout": "", "n_demand": 0, "n_flags": 0, "covered": "", "error": ""}
        try:
            z = zipfile.ZipFile(RAW / "st-pasa" / "zips" / m["name"])
            names = z.namelist()
            dem_json = [n for n in names if n.endswith("DemandForecast.json")]
            dem_csv = [n for n in names if n.endswith("DemandForecast.csv")]
            short_json = [n for n in names if n.endswith("Shortfall_Report.json")]
            old = [n for n in names if n.endswith(".csv") and "Shortfall" not in n and "DemandForecast" not in n] or \
                  [n for n in names if n.endswith(".xlsx") and "Shortfall" not in n]
            dem, flags, covered = [], [], []
            if dem_json or dem_csv:
                try:
                    j = json.loads(z.read(dem_json[0]).decode("utf-8-sig"))
                    a["layout"] = "json"
                except (ValueError, IndexError):  # 2024-11-19: a CSV body in the .json member
                    j = list(csv.DictReader(io.StringIO(z.read((dem_json or dem_csv)[0]).decode("utf-8-sig"))))
                    a["layout"] = "json(csv body)"
                dem = [{"target_time": _interval(r["dispatchInterval"]), "scenario": DEMAND_COLS[k], "value": float(v)}
                       for r in j for k, v in r.items() if k in DEMAND_COLS and v not in (None, "")]
                if short_json:
                    j = json.loads(z.read(short_json[0]).decode("utf-8-sig"))
                    if j and "ShortfallIntervalsScenario" in json.dumps(j):
                        flags, covered, counts = _flags_v2(j)
                        a["layout"] += "+short_v2"
                        a["v2_counts"] = json.dumps({f"{k[0]}|{k[1]}": v for k, v in counts.items()})
                    else:
                        flags, covered = _flags_v1(j)
                        a["layout"] += "+short_v1"
            elif old:
                xlsx = [n for n in names if n.endswith(".xlsx") and "Shortfall" not in n]
                try:
                    rows = _table(z, old[0])
                except csv.Error:  # 2024-02-21, 2024-03-05: bare newlines inside the CSV; the xlsx is intact
                    old = xlsx
                    rows = _table(z, old[0])
                dem = _demand_from_table(rows)
                flags, covered = _flags_from_table(rows)
                a["layout"] = "old_" + old[0].rsplit(".", 1)[1]
            else:
                raise ValueError(f"no known member in {names}")
            for r in dem:
                records.append({**base, **r, "facility_or_service": "system", "variable": "demand_forecast", "unit": "MW"})
            for r in flags:
                records.append({**base, **r, "unit": "MW" if r["variable"] == "short_mw" else "flag"})
            horizon = [r["target_time"] for r in dem] or [r["target_time"] for r in flags]
            a.update(n_demand=len(dem), n_flags=len(flags), covered="|".join(f"{s}:{k}" for s, k in covered),
                     horizon_start=min(horizon) if horizon else pd.NaT, horizon_end=max(horizon) if horizon else pd.NaT)
        except Exception as exc:  # unparsed layouts are listed in the audit, not skipped silently
            a["error"] = repr(exc)[:200]
        audit.append(a)
    return _frame(records, source="stpasa"), pd.DataFrame(audit)


# ---------------------------------------------------------------- weather vintages

WX_UNITS = {"temperature_2m": "degC", "shortwave_radiation": "W/m2", "cloud_cover": "%", "wind_speed_100m": "km/h"}


def weather() -> pd.DataFrame:
    """Hourly ECMWF values per vintage (scripts/download_weather_vintages.py). Radiation is the mean over
    the hour before its label, so it is stored at the start of that hour, as vpp.data.load_weather does."""
    fetched = pd.read_csv(RAW / "weather-vintages" / "fetched.csv", parse_dates=["fetched_at"]).set_index("location")["fetched_at"]
    parts = []
    for loc in fetched.index:
        df = pd.read_csv(RAW / "weather-vintages" / f"{loc}.csv", parse_dates=["time"])
        long = df.melt(id_vars="time", var_name="col", value_name="value").dropna(subset=["value"])
        parts.append(long.assign(facility_or_service=loc, source_file=f"weather-vintages/{loc}.csv", fetched_at=fetched[loc]))
    w = pd.concat(parts, ignore_index=True)
    w[["variable", "n"]] = w.pop("col").str.extract(r"(.+)_previous_day(\d)")
    n = w["n"].astype(int).to_numpy()
    init_utc = (w["time"] - pd.Timedelta(hours=8) - pd.to_timedelta(24 * n, unit="h")).dt.floor("6h")
    w["run_at"] = init_utc + pd.Timedelta(hours=8)
    w["target_time"] = w["time"] - pd.to_timedelta(np.where(w["variable"] == "shortwave_radiation", 1, 0), unit="h")
    w["record_id"] = "ecmwf_ifs025_" + init_utc.dt.strftime("%Y%m%d%HZ")
    w["version"] = "previous_day" + w.pop("n")
    w["evidence_at"] = w["run_at"]
    w["available_at"] = w["run_at"] + AVAILABILITY["weather"]
    w["unit"] = w["variable"].map(WX_UNITS)
    return _frame(w.drop(columns="time"), source="weather", scenario="ecmwf_ifs025")


def main() -> None:
    PIT.mkdir(parents=True, exist_ok=True)
    wx = weather()
    wx.to_parquet(PIT / "weather.parquet", index=False)
    print(f"weather: {len(wx):,} records, vintages {wx['version'].value_counts().to_dict()}")
    pd_rec = predispatch()
    pd_rec.to_parquet(PIT / "predispatch.parquet", index=False)
    print(f"predispatch: {len(pd_rec):,} records, {pd_rec['record_id'].nunique()} runs")
    st_rec, st_audit = stpasa()
    st_rec.to_parquet(PIT / "stpasa.parquet", index=False)
    st_audit.to_csv(PIT / "stpasa_audit.csv", index=False)
    print(f"stpasa: {len(st_rec):,} records from {st_audit['error'].eq('').sum()} reports; "
          f"{st_audit['error'].ne('').sum()} unparsed; layouts {st_audit['layout'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
