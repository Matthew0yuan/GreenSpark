"""Build Pitch.html: the six-screen pitch story, from saved model outputs only.

Sources (read-only):
  ../ui_redesign/data/site_summary.json   Q2A forecast error and Q2B uplift, three periods
  ../ui_redesign/data/fleet.json          Hot Season 2025-26 calls, value by party, holdouts, constraints, stress tests
  ../customer_kwh_value/out/daily.csv     per day, 09:00: what 1 kWh of event export is worth to the customer and to
                                          Zenith (default window 17:30-19:30)
Cross-checks the kWh summary against the reviewed table in ../customer_kwh_value/RESULTS.md (section 2) and stops on
a mismatch; also checks the season's calls and the parties' split add up.
Usage: .venv/Scripts/python.exe analysis/2026-10-26/pitch/build_pitch.py
"""

import csv
import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
UI = HERE.parent / "ui_redesign" / "data"
KWH = HERE.parent / "customer_kwh_value"
WINDOW = "17:30-19:30"
OFFER, SCEN = "custom", "S3"   # the pitch's main case: the model's offer, homes reached through Synergy (TPA)
PERIOD_LABEL = {"样本内": "in-sample", "测试期 已看过": "test, seen", "测试期 未看过": "test, unseen"}


def load(name: str) -> dict:
    return json.loads((UI / name).read_text(encoding="utf-8"))


def period_label(p: str) -> str:
    return next(v for k, v in PERIOD_LABEL.items() if p.startswith(k))


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def r1(x: float) -> float:
    return round(float(x), 1)


site, fleet = load("site_summary.json"), load("fleet.json")

# Q2A and Q2B: three periods each
q2a = [{**p["period"], "model": p["model"], "baseline": p["baseline"], "change_pct": p["change_pct"]}
       for p in site["q2a"]["periods"]]
q2b = [{**p["period"], "baseline": p["baseline_aud_per_day"], "optimizer": p["optimizer_aud_per_day"],
        "uplift": p["uplift_aud_per_day"], "uplift_pct": p["uplift_pct"], "ci95": p.get("ci95_aud_per_day")}
       for p in site["q2b"]["periods"]]

# the value of 1 kWh, per day at 09:00
rows = [r for r in csv.DictReader((KWH / "out" / "daily.csv").open(encoding="utf-8")) if r["window"] == WINDOW]
by_day: dict[str, dict] = {}
for r in rows:
    d = by_day.setdefault(r["day"], {"day": r["day"], "period": period_label(r["period"]),
                                     "hot": r["hot_season"] == "True",
                                     "cmin": r1(r["d_customer_min_c"]), "energy": r1(r["b_energy_0900_c"])})
    if r["scenario"] == SCEN:  # S1 has no capacity value, so it never flags; the flag rule is the same in S2a-S3
        d["flagged"] = r["flagged"] == "True"
    d["zmax_" + r["scenario"]] = r1(r["d_zenith_max_0900_c"])
    d["ircr_" + r["scenario"]] = r1(r["c_ircr_0900_c"])
days = sorted(by_day.values(), key=lambda d: d["day"])
for d in days:
    for k in ("cmin", "zmax_S2b", "zmax_S3"):
        if not math.isfinite(d[k]):
            sys.exit(f"{d['day']}: {k} missing")

year = [r for r in rows if r["period"].startswith("样本内")]
def avg(col: str, scen: str, flagged: bool) -> float:
    return mean([float(r[col]) for r in year if r["scenario"] == scen and (r["flagged"] == "True") == flagged])
n_flag = sum(1 for r in year if r["scenario"] == "S3" and r["flagged"] == "True")
n_other = sum(1 for r in year if r["scenario"] == "S3" and r["flagged"] != "True")
kwh = {"period": {"label": "in-sample", "segment": "contract year 2025-26", "from": "2025-07-01", "to": "2026-06-30",
                  "days": n_flag + n_other},
       "window": WINDOW, "flagged_days": n_flag, "other_days": n_other,
       "customer_min": {"flagged": avg("d_customer_min_c", "S3", True), "other": avg("d_customer_min_c", "S3", False)},
       "zenith_max": {s: {"flagged": avg("d_zenith_max_0900_c", s, True), "other": avg("d_zenith_max_0900_c", s, False)}
                      for s in ("S2b", "S3")},
       "energy": {"flagged": avg("b_energy_0900_c", "S3", True), "other": avg("b_energy_0900_c", "S3", False)},
       "self_use": {"flagged": avg("a_self_use_c", "S3", True), "other": avg("a_self_use_c", "S3", False)},
       "ref_pay_c": next(o["pay_c_per_kwh"] for o in fleet["offers"] if o["id"] == "ref"),
       "custom_pay_c": 85}

print(f"kWh summary: customer min {kwh['customer_min']['flagged']:.1f}c (flagged) / {kwh['customer_min']['other']:.1f}c; "
      f"Zenith max S2b {kwh['zenith_max']['S2b']['flagged']:.1f}c, S3 {kwh['zenith_max']['S3']['flagged']:.1f}c (flagged); "
      f"other days S2b {kwh['zenith_max']['S2b']['other']:.1f}c, S3 {kwh['zenith_max']['S3']['other']:.1f}c; "
      f"{n_flag} flagged + {n_other} other days")
