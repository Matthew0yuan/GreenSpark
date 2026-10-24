"""Dispatch one task to a headless Claude Code run whose permissions fit the task, then check what it did and keep the
record (AGENTS.md, section "按任务书工作的 agent"; how to use it: agents/README.md).

A task is a folder analysis/<date>/<name>/ holding TASK.md. The run may write only inside that folder; in profile
"code" it may run Python only as `.venv/Scripts/python.exe <task>/<script>.py`; in profile "research" (or with
--web) it may search and fetch the web. Its first step is a preflight - write STATUS.md, run <task>/_preflight.py -
so a permission that does not fit stops it within a minute instead of after a long think. The launcher also
  - starts each run as a fresh print-mode session, not `claude --bg` (whose worktree is cut from main and holds only
    committed files), with AGENTS.md appended to the system prompt and the final reply held to
    agents/report_schema.json;
  - stops a run after --max-denials refused calls or after --timeout-min, and retries a run that died before its
    first turn (usually the network) in a new session;
  - writes the raw event stream to agents/logs/ (kept out of git) and RUN_SUMMARY.json, RUN_REPORT.json and
    FINAL_REPLY.md into the task folder, with every file outside the folder that changed during the run;
  - runs agents/check.py on the folder.

Run:  python -m agents.launch <task dir> --profile research|code [--web] [--model M] [--effort E]
          [--max-turns N] [--max-budget-usd X] [--timeout-min T] [--preflight-only] [--resume SESSION]
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # also runnable as a script: python agents/launch.py
    sys.path.insert(0, str(ROOT))

from agents import check as ck  # noqa: E402

PYTHON = ".venv/Scripts/python.exe"
LOGS = "agents/logs"
SCHEMA = ROOT / "agents" / "report_schema.json"
PROFILES = {"research": "联网调研，只写任务目录", "code": "写代码并用 .venv 的 Python 运行，只写任务目录"}
MAX_PROMPT = 24_000   # characters; a longer brief is read from disk instead (Windows caps a command line at 32,767)
RESUME_PROMPT = "上次运行中断了。从中断的地方接着做，仍然遵守最初的运行约定和任务书。"
# set by a Claude Code session that runs this launcher; without them the run starts as its own top-level session
PARENT_SESSION_VARS = {
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SSE_PORT", "CLAUDE_CODE_EMIT_STARTUP_TIMING",
    "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING", "CLAUDE_CODE_QUESTION_PREVIEW_FORMAT", "CLAUDE_CODE_ENABLE_TASKS",
    "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_EFFORT", "CLAUDE_PID"}
PREFLIGHT = '''"""Written by agents/launch.py to prove this run may execute Python here; removed after the run."""
import sys

import numpy
import pandas

print("preflight ok: Python", sys.version.split()[0], "| pandas", pandas.__version__, "| numpy", numpy.__version__)
'''


def find_claude(explicit: str | None) -> list[str]:
    """The command that starts Claude Code: --claude-bin, else `claude` on the PATH, else the standard install."""
    if explicit:
        return [sys.executable, explicit] if explicit.endswith(".py") else [explicit]
    home = Path.home() / ".local" / "bin"
    found = shutil.which("claude") or next((str(p) for p in (home / "claude.exe", home / "claude") if p.exists()), None)
    if not found:
        raise SystemExit("找不到 claude 命令：先装好 Claude Code，或者用 --claude-bin 指定路径")
    return [found]


def permissions(task: str, profile: str, web: bool) -> list[str]:
    """--tools (what exists) and --allowedTools (what runs without asking; in dontAsk mode everything else is
    refused). Edit(...) rules cover Write as well - a Write(...) path rule never matches - and a path without a
    leading slash is read from the working directory, the repository root. A Bash rule matches the command text,
    which is why the run is told the one form to use. NOTE.md is the user's own: refused outright."""
    tools, allow = ["Read", "Glob", "Grep", "Write", "Edit"], [f"Edit({task}/**)"]
    if profile == "code":
        tools.append("Bash")
        allow.append(f"Bash({PYTHON} {task}/*)")
    if web or profile == "research":
        tools += ["WebSearch", "WebFetch"]
        allow += ["WebSearch", "WebFetch"]
    return ["--tools", ",".join(tools), "--allowedTools", ",".join(allow), "--disallowedTools", "Read(NOTE.md)"]


