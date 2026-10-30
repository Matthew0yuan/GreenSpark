"""Tests for the dispatch and check tools (agents/launch.py, agents/check.py). The launcher runs end to end on a
throwaway git repository with agents/fake_claude.py standing in for Claude Code, so nothing here calls a model. The
NUMBERS.csv cases are errors the reviews of 2026-10-24 found by hand (analysis/2026-10-24/*/CODEX_REVIEW.md,
REVIEW_NOTES.md). Plain functions named test_*, so pytest finds them too; without pytest:

Run:  python -m agents.agent_tests
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from agents import check as ck
from agents import launch as la

FAKE = str(Path(__file__).with_name("fake_claude.py"))
TASK = "analysis/2026-10-24/demo"
NUMBERS = "name,value,unit,formula,period,source,status,check,note\n"
CLAIMS = "claim,source_url,locator,quote,status,accessed\n"


def _table(tmp: Path, name: str, text: str) -> Path:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return path


def _fails(findings: list[tuple[str, str]]) -> list[str]:
    return [message for level, message in findings if level == ck.FAIL]


# ----------------------------------------------------------------------------------------------------------- units
def test_units_scale_and_dimensions():
    assert ck.parse_unit("MWh") == (1e6, {"W": 1, "h": 1})
    factor, dims = ck.parse_unit("A$/(kW*年)")
    assert abs(factor - 1e-3) < 1e-15 and dims == {"A$": 1, "W": -1, "年": -1}
    assert ck.parse_unit("A$/kW/年") == ck.parse_unit("A$/(kW*年)")
    factor, dims = ck.parse_unit("c/kWh")
    assert abs(factor - 1e-5) < 1e-18 and dims == {"A$": 1, "W": -1, "h": -1}
    assert ck.parse_unit("户[报名]")[1] != ck.parse_unit("户[响应]")[1]   # the two kinds of home never mix
    assert ck.parse_unit("年")[1] != ck.parse_unit("季")[1]               # nor a year and a season
    assert ck.parse_unit("")[1] == {} and ck.parse_unit("%")[0] == 0.01
    for bad in ("A$/(kW", "kW^x", "/kW"):
        try:
            ck.parse_unit(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should not parse")


# ----------------------------------------------------------------------------------------------------- NUMBERS.csv
def test_numbers_pass_when_consistent():
    """One call's payment from export x price; MW x h = MWh; energy that balances."""
    with tempfile.TemporaryDirectory() as tmp:
        found = ck.check_numbers(_table(Path(tmp), "NUMBERS.csv", NUMBERS + (
            "出口电量,5,kWh/户[响应],,一次调用,实验设定,假设,,\n"
            "补偿单价,70,c/kWh,,Battery Rewards V3,Synergy 条款,已核实,,\n"
            "单户付款,3.5,A$/户[响应],{出口电量} * {补偿单价},一次调用,推算,推算,,\n"
            "放电功率,10,MW,,一次调用,电池设定,假设,,\n"
            "放电时长,2,h,,一次调用,实验设定,假设,,\n"
            "放电电量,20,MWh,{放电功率} * {放电时长},一次调用,推算,推算,,\n"
            "可用电量,8.5,kWh,,一次调用,实验设定,假设,,\n"
            "家庭负荷,6,kWh,,一次调用,实验设定,假设,,\n"
            "出口,2.5,kWh,,一次调用,实验设定,假设,,\n"
            "电量缺口,0,kWh,{可用电量} - {家庭负荷} - {出口},一次调用,推算,推算,>= 0,\n")))
        assert not _fails(found), found
        assert any(level == ck.PASS and "3 个公式" in message for level, message in found), found


def test_numbers_catch_kw_written_as_mw():
    """REVIEW_NOTES.md item 2: a fixed cost over an availability fee in $/kW gives kW, not MW."""
    with tempfile.TemporaryDirectory() as tmp:
        fails = _fails(ck.check_numbers(_table(Path(tmp), "NUMBERS.csv", NUMBERS + (
            "固定成本,130000,A$/年,,合同年,实验设定,假设,,\n"
            "可用费,200,A$/(kW*年),,合同年,假设报价,假设,,\n"
            "每kW成本,44.72,A$/(kW*年),,合同年,推算,推算,,\n"
            "盈亏平衡规模,837.2,MW,{固定成本} / ({可用费} - {每kW成本}),合同年,推算,推算,,\n"))))
        assert len(fails) == 1 and "单位换算" in fails[0], fails