expected = {"customer min, flagged": (kwh["customer_min"]["flagged"], 22.3),
            "Zenith max S2b, flagged": (kwh["zenith_max"]["S2b"]["flagged"], 319),
            "Zenith max S3, flagged": (kwh["zenith_max"]["S3"]["flagged"], 157),
            "Zenith max S2b, other": (kwh["zenith_max"]["S2b"]["other"], -1.3),
            "Zenith max S3, other": (kwh["zenith_max"]["S3"]["other"], -4.8)}
for k, (got, want) in expected.items():  # the reviewed RESULTS.md section 2 table, rounded as printed there
    tol = 0.5 if abs(want) >= 100 else 0.05
    if abs(round(got, 0 if abs(want) >= 100 else 1) - want) > tol:
        sys.exit(f"cross-check failed: {k} = {got:.2f}, RESULTS.md says {want}")
print("cross-check against customer_kwh_value/RESULTS.md section 2: OK")

# Hot Season 2025-26: the 09:00 call rule, custom offer under S3
sc = fleet["season_calls"]
season = {"period": sc["period"], "official_peak_days": sc["summary"]["official_peak_days"],
          "calls": sc["summary"]["calls"][f"{OFFER}_{SCEN}"],
          "cap": next(o for o in fleet["offers"] if o["id"] == OFFER)["max_calls_per_year"],
          "covered": sc["summary"]["peak_days_covered"][f"{OFFER}_{SCEN}"],
          "missed": sc["summary"]["peak_days_missed"][f"{OFFER}_{SCEN}"],
          "days": [{"day": d["day"], "fc": d["forecast_peak_mw"], "thr": d["threshold_mw"],
                    "called": d[f"called_{OFFER}_s3"], "peak": d["ircr_peak_day"], "actual": d["actual_peak_mw"]}
                   for d in sc["days"]]}
if season["calls"] != sum(d["called"] for d in season["days"]):
    sys.exit("season calls do not add up")

# who gains: the main case and the S2b comparison, contract year 2025-26
def party(scen: str) -> dict:
    r = next(r for r in fleet["value_by_party"]["rows"] if r["offer"] == OFFER and r["scenario"] == scen
             and r["period"].get("segment") == "contract year 2025-26")
    total = r["zenith_aud"] + r["customers_aud"] + r["retailer_aud"] + r["other_aud"]
    if abs(total - r["value_created_aud"]) > 1:
        sys.exit(f"{scen}: parties do not add up to value created")
    return {k: r[k] for k in ("period", "calls", "zenith_aud", "customers_aud", "retailer_aud", "other_aud",
                              "value_created_aud", "transfers_detail_aud", "homes", "zenith_aud_per_enrolled_home",
                              "customers_aud_per_enrolled_home", "peak_days_covered")}

v = fleet["validation"]
hold = next(h for h in v["holdouts"] if h["scenario"] == SCEN and h["chronological"] and h["case"].startswith(OFFER))
YEAR = "contract year 2025-26"
cons = [c for c in v["constraints"] if c["offer"] == OFFER and c["scenario"] == SCEN and c["period"].get("segment") == YEAR]
stress = [s for s in v["stress"] if s["offer"] == OFFER and s["scenario"] == SCEN and s["period"].get("segment") == YEAR]
if not cons or not stress:
    sys.exit("no constraint or stress rows for the contract year")
trust = {"holdout": {k: hold[k] for k in ("held_out_season", "zenith_realised_held_out_aud",
                                          "zenith_expected_from_training_aud_per_year", "peak_days_covered_of_4",
                                          "customers_realised_held_out_aud_per_enrolled_home", "period")},
         "constraints": {"checked": len(cons), "violations": sum(c["violations"] for c in cons),
                         "labels": [c["label"] for c in cons], "period": cons[0]["period"]},
         "stress": {"run": len(stress), "period": stress[0]["period"],
                    "failed": [{k: s[k] for k in ("stress", "calls", "zenith_aud", "peak_days_covered_of_4")}
                               for s in stress if s["fails"]]}}
if sum(d["flagged"] for d in days if d["period"] == "in-sample") != n_flag:
    sys.exit("flagged days do not match the year's count")

data = {"generated": fleet["generated"], "case": {"offer": OFFER, "scenario": SCEN}, "q2a": q2a, "q2b": q2b,
        "kwh": kwh, "days": days, "season": season, "parties": {s: party(s) for s in ("S3", "S2b")}, "trust": trust,
        "sources": {"site_summary": "analysis/2026-10-26/ui_redesign/data/site_summary.json",
                    "fleet": "analysis/2026-10-26/ui_redesign/data/fleet.json",
                    "kwh": "analysis/2026-10-26/customer_kwh_value/out/daily.csv (window 17:30-19:30)"}}
(HERE / "pitch_data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")

blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
html = (HERE / "pitch_src.html").read_text(encoding="utf-8").replace("/*PITCH_DATA*/null", blob)
(HERE / "Pitch.html").write_text(html, encoding="utf-8")
print(f"wrote Pitch.html ({len(html) / 1024:.0f} KB): {len(days)} days, {len(season['days'])} season days, "
      f"holdout {trust['holdout']['held_out_season']}, {trust['constraints']['checked']} constraints, "
      f"{trust['stress']['run']} stress tests ({len(trust['stress']['failed'])} fail)")
