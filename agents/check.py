"""Automatic part of the review of a task folder (AGENTS.md, "固定检查清单" and "任务目录里交什么"):
  - STATUS.md exists and its last line says 完成 or 阻碍;
  - the run's record agrees with its final report: every command the report says it ran was run and not denied
    (read from the stream-json log), every file it lists exists, nothing outside the task folder changed during the
    run (RUN_SUMMARY.json, written by agents/launch.py);
  - NUMBERS.csv recomputes: each formula, in its units, gives the value in the table. This catches kW written as MW,
    money per responding home mixed with money per enrolled home, and energy that does not balance;
  - CLAIMS.csv gives every verified claim a URL, a section or page and the original words.
Whether a source really says what a claim says, later information leaking into a decision, "expected" read as a hard
cap: those stay with the reviewer, against the checklist in AGENTS.md.

Run:  python -m agents.check <task dir> [--log <stream-json log>]     exit status 1 if any check fails
"""

import ast
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

FAIL, WARN, PASS = "FAIL", "WARN", "PASS"
NUMBER_STATUS = {"已核实", "调研", "推算", "假设", "回测", "verified", "researched", "derived", "assumption", "backtest"}
CLAIM_STATUS = {"已核实", "调研", "推算", "待核实", "verified", "researched", "derived", "unverified"}
SOURCED = {"已核实", "调研", "verified", "researched"}
RTOL = 0.005                  # report numbers are rounded; 0.5% still catches a unit or a denominator slip
DENIED = "has been denied"    # Claude Code's wording for a call the permission mode refused
STATUS_ZH = {"ok": "正常结束", "stopped_denials": "被拒次数太多，已停止", "timeout": "超时，已停止",
             "no_start": "第一轮之前就失败了（多半是网络或服务问题）", "crashed": "中途退出，没有最终结果",
             "error_max_turns": "达到轮数上限", "error_max_budget_usd": "达到花费上限",
             "error_during_execution": "运行出错"}


# ----------------------------------------------------------------------------------------------------------- units
# unit -> (factor to the base unit, base dimensions). Hours turn power into energy (MW x h = MWh); a year, a season, a
# month and a day do not convert into each other or into hours, on purpose: in these accounts a contract year and a
# Hot Season are different periods, and mixing them is one of the errors this check is for.
UNITS: dict[str, tuple[float, dict]] = {}


def _define(names: str, factor: float, dims: dict) -> None:
    for name in names.split():
        UNITS[name] = (factor, dims)


_define("A$ $ AUD", 1.0, {"A$": 1})
_define("c ¢", 0.01, {"A$": 1})
_define("A$k k$", 1e3, {"A$": 1})
_define("A$M M$", 1e6, {"A$": 1})
for _prefix, _factor in (("", 1.0), ("k", 1e3), ("M", 1e6), ("G", 1e9)):
    _define(f"{_prefix}W", _factor, {"W": 1})
    _define(f"{_prefix}Wh", _factor, {"W": 1, "h": 1})
    _define(f"{_prefix}VA", _factor, {"VA": 1})
_define("h hr hour hours 小时", 1.0, {"h": 1})
_define("min 分钟", 1 / 60, {"h": 1})
_define("%", 0.01, {})
_define("1 - 无", 1.0, {})
_define("年 year years yr", 1.0, {"年": 1})
_define("季 season seasons", 1.0, {"季": 1})
_define("月 month months", 1.0, {"月": 1})
_define("天 日 day days", 1.0, {"天": 1})
_define("次 event events call calls activation activations", 1.0, {"次": 1})

_OPERATORS = set("*·⋅×/^()")
_TIMES = set("*·⋅×")


def _combine(a: dict, b: dict, sign: int = 1) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + sign * v
        if out[k] == 0:
            del out[k]
    return out


def _tokens(text: str) -> list[str]:
    out, word = [], ""
    for ch in text:
        if ch in _OPERATORS or ch.isspace():
            if word:
                out.append(word)
                word = ""
            if not ch.isspace():
                out.append(ch)
        else:
            word += ch
    return out + [word] if word else out


