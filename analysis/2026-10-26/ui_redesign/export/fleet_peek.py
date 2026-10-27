"""Read-only summary of data/fleet.json (prints only).

Run from the repository root:  .venv/Scripts/python.exe analysis/2026-10-26/ui_redesign/export/fleet_peek.py
"""
import sys

sys.dont_write_bytecode = True
import json  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
f = json.loads((ROOT / "analysis/2026-10-26/ui_redesign/data/fleet.json").read_text(encoding="utf-8"))
for k, v in f.items():
    print(f"{k:16s} {len(json.dumps(v, ensure_ascii=False)) / 1024:7.1f} KB")
print()
for row in f["value_by_party"]["rows"]:
    p = row["period"]
    print(f"{row['offer']:6s} {row['scenario']:3s} {p['label']:11s} {p['from']}..{p['to']} ({p['days']:3d} d) calls={row['calls']:2d} "
          f"Z={row['zenith_aud']:>13,.0f} C={row['customers_aud']:>11,.0f} R={row['retailer_aud']:>11,.0f} "
          f"O={row['other_aud']:>11,.0f} V={row['value_created_aud']:>13,.0f} T={row['transfers_aud']:>11,.0f} "
          f"homes={row['homes']} Z/h={row['zenith_aud_per_enrolled_home']} C/h={row['customers_aud_per_enrolled_home']}")
print()
for row in f["value_by_party"]["expected_year"]:
    print(row["offer"], row["scenario"], row["zenith_aud"], row["zenith_aud_per_enrolled_home"], row["customers_aud_per_enrolled_home"], row["homes"])
print()
sc = f["season_calls"]
print(json.dumps(sc["summary"], indent=1))
for d in sc["days"][:6] + [x for x in sc["days"] if x["ircr_peak_day"]]:
    print(d)
print()
print(json.dumps(f["per_call"]["by_offer"][:3], indent=1)[:1500])
print(json.dumps(f["ircr"], indent=1)[:1800])
for c in f["validation"]["constraints"][:8]:
    print(c["offer"], c["scenario"], c["period"]["label"], c["id"], c["violations"], c["worst"])
print(len(f["validation"]["constraints"]), "constraint rows; total violations",
      sum(c["violations"] for c in f["validation"]["constraints"]))
for h in f["validation"]["holdouts"]:
    print(h["scenario"], h["held_out_season"], h["chronological"], h["case"][:6], h["zenith_realised_held_out_aud"], h["peak_days_covered_of_4"])
print(json.dumps(f["gaps"], indent=1, ensure_ascii=False)[:3000])
print(len(f["value_by_party"]["ledger_lines"]), "ledger lines")
print(f["value_by_party"]["ledger_lines"][0])
