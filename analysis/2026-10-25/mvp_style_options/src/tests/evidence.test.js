/* Evidence, site battery (round 6: cut to the Plan page's level, ROUND6.md; wording per ROUND5.md section 7; hover notes
   from the shared glossary GLOSS). Run with src/tests/run.py evidence. */
goTo("evidence");
function svgTxt(sel){ var s = $(sel + " svg"); return s ? Array.prototype.map.call(s.querySelectorAll("text"), function(t){ return t.textContent; }).join(" | ") : ""; }
/* every text node and tspan of a chart, one entry each (a label may hold a tspan with its own meaning) */
function svgNodes(sel){ var out = []; $$(sel + " svg text").forEach(function(t){ Array.prototype.forEach.call(t.childNodes, function(n){ var v = n.textContent.trim(); if (v) out.push(v); }); }); return out; }
function noted(el){ return !!el && (el.getAttribute("data-note") || "").length > 15 && el.tabIndex === 0; }
function note(el){ return el ? el.getAttribute("data-note") || "" : ""; }
function rows(){ return $$("#ev-dots .hit"); }
function selRow(){ return rows().filter(function(r){ return r.getAttribute("aria-pressed") === "true"; }).map(function(r){ return r.getAttribute("data-p"); }).join(); }
function colOn(){ return $$("#ev-matrix thead th.on").map(function(t){ return t.textContent; }).join(); }
/* words of a label: "·" is a separator, not a word */
function words(t){ return t.trim().split(/\s+/).filter(function(w){ return w !== "·"; }).length; }
function chipTexts(sel){ return $$(sel).map(function(c){ return c.textContent; }); }
/* the periods as ROUND5.md 7.1 names them: name, dates, days */
var PER = {ins:["Training period", "1 Apr 2025 – 30 Jun 2026", 456], tseen:["Test · reviewed", "1 Jul – 15 Oct 2026", 107], tnew:["Test · blind", "16–22 Oct 2026", 7]};
var PKEYS = ["ins", "tseen", "tnew"];
function span(p){ return PER[p][1] + " · " + PER[p][2] + " d"; }
function hasDatesDays(s, p){ return s.indexOf(PER[p][1]) >= 0 && (s.indexOf(PER[p][2] + " d") >= 0 || s.indexOf(PER[p][2] + " days") >= 0); }
var PERIOD_RE = "Training period|Test · reviewed|Test · blind", SPAN_RE = "\\d{1,2}(–\\d{1,2})?( \\w{3})?( \\d{4})?( – \\d{1,2} \\w{3} \\d{4})? · \\d+ d";
/* ---- the round-6 chart rule: only row names, units, numbers and "number + one word" labels on a chart ---- */
var WL = {
  dots:new RegExp("^(Extra profit, A\\$\\/day|[+−]?\\d+|" + PERIOD_RE + "|" + SPAN_RE + "|[+−]A\\$[\\d,]+|includes 0)$"),
  abs:new RegExp("^(" + PERIOD_RE + "|Profit per day, A\\$|Previous \\(v1\\)|This dispatcher|Prices known ahead|A\\$[\\d,]+)$"),
  q2a:new RegExp("^((" + PERIOD_RE + ") · \\d+ d|Price, all day|Price, 17:00–21:00|Spike ≥ A\\$200|error, A\\$\\/MWh|Brier score|[\\d.]+ · (yesterday|model|28-day rate)|[\\d.]+% (lower|higher) error)$")
};
function whitelist(state){
  [["#ev-dots", "dots"], ["#ev-abs", "abs"], ["#ev-q2a", "q2a"]].forEach(function(c){
    var bad = svgNodes(c[0]).filter(function(v){ return !WL[c[1]].test(v); });
    ok(state + ": " + c[0] + " text is only names, units and numbers", !bad.length, bad.join(" | "));
  });
}
/* ---- the round-6 page rule: no caption, footnote or range sentence anywhere visible; the explanations are in notes ---- */
var CAPTIONS = /dot = average|95% (range|confidence|interval)|ceiling|unattainable|lower is better|Scope|bootstrap|cost counted|not a hard limit|yesterday's prices|same days and prices|no clear gain|every input was public|114 test days|\baverage\b/;
function pageText(){ var cl = $("#s-evidence").cloneNode(true); Array.prototype.forEach.call(cl.querySelectorAll("svg"), function(g){ g.remove(); }); return cl.textContent + " | " + svgTxt("#ev-dots") + " | " + svgTxt("#ev-abs") + " | " + svgTxt("#ev-q2a"); }
function hits(re, s){ var m = s.match(new RegExp(re.source, re.flags + "g")); return m ? m.join(",") : ""; }
function notes(){ return $$("#s-evidence [data-note], #ph-chips [data-note]").map(note).join(" | "); }
function cutSentences(state){
  var t = pageText(), n = notes();
  ok(state + ": no caption or footnote sentence is visible", !CAPTIONS.test(t), hits(CAPTIONS, t));
  var need = [/Profit per day = energy sold \+ grid services \+ the factory's network bill saving − battery wear/, /replayed on the same days with the same prices/, /The 95% range crosses zero/, /A cost, not a hard limit/,
              /cost counted/, /Lower is better; it is not an accuracy percentage/, /An upper bound no dispatcher can reach/, /114 test days, 1 Jul – 22 Oct 2026: 107 reviewed, 7 blind/, /yesterday's price for the same half-hour/, /28-day rate/, /synthetic/];
  var missing = need.filter(function(re){ return !re.test(n); }).map(String);
  ok(state + ": every cut sentence is still in a hover note", !missing.length, missing.join(" "));
}
/* ---- the wording rules (ROUND5.md 7), checked in every state ---- */
var JARGON_HTML = /In-sample|Test seen|Test unseen|frozen v1|Frozen v1|Uplift|\bpriced\b|Adopted dispatch|Perfect price foresight|Hard-limit|economic limit|28-day frequency|Brier baseline|Meter limits|Power rating|No double count|Inputs by 09:00|Constraint checks/;
var JARGON_SEEN = /in-sample|out-of-sample|\bseen\b|unseen|frozen|uplift(?! yet)|\bpriced\b|foresight|hard-limit|economic limit|\binterval\b|\bmean\b/i;
function visible(){ return txt("#ph-ctx") + " | " + txt("#ph-chips") + " | " + $("#s-evidence").textContent; }
var ACR = [["MAE", /Mean absolute error/i], ["Brier", /How wrong the model's probability of a price spike/], ["CMD", /import contract \(CMD\): Contract maximum demand/i],
           ["FCESS", /grid services \(FCESS\): Frequency control services/i], ["SOC", /state of charge \(SOC\)/i], ["AEMO", /market operator.{0,4}\(AEMO\)/i],
           ["WEM", /Wholesale Electricity Market \(WEM\)/], ["IRCR", /individual reserve capacity requirement/i]];
var ACR_RE = /\b(MAE|Brier|CMD|FCESS|SOC|AEMO|WEM|IRCR)\b/;
function wording(state){
  var html = $("#s-evidence").innerHTML + txt("#ph-ctx") + $("#ph-chips").innerHTML;
  ok(state + ": no internal jargon in labels, notes or aria-labels", !JARGON_HTML.test(html), hits(JARGON_HTML, html));
  ok(state + ": no internal jargon in visible text", !JARGON_SEEN.test(visible()), hits(JARGON_SEEN, visible()));
  var known = visible() + " | " + notes(), used = [], missing = [];
  ACR.forEach(function(a){ if (new RegExp("\\b" + a[0] + "\\b").test(html)) { used.push(a[0]); if (!a[1].test(known)) missing.push(a[0]); } });
  ok(state + ": acronyms MAE, Brier, CMD, FCESS are used and explained", ["MAE", "Brier", "CMD", "FCESS"].every(function(a){ return used.indexOf(a) >= 0; }), used.join());
  ok(state + ": every acronym used has its long form shown or in a hover note", !missing.length, "missing: " + missing.join() + " · used: " + used.join());
  /* an acronym in text sits under a hover note (HTML) or, on a chart, its text carries a hover area (data-hot) */
  var bare = [], w = document.createTreeWalker($("#s-evidence"), NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { var n = w.currentNode, el = n.parentElement;
    if (!ACR_RE.test(n.textContent)) continue;
    if (el.closest("svg")) { if (!el.closest("text").hasAttribute("data-hot")) bare.push(n.textContent); }
    else if (!el.closest("[data-note]")) bare.push(n.textContent); }
  ok(state + ": every acronym in text carries a hover note", !bare.length, bare.join(" | "));
  /* every period label carries its dates and days: shown, or in the same element's hover note */
  var bad = [];
  PKEYS.forEach(function(p){
    var nm = $('#ev-dots text[data-n="' + p + '"]'), dd = $('#ev-dots text[data-d="' + p + '"]');
    if (!nm || nm.textContent !== PER[p][0] || !dd || dd.textContent !== span(p)) bad.push("dot row " + p);
  });
  $$("#ev-matrix thead th").forEach(function(th, i){ var p = PKEYS[i]; if (th.textContent !== PER[p][0] || !hasDatesDays(note(th), p)) bad.push("matrix column " + p); });
  var sp = S_PER(), hp = $("#ev-head .ev-per");
  if (!hp || hp.textContent !== PER[sp][0] + " · " + PER[sp][2] + " d" || !hasDatesDays(note(hp), sp)) bad.push("headline period");
  var at = svgTxt("#ev-abs"), ap = $("#ev-abs text.lt");
  if (at.indexOf(PER[sp][0]) < 0 || !ap || !hasDatesDays(ap.getAttribute("data-hot") || "", sp)) bad.push("bars period");
  var qp = $("#ev-q2a text[data-q2p]");
  if (!qp || qp.textContent !== PER[sp][0] + " · " + PER[sp][2] + " d" || !hasDatesDays(qp.getAttribute("data-hot") || "", sp)) bad.push("price forecast period");
  var ft = $("#ev-mx-chips .tipc");
  if (!ft || !/1 Jul – 22 Oct 2026/.test(note(ft)) || !/114 test days/.test(note(ft))) bad.push("test-days chip");
  ok(state + ": every period label carries its dates and days", !bad.length, bad.join(", "));
  /* round 6: the decision row holds at most 4 chips of at most 4 words */
  var dc = $$("#ev-dec .tipc, #ev-dec .ck").filter(function(c){ return !c.classList.contains("ev-def"); });
  ok(state + ": decision row ≤ 4 chips, each ≤ 4 words", dc.length <= 4 && dc.every(function(c){ return words(c.textContent.replace(/^[!✓]/, "")) <= 4; }), dc.map(function(c){ return c.textContent; }).join("|"));
  whitelist(state); cutSentences(state); glossary(state);
}
/* ---- the shared glossary: notes reuse GLOSS wording (page scope is not visible, so a distinctive phrase of each entry is checked) ---- */
var G = {DISPATCHER:"this tool's scheduling software", RUN:"A saved replay of past days with one fixed set of inputs", CMD:"Contract maximum demand: the grid import the factory has contracted for",
         NETBILL:"The charge the network operator bills the factory", FCESS:"Frequency control services: the battery is paid", GRIDRESERVE:"Energy the battery must keep so it can deliver",
         BACKUP:"Energy kept so the factory can ride through a grid outage", SOC:"How much energy the battery holds at a moment", MAE:"Mean absolute error: the average gap",
         BRIER:"How wrong the model's probability of a price spike", TRAIN:"the data used to build and choose the method", TREV:"looked at repeatedly while refining it",
         TBLIND:"never looked at before the method was locked", HOTSEASON:"1 Dec – 31 Mar in the WA market: when system demand peaks", DECISION:"The plan is made at 09:00 each trading day",
         AEMO:"The Australian Energy Market Operator runs the wholesale market", HINDSIGHT:"Known only after the fact."};
var PG = {ins:"TRAIN", tseen:"TREV", tnew:"TBLIND"};
function has(el, keys){ var n = el ? (el.getAttribute("data-note") || el.getAttribute("data-hot") || "") : ""; return !!el && keys.every(function(k){ return n.indexOf(G[k]) >= 0; }); }
var BANNED = /frozen|out-of-sample|in-sample|unseen/i;
function glossary(state){
  var all = $$("#s-evidence [data-note], #ph-chips [data-note]"), sp = S_PER(), bad = [];
  var banned = all.filter(function(el){ return BANNED.test(note(el)); }).map(function(el){ return hits(BANNED, note(el)); });
  ok(state + ": no note says frozen, out-of-sample, in-sample or unseen", !banned.length, banned.join(" | "));
  if (!has($("#ev-head .ev-def"), ["DISPATCHER"])) bad.push("headline dispatcher");
  if (!has($("#ev-head .ev-num"), ["NETBILL", "CMD"])) bad.push("headline number");
  if (!has($("#ev-head .ev-per"), [PG[sp]])) bad.push("headline period");
  $$("#ev-matrix thead th").forEach(function(th, i){ if (!has(th, [PG[PKEYS[i]]])) bad.push("matrix column " + i); });
  if (!has($("#ev-dots .ev-ov"), ["TRAIN"])) bad.push("training chip in the dot plot");
  $$("#ev-chips .ev-cav").forEach(function(c){ if (!has(c, [PG[sp]])) bad.push("caveat chip " + c.textContent); });
  if (!has($("#ev-chips .tipc:not(.ev-cav)"), ["RUN"])) bad.push("run chip");
  if (!has($("#ev-viol .ck"), ["CMD"])) bad.push("import contract tile");
  var mr = $$("#ev-matrix tbody th");
  [["SOC"], [], ["GRIDRESERVE", "BACKUP"], [], ["FCESS"], ["DECISION", "AEMO"], ["CMD"]].forEach(function(k, i){ if (!has(mr[i], k)) bad.push("matrix row " + i); });
  if (!has($("#ev-mx-chips .tipc"), ["TREV", "TBLIND", "HOTSEASON"])) bad.push("matrix chip");
  var ah = $$("#ev-abs [data-hot]");
  if (ah.length !== 5 || !has(ah[0], [PG[sp]]) || !has(ah[1], ["NETBILL", "CMD"]) || !has(ah[2], ["DISPATCHER"]) || !has(ah[3], ["DISPATCHER"]) || !has(ah[4], ["HINDSIGHT"])) bad.push("bar labels");
  var qh = $$("#ev-q2a [data-hot]");
  if (qh.length !== 7 || !has(qh[0], [PG[sp]]) || !has(qh[1], ["MAE"]) || !has(qh[2], ["MAE"]) || !has(qh[3], ["MAE"]) || !/evening peak/.test(qh[3].getAttribute("data-hot")) || !has(qh[5], ["BRIER"]) || !has(qh[6], ["BRIER"])) bad.push("price forecast labels");
  ok(state + ": acronyms, periods and 'dispatcher' take their notes from the glossary", !bad.length, bad.join(", "));
  ok(state + ": every matrix row note says why it is a check", mr.length === 7 && mr.every(function(th){ return /Why it is a check: \w/.test(note(th)); }));
  /* hover areas over chart labels exist for every data-hot (evHots) */
  ok(state + ": every chart label with a note has its hover area", $$("#ev-abs .ev-hot").length === 5 && $$("#ev-q2a .ev-hot").length === 7 && $$("#ev-abs .ev-hot, #ev-q2a .ev-hot").every(noted), $$("#ev-abs .ev-hot").length + "/" + $$("#ev-q2a .ev-hot").length);
  var qt = svgTxt("#ev-q2a");
  ok(state + ": price forecast says 'lower error', no bare minus percentage", (qt.match(/\d+\.\d% lower error/g) || []).length === 3 && !/[−-]\d+(\.\d+)?%/.test(qt), qt.slice(0, 200));
  /* a reserve is always named: grid-service reserve or grid reserve (factory backup is its own word) */
  var ws = visible() + " | " + svgTxt("#ev-dots") + " | " + svgTxt("#ev-abs") + " | " + svgTxt("#ev-q2a") + " | " + notes(), bare = [], re = /(.{0,14})\breserves?\b/gi, m;
  while ((m = re.exec(ws))) if (!/grid(-service)? $/i.test(m[1])) bare.push(m[0]);
  ok(state + ": no bare 'reserve' (always grid-service reserve or factory backup)", !bare.length, bare.join(" | "));
}
function S_PER(){ return selRow() || "tseen"; }

/* ---- the first screen (1440×900): words as run.py --words counts them ---- */
(function(){
  window.scrollTo(0, 0);
  var tot = 0, blocks = [], seen = [], walk = document.createTreeWalker(document.getElementById("main"), NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) {
    var n = walk.currentNode, el = n.parentElement, t = n.textContent.trim();
    if (!t || el.closest("svg") || el.closest(".screen:not(.on)") || el.closest("details:not([open])") && !el.closest("summary")) continue;
    var r = el.getBoundingClientRect(); if (!r.width || r.top >= 900 || r.bottom <= 80) continue;
    var cs = getComputedStyle(el); if (cs.visibility === "hidden" || cs.display === "none") continue;
    tot += t.split(/\s+/).length;
    if (el.closest(".tipc,.ck")) continue;
    var blk = el.closest("li,p,td,th,h1,h2,h3,div,span,summary,button,label");
    if (seen.indexOf(blk) < 0) { seen.push(blk); var w = blk.textContent.trim().split(/\s+/).length; if (w >= 8) blocks.push(w + ": " + blk.textContent.trim().slice(0, 60)); }
  }
  ok("first screen ≤ 75 words (Plan is 61)", tot <= 75, tot);
  ok("first screen: at most one block of 8+ words (the header line)", blocks.length <= 1, blocks.join(" | "));
})();

/* ---- default: Test · reviewed ---- */
ok("page opens alone", $("#s-evidence").classList.contains("on") && $$(".screen.on").length === 1);
ok("header chip 2 · Prove only", txt("#ph-chips") === "2 · Prove", txt("#ph-chips"));
ok("header context: dates and days, ≤ 12 words", txt("#ph-ctx") === "1 Apr 2025 – 22 Oct 2026 · 570 days replayed" && txt("#ph-ctx").split(/\s+/).length <= 12, txt("#ph-ctx"));
ok("headline", txt("#ev-head .ev-hl") === "+A$350 /day vs previous dispatcher (v1)", txt("#ev-head .ev-hl"));
ok("headline small: +20.6% and the period", txt("#ev-head .ev-sub") === "+20.6% Test · reviewed · 107 d", txt("#ev-head .ev-sub"));
ok("headline number green, not grey", !$("#ev-head .ev-num").classList.contains("zero"));
ok("headline number opens what it is made of", noted($("#ev-head .ev-num")) && /energy sold \+ grid services \+ the factory's network bill saving − battery wear/.test(note($("#ev-head .ev-num"))) && /synthetic/.test(note($("#ev-head .ev-num"))));
ok("previous dispatcher: a glossary chip on hover", noted($("#ev-head .ev-def")) && $("#ev-head .ev-def").classList.contains("tipc") && $("#ev-head .ev-def").textContent === "previous dispatcher (v1)" && /previous version \(v1\) is kept unchanged as the baseline/.test(note($("#ev-head .ev-def"))));
ok("headline period: dates on hover", noted($("#ev-head .ev-per")) && /1 Jul – 15 Oct 2026, 107 days/.test(note($("#ev-head .ev-per"))) && /looked at repeatedly/.test(note($("#ev-head .ev-per"))));
ok("decision chips: only the run", chipTexts("#ev-chips .tipc").join("|") === "run R‑0142" && noted($("#ev-chips .tipc")) && /placeholders/.test(note($("#ev-chips .tipc"))), chipTexts("#ev-chips .tipc").join("|"));
ok("no caveat chip by default", !$("#ev-chips .ev-cav"));
ok("limits broken: caption and 0 of 5,136 half-hours", txt("#ev-viol .cap") === "Limits broken" && noted($("#ev-viol .cap [data-note]")) && /9 MW import contract is a cost, not a hard limit/.test(note($("#ev-viol .cap [data-note]"))) && txt("#ev-viol .dv") === "0 of 5,136 half-hours" && $("#ev-viol .dv").classList.contains("ok"), txt("#ev-viol .dv"));
ok("import contract tile: exceeded, cost counted on hover", $("#ev-viol .ck.w") && txt("#ev-viol .ck") === "!9 MW contract exceeded" && noted($("#ev-viol .ck")) && /^Contract maximum demand: /.test(note($("#ev-viol .ck"))) && /Exceeding it is allowed; the extra demand charge is counted in the result/.test(note($("#ev-viol .ck"))) && /at times/.test(note($("#ev-viol .ck"))), txt("#ev-viol"));

/* dot plot */
var dsvg = $("#ev-dots svg"), dt = svgTxt("#ev-dots");
ok("dot plot is an image with a label that explains dot and line", dsvg && dsvg.getAttribute("role") === "img" && /dot is the average, the line its 95% confidence range/.test(dsvg.getAttribute("aria-label")));
ok("three rows, Test · reviewed selected", rows().length === 3 && selRow() === "tseen", selRow());
["Extra profit, A$/day", "Training period", "Test · reviewed", "Test · blind", "1 Apr 2025 – 30 Jun 2026 · 456 d", "1 Jul – 15 Oct 2026 · 107 d", "16–22 Oct 2026 · 7 d",
 "+A$410", "+A$350", "+A$70", "+A$190", "+A$620", "+A$140", "+A$560", "−A$20", "+A$160"].forEach(function(w){ ok("dot plot label: " + w, dt.indexOf(w) >= 0); });
ok("dot plot: no 'average' or '95% range' words, only the numbers", !/average|range/.test(dt), dt);
ok("'includes 0' only on the 7-day row", (dt.match(/includes 0/g) || []).length === 1);
var zh = $$("#ev-dots .ev-hot");
ok("'includes 0' opens its note on hover", zh.length === 1 && noted(zh[0]) && note(zh[0]) === "The 95% range crosses zero: we cannot tell this period's gain from luck.", zh.length);
ok("zero line drawn", dsvg.querySelectorAll('line[style*="axis-strong"]').length === 1);
var dots = dsvg.querySelectorAll("circle");
ok("three dots: green, green, grey; training period hollow", dots.length === 3 && /--ok/.test(dots[0].getAttribute("style")) && /fill:var\(--surface\)/.test(dots[0].getAttribute("style")) && /fill:var\(--ok\)/.test(dots[1].getAttribute("style")) && /--text-4/.test(dots[2].getAttribute("style")));
var ins = $("#ev-dots .ev-ov");
ok("training-period row carries 'not a test' with the caveat", ins && ins.textContent === "not a test" && noted(ins) && /cannot show how the method does on new data/.test(note(ins)) && /1 Apr 2025 – 30 Jun 2026/.test(note(ins)));
var nmr = $('#ev-dots text[data-n="ins"]').getBoundingClientRect(), ir = ins.getBoundingClientRect(), zr = dsvg.querySelector('line[style*="axis-strong"]').getBoundingClientRect();
ok("'not a test' between the name and the zero line", ir.left > nmr.right && ir.right < zr.left - 4, Math.round(nmr.right) + " < " + Math.round(ir.left) + "…" + Math.round(ir.right) + " < " + Math.round(zr.left));
ok("rows are keyboard buttons", rows().every(function(r){ return r.getAttribute("role") === "button" && r.getAttribute("tabindex") === "0" && r.getAttribute("aria-label").length > 30; }));
ok("row names say the dates in their aria-label", /^Test · reviewed, 1 Jul – 15 Oct 2026, 107 days: extra \+A\$350 per day/.test(rows()[1].getAttribute("aria-label")), rows()[1].getAttribute("aria-label"));
/* no two labels overlap on the dot plot */
(function(){ var ts = $$("#ev-dots svg text").map(function(e){ var b = e.getBBox(); return {t:e.textContent, x:b.x, y:b.y, r:b.x + b.width, b:b.y + b.height}; }), bad = [];
  for (var i = 0; i < ts.length; i++) for (var j = i + 1; j < ts.length; j++) { var p = ts[i], q = ts[j]; if (p.x < q.r && q.x < p.r && p.y < q.b && q.y < p.b) bad.push(p.t + " × " + q.t); }
  ok("no two labels overlap on the dot plot", !bad.length, bad.join("; ")); })();

/* absolute bars for the selected period */
var at = svgTxt("#ev-abs");
["Test · reviewed", "Profit per day, A$", "Previous (v1)", "A$1,700", "This dispatcher", "A$2,050", "Prices known ahead", "A$3,300"].forEach(function(w){ ok("bars label: " + w, at.indexOf(w) >= 0); });
ok("bars: names end before the bars start", $$("#ev-abs text.ann").every(function(t){ return t.getBBox().x + t.getBBox().width < +$("#ev-abs rect[rx]").getAttribute("x") - 6; }));
ok("'prices known ahead' hatched, the only hatch; its note says unattainable", $$("#ev-abs rect").filter(function(r){ return /^url\(#hEv\)/.test(r.getAttribute("fill") || ""); }).length === 1 && $$("#s-evidence pattern").length === 1 && /upper bound no dispatcher can reach/.test($$("#ev-abs [data-hot]")[4].getAttribute("data-hot")));

/* price forecast */
var qt = svgTxt("#ev-q2a");
["Test · reviewed · 107 d", "Price, all day", "Price, 17:00–21:00", "Spike ≥ A$200", "error, A$/MWh", "Brier score", "33 · model", "45 · yesterday", "26.7% lower error", "71 · model", "99 · yesterday", "28.3% lower error", "0.064 · model", "0.077 · 28-day rate", "16.9% lower error"].forEach(function(w){ ok("price forecast label: " + w, qt.indexOf(w) >= 0, w); });
var qx = +$("#ev-q2a rect").getAttribute("x");
ok("price forecast: metric names end before the bars start", $$("#ev-q2a text.lt:not([data-q2p]), #ev-q2a text.lu").every(function(t){ return t.getBBox().x + t.getBBox().width < qx - 4; }));
ok("metric names explain the error and its baseline on hover", /Mean absolute error/.test($$("#ev-q2a [data-hot]")[1].getAttribute("data-hot")) && /yesterday's price for the same half-hour/.test($$("#ev-q2a [data-hot]")[1].getAttribute("data-hot")) && /previous 28 days/.test($$("#ev-q2a [data-hot]")[5].getAttribute("data-hot")));
ok("no chip row under the price forecast", !$("#ev-q2a-chips"));

/* limits matrix */
var mr = $$("#ev-matrix tbody tr");
ok("matrix: 7 rows × 3 periods", mr.length === 7 && mr.every(function(r){ return r.querySelectorAll("td").length === 3; }) && $$("#ev-matrix thead th").length === 3);
ok("matrix columns: period names, dates and days on hover", $$("#ev-matrix thead th").map(function(t){ return t.textContent; }).join("|") === "Training period|Test · reviewed|Test · blind" && $$("#ev-matrix thead th").every(noted) && /1 Jul – 15 Oct 2026 · 107 d/.test(note($$("#ev-matrix thead th")[1])), $$("#ev-matrix thead th").map(function(t){ return t.textContent; }).join("|"));
ok("matrix row names ≤ 3 words", mr.map(function(r){ return r.querySelector("th").textContent; }).join("|") === "Energy adds up|Within 10 MW|Grid reserve + backup|Connection limits|Nothing sold twice|Only 09:00 data|Import contract 9 MW" && mr.every(function(r){ return words(r.querySelector("th").textContent.replace(" + ", " ")) <= 4; }), mr.map(function(r){ return r.querySelector("th").textContent; }).join("|"));
ok("row names carry the definitions", mr.every(function(r){ return noted(r.querySelector("th")); }) && /SOC\(t\+1\)/.test(note(mr[0].querySelector("th"))) && /never share the same MW or MWh/.test(note(mr[4].querySelector("th"))) && /^Contract maximum demand: /.test(note(mr[6].querySelector("th"))) && /A cost, not a hard limit/.test(note(mr[6].querySelector("th"))) && /grid-service reserve plus the factory backup/.test(note(mr[2].querySelector("th"))));
ok("cells are icons only", $$("#ev-matrix td.c").every(function(c){ return /^[✓!✕]$/.test(c.textContent.trim()); }), $$("#ev-matrix td.c").map(function(c){ return c.textContent.trim(); }).join());
ok("six hard limits: ✓ everywhere", mr.slice(0, 6).every(function(r){ return Array.prototype.every.call(r.querySelectorAll("td"), function(c){ return c.querySelector(".ci.ok"); }); }));
var cmd = mr[6].querySelectorAll("td");
ok("import contract row: !, !, ✓ with 'cost counted' on hover", cmd[0].querySelector(".ci.w") && cmd[1].querySelector(".ci.w") && cmd[2].querySelector(".ci.ok") && /cost counted/.test(note(cmd[0])) && /counted in the result/.test(note(cmd[1])), Array.prototype.map.call(cmd, function(c){ return c.textContent; }).join());
var cells = $$("#ev-matrix td.c");
ok("every cell has a hover note", cells.length === 21 && cells.every(noted));
ok("cell notes: half-hour counts", /0 of 21,888 half-hours/.test(note(mr[0].querySelectorAll("td")[0])) && /0 of 5,136 half-hours/.test(note(mr[0].querySelectorAll("td")[1])) && /0 of 336 half-hours/.test(note(mr[0].querySelectorAll("td")[2])));
ok("selected column shaded", colOn() === "Test · reviewed", colOn());
ok("matrix fits its block (no column pushed out)", $("#ev-matrix").getBoundingClientRect().right <= $("#ev-lim").getBoundingClientRect().right + 1);
var mc = $$("#ev-mx-chips .tipc");
ok("one matrix chip: no Hot Season tested yet, the count on hover", mc.length === 1 && mc[0].textContent === "no Hot Season tested yet" && /114 test days, 1 Jul – 22 Oct 2026: 107 reviewed, 7 blind/.test(note(mc[0])) && /1 Dec – 31 Mar/.test(note(mc[0])) && /peak prices and the capacity charge happen then/.test(note(mc[0])), mc.map(function(c){ return c.textContent; }).join("|"));

/* round-4 pieces are gone */
ok("old blocks gone", !$("#cmp-hero") && !$("#sv-tiles") && !$("#val-note") && !$("#val-checks") && !$("#cmp-q2a") && !$("#s-evidence .vt-tiles") && !$("#s-evidence .scope"));
ok("no period toolbar", !$("[data-per]") && !$("[data-cper]") && !$("#s-evidence .toolbar"));
wording("default");

/* hover a row: name, five numbers, one or two words each, no subtitle, 230 wide */
var r0 = rows()[0], bb = r0.getBoundingClientRect();
r0.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:bb.left + 40, clientY:bb.top + 10}));
var tip = $(".ctip"), tl = $$(".ctip td:nth-child(2)");
ok("row hover: the period and its five numbers", tip && tip.style.display === "block" && /^Training period · 456 d/.test(tip.textContent) && /Average\+A\$410 \/day/.test(tip.textContent) && /Range\+A\$190…620/.test(tip.textContent) && /PreviousA\$2,640/.test(tip.textContent) && /ThisA\$3,050/.test(tip.textContent) && /Prices knownA\$4,900/.test(tip.textContent), tip && tip.textContent.slice(0, 160));
ok("row hover: ≤ 5 rows, labels ≤ 2 words, no subtitle", tl.length === 5 && tl.every(function(l){ return words(l.textContent) <= 2; }) && !$(".ctip .ct-h span"), tl.map(function(l){ return l.textContent; }).join(","));
ok("row hover: labels on one line, box 230 wide", tl.every(function(l){ return l.getBoundingClientRect().height <= 24; }) && Math.round(tip.getBoundingClientRect().width) === 230, tl.map(function(l){ return l.textContent + ":" + Math.round(l.getBoundingClientRect().height); }).join(",") + " w" + tip.getBoundingClientRect().width);
document.body.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:5, clientY:5}));

/* click Training period */
click(rows()[0]);
ok("training period: headline follows", txt("#ev-head .ev-hl") === "+A$410 /day vs previous dispatcher (v1)" && txt("#ev-head .ev-sub") === "+15.5% Training period · 456 d", txt("#ev-head"));
ok("training period: selected row", selRow() === "ins", selRow());
var cav = $("#ev-chips .ev-cav");
ok("training period: caveat chip leads the decision chips", cav && cav === $("#ev-chips .tipc") && cav.textContent === "not a test" && noted(cav) && /cannot show how the method does on new data/.test(note(cav)) && !$("#ev-chips .ev-zc") && chipTexts("#ev-chips .tipc").length === 2);
at = svgTxt("#ev-abs");
ok("training period: bars follow", /Training period/.test(at) && /A\$2,640/.test(at) && /A\$3,050/.test(at) && /A\$4,900/.test(at), at);
qt = svgTxt("#ev-q2a");
ok("training period: price forecast follows", /41 · model/.test(qt) && /58 · yesterday/.test(qt) && /0\.081 · model/.test(qt) && /Training period · 456 d/.test(qt), qt);
ok("training period: matrix column", colOn() === "Training period");
ok("training period: 21,888 half-hours", /of 21,888 half-hours/.test(txt("#ev-viol .dv")));
wording("training period");

/* click Test · blind: the range includes 0 */
click(rows()[2]);
ok("blind: headline +A$70, grey", txt("#ev-head .ev-hl") === "+A$70 /day vs previous dispatcher (v1)" && $("#ev-head .ev-num").classList.contains("zero") && txt("#ev-head .ev-sub") === "+5.3% Test · blind · 7 d", txt("#ev-head"));
var zc = $("#ev-chips .ev-zc");
ok("blind: 'range includes 0' chip with the full caveat on hover", zc && zc.textContent === "range includes 0" && noted(zc) && /^The 95% range crosses zero: we cannot tell this period's gain from luck/.test(note(zc)) && /−A\$20 to \+A\$160/.test(note(zc)) && /too short to prove anything/.test(note(zc)) && $$("#ev-chips .ev-cav").length === 1);
at = svgTxt("#ev-abs");
ok("blind: bars follow", /Test · blind/.test(at) && /A\$1,330/.test(at) && /A\$1,400/.test(at) && /A\$2,100/.test(at), at);
ok("blind: price forecast follows", /22 · model/.test(svgTxt("#ev-q2a")) && /27 · yesterday/.test(svgTxt("#ev-q2a")) && /Test · blind · 7 d/.test(svgTxt("#ev-q2a")));
ok("blind: import contract held, 336 half-hours", $("#ev-viol .ck.ok") && txt("#ev-viol .ck") === "✓9 MW contract held" && /^Contract maximum demand: /.test(note($("#ev-viol .ck"))) && /of 336 half-hours/.test(txt("#ev-viol .dv")), txt("#ev-viol"));
ok("blind: matrix column", colOn() === "Test · blind");
ok("blind: still one 'includes 0' label", (svgTxt("#ev-dots").match(/includes 0/g) || []).length === 1);
wording("blind");

/* keyboard: Arrow Up from Test · blind selects Test · reviewed and keeps focus on the row */
rows()[2].focus();
rows()[2].dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowUp", bubbles:true}));
ok("arrow up selects Test · reviewed", selRow() === "tseen" && document.activeElement && document.activeElement.getAttribute("data-p") === "tseen", selRow());
rows()[0].dispatchEvent(new KeyboardEvent("keydown", {key:"Enter", bubbles:true}));
ok("Enter selects Training period", selRow() === "ins");
click(rows()[1]);
ok("back to the default", /^\+A\$350/.test(txt("#ev-head")) && !$("#ev-chips .ev-cav"));

/* the Plan page links here */
goTo("plan"); click($('#s-plan [data-go="evidence"]'));
ok("Plan link opens Evidence on the same period", $("#s-evidence").classList.contains("on") && selRow() === "tseen");
goTo("plan");
done();
