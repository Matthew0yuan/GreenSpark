"""Local check of NUMBERS.csv in this directory (not agents/check.py): every formula row is recomputed from the rows it
names and must match its value within 0.5%; every `check` (">= 0", "<= 1e-6", "== 0") must hold; and the planned
data-bundle size (ARCHITECTURE.md section 5.1) is recomputed from the real file sizes. Unit consistency of formulas is
checked by hand, not here. Reads only; writes CHECK_RESULT.json next to this script.

Run from the repository root:  .venv/Scripts/python.exe analysis/2026-10-25/mvp_architecture/check_numbers_local.py
"""

import sys

sys.dont_write_bytecode = True

import ast
import csv
import json
import operator
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RTOL = 0.005
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.USub: operator.neg, ast.Pow: operator.pow}

# the bundle's files (ARCHITECTURE.md section 5.1)
BUNDLE_DATA = ["wem_30min.parquet", "wem_5min.parquet", "esroi.parquet", "aemo_predispatch.parquet",
               "weather_30min.parquet", "forecast_dispatch_0900.parquet", "forecast_dispatch_0900_d1.parquet",
               "spike_risk_0900.parquet", "spike_table.parquet", "scenarios_0900.parquet",
               "v2_value_median_h50_state.npz", "v2_test_log.parquet"]
BUNDLE_REPORTS = ["v2_test_daily.csv", "v2_test_bills.csv", "v2_test_ircr.csv", "v2_test_result.csv"]


def bundle_mb() -> float:
    files = [ROOT / "data" / "processed" / f for f in BUNDLE_DATA] + [ROOT / "reports" / f for f in BUNDLE_REPORTS]
    files += sorted((ROOT / "reports" / "policy").glob("*.csv"))
    files += [p for p in (ROOT / "vpp").rglob("*.py") if "__pycache__" not in p.parts]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(missing)
    return sum(f.stat().st_size for f in files) / 1e6


def evaluate(expr: str, values: dict) -> float:
    text = re.sub(r"\{([^{}]+)\}", lambda m: f"__v{list(values).index(m.group(1))}", expr)
    names = {f"__v{i}": v for i, v in enumerate(values.values())}

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return names[node.id]
        if isinstance(node, ast.BinOp):
            return OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp):
            return OPS[type(node.op)](ev(node.operand))
        raise ValueError(f"unsupported in formula: {ast.dump(node)}")
    return ev(ast.parse(text, mode="eval"))


rows = list(csv.DictReader((HERE / "NUMBERS.csv").open(encoding="utf-8-sig", newline="")))
values = {r["name"]: float(r["value"]) for r in rows}
findings = []
for r in rows:
    v = float(r["value"])
    if r["formula"].strip():
        got = evaluate(r["formula"], values)
        ok = abs(got - v) <= RTOL * max(abs(got), abs(v), 1e-12)
        findings.append({"name": r["name"], "kind": "formula", "ok": ok, "value": v, "recomputed": got})
    if r["check"].strip():
        op, bound = r["check"].split()
        ok = {">=": v >= float(bound), "<=": v <= float(bound), "==": v == float(bound), ">": v > float(bound)}[op]
        findings.append({"name": r["name"], "kind": "check", "ok": ok, "value": v, "rule": r["check"]})
    if not r["period"].strip() or not r["unit"].strip() or r["status"] not in {"已核实", "调研", "推算", "假设", "回测"}:
        findings.append({"name": r["name"], "kind": "fields", "ok": False, "note": "period, unit or status missing"})
real = bundle_mb()
planned = values.get("bundle_planned_mb")
findings.append({"name": "bundle_planned_mb", "kind": "file sizes", "ok": planned is not None and
                 abs(real - planned) <= RTOL * real, "value": planned, "recomputed": round(real, 2)})
bad = [f for f in findings if not f["ok"]]
(HERE / "CHECK_RESULT.json").write_text(json.dumps({"rows": len(rows), "findings": findings, "failed": len(bad)},
                                                   indent=1, ensure_ascii=False), encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for f in findings:
    print(("PASS " if f["ok"] else "FAIL ") + json.dumps(f, ensure_ascii=False))
print(f"{len(rows)} rows, {len(findings)} checks, {len(bad)} failed")
sys.exit(1 if bad else 0)