def contract(task: str, profile: str, web: bool, session: str, preflight_only: bool) -> str:
    """The run's own rules, put in front of the brief (AGENTS.md goes into the system prompt separately)."""
    code = profile == "code"
    lines = [
        "# 运行约定（agents/launch.py 生成）",
        "",
        f"- 任务目录：`{task}/`；档位：{profile}（{PROFILES[profile]}）；会话 {session}，"
        f"开始于 {datetime.now():%Y-%m-%d %H:%M}。",
        "- 共用规则是仓库根目录的 AGENTS.md，已经附在系统提示里；其中“按任务书工作的 agent”一节对你适用。",
        "",
        "## 这次能做的事（别的操作都会被自动拒绝）",
        "- 读整个仓库，NOTE.md 除外。",
        f"- 写：只能用 Write 或 Edit，在 `{task}/` 里新建或修改文件。",
    ]
    if code:
        lines.append(f"- 运行 Python：只能在仓库根目录这样运行，不加 cd、不加重定向：`{PYTHON} {task}/<脚本>.py`。"
                     "临时检查也先写成脚本放进任务目录再跑；不用 `python -c`、heredoc 或管道。")
    if web or profile == "research":
        lines.append("- 联网：WebSearch、WebFetch。")
    if preflight_only:
        first = (f"用 Write 写 `{task}/_preflight_ok.md`：开始时间、档位，再照抄系统提示里 AGENTS.md 开头“版本：”那一行"
                 "（只凭系统提示，不要用 Read 去读 AGENTS.md；看不到就写“看不到 AGENTS.md”）。")
    else:
        first = f"用 Write 写 `{task}/STATUS.md`，一行就够：开始时间和档位。"
    lines += ["", "## 第一步：自检（做完再开始任务）", f"1. {first}"]
    if code:
        lines.append(f"2. 运行 `{PYTHON} {task}/_preflight.py`，应该打印 preflight ok。")
    lines.append("任何一步被拒绝或出错：立刻停止，不要换别的办法，直接给出最终报告"
                 "（status 写 blocked，blocked 里写被拒的工具和原样的命令）。")
    if preflight_only:
        lines += ["", "这次只做自检：做完上面几步就给出最终报告，不读任务书，不做别的。"]
    else:
        lines += ["", "## 收尾",
                  "- STATUS.md 最后一行写“完成”或“阻碍：……”。",
                  "- 算出来的数字写进 NUMBERS.csv；调研得到的事实结论写进 CLAIMS.csv（格式见 AGENTS.md）。",
                  "- 最终回复用要求的 JSON 格式：commands_run 只列真正执行过的命令，逐字照抄；没做成的写进 blocked。"]
    return "\n".join(lines) + "\n"


def prompt(root: Path, task: str, args: argparse.Namespace, session: str) -> str:
    text = contract(task, args.profile, args.web, session, args.preflight_only)
    if args.preflight_only:
        return text
    brief = (root / task / "TASK.md").read_text(encoding="utf-8")
    if len(text) + len(brief) > MAX_PROMPT:
        brief = f"任务书太长，没有放在这里：先用 Read 完整读 `{task}/TASK.md`，再开始。"
    return f"{text}\n# 任务书（{task}/TASK.md）\n\n{brief}"


def command(claude: list[str], text: str, root: Path, task: str, args: argparse.Namespace, session: str) -> list[str]:
    cmd = [*claude, "-p", text, "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
           *permissions(task, args.profile, args.web), "--append-system-prompt-file", str(root / "AGENTS.md"),
           "--strict-mcp-config", "--max-turns", str(args.max_turns), "--max-budget-usd", f"{args.max_budget_usd:g}"]
    cmd += ["--resume", args.resume] if args.resume else ["--session-id", session]
    if not args.no_schema:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False, separators=(",", ":"))]
    if args.model:
        cmd += ["--model", args.model]
    if args.effort:
        cmd += ["--effort", args.effort]
    if not args.no_safe_mode:
        cmd.append("--safe-mode")
    return cmd


def child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in PARENT_SESSION_VARS}
    env["PYTHONDONTWRITEBYTECODE"] = "1"   # the run's Python leaves the tracked __pycache__ files alone
    env["PYTHONIOENCODING"] = "utf-8"      # its Chinese output survives a Windows pipe (default code page GBK)
    return env


def _stop(proc: subprocess.Popen, state: dict, why: str) -> None:
    state["stopped"] = state["stopped"] or why
    if proc.poll() is None:
        if os.name == "nt":   # the whole tree: claude.exe and any shell or Python it started
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.kill()


