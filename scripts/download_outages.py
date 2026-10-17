"""Download AEMO Real-Time Outage snapshots as they were public at each forecast issue time.

Every 30 minutes AEMO writes the whole outage register (every outage since market start, latest
version of each) to /public/market-data/outages/realtime-outages/GeneratorOutages_<YYYYMMDDHHMM>.csv.
The listing time is ~1 minute after the label (99.9% of 53,417 files; batches delayed by up to 151
minutes in Feb 2024 show the listing keeps the real upload time), so a file is a snapshot of what was
public then. For the forecast issued at ISSUE_HOUR on trading day D we take the newest file listed
before that time - normally the 08:30 snapshot, listed 08:31.

The files grow to ~30 MB, so each is parsed in memory and only rows that can matter for the forecast
are kept: outages overlapping [issue - 7 days, issue + 9 days] or submitted/modified in the 7 days
before the issue. Provenance per snapshot (file, listing time, ExtractDateTime, fetch time, sha256,
rows, columns, newest ModifiedDateTime) goes to the manifest; failures are recorded, not skipped.

Output: data/raw/realtime-outages/extracts/<YYYYMMDD>.parquet, manifest.csv, listing.csv
Usage:  python scripts/download_outages.py [START YYYY-MM-DD] [END YYYY-MM-DD]
"""

import datetime as dt
import hashlib
import io
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aemo_listing import fetch, listing  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "realtime-outages"
ISSUE_HOUR = 9  # AWST on the trading day; vpp.spike.protocol.ISSUE
KEEP = [
    "OutageNumber", "OutageVersion", "Facility", "OutageType", "CommencementInterval", "EndInterval", "Status",
    "AtRiskflag", "FacilityRAC", "FTT_IntermittentGenerationSystem_RAC", "FTT_NonIntermittentGenerationSystem_RAC",
    "FTT_ElectricStorageResource_Capacity_RAC", "FTT_ElectricStorageResource_ObligationDuration_RAC",
    "ESS_RegulationRaise_Availability", "ESS_RegulationRaise_RAC", "ESS_ContingencyReserveRaise_Availability",
    "ESS_ContingencyReserveRaise_RAC", "RiskofExtension", "RecoveryTime_Hours", "RecoveryTime_Minutes",
    "FirstSubmissionDate", "ModifiedDateTime", "DateTimeofNotification_ForcedOutage",
    "DateTimeofNotification_LateRejectionOrRecall", "ExtractDateTime", "Description",
]
TIMES = ["CommencementInterval", "EndInterval", "FirstSubmissionDate", "ModifiedDateTime",
         "DateTimeofNotification_ForcedOutage", "DateTimeofNotification_LateRejectionOrRecall", "ExtractDateTime"]


def awst(s: pd.Series) -> pd.Series:
    """'2023-10-01T08:30:02.2639480+08:00' -> naive AWST timestamp (NaT if empty)."""
    return pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601").dt.tz_convert("Australia/Perth").dt.tz_localize(None)


def pick_snapshots(files: pd.DataFrame, days: pd.DatetimeIndex) -> pd.DataFrame:
    """Newest snapshot listed strictly before each issue time."""
    files = files.sort_values("listed_at")
    issues = pd.DataFrame({"issue_date": days, "issue_time": days + pd.Timedelta(hours=ISSUE_HOUR)})
    # newest label among files listed before the issue (listing times are monotone in the label, nearly)
    files = files.assign(best_label=files["label"].cummax())
    out = pd.merge_asof(issues.sort_values("issue_time"), files[["listed_at", "best_label"]],
                        left_on="issue_time", right_on="listed_at", allow_exact_matches=False)
    out = out.merge(files[["label", "name", "url", "listed_at"]].rename(columns={"listed_at": "file_listed_at"}),
                    left_on="best_label", right_on="label", how="left")
    return out.drop(columns=["best_label", "listed_at"])


def header_info(cols: list[str]) -> dict:
    return {"missing_columns": "|".join(c for c in KEEP if c not in cols), "extra_columns": "|".join(c for c in cols if c not in KEEP),
            "header_sha": hashlib.sha256(",".join(cols).encode()).hexdigest()[:12]}


