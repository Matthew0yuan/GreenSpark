"""Rebuild the spike table up to the last trading day with prices (2026-10-22) without touching data/pit
or data/processed, and check that the rows the old table already had are unchanged (PLAN section 3).

PIT records are rebuilt by vpp.spike.sources into out/pit/; vpp.spike.features is pointed at that folder
and its build() writes nothing itself.

Output: out/pit/*.parquet, out/spike_table_ext.parquet, out/consistency.csv
Run:    .venv/bin/python analysis/2026-10-28/scarcity_signals/build_table.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from vpp.data import PROCESSED  # noqa: E402
from vpp.spike import features, sources  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
KEY = ["issue_date", "days_ahead", "target_time"]
LABELS = ["rtp", "max5", "spike", "spike300", "any5"]
MUST_MATCH = LABELS + features.GROUPS["existing"] + features.GROUPS["price_extra"]


def rebuild_pit() -> None:
    pit = OUT / "pit"
    pit.mkdir(parents=True, exist_ok=True)
    sources.weather().to_parquet(pit / "weather.parquet", index=False)
    sources.predispatch().to_parquet(pit / "predispatch.parquet", index=False)
    st, audit = sources.stpasa()
    st.to_parquet(pit / "stpasa.parquet", index=False)
    audit.to_csv(pit / "stpasa_audit.csv", index=False)
    features.PIT = pit


TOL = 1e-9   # p_std_7 is a rolling mean over the whole price history: appending days moves it by ~1e-14


def same(a: pd.Series, b: pd.Series, tol: float = 0.0) -> np.ndarray:
    if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
        x, y = a.to_numpy(float), b.to_numpy(float)
        return (np.abs(x - y) <= tol) | (np.isnan(x) & np.isnan(y))
    return ((a == b) | (a.isna() & b.isna())).to_numpy()


def consistency(new: pd.DataFrame, old: pd.DataFrame) -> pd.DataFrame:
    """Per column of the old table: exact mismatches, and those left after the two allowances (PLAN change
    log 1): numeric |diff| <= TOL, and a label that was NaN in the old table because its trading day had no
    prices yet (target days after the old table's last labelled day)."""
    n = new[new["issue_date"] <= old["issue_date"].max()].set_index(KEY).sort_index()
    o = old.set_index(KEY).sort_index()
    rows = [{"column": "_rows", "must_match": True, "old_rows": len(o), "new_rows": len(n),
             "mismatches": int(len(o.index.symmetric_difference(n.index))), "after_allowances": 0}]
    n = n.reindex(o.index)
    last_label = pd.Timestamp(o.loc[o["spike"].notna()].index.get_level_values("target_time").max())
    late = (o.index.get_level_values("target_time") > last_label) & o["spike"].isna().to_numpy()
    for c in o.columns:
        if c not in n:
            rows.append({"column": c, "must_match": c in MUST_MATCH, "mismatches": -1, "note": "missing in new table"})
            continue
        exact = ~same(o[c], n[c])
        left = ~same(o[c], n[c], TOL)
        if c in LABELS:
            left &= ~late
        first = o.index[left][0] if left.any() else None
        rows.append({"column": c, "must_match": c in MUST_MATCH, "mismatches": int(exact.sum()),
                     "after_allowances": int(left.sum()), "max_abs_diff": float(np.nanmax(np.abs(o[c] - n[c])))
                     if pd.api.types.is_numeric_dtype(o[c]) and exact.any() else 0.0,
                     "first_mismatch": str(first) if first is not None else ""})
    return pd.DataFrame(rows)


def main() -> None:
    if "--check-only" not in sys.argv:
        rebuild_pit()
        new = features.build()
        new.to_parquet(OUT / "spike_table_ext.parquet", index=False)
    new = pd.read_parquet(OUT / "spike_table_ext.parquet")
    print(f"{len(new):,} rows, issues {new['issue_date'].min().date()} .. {new['issue_date'].max().date()}")
    old = pd.read_parquet(PROCESSED / "spike_table.parquet")
    c = consistency(new, old)
    c.to_csv(OUT / "consistency.csv", index=False)
    bad = c[c["mismatches"] != 0]
    print("columns that differ on the old rows:")
    print(bad.drop(columns="first_mismatch").to_string(index=False) if len(bad) else "  none")
    must = c[c["must_match"] & (c["after_allowances"] != 0)]
    print("RESULT:", "PASS (labels, existing and price_extra unchanged)" if must.empty else f"FAIL: {must['column'].tolist()}")


if __name__ == "__main__":
    main()