def run_once(cmd: list[str], root: Path, log_path: Path, max_denials: int, timeout_s: float) -> dict:
    """Start one run, copy its events into the log as they come, and stop it on too many denials or at the timeout."""
    state = {"started": False, "denied": set(), "stopped": None, "exit": None}
    with open(log_path, "w", encoding="utf-8") as log, \
            open(log_path.with_suffix(".stderr.txt"), "w", encoding="utf-8") as err:
        proc = subprocess.Popen(cmd, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
                                text=True, encoding="utf-8", errors="replace", env=child_env())
        timer = threading.Timer(timeout_s, _stop, (proc, state, "timeout"))
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                event = ck.json_line(line)
                if event is None:
                    continue
                state["started"] |= event.get("type") == "assistant"
                state["denied"].update(ck.denials_in(event))
                if len(state["denied"]) >= max_denials and not state["stopped"]:
                    _stop(proc, state, "denials")
            state["exit"] = proc.wait()
        finally:
            timer.cancel()
    return state


def _digest(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return "<gone>"
    if st.st_size > 50_000_000:          # a large data file: size and time are enough to see a change
        return f"{st.st_size}:{st.st_mtime_ns}"
    return hashlib.sha1(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    """Content hash of every file git reports as changed or untracked, so a change during the run shows even in a
    file that was already dirty. Compiled caches and the agents' own logs are left out."""
    out = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root,
                         capture_output=True)
    if out.returncode != 0:
        return {}
    fields, paths, i = out.stdout.decode("utf-8", "replace").split("\0"), [], 0
    while i < len(fields):
        if len(fields[i]) > 3:
            paths.append(fields[i][3:])
            i += any(c in "RC" for c in fields[i][:2])    # a rename or copy: the next field is the old path
        i += 1
    return {p: _digest(root / p) for p in paths
            if "__pycache__" not in p and not p.endswith(".pyc") and not p.startswith(LOGS + "/")}


def changed_outside(before: dict[str, str], after: dict[str, str], task: str) -> list[str]:
    inside = task.rstrip("/") + "/"
    return sorted(p for p in set(before) | set(after) if not p.startswith(inside) and before.get(p) != after.get(p))


def outcome(state: dict, parsed: dict) -> str:
    if state["stopped"] == "denials":
        return "stopped_denials"
    if state["stopped"] == "timeout":
        return "timeout"
    result = parsed["result"]
    if result is None:
        return "crashed" if state["started"] else "no_start"
    return (result.get("subtype") or "error") if result.get("is_error") else "ok"


def final_report(result: dict) -> dict | None:
    """The run's final report: the schema-checked structured output, or the reply itself when it is that JSON."""
    report = result.get("structured_output")
    if isinstance(report, dict):
        return report
    try:
        report = json.loads(result.get("result") or "")
    except (TypeError, ValueError):
        return None
    return report if isinstance(report, dict) and "status" in report else None


def _version(agents_md: Path) -> str | None:
    m = re.search(r"^版本[:：]\s*(\S+)", agents_md.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _refuse(message: str) -> int:
    print(f"没有启动：{message}")
    return 2


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m agents.launch",
                                description="把 analysis/<日期>/<任务名>/TASK.md 派给后台 Claude：按档位给权限，开工先自检，"
                                            "结束后自动检查。")
    p.add_argument("task", help="任务目录，例如 analysis/2026-10-24/peak_day_probability")
    p.add_argument("--profile", choices=sorted(PROFILES), required=True, help="research：联网调研；code：写代码并运行")
    p.add_argument("--web", action="store_true", help="code 档也允许联网")
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--effort", help="low / medium / high / xhigh / max；不写就用 Claude 的默认")
    p.add_argument("--max-turns", type=int, default=200)
    p.add_argument("--max-budget-usd", type=float, default=40.0)
    p.add_argument("--timeout-min", type=float, default=240.0)
    p.add_argument("--max-denials", type=int, default=3, help="被拒这么多次就停止运行（默认 3）")
    p.add_argument("--retries", type=int, default=1, help="第一轮之前就失败时，换新会话重试几次（默认 1）")
    p.add_argument("--preflight-only", action="store_true", help="只做自检：写一个文件、跑一个小脚本、确认 AGENTS.md 传进去了")
    p.add_argument("--resume", metavar="SESSION", help="同一个任务中途断了，接着这个会话继续")
    p.add_argument("--no-safe-mode", action="store_true", help="不加 --safe-mode（会加载插件、hooks 和自动记忆）")
    p.add_argument("--no-schema", action="store_true", help="最终回复不要求固定的 JSON 格式")
    p.add_argument("--claude-bin", help=argparse.SUPPRESS)   # tests: agents/fake_claude.py
    p.add_argument("--root", help=argparse.SUPPRESS)         # tests: a throwaway repository
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    ck.utf8_stdout()
    root = Path(args.root).resolve() if args.root else ROOT
    given = Path(args.task)
    task_dir = (given if given.is_absolute() else root / given).resolve()
    try:
        task = task_dir.relative_to(root).as_posix()
    except ValueError:
        return _refuse(f"任务目录要在仓库里：{task_dir}")
    if not task.startswith("analysis/") or task.count("/") < 2 or not task_dir.is_dir():
        return _refuse(f"任务目录要是已经建好的 analysis/<日期>/<任务名>/：{task}")
    if not (args.preflight_only or args.resume or (task_dir / "TASK.md").is_file()):
        return _refuse(f"{task}/ 里没有 TASK.md")
    agents_md = root / "AGENTS.md"
    if not agents_md.is_file():
        return _refuse("仓库根目录没有 AGENTS.md")
    if args.profile == "code" and not (root / PYTHON).exists():
        return _refuse(f"找不到 {PYTHON}")
    claude = find_claude(args.claude_bin)
    version = _version(agents_md)
    if args.profile == "code":
        (task_dir / "_preflight.py").write_text(PREFLIGHT, encoding="utf-8")
    log_dir = root / LOGS / task.replace("/", "__")
    log_dir.mkdir(parents=True, exist_ok=True)

    before, started_at, attempts = snapshot(root), datetime.now(), []
    for attempt in range(args.retries + 1):
        session = args.resume or str(uuid.uuid4())
        log_path = log_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{'preflight-' if args.preflight_only else ''}{session[:8]}.jsonl"
        text = RESUME_PROMPT if args.resume else prompt(root, task, args, session)
        print(f"启动{'自检' if args.preflight_only else '任务'}：{task}（{args.profile}，会话 {session}，第 {attempt + 1} 次）",
              flush=True)
        state = run_once(command(claude, text, root, task, args, session), root, log_path, args.max_denials,
                         args.timeout_min * 60)
        attempts.append({"session_id": session, "log": log_path.relative_to(root).as_posix(),
                         "exit_code": state["exit"], "started": state["started"], "stopped": state["stopped"]})
        if state["started"] or state["stopped"] or args.resume:
            break
        if attempt < args.retries:
            print("运行在第一轮之前就结束了（多半是网络或服务问题），换一个新会话重试。", flush=True)
    after = snapshot(root)

    parsed = ck.parse_log(log_path)
    status, result = outcome(state, parsed), parsed["result"] or {}
    report = final_report(result)
    summary = {
        "task": task, "profile": args.profile, "web": bool(args.web or args.profile == "research"),
        "preflight_only": args.preflight_only, "model": args.model, "effort": args.effort,
        "started_at": started_at.isoformat(timespec="seconds"), "ended_at": datetime.now().isoformat(timespec="seconds"),
        "status": status, "status_zh": ck.STATUS_ZH.get(status, status),
        "session_id": attempts[-1]["session_id"], "log": attempts[-1]["log"], "attempts": attempts,
        "num_turns": result.get("num_turns"), "cost_usd": result.get("total_cost_usd"),
        "duration_s": round(result["duration_ms"] / 1000) if result.get("duration_ms") else None,
        "agents_md_version": version, "structured_report": report is not None,
        "denials": parsed["denials"], "outside_changes": changed_outside(before, after, task),
        "tools": parsed["tools"], "bash": parsed["bash"],
    }
    if args.preflight_only:
        _write_json(task_dir / "PREFLIGHT.json", summary)
        findings = ck.check_preflight(task_dir, parsed, args.profile, version)
    else:
        _write_json(task_dir / "RUN_SUMMARY.json", summary)
        if report is not None:
            _write_json(task_dir / "RUN_REPORT.json", report)
        reply = (result.get("result") or "").strip() or (report or {}).get("summary_zh", "").strip()
        if reply:
            (task_dir / "FINAL_REPLY.md").write_text(reply + "\n", encoding="utf-8")
        findings = ck.check_task(task_dir, log_path)
    for leftover in ("_preflight.py", "_preflight_ok.md"):
        (task_dir / leftover).unlink(missing_ok=True)

    cost = f"约 ${summary['cost_usd']:.2f}" if summary["cost_usd"] is not None else "花费不详"
    print(f"\n== {'自检' if args.preflight_only else '任务'} {task}：{summary['status_zh']}")
    print(f"会话 {summary['session_id']}，{summary['num_turns'] or 0} 轮，{(summary['duration_s'] or 0) / 60:.1f} 分钟，"
          f"{cost}（日志 {summary['log']}）")
    if summary["outside_changes"]:
        print(f"运行期间任务目录以外变了的文件（可能来自同时在跑的其他会话）：{'，'.join(summary['outside_changes'])}")
    print("\n== 检查")
    ck.print_findings(findings)
    return 0 if status == "ok" and not any(level == ck.FAIL for level, _ in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