def test_numbers_catch_mixed_households():
    """CODEX_REVIEW.md: money per responding home and money per enrolled home are different numbers."""
    with tempfile.TemporaryDirectory() as tmp:
        fails = _fails(ck.check_numbers(_table(Path(tmp), "NUMBERS.csv", NUMBERS + (
            "每响应户补偿,113.41,A$/(户[响应]*年),,合同年 2025-26,回测,回测,,\n"
            "每报名户平台费,25,A$/(户[报名]*年),,合同年,实验设定,假设,,\n"
            "每户净额,88.41,A$/(户[报名]*年),{每响应户补偿} - {每报名户平台费},合同年 2025-26,推算,推算,,\n"
            "响应比例,0.8,户[响应]/户[报名],,合同年,实验设定,假设,,\n"
            "每报名户补偿,90.728,A$/(户[报名]*年),{每响应户补偿} * {响应比例},合同年 2025-26,推算,推算,,\n"))))
        assert len(fails) == 1 and "量纲不一致" in fails[0] and "每户净额" in fails[0], fails


def test_numbers_catch_unbalanced_energy():
    """CODEX_REVIEW.md: the 4-hour case had 8 kWh for 6 kWh of load and 2.5 kWh of export."""
    rows = ("可用电量,8,kWh,,4 小时调用,实验设定,假设,,\n"
            "家庭负荷,6,kWh,,4 小时调用,实验设定,假设,,\n"
            "出口,2.5,kWh,,4 小时调用,实验设定,假设,,\n")
    with tempfile.TemporaryDirectory() as tmp:
        honest = _fails(ck.check_numbers(_table(Path(tmp), "NUMBERS.csv", NUMBERS + rows +
                                                "电量缺口,-0.5,kWh,{可用电量} - {家庭负荷} - {出口},4 小时调用,推算,推算,>= 0,\n")))
        assumed = _fails(ck.check_numbers(_table(Path(tmp), "NUMBERS.csv", NUMBERS + rows +
                                                 "电量缺口,0,kWh,{可用电量} - {家庭负荷} - {出口},4 小时调用,推算,推算,>= 0,\n")))
        assert len(honest) == 1 and "不满足 >= 0" in honest[0], honest
        assert len(assumed) == 1 and "算出 -0.5" in assumed[0], assumed


def test_numbers_table_format():
    with tempfile.TemporaryDirectory() as tmp:
        no_period = _fails(ck.check_numbers(_table(Path(tmp), "NUMBERS.csv",
                                                   "name,value,unit,source,status\nx,1,kW,a,假设\n")))
        assert len(no_period) == 1 and "缺少列" in no_period[0]
        fails = _fails(ck.check_numbers(_table(Path(tmp), "NUMBERS.csv", NUMBERS + (
            "a,12%,1,,样本内,s,假设,,\n"
            "b,1,kW,,样本内,s,猜的,,\n"
            "c,1,kW,,,s,假设,,\n"
            "c,2,kW,{b} + {z},样本内,s,假设,,\n"))))
        text = " | ".join(fails)
        for expected in ("value 只能写数字", "status 要写", "没写时间段", "name 重复", "表里没有的名字 {z}"):
            assert expected in text, (expected, fails)


def test_claims_need_source_and_words():
    with tempfile.TemporaryDirectory() as tmp:
        found = ck.check_claims(_table(Path(tmp), "CLAIMS.csv", CLAIMS + (
            "Byford 接受聚合的住宅 DER,https://www.westernpower.com.au/byford.pdf,§2.3,aggregated residential DER,"
            "已核实,2026-10-24\n"
            "TPA 包括补充容量,https://www.synergy.net.au/tpa.pdf,§3.1,,已核实,2026-10-24\n"
            "Byford 每年最多 20 次,,,,待核实,\n")))
        fails = _fails(found)
        assert len(fails) == 1 and "原文" in fails[0], found
        assert any(level == ck.WARN and "待核实" in message for level, message in found), found


# ------------------------------------------------------------------------------------------------------------ log
def test_successful_read_of_denial_text_is_not_a_denial():
    # Grep of this checker's own source interrupted a real architecture task.
    for content in ('28:DENIED = "has been denied"',
                    [{"type": "text", "text": "Permission to use Bash has been denied because ..."}]):
        for flag in (None, False):
            result = {"type": "tool_result", "tool_use_id": "read1", "content": content}
            if flag is not None:
                result["is_error"] = flag
            assert ck.denials_in({"type": "user", "message": {"content": [result]}}) == []