def parse_unit(text: str) -> tuple[float, dict]:
    """(factor to base units, dimensions) of a unit such as "A$/(kW*年)" or "kWh/户[响应]". A name it does not know
    becomes a dimension of its own, so 户[报名] and 户[响应] never cancel or add."""
    tokens = _tokens(str(text or "").strip())
    pos = 0

    def product() -> tuple[float, dict]:
        nonlocal pos
        factor, dims = power()
        while pos < len(tokens) and tokens[pos] != ")":
            op = "*"                                     # "kW h" reads as kW*h
            if tokens[pos] in _TIMES or tokens[pos] == "/":
                op = tokens[pos]
                pos += 1
            f, d = power()
            sign = -1 if op == "/" else 1
            factor, dims = factor * f ** sign, _combine(dims, d, sign)
        return factor, dims

    def power() -> tuple[float, dict]:
        nonlocal pos
        factor, dims = atom()
        if pos < len(tokens) and tokens[pos] == "^":
            if pos + 1 >= len(tokens) or not re.fullmatch(r"-?\d+", tokens[pos + 1]):
                raise ValueError(f"单位 {text!r}：^ 后面要跟整数")
            n = int(tokens[pos + 1])
            pos += 2
            factor, dims = factor ** n, {k: v * n for k, v in dims.items()}
        return factor, dims

    def atom() -> tuple[float, dict]:
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError(f"单位 {text!r} 不完整")
        token = tokens[pos]
        pos += 1
        if token == "(":
            inner = product()
            if pos >= len(tokens) or tokens[pos] != ")":
                raise ValueError(f"单位 {text!r}：括号没配对")
            pos += 1
            return inner
        if token in _OPERATORS:
            raise ValueError(f"单位 {text!r}：{token} 的位置不对")
        return UNITS.get(token, (1.0, {token: 1}))

    if not tokens:
        return 1.0, {}
    result = product()
    if pos != len(tokens):
        raise ValueError(f"单位 {text!r}：括号没配对")
    return result


def show_dims(dims: dict) -> str:
    def part(items: list) -> str:
        return "*".join(k if abs(v) == 1 else f"{k}^{abs(v):g}" for k, v in items)
    up = sorted((k, v) for k, v in dims.items() if v > 0)
    down = sorted((k, v) for k, v in dims.items() if v < 0)
    text = part(up) or "1"
    if down:
        text += "/" + (part(down) if len(down) == 1 else f"({part(down)})")
    return text


class Quantity:
    """A value in base units with its dimensions."""
    __slots__ = ("value", "dims")

    def __init__(self, value: float, dims: dict):
        self.value, self.dims = value, dims


_REF = re.compile(r"\{([^{}]+)\}")


def evaluate(formula: str, known: dict[str, Quantity]) -> Quantity:
    """A formula over {name} references, with only numbers, + - * / **, brackets and min, max, abs."""
    names: dict[str, Quantity] = {}

    def ref(m: re.Match) -> str:
        key = m.group(1).strip()
        if key not in known:
            raise ValueError(f"引用了表里没有的名字 {{{key}}}")
        var = f"_v{len(names)}"
        names[var] = known[key]
        return var

    return _eval(ast.parse(_REF.sub(ref, formula), mode="eval").body, names)


def _eval(node: ast.AST, names: dict[str, Quantity]) -> Quantity:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return Quantity(float(node.value), {})
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        q = _eval(node.operand, names)
        return Quantity(-q.value if isinstance(node.op, ast.USub) else q.value, q.dims)
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, names), _eval(node.right, names)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if a.dims != b.dims:
                raise ValueError(f"量纲不一致：{show_dims(a.dims)} 和 {show_dims(b.dims)} 不能相加减")
            return Quantity(a.value + b.value if isinstance(node.op, ast.Add) else a.value - b.value, a.dims)
        if isinstance(node.op, ast.Mult):
            return Quantity(a.value * b.value, _combine(a.dims, b.dims))
        if isinstance(node.op, ast.Div):
            if b.value == 0:
                raise ValueError("除以 0")
            return Quantity(a.value / b.value, _combine(a.dims, b.dims, -1))
        if isinstance(node.op, ast.Pow):
            if b.dims:
                raise ValueError("指数必须是纯数字")
            return Quantity(a.value ** b.value, {k: v * b.value for k, v in a.dims.items() if v * b.value})
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("min", "max", "abs")
            and node.args and not node.keywords):
        args = [_eval(x, names) for x in node.args]
        if any(x.dims != args[0].dims for x in args):
            raise ValueError(f"{node.func.id} 的参数量纲不一致")
        if node.func.id == "abs":
            if len(args) != 1:
                raise ValueError("abs 只能有一个参数")
            return Quantity(abs(args[0].value), args[0].dims)
        pick = min if node.func.id == "min" else max
        return Quantity(pick(x.value for x in args), args[0].dims)
    raise ValueError("公式里只能用数字、{名字}、+ - * / **、括号和 min、max、abs")


