"""Manager audit of task tool logs; no model execution or external requests."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from agents.check import parse_log

result = {}
for task in ("mvp_architecture", "mvp_product_design"):
    task_path = f"analysis/2026-10-25/{task}/"
    logs = ROOT / "agents/logs" / f"analysis__2026-10-25__{task}"
    runs = []
    for log in sorted(logs.glob("*.jsonl")):
        if "preflight" in log.name:
            continue
        parsed = parse_log(log)
        writes = parsed["writes"]
        bad_writes = []
        for name in writes:
            p = Path(name)
            if not p.is_absolute():
                p = ROOT / p
            if not p.resolve().is_relative_to((ROOT / task_path).resolve()):
                bad_writes.append(name)
        runs.append({"log": log.name, "result_present": parsed["result"] is not None,
                     "denials": parsed["denials"], "writes_outside_own_task": bad_writes,
                     "note_file_read": any(Path(p).name.lower() == "note.md" for p in parsed["reads"]),
                     "tools": parsed["tools"]})
    result[task] = runs

out = Path(__file__).with_name("TOOL_AUDIT.json")
out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