def extract(snap: pd.Series) -> dict:
    dest = OUT / "extracts" / f"{snap['issue_date']:%Y%m%d}.parquet"
    info = {"issue_date": snap["issue_date"].strftime("%Y-%m-%d"), "issue_time": snap["issue_time"], "file": snap["name"],
            "label": snap["label"], "listed_at": snap["file_listed_at"]}
    if dest.exists():
        # provenance rebuilt from the extract and the file's header line (full-file hash and size not recomputed)
        try:
            df = pd.read_parquet(dest)
            cols = fetch(snap["url"], timeout=60, first_bytes=16384).decode("utf-8", "replace").splitlines()[0].split(",")
            return {**info, "status": "cached", "fetched_at": df["fetched_at"].iloc[0].isoformat(), "rows_kept": len(df),
                    "extract_datetimes": "|".join(df["ExtractDateTime"].dropna().astype(str).unique()[:3]),
                    "max_modified": df["ModifiedDateTime"].max(), **header_info(cols)}
        except Exception as exc:
            return {**info, "status": f"cached; header check failed: {exc!r}"}
    try:
        data = fetch(snap["url"], timeout=600)
        fetched = dt.datetime.now().isoformat(timespec="seconds")
        raw = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, na_values=[""])
        cols = list(raw.columns)
        missing = [c for c in KEEP if c not in cols]
        for c in missing:
            raw[c] = pd.NA
        df = raw[KEEP].copy()
        for c in TIMES:
            df[c] = awst(df[c])
        t = snap["issue_time"]
        lo, hi, recent = t - pd.Timedelta(days=7), t + pd.Timedelta(days=9), t - pd.Timedelta(days=7)
        keep = (((df["CommencementInterval"] <= hi) & (df["EndInterval"] >= lo))
                | (df["FirstSubmissionDate"] >= recent) | (df["ModifiedDateTime"] >= recent))
        df = df[keep].copy()
        df["Description"] = df["Description"].str.slice(0, 200)
        df = df.assign(issue_date=snap["issue_date"], snapshot_file=snap["name"], snapshot_label=snap["label"],
                       listed_at=snap["file_listed_at"], fetched_at=pd.Timestamp(fetched))
        df.to_parquet(dest, index=False)
        extract_time = raw["ExtractDateTime"].dropna().unique()
        return {**info, "status": "downloaded", "fetched_at": fetched, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "rows_total": len(raw), "rows_kept": len(df),
                "extract_datetimes": "|".join(extract_time[:3]), "max_modified": awst(raw["ModifiedDateTime"]).max(),
                **header_info(cols)}
    except Exception as exc:  # recorded in the manifest, never skipped silently
        return {**info, "status": f"error: {exc!r}"}


def main(start: dt.date, end: dt.date, workers: int = 6) -> None:
    (OUT / "extracts").mkdir(parents=True, exist_ok=True)
    files = pd.DataFrame(listing("public/market-data/outages/realtime-outages"))
    files = files[files["name"].str.fullmatch(r"GeneratorOutages_\d{12}\.csv")].copy()
    files["label"] = pd.to_datetime(files["name"].str[17:29], format="%Y%m%d%H%M")
    files.to_csv(OUT / "listing.csv", index=False)
    snaps = pick_snapshots(files, pd.date_range(start, end))
    results = []
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(extract, s) for _, s in snaps.iterrows() if pd.notna(s["name"])]
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            print(f"[{i}/{len(futures)}] {r['issue_date']} {r['file']} {r['status']}", flush=True)
    manifest = OUT / "manifest.csv"
    new = pd.DataFrame(results)
    if manifest.exists():  # a row from an earlier full download beats one rebuilt from a cached extract
        old = pd.read_csv(manifest, dtype={"issue_date": str})
        old = old[old["status"] == "downloaded"]
        new = pd.concat([old, new[~new["issue_date"].isin(old["issue_date"])]], ignore_index=True)
    new.sort_values("issue_date").to_csv(manifest, index=False)
    bad = new[new["status"].astype(str).str.startswith("error")]
    print(f"done: {len(results)} snapshots, {len(bad)} errors: {bad['issue_date'].astype(str).tolist()}")


if __name__ == "__main__":
    args = [dt.date.fromisoformat(a) for a in sys.argv[1:3]]
    main(args[0] if args else dt.date(2023, 10, 1), args[1] if len(args) > 1 else dt.date.today() - dt.timedelta(days=1))
