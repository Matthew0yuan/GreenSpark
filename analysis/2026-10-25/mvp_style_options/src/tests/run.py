"""Click checks and word counts for the built prototype, in headless Chrome.

  python3.13 src/tests/run.py                 build A, run every src/tests/*.test.js, print PASS/FAIL lines
  python3.13 src/tests/run.py plan calls      only these test files
  python3.13 src/tests/run.py --words         words in the first 1440x900 viewport of every page (see budget in ROUND5.md)
  python3.13 src/tests/run.py --shots DIR     screenshot every page at 1440x900 and 1280x720 into DIR
  python3.13 src/tests/run.py --html FILE     test an already built file instead of building

A test file is plain JavaScript that runs after the page loaded. It gets `ok(name, cond, detail)` and `done()`; it must
call done() at the end. Helpers: $, $$ (page scope is not visible), txt(sel), click(el), setExp(v), goTo(page).
Chrome is looked up at CHROME, or the default macOS path."""
import html as html_mod
import os
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
SRC = HERE.parent
ROOT = SRC.parent
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PAGES = ["plan", "evidence", "calls", "value", "config"]

PRELUDE = r"""
<script>window.__errs=[];window.addEventListener('error',function(e){window.__errs.push(String(e.message)+' @'+e.lineno)});</script>
"""
HELPERS = r"""
var R = [];
function $(s){ return document.querySelector(s); }
function $$(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); }
function ok(name, cond, detail){ R.push((cond ? "PASS " : "FAIL ") + name + (detail !== undefined ? "  [" + String(detail).slice(0, 160) + "]" : "")); }
function txt(s){ var e = $(s); return e ? e.textContent.replace(/\s+/g, " ").trim() : ""; }
function click(el){ if (!el) { R.push("FAIL click on missing element"); return; } el.dispatchEvent(new MouseEvent("click", {bubbles:true})); }
function setExp(v){ var e = $("#c-exp"); e.value = v; e.dispatchEvent(new Event("change", {bubbles:true})); }
function goTo(p){ click($('[data-go="' + p + '"]')); }
function done(){ ok("no script errors", window.__errs.length === 0, JSON.stringify(window.__errs)); var pre = document.createElement("pre"); pre.id = "T"; pre.textContent = R.join("\n"); document.body.appendChild(pre); }
"""
WORDS = r"""
(function(){
  var out = [];
  %PAGES%.forEach(function(p){
    goTo(p); window.scrollTo(0, 0);
    var tot = 0, blocks = [], seen = [];
    var walk = document.createTreeWalker(document.getElementById("main"), NodeFilter.SHOW_TEXT);
    while (walk.nextNode()) {
      var n = walk.currentNode, el = n.parentElement, t = n.textContent.trim();
      if (!t || el.closest("svg") || el.closest(".screen:not(.on)") || el.closest("details:not([open])") && !el.closest("summary")) continue;
      var r = el.getBoundingClientRect(); if (!r.width || r.top >= 900 || r.bottom <= 80) continue;
      var cs = getComputedStyle(el); if (cs.visibility === "hidden" || cs.display === "none") continue;
      tot += t.split(/\s+/).length;
      if (el.closest(".tipc,.ck")) continue;   /* labels are counted as words but are not text blocks (ROUND5.md 7.4) */
      var blk = el.closest("li,p,td,th,h1,h2,h3,div,span,summary,button,label");
      if (seen.indexOf(blk) < 0) { seen.push(blk); var w = blk.textContent.trim().split(/\s+/).length; if (w >= 8) blocks.push(w + " words: " + blk.textContent.trim().replace(/\s+/g, " ").slice(0, 80)); }
    }
    out.push(p.toUpperCase() + "  words " + tot + "  blocks>=8 " + blocks.length + (blocks.length ? "\n    " + blocks.join("\n    ") : ""));
  });
  var pre = document.createElement("pre"); pre.id = "T"; pre.textContent = out.join("\n") + "\nERRS " + JSON.stringify(window.__errs); document.body.appendChild(pre);
})();
"""


def build(out_dir):
    subprocess.run([sys.executable, str(SRC / "build.py"), "a", "--out", str(out_dir)], check=True, stdout=subprocess.DEVNULL)
    return out_dir / "A_minimal_focus.html"


def inject(src_html, script):
    s = src_html.replace("<head>", "<head>" + PRELUDE, 1)
    i = s.rindex("</body>")
    return s[:i] + "<script>\n" + HELPERS + script + "\n</script>" + s[i:]


def run_chrome(path, size="1440,900", shot=None):
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=" + size, "--virtual-time-budget=5000"]
    if shot:
        cmd += ["--screenshot=" + str(shot)]
    else:
        cmd += ["--dump-dom"]
    cmd.append("file://" + str(path))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if shot:
        return ""
    m = re.search(r'<pre id="T">(.*?)</pre>', r.stdout, re.S)
    return html_mod.unescape(m.group(1)) if m else "NO RESULT (page did not finish; stdout " + str(len(r.stdout)) + " bytes)"


def main():
    args = sys.argv[1:]
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="proto_"))
    if "--html" in args:
        i = args.index("--html"); built = pathlib.Path(args[i + 1]); args = args[:i] + args[i + 2:]
    else:
        built = build(tmp)
    src_html = built.read_text(encoding="utf-8")
    if "--shots" in args:
        d = pathlib.Path(args[args.index("--shots") + 1]); d.mkdir(parents=True, exist_ok=True)
        for p in PAGES:
            for size in ("1440,900", "1280,720"):
                run_chrome(str(built) + "?clean=1#" + p, size, d / (p + "_" + size.split(",")[0] + ".png"))
        print("screenshots in", d)
        return
    if "--words" in args:
        page = tmp / "words.html"
        page.write_text(inject(src_html, WORDS.replace("%PAGES%", str([p for p in PAGES if p != "config"]))), encoding="utf-8")
        print(run_chrome(str(page) + "#plan"))
        return
    names = args or [p.stem.replace(".test", "") for p in sorted(HERE.glob("*.test.js"))]
    total = fails = 0
    for n in names:
        tf = HERE / (n + ".test.js")
        if not tf.exists():
            print("no test file", tf); fails += 1; continue
        page = tmp / (n + ".html")
        page.write_text(inject(src_html, tf.read_text(encoding="utf-8")), encoding="utf-8")
        res = run_chrome(str(page) + "#plan")
        lines = res.splitlines()
        f = [l for l in lines if not l.startswith("PASS")]
        total += len(lines); fails += len(f)
        print("== " + n + ": " + str(sum(l.startswith("PASS") for l in lines)) + " pass / " + str(len(lines)))
        for l in f:
            print("   " + l)
    print("TOTAL " + str(total - fails) + " pass, " + str(fails) + " fail")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