def test_error_result_still_reports_permission_denial():
    result = {"type": "tool_result", "tool_use_id": "denied1", "is_error": True,
              "content": "Permission to use Bash has been denied because Claude Code is running in don't ask mode."}
    assert ck.denials_in({"type": "user", "message": {"content": [result]}}) == ["denied1"]
    assert ck.denials_in({"type": "system", "subtype": "permission_denied",
                         "tool_use_id": "denied2"}) == ["denied2"]


def test_log_parse_commands_and_denials():
    events = [
        {"type": "system", "subtype": "init", "session_id": "s"},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b1", "name": "Bash",
                                                       "input": {"command": "run ok"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b1", "content": "fine"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b2", "name": "Bash",
                                                       "input": {"command": "python -c 1"}}]}},
        {"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_use_id": "b2"},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b2", "is_error": True,
                                                  "content": "Permission to use Bash has been denied because ..."}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "b3", "name": "Bash",
                                                       "input": {"command": "tests that fail"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b3", "is_error": True,
                                                  "content": "Exit code 1"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "w1", "name": "Write",
                                                       "input": {"file_path": "analysis\\x\\STATUS.md"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "w1", "content": "ok"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        log = _table(Path(tmp), "run.jsonl", "".join(json.dumps(e) + "\n" for e in events) + "not json\n")
        parsed = ck.parse_log(log)
    assert parsed["bash"] == [{"command": "run ok", "ran": True, "ok": True},
                              {"command": "python -c 1", "ran": False, "ok": False},
                              {"command": "tests that fail", "ran": True, "ok": False}]
    assert parsed["denials"] == [{"tool": "Bash", "input": "python -c 1"}]
    assert parsed["writes"] == ["analysis/x/STATUS.md"] and parsed["result"]["num_turns"] == 3


# ------------------------------------------------------------------------------------------------------- launcher
def test_permissions_match_the_profile():
    flags = la.permissions(TASK, "research", web=False)
    tools, allow = (flags[flags.index(f) + 1].split(",") for f in ("--tools", "--allowedTools"))
    assert "Bash" not in tools and "WebSearch" in allow and f"Edit({TASK}/**)" in allow
    assert not any(a.startswith("Write(") for a in allow)   # a Write(...) path rule never matches
    assert flags[flags.index("--disallowedTools") + 1] == "Read(NOTE.md)"
    flags = la.permissions(TASK, "code", web=False)
    allow = flags[flags.index("--allowedTools") + 1].split(",")
    assert f"Bash(.venv/Scripts/python.exe {TASK}/*)" in allow and "WebSearch" not in allow
    assert "WebFetch" in la.permissions(TASK, "code", web=True)[3]


def test_contract_names_the_only_python_form():
    text = la.contract(TASK, "code", False, "s", preflight_only=False)
    assert f"`.venv/Scripts/python.exe {TASK}/<脚本>.py`" in text and "python -c" in text
    assert f"{TASK}/STATUS.md" in text and f"{TASK}/_preflight.py" in text and "blocked" in text
    pre = la.contract(TASK, "research", False, "s", preflight_only=True)
    assert "_preflight_ok.md" in pre and "_preflight.py" not in pre and "版本" in pre and "不读任务书" in pre


def test_child_env_drops_the_parent_session():
    old = os.environ.get("CLAUDE_CODE_SESSION_ID")
    os.environ["CLAUDE_CODE_SESSION_ID"] = "parent"
    try:
        env = la.child_env()
    finally:
        os.environ.pop("CLAUDE_CODE_SESSION_ID") if old is None else os.environ.update(CLAUDE_CODE_SESSION_ID=old)
    assert "CLAUDE_CODE_SESSION_ID" not in env and env["PYTHONDONTWRITEBYTECODE"] == "1" and "PATH" in env


def _repo(tmp: Path) -> Path:
    """A throwaway repository shaped like this one: AGENTS.md, a task folder with TASK.md, a .venv python."""
    root = tmp / "repo"
    (root / TASK).mkdir(parents=True)
    (root / "vpp").mkdir()
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / ".venv" / "Scripts" / "python.exe").write_text("")
    (root / ".gitignore").write_text(".venv/\nagents/logs/\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# 规则\n\n版本：test-7\n", encoding="utf-8")
    (root / TASK / "TASK.md").write_text("# 演示任务\n\n只是测试。\n", encoding="utf-8")
    (root / "vpp" / "keep.py").write_text("x = 1\n", encoding="utf-8")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for step in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "start"]):
        subprocess.run([*git, *step], cwd=root, check=True, capture_output=True)
    return root