def _close(a: float, b: float, rtol: float) -> bool:
    return abs(a - b) <= max(rtol * max(abs(a), abs(b)), 1e-9)


def _ratio_hint(got: float, want: float) -> str:
    if got == 0 or want == 0:
        return ""
    r = got / want
    for factor, label in ((1e3, "1000"), (1e-3, "1/1000"), (1e6, "100 万"), (1e-6, "1/100 万"), (100, "100"),
                          (0.01, "1/100")):
        if abs(r / factor - 1) < 0.02:
            return f"（差 {label} 倍，像是单位换算错了，例如 kW 和 MW、c 和 $）"
    return f"（差 {r:.3g} 倍）"


def _compare(value: float, op: str, bound: float) -> bool:
    if op in ("=", "=="):
        return abs(value - bound) <= 1e-9 * max(1.0, abs(bound))
    return {"<": value < bound, "<=": value <= bound, ">": value > bound, ">=": value >= bound}[op]


# ------------------------------------------------------------------------------------------------------- tables
def _read_csv(path: Path, need: list[str]) -> tuple[list[dict], list[tuple[str, str]]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
        rows = [{(k or "").strip().lower(): (v or "").strip() if isinstance(v, str) else "" for k, v in r.items()}
                for r in reader]
    missing = [c for c in need if c not in header]
    if missing:
        return [], [(FAIL, f"{path.name} 缺少列：{', '.join(missing)}（格式见 AGENTS.md）")]
    return rows, []


def check_numbers(path: Path, rtol: float = RTOL) -> list[tuple[str, str]]:
    """Every row has a number, a unit, a period, a source and a status; every formula recomputes, in units, to the
    value in its row; every check (such as >= 0) holds."""
    rows, out = _read_csv(path, ["name", "value", "unit", "period", "source", "status"])
    if out:
        return out
    seen = Counter(r["name"] for r in rows if r["name"])
    known, parsed = {}, []
    for line, r in enumerate(rows, start=2):
        name = r["name"]
        where = f"{path.name} 第 {line} 行「{name or '?'}」"
        if not name:
            out.append((FAIL, f"{path.name} 第 {line} 行：没写 name"))
            continue
        if seen[name] > 1:
            out.append((FAIL, f"{where}：name 重复了 {seen[name]} 次"))
            seen[name] = 1
        try:
            value = float(r["value"].replace(",", "").replace("，", ""))
        except ValueError:
            out.append((FAIL, f"{where}：value 只能写数字（现在是 {r['value']!r}），单位写在 unit 里"))
            continue
        try:
            factor, dims = parse_unit(r["unit"])
        except ValueError as e:
            out.append((FAIL, f"{where}：{e}"))
            continue
        if not r["unit"]:
            out.append((WARN, f"{where}：没写单位；纯数字就写 1"))
        for col, zh in (("period", "时间段"), ("source", "来源")):
            if not r[col]:
                out.append((FAIL, f"{where}：没写{zh}（{col}）"))
        if r["status"].lower() not in {s.lower() for s in NUMBER_STATUS}:
            out.append((FAIL, f"{where}：status 要写 已核实、调研、推算、假设、回测 之一（现在是 {r['status']!r}）"))
        known[name] = Quantity(value * factor, dims)
        parsed.append((line, name, value, factor, dims, r))
    recomputed = 0
    for line, name, value, factor, dims, r in parsed:
        where = f"{path.name} 第 {line} 行「{name}」"
        formula = r.get("formula", "")
        if formula:
            try:
                got = evaluate(formula, known)
            except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as e:
                out.append((FAIL, f"{where}：公式 {formula} 算不了：{e}"))
                continue
            if got.dims != dims:
                out.append((FAIL, f"{where}：公式 {formula} 算出来的单位是 {show_dims(got.dims)}，"
                                  f"表里写的是 {r['unit']}（{show_dims(dims)}）"))
            elif not _close(got.value, value * factor, rtol):
                out.append((FAIL, f"{where}：按公式 {formula} 算出 {got.value / factor:.6g} {r['unit']}，"
                                  f"表里写的是 {value:g}{_ratio_hint(got.value, value * factor)}"))
            else:
                recomputed += 1
        rule = r.get("check", "")
        if rule:
            m = re.fullmatch(r"(<=|>=|==|=|<|>)\s*(-?\d+(?:\.\d+)?(?:[eE]-?\d+)?)", rule)
            if not m:
                out.append((FAIL, f"{where}：check 要写成 >= 0 这样（现在是 {rule!r}）"))
            elif not _compare(value, m.group(1), float(m.group(2))):
                out.append((FAIL, f"{where}：{value:g} {r['unit']} 不满足 {rule}"))
    if not any(level == FAIL for level, _ in out):
        out.append((PASS, f"{path.name}：{len(parsed)} 个数字，{recomputed} 个公式按单位复算一致"))
    return out


def check_claims(path: Path) -> list[tuple[str, str]]:
    """Every verified or researched claim names its URL, its section or page and the original words."""
    rows, out = _read_csv(path, ["claim", "source_url", "locator", "quote", "status", "accessed"])
    if out:
        return out
    sourced = 0
    for line, r in enumerate(rows, start=2):
        where = f"{path.name} 第 {line} 行"
        if not r["claim"]:
            out.append((FAIL, f"{where}：没写 claim"))
            continue
        status = r["status"]
        if status.lower() not in {s.lower() for s in CLAIM_STATUS}:
            out.append((FAIL, f"{where}：status 要写 已核实、调研、推算、待核实 之一（现在是 {status!r}）"))
        if status.lower() in {s.lower() for s in SOURCED}:
            bad = len(out)
            if not re.match(r"https?://", r["source_url"]):
                out.append((FAIL, f"{where}：{status} 的结论要写来源网址（source_url）"))
            if not r["locator"]:
                out.append((FAIL, f"{where}：要写条款号或页码（locator）"))
            if not r["quote"]:
                out.append((FAIL, f"{where}：要摘一句原文（quote）"))
            elif len(r["quote"]) > 400:
                out.append((WARN, f"{where}：quote 有 {len(r['quote'])} 字，只摘关键的一两句"))
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["accessed"]):
                out.append((WARN, f"{where}：accessed 写成 YYYY-MM-DD"))
            sourced += len(out) == bad
        if status in ("待核实", "unverified"):
            out.append((WARN, f"{where}：待核实——{r['claim'][:60]}"))
    if not any(level == FAIL for level, _ in out):
        out.append((PASS, f"{path.name}：{len(rows)} 条结论，{sourced} 条有完整的原文出处"))
    return out


