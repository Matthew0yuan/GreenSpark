"""Replace the same-day v1 baseline in data/site_days.json with the fixed-code replay from site_summary_replay.py.

site_days_export.py took v1 from reports/v2_test_daily.csv, which predates the 2026-10-26 power-cap fix. The fix changes
v1 on 2025-12-05, 2025-12-08 and 2026-02-02, so on 2026-02-02 the published baseline is A$596.50 too low and the
day's uplift too high. The full-period numbers in site_summary.json already use the fixed code; this makes the two
demo days agree with them. The old value is kept in the record. Also checks that e5 matches the outcome.
Run from the repo root: .venv/Scripts/python.exe analysis/2026-10-26/ui_redesign/export/site_days_baseline_fix.py"""
import csv
import json
import pathlib

HERE = pathlib.Path(__file__).parent
DAYS = HERE.parent / "data" / "site_days.json"
money = {r["day"]: r for r in csv.DictReader(open(HERE / "site_summary_replay_money.csv", encoding="utf-8"))}

days = json.loads(DAYS.read_text(encoding="utf-8"))
for d in days:
    row = money[d["day"]]
    e5, v1 = float(row["rw50+state t5+roll"]), float(row["v1"])
    assert abs(e5 - d["outcome"]["money_aud"]["net"]) < 1e-6, (d["day"], e5, d["outcome"]["money_aud"]["net"])
    b = d["baseline_same_day"]
    old = b.get("net_aud_published", b["net_aud"])
    b.update({
        "net_aud": v1,
        "net_aud_published": old,
        "status": "backtest",
        "source": "analysis/2026-10-26/ui_redesign/export/site_summary_replay_money.csv column v1 (fixed code, "
                  "site_summary_replay.py); published value from reports/v2_test_daily.csv kept as net_aud_published",
        "note": ("fixed-code replay; the published (old-code) baseline was A$%.2f, which went over 10 MW on this day"
                 % old) if abs(v1 - old) > 0.005 else "fixed-code replay; equal to the published value",
    })
    for k in ("market_net_aud", "network_aud", "energy_aud", "fcess_aud", "wear_aud"):
        if abs(v1 - old) > 0.005:
            b.pop(k, None)   # these components are old-code values; only the net is replayed here
    d["gaps"] = [g for g in d.get("gaps", []) if g.get("field") != "baseline_same_day"]
    print(d["day"], "e5", round(e5, 2), "v1", round(v1, 2), "published v1", round(old, 2), "uplift", round(e5 - v1, 2))
DAYS.write_text(json.dumps(days, ensure_ascii=False, indent=1), encoding="utf-8")