def _launch(tmp: Path, scenario: str, *extra: str) -> tuple[int, str, list[str], Path]:
    """Run the launcher on a fresh throwaway repository with the fake claude playing the scenario."""
    root = _repo(tmp)
    saved = tmp / "args.json"        # outside the repository, so it is not a change "outside the task folder"
    env = {"FAKE_CLAUDE_SCENARIO": scenario, "FAKE_CLAUDE_TASK": TASK, "FAKE_CLAUDE_ARGS": str(saved)}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = la.main([TASK, "--profile", "code", "--root", str(root), "--claude-bin", FAKE, *extra])
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.update({k: v})
    args = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else []
    return code, out.getvalue(), args, root


def _summary(root: Path, name: str = "RUN_SUMMARY.json") -> dict:
    return json.loads((root / TASK / name).read_text(encoding="utf-8"))


def test_launch_ok_writes_the_record():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        code, out, args, root = _launch(Path(tmp), "ok")
        summary, task_dir = _summary(root), root / TASK
        assert code == 0, out
        assert summary["status"] == "ok" and summary["structured_report"] and not summary["outside_changes"]
        assert (task_dir / "RUN_REPORT.json").exists() and (task_dir / "FINAL_REPLY.md").exists()
        assert not (task_dir / "_preflight.py").exists()
        joined = " ".join(args)
        for flag in ("--permission-mode dontAsk", "--safe-mode", "--strict-mcp-config", "--json-schema",
                     "--append-system-prompt-file", "--max-budget-usd", "--session-id"):
            assert flag in joined, flag
        assert f"Edit({TASK}/**)" in args[args.index("--allowedTools") + 1]
        assert "# 任务书" in args[args.index("-p") + 1] and "演示任务" in args[args.index("-p") + 1]


def test_launch_stops_after_denials():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        start = time.monotonic()
        code, out, _, root = _launch(Path(tmp), "denials")
        summary = _summary(root)
        assert code == 1 and summary["status"] == "stopped_denials", out
        assert 3 <= len(summary["denials"]) < 10, summary["denials"]   # stopped, not left to print all ten
        assert summary["attempts"][0]["stopped"] == "denials" and time.monotonic() - start < 15


def test_launch_retries_a_run_that_never_started():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        code, out, _, root = _launch(Path(tmp), "no_start")
        summary = _summary(root)
        assert code == 1 and summary["status"] == "no_start" and len(summary["attempts"]) == 2, out
        assert summary["attempts"][0]["session_id"] != summary["attempts"][1]["session_id"]


def test_launch_sees_changes_outside_the_task():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        code, out, _, root = _launch(Path(tmp), "outside")
        assert _summary(root)["outside_changes"] == ["vpp/touched.txt"] and code == 1, out
        assert any("任务目录以外" in m for m in _fails(ck.check_task(root / TASK)))


def test_check_catches_a_claimed_test_that_never_ran():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        code, out, _, root = _launch(Path(tmp), "claims_lie")
        fails = _fails(ck.check_task(root / TASK))
        assert code == 1 and any("跑过测试" in m for m in fails) and any("说运行过" in m for m in fails), fails


def test_preflight_only():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        code, out, args, root = _launch(Path(tmp), "preflight", "--preflight-only")
        assert code == 0, out
        assert "写任务目录：可以" in out and "运行 Python：可以" in out and "版本 test-7" in out, out
        assert _summary(root, "PREFLIGHT.json")["preflight_only"] and not (root / TASK / "RUN_SUMMARY.json").exists()
        assert not (root / TASK / "_preflight_ok.md").exists() and "# 任务书" not in args[args.index("-p") + 1]


def test_launch_refuses_a_folder_outside_analysis():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = _repo(Path(tmp))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = la.main(["vpp", "--profile", "code", "--root", str(root), "--claude-bin", FAKE])
        assert code == 2 and "analysis/" in out.getvalue()


def main() -> int:
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:  # noqa: BLE001  report every failure, then exit non-zero
            failed += 1
            print(f"FAIL  {name}\n{traceback.format_exc()}")
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