# --------------------------------------------------------------------------------------------------------- the log
def json_line(line: str) -> dict | None:
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def _content(event: dict) -> list[dict]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [c for c in content if isinstance(c, dict)] if isinstance(content, list) else []


def _text(content) -> str:
    if isinstance(content, list):
        return " ".join(_text(c.get("text", "")) if isinstance(c, dict) else str(c) for c in content)
    return str(content or "")


def denials_in(event: dict) -> list[str]:
    """Ids of the tool calls an event reports as refused by the permission mode."""
    if event.get("type") == "system" and event.get("subtype") == "permission_denied":
        return [str(event.get("tool_use_id"))]
    if event.get("type") == "user":
        return [str(c.get("tool_use_id")) for c in _content(event)
                if c.get("type") == "tool_result" and c.get("is_error") is True
                and DENIED in _text(c.get("content")).lower()]
    return []


def _brief(tool_input: dict) -> str:
    for key in ("command", "file_path", "url", "query", "pattern"):
        if tool_input.get(key):
            return str(tool_input[key])[:160]
    return json.dumps(tool_input, ensure_ascii=False)[:160]


def parse_log(path: Path) -> dict:
    """What a stream-json log shows the run did: each Bash command, whether it ran (had a result and was not denied)
    and whether it succeeded; the files it wrote or read; the denied calls; how often it used each tool; the final
    result event."""
    uses, results, denied, counts = {}, {}, {}, Counter()
    init = result = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            event = json_line(line)
            if event is None:
                continue
            kind = event.get("type")
            if kind == "system" and event.get("subtype") == "init":
                init = event
            elif kind == "result":
                result = event
            elif kind == "assistant":
                for c in _content(event):
                    if c.get("type") == "tool_use":
                        uses[str(c.get("id"))] = (c.get("name"), c.get("input") or {})
                        counts[c.get("name")] += 1
            elif kind == "user":
                for c in _content(event):
                    if c.get("type") == "tool_result":
                        results[str(c.get("tool_use_id"))] = c
            for tid in denials_in(event):
                denied[tid] = event.get("tool_name") or uses.get(tid, ("?", {}))[0]

    def ran(tid: str) -> bool:
        return tid in results and tid not in denied

    def ok(tid: str) -> bool:
        return ran(tid) and not results[tid].get("is_error")

    paths = {"writes": ("Write", "Edit"), "reads": ("Read",)}
    return {
        "init": init, "result": result, "tools": dict(counts),
        "bash": [{"command": inp.get("command", ""), "ran": ran(tid), "ok": ok(tid)}
                 for tid, (name, inp) in uses.items() if name == "Bash"],
        **{key: sorted({str(inp.get("file_path", "")).replace("\\", "/") for tid, (name, inp) in uses.items()
                        if name in tools and (key == "reads" or ok(tid))}) for key, tools in paths.items()},
        "denials": [{"tool": tool, "input": _brief(uses.get(tid, (tool, {}))[1])} for tid, tool in denied.items()],
    }


