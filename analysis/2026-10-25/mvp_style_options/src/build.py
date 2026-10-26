"""Assemble the five style options as self-contained HTML files: base.css + <x>.css, layout_<x>.html with
shared fragments expanded, and the shared engine.js. Usage: build.py [a b c d e]"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
OUT = HERE.parent  # the HTML files sit next to src/

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

frag = {}
for m in re.finditer(r"<!--@([A-Z_]+)-->\n?(.*?)\n?<!--@/\1-->", (HERE / "fragments.html").read_text(encoding="utf-8"), re.S):
    frag[m.group(1)] = m.group(2)


def expand(s):
    for _ in range(5):
        s2 = re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: frag[m.group(1)] if m.group(1) in frag else m.group(0), s)
        if s2 == s:
            break
        s = s2
    return s


base = (HERE / "base.css").read_text(encoding="utf-8")
engine = (HERE / "engine.js").read_text(encoding="utf-8")
for key in (sys.argv[1:] or list(LAYOUTS)):
    fname, label = LAYOUTS[key]
    body = expand((HERE / f"layout_{key}.html").read_text(encoding="utf-8"))
    for k in LAYOUTS:
        body = body.replace("{{ON_" + k.upper() + "}}", "on" if k == key else "")
    left = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", body)))
    if left:
        sys.exit(f"{key}: unknown fragments {left}")
    css = base + "\n/* ===== " + label + " ===== */\n" + (HERE / f"{key}.css").read_text(encoding="utf-8")
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GreenSpark Dispatch Desk · {label} (style option, illustrative data)</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?{FONTS[key]}&display=swap">
<script>if (/[?&]clean=1\\b/.test(location.search)) document.documentElement.className += " clean";</script>
<style>
{css}</style>
</head>
{body}
<script>
{engine}</script>
</body>
</html>
"""
    for i in ("id=\"",):
        ids = re.findall(r'\bid="([^"]+)"', body)
        dup = sorted({x for x in ids if ids.count(x) > 1})
        if dup:
            sys.exit(f"{key}: duplicate ids {dup}")
    (OUT / fname).write_text(html, encoding="utf-8", newline="\n")
    print(f"{fname}: {len(html):,} bytes")
