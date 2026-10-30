"""Test double for the `claude` command, used only by agents/agent_tests.py: prints a canned stream-json run chosen by
FAKE_CLAUDE_SCENARIO, writes into FAKE_CLAUDE_TASK the files such a run would, and saves its arguments to
FAKE_CLAUDE_ARGS.

Scenarios: ok; preflight; denials (keeps being refused); no_start (dies before its first turn); outside (also writes
outside the task folder); claims_lie (says it ran tests it never ran).
"""

import json
import os
import sys
import time
from pathlib import Path


def emit(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def tool(tid: str, name: str, tool_input: dict, reply: str, denied: bool = False) -> None:
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": name,
                                                        "input": tool_input}]}})
    if denied:
        emit({"type": "system", "subtype": "permission_denied", "tool_name": name, "tool_use_id": tid})
        reply = f"Permission to use {name} has been denied because Claude Code is running in don't ask mode."
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": reply,
                                                    "is_error": denied}]}})


def finish(report: dict) -> None:
    emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 4, "total_cost_usd": 0.01,
          "duration_ms": 2000, "result": "做完了。", "structured_output": report, "permission_denials": []})


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")   # as Claude Code writes its events
    args = sys.argv[1:]
    if os.environ.get("FAKE_CLAUDE_ARGS"):
        Path(os.environ["FAKE_CLAUDE_ARGS"]).write_text(json.dumps(args, ensure_ascii=False), encoding="utf-8")
    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "ok")
    task = os.environ.get("FAKE_CLAUDE_TASK", "")
    session = args[args.index("--session-id") + 1] if "--session-id" in args else "resumed"
    emit({"type": "system", "subtype": "init", "session_id": session, "permissionMode": "dontAsk"})
    if scenario == "no_start":
        emit({"type": "system", "subtype": "api_retry", "attempt": 1, "error": "unknown"})
        return 1
    if scenario == "denials":
        for i in range(10):
            tool(f"d{i}", "Bash", {"command": 'python -c "print(1)"'}, "", denied=True)
            time.sleep(0.3)
        return 0
    python = f".venv/Scripts/python.exe {task}/_preflight.py"
    if scenario == "preflight":
        lines = Path("AGENTS.md").read_text(encoding="utf-8").splitlines()
        version = next((x for x in lines if x.startswith("版本")), "看不到 AGENTS.md")
        Path(task, "_preflight_ok.md").write_text(f"开始，code，{version}\n", encoding="utf-8")
        tool("w1", "Write", {"file_path": f"{task}/_preflight_ok.md"}, "ok")
        tool("b1", "Bash", {"command": python}, "preflight ok")
        finish({"status": "done", "summary_zh": "自检通过", "deliverables": [], "commands_run": [], "tests": {"ran": False},
                "blocked": []})
        return 0
    Path(task, "STATUS.md").write_text("开始 code\n完成\n", encoding="utf-8")
    tool("w1", "Write", {"file_path": f"{task}/STATUS.md"}, "ok")
    tool("b1", "Bash", {"command": python}, "preflight ok")
    if scenario == "outside":
        Path("vpp", "touched.txt").write_text("changed by the run\n", encoding="utf-8")
    report = {"status": "done", "summary_zh": "做完了。", "deliverables": [{"path": f"{task}/STATUS.md", "what": "进度"}],
              "commands_run": [{"command": python, "ok": True}], "tests": {"ran": False}, "blocked": []}
    if scenario == "claims_lie":
        tests = f".venv/Scripts/python.exe {task}/test_demo.py"
        report["commands_run"].append({"command": tests, "ok": True})
        report["tests"] = {"ran": True, "command": tests, "passed": 8, "failed": 0}
    finish(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
