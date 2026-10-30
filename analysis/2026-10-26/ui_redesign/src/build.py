"""Assemble GreenSpark.html: layout.html + app.css + the exported data (data/*.json, inlined) + engine.js.
A data file that is missing is left out; the page then shows Not available where it would be used.
Usage: build.py"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
DATA = {"site_days": "site_days.json", "site_summary": "site_summary.json", "fleet": "fleet.json"}

data, missing = {}, []
for key, fname in DATA.items():
    p = ROOT / "data" / fname
    if p.exists():
        data[key] = json.loads(p.read_text(encoding="utf-8"))
    else:
        missing.append(fname)

body = (HERE / "layout.html").read_text(encoding="utf-8")
ids = re.findall(r'\bid="([^"]+)"', body)
dup = sorted({x for x in ids if ids.count(x) > 1})
if dup:
    sys.exit(f"duplicate ids {dup}")

# keep "</script>" inside strings from closing the data block
blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GreenSpark · Battery decisions</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<style>
{(HERE / "app.css").read_text(encoding="utf-8")}</style>
</head>
{body}
<script>window.GS_DATA = {blob};</script>
<script>
{(HERE / "engine.js").read_text(encoding="utf-8")}</script>
</body>
</html>
"""
(ROOT / "GreenSpark.html").write_text(html, encoding="utf-8", newline="\n")
print(f"GreenSpark.html: {len(html):,} bytes" + (f"; missing data: {', '.join(missing)}" if missing else ""))
