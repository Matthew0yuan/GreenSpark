"""After reports/v2_test_*.csv were regenerated with the power-cap fix (2026-10-26), bring the wording in
data/site_summary.json up to date and check that the regenerated reports equal the replay values this file carries.
Run from the repo root: .venv/Scripts/python.exe analysis/2026-10-26/ui_redesign/export/site_summary_reports_updated.py"""
import json
import pathlib

import pandas as pd

HERE = pathlib.Path(__file__).parent
F = HERE.parent / "data" / "site_summary.json"
s = json.loads(F.read_text(encoding="utf-8"))

# the regenerated report must equal the replay used here, period by period
res = pd.read_csv("reports/v2_test_result.csv")
row = {"in-sample": "validation", "test-seen": "test to 2026-10-15", "test-unseen": "test from 2026-10-16"}
for p in s["q2b"]["periods"]:
    r = res[(res.period == row[p["period"]["label"]]) & (res.config == "rw50+state t5+roll") & (res.base == "v1")].iloc[0]
    assert abs(r.vs_base - p["uplift_aud_per_day"]) < 0.01, (p["period"]["label"], r.vs_base, p["uplift_aud_per_day"])
    if p["ci95_aud_per_day"]:
        assert abs(r.vs_base_lo - p["ci95_aud_per_day"][0]) < 0.05 and abs(r.vs_base_hi - p["ci95_aud_per_day"][1]) < 0.05
    print(p["period"]["label"], "report", round(r.vs_base, 2), "json", p["uplift_aud_per_day"])

s["q2b"]["note"] = s["q2b"]["note"].replace(
    "; the reports and findings.md section 27 still carry the old values.",
    "; on 2026-10-26 reports/v2_test_*.csv and findings.md section 27 were regenerated with the fixed code and now equal these values.")
s["hard_limits"]["power_cap_note"] = s["hard_limits"]["power_cap_note"].replace(
    "Published results predate the 2026-10-26 fix", "Results made before the 2026-10-26 fix")
s["gaps"] = [g for g in s["gaps"] if g["field"] != "q2b, reports/findings.md section 27 and reports/v2_test_result.csv"]
F.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
print("updated", F)