def _norm(command: str) -> str:
    return " ".join(str(command).strip().strip("`'\"").replace("\\", "/").split())


def _ran(claimed: str, pool: list[str]) -> bool:
    n = _norm(claimed)
    return bool(n) and any(n == e or n in e for e in pool)


# ------------------------------------------------------------------------------------------------------ the folder
def repo_root(start: Path) -> Path:
    for p in (start, *start.parents):
        if (p / ".git").exists():
            return p
    return Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _check_status(task_dir: Path) -> list[tuple[str, str]]:
    path = task_dir / "STATUS.md"
    lines = [x.strip() for x in path.read_text(encoding="utf-8", errors="replace").splitlines()
             if x.strip()] if path.exists() else []
    if not lines:
        return [(FAIL, "没有 STATUS.md，或者是空的")]
    if re.search(r"完成|阻碍|done|blocked", lines[-1], re.I):
        return [(PASS, f"STATUS.md 最后一行：{lines[-1][:80]}")]
    return [(WARN, f"STATUS.md 最后一行没写“完成”或“阻碍”：{lines[-1][:80]}")]


def _check_run(task_dir: Path, root: Path, summary: dict | None, report: dict | None,
               parsed: dict | None) -> list[tuple[str, str]]:
    out = []
    if summary is None:
        out.append((WARN, "没有 RUN_SUMMARY.json：不是用 agents/launch.py 派的任务，运行记录没法核对"))
    else:
        status = summary.get("status")
        if status == "ok":
            out.append((PASS, f"运行正常结束：{summary.get('num_turns')} 轮，约 ${summary.get('cost_usd') or 0:.2f}"))
        else:
            out.append((FAIL, f"运行没有正常结束：{STATUS_ZH.get(status, status)}"))
        if summary.get("denials"):
            listed = "；".join(f"{d['tool']} {d['input']}" for d in summary["denials"][:5])
            out.append((WARN, f"被拒 {len(summary['denials'])} 次：{listed}"))
        changed = summary.get("outside_changes") or []
        if changed:
            out.append((FAIL, f"运行期间任务目录以外有 {len(changed)} 个文件变了：{'，'.join(changed[:8])}"
                              "（也可能是同时在跑的其他会话改的，要核对）"))
    if report is None:
        if summary is not None:
            out.append((WARN, "没有结构化的最终报告 RUN_REPORT.json"))
        return out
    if report.get("status") == "done" and summary and summary.get("denials"):
        out.append((WARN, "最终报告说完成了，但运行中有被拒的操作，看一下被拒的是什么"))
    for item in report.get("deliverables") or []:
        p = str(item.get("path", "")).strip()
        hits = [q for q in (root / p, task_dir / p, task_dir / Path(p).name) if p and q.is_file()]
        if not hits or hits[0].stat().st_size == 0:
            out.append((FAIL, f"最终报告列出的交付物不存在或是空的：{p or '（没写路径）'}"))
    tests = report.get("tests") or {}
    if parsed is None:
        if report.get("commands_run") or tests.get("ran"):
            out.append((WARN, "找不到运行日志，没法核对最终报告里说运行过的命令"))
        return out
    succeeded = [_norm(b["command"]) for b in parsed["bash"] if b["ok"]]
    executed = [_norm(b["command"]) for b in parsed["bash"] if b["ran"]]
    for item in report.get("commands_run") or []:
        if not _ran(item.get("command", ""), succeeded if item.get("ok") else executed):
            out.append((FAIL, f"最终报告说运行过{'' if item.get('ok') else '（失败）'}，但日志里没有这条命令"
                              f"{'成功执行' if item.get('ok') else '执行'}的记录：{item.get('command')}"))
    if tests.get("ran"):
        command = tests.get("command") or ""
        if not _ran(command, executed):        # failing tests exit non-zero, so "ran" is the test here, not "ok"
            out.append((FAIL, f"最终报告说跑过测试，但日志里没有执行测试命令的记录：{command or '（没写命令）'}"))
        else:
            out.append((PASS, f"测试命令确实执行过：报告 {tests.get('passed', '?')} 项通过、{tests.get('failed', '?')} 项失败"))
            if tests.get("failed"):
                out.append((WARN, f"有 {tests['failed']} 项测试没通过"))
    return out


