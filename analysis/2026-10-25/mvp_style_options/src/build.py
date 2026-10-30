"""Assemble the prototype as one self-contained HTML file.

Round 5 (2026-10-27): the product prototype is scheme A only. Its source is split by page:
  engine.js            core: data, chart helpers, the plan page, settings, the shell (opens one function scope)
  pages/<name>.js      one page each; runs inside the same scope, after engine.js; registers SCREENS.<name>, PAGES.<name>
  init.js              start-up, runs last and closes the scope
  pages/<name>.html    the page's section, expanded into layout_a.html at {{PAGE_<NAME>}}
  pages/<name>.css     the page's own styles, appended after base.css and a.css
  fragments.html       shared HTML pieces ({{NAME}})
B to E are frozen at round 4; their HTML files stay as built. Rebuilding them from this source is possible with
`build.py b c d e` but they keep the round-4 page ids, so the round-5 pages do not render there.

Usage: build.py [a b c d e] [--out DIR]"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
PAGES = HERE / "pages"

FONTS = {
    "a": "family=Inter:wght@400;500;600;700",
    "b": "family=Inter:wght@400;500;600;700&family=Source+Serif+4:ital,wght@0,400;0,600;0,700;1,400",
    "c": "family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600",
    "d": "family=Plus+Jakarta+Sans:wght@400;500;600;700;800",
    "e": "family=Roboto:wght@400;500;600;700",
}
LAYOUTS = {
    "a": ("A_minimal_focus.html", "A minimal focus"),
    "b": ("B_editorial_briefing.html", "B editorial briefing"),
    "c": ("C_dark_console.html", "C dark console"),
    "d": ("D_soft_bento.html", "D soft cards"),
    "e": ("E_industry_tool.html", "E industry tool"),
}

args = sys.argv[1:]
out_dir = HERE.parent  # the HTML files sit next to src/
if "--out" in args:
    i = args.index("--out")
    out_dir = pathlib.Path(args[i + 1])
    args = args[:i] + args[i + 2:]
keys = args or ["a"]
for k in keys:
    if k not in LAYOUTS:
        sys.exit(f"unknown scheme {k!r}; use a b c d e")
    if k != "a":
        print(f"warning: {k} is frozen at round 4; rebuilt from the round-5 source its pages may not render", file=sys.stderr)

frag = {}
for m in re.finditer(r"<!--@([A-Z_]+)-->\n?(.*?)\n?<!--@/\1-->", (HERE / "fragments.html").read_text(encoding="utf-8"), re.S):
    frag[m.group(1)] = m.group(2)
page_files = sorted(PAGES.glob("*.html"))
for p in page_files:
    frag["PAGE_" + p.stem.upper()] = p.read_text(encoding="utf-8").rstrip("\n")


def expand(s):
    for _ in range(6):
        s2 = re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: frag[m.group(1)] if m.group(1) in frag else m.group(0), s)
        if s2 == s:
            break
        s = s2
    return s


base = (HERE / "base.css").read_text(encoding="utf-8")
page_css = "".join("\n/* ===== page: " + p.stem + " ===== */\n" + p.read_text(encoding="utf-8") for p in sorted(PAGES.glob("*.css")))
engine = (HERE / "engine.js").read_text(encoding="utf-8").rstrip("\n")
# round 7: the plan page reads one real trading day of the model (backtest replay), exported for the redesign
# (analysis/2026-10-26/ui_redesign/data/site_days.json, DATA_CONTRACT.md section 1). Read only; nothing is recomputed.
import json  # noqa: E402

SITE_DAYS = HERE.parents[2] / "2026-10-26" / "ui_redesign" / "data" / "site_days.json"
SITE_DAY = "2026-02-02"
_day = [d for d in json.loads(SITE_DAYS.read_text(encoding="utf-8")) if d["day"] == SITE_DAY]
if len(_day) != 1:
    sys.exit(f"{SITE_DAYS}: trading day {SITE_DAY} not found")
if "/*@SITE_DAY@*/null" not in engine:
    sys.exit("engine.js: placeholder /*@SITE_DAY@*/null missing")
engine = engine.replace("/*@SITE_DAY@*/null", json.dumps(_day[0], ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
page_js = "".join("\n\n/* ===== page: " + p.stem + " ===== */\n" + p.read_text(encoding="utf-8").rstrip("\n") for p in sorted(PAGES.glob("*.js")))
init = (HERE / "init.js").read_text(encoding="utf-8").rstrip("\n")
for key in keys:
    fname, label = LAYOUTS[key]
    body = expand((HERE / f"layout_{key}.html").read_text(encoding="utf-8"))
    for k in LAYOUTS:
        body = body.replace("{{ON_" + k.upper() + "}}", "on" if k == key else "")
    left = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", body)))
    if left:
        sys.exit(f"{key}: unknown fragments {left}")
    css = base + "\n/* ===== " + label + " ===== */\n" + (HERE / f"{key}.css").read_text(encoding="utf-8") + page_css
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GreenSpark Dispatch Desk · {label} (design prototype)</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?{FONTS[key]}&display=swap">
<script>if (/[?&]clean=1\\b/.test(location.search)) document.documentElement.className += " clean";</script>
<style>
{css}</style>
</head>
{body}
<script>
{engine}{page_js}

{init}</script>
</body>
</html>
"""
    ids = re.findall(r'\bid="([^"]+)"', body)
    dup = sorted({x for x in ids if ids.count(x) > 1})
    if dup:
        sys.exit(f"{key}: duplicate ids {dup}")
    (out_dir / fname).write_text(html, encoding="utf-8")
    print(f"{out_dir / fname}: {len(html):,} bytes")
