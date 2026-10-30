import sys
sys.dont_write_bytecode = True
from pathlib import Path
import csv
import json
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parents[2]))
from agents.check import check_numbers
summaries = json.loads((OUT / "LOCAL_RESULTS.json").read_text(encoding="utf-8"))
with (OUT / "NUMBERS.csv").open(encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    rows = list(reader)
for s in summaries:
    sc = s["scenario"]
    template = next(r for r in rows if r["name"] == sc + "_expected_profit")
    for name, value, formula in (
        (sc + "_expected_risk_profit", s["expected_risk_profit"], ""),
        (sc + "_risk_after_fixed", s["expected_risk_profit"] - 100000,
         "{" + sc + "_expected_risk_profit} - {" + sc + "_fixed_cost}"),
    ):
        if not any(r["name"] == name for r in rows):
            rows.append(dict(template, name=name, value=value, formula=formula, source="LOCAL_RESULTS.json；原模型fleet返回下行情景，扣同一固定成本", status="推算"))
    print(sc, "risk after fixed", s["expected_risk_profit"] - 100000)
with (OUT / "NUMBERS.csv").open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
checks = check_numbers(OUT / "NUMBERS.csv")
for level, message in checks:
    print(level, message)
assert not any(level == "FAIL" for level, _ in checks)