def check_task(task_dir: Path, log: Path | None = None) -> list[tuple[str, str]]:
    """Every automatic check that applies to a task folder, as (FAIL | WARN | PASS, message) pairs."""
    task_dir = Path(task_dir).resolve()
    root = repo_root(task_dir)
    summary = _read_json(task_dir / "RUN_SUMMARY.json")
    report = _read_json(task_dir / "RUN_REPORT.json")
    if log is None and summary and summary.get("log"):
        log = root / summary["log"]
    parsed = parse_log(Path(log)) if log and Path(log).exists() else None
    out = _check_status(task_dir) + _check_run(task_dir, root, summary, report, parsed)
    numbers, claims = task_dir / "NUMBERS.csv", task_dir / "CLAIMS.csv"
    if numbers.exists():
        out += check_numbers(numbers)
    elif any((task_dir / n).exists() for n in ("RESULTS.md", "REPORT.md", "RESEARCH_REPORT.md")):
        out.append((WARN, "有报告但没有 NUMBERS.csv：报告里算出来的数字没法自动复算"))
    if claims.exists():
        out += check_claims(claims)
    elif summary and summary.get("profile") == "research":
        out.append((FAIL, "调研任务要交 CLAIMS.csv（每条事实结论的出处和原文）"))
    return out


def check_preflight(task_dir: Path, parsed: dict | None, profile: str, version: str | None) -> list[tuple[str, str]]:
    """Whether a preflight-only run proved its permissions: it wrote in the task folder, ran Python there (profile
    code) and saw AGENTS.md in its system prompt (it was asked to copy the version line without reading the file)."""
    marker = Path(task_dir) / "_preflight_ok.md"
    text = marker.read_text(encoding="utf-8", errors="replace") if marker.exists() else ""
    out = [(PASS, "写任务目录：可以") if text.strip() else (FAIL, "写任务目录：不行（自检文件没写出来）")]
    if parsed is None:
        return out + [(FAIL, "找不到运行日志")]
    if profile == "code":
        ran = any(b["ok"] and "_preflight.py" in b["command"] for b in parsed["bash"])
        out.append((PASS, "运行 Python：可以") if ran else (FAIL, "运行 Python：不行"))
    if version:
        read_it = any(p == "AGENTS.md" or p.endswith("/AGENTS.md") for p in parsed["reads"])
        if read_it:
            out.append((WARN, "共用规则：它自己去读了 AGENTS.md，没法判断系统提示里有没有"))
        elif version in text:
            out.append((PASS, f"共用规则：系统提示里有 AGENTS.md（版本 {version}）"))
        else:
            out.append((FAIL, "共用规则：自检文件里没有 AGENTS.md 的版本号，可能没传进系统提示"))
    out += [(WARN, f"被拒：{d['tool']} {d['input']}") for d in parsed["denials"]]
    return out


def print_findings(findings: list[tuple[str, str]]) -> None:
    order = {FAIL: 0, WARN: 1, PASS: 2}
    for level, message in sorted(findings, key=lambda f: order[f[0]]):
        print(f"{level}  {message}")
    n = Counter(level for level, _ in findings)
    print(f"\n检查结果：{n[FAIL]} 项不通过，{n[WARN]} 项要看一下，{n[PASS]} 项通过")


def utf8_stdout() -> None:
    """Windows consoles default to a narrower code page; a captured stream (tests) has no reconfigure."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    utf8_stdout()
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    log = Path(argv[argv.index("--log") + 1]) if "--log" in argv else None
    findings = check_task(Path(argv[0]), log)
    print_findings(findings)
    return 1 if any(level == FAIL for level, _ in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
