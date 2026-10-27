/* Evidence, site battery (round 5, wording per ROUND5.md section 7; hover notes from the shared glossary GLOSS). Run with src/tests/run.py evidence. */
goTo("evidence");
function svgTxt(sel){ var s = $(sel + " svg"); return s ? Array.prototype.map.call(s.querySelectorAll("text"), function(t){ return t.textContent; }).join(" | ") : ""; }
function noted(el){ return !!el && (el.getAttribute("data-note") || "").length > 15 && el.tabIndex === 0; }
function note(el){ return el ? el.getAttribute("data-note") || "" : ""; }
function rows(){ return $$("#ev-dots .hit"); }
function selRow(){ return rows().filter(function(r){ return r.getAttribute("aria-pressed") === "true"; }).map(function(r){ return r.getAttribute("data-p"); }).join(); }
/* a matrix column header is the period name, then its days on a second line */
function colName(th){ return th.firstChild ? th.firstChild.textContent : ""; }
function colOn(){ return $$("#ev-matrix thead th.on").map(colName).join(); }
/* the periods as ROUND5.md 7.1 names them: name, dates, days */
var PER = {ins:["Training period", "1 Apr 2025 – 30 Jun 2026", 456], tseen:["Test · reviewed", "1 Jul – 15 Oct 2026", 107], tnew:["Test · blind", "16–22 Oct 2026", 7]};
var PKEYS = ["ins", "tseen", "tnew"];
function span(p){ return PER[p][1] + " · " + PER[p][2] + " d"; }
function hasDatesDays(s, p){ return s.indexOf(PER[p][1]) >= 0 && (s.indexOf(PER[p][2] + " d") >= 0 || s.indexOf(PER[p][2] + " days") >= 0); }

/* ---- the wording rules, checked in every state (default, then each clicked period) ---- */
/* (a) no internal jargon: not in the markup (labels, notes, aria-labels), and not in any visible text */
var JARGON_HTML = /In-sample|Test seen|Test unseen|frozen v1|Frozen v1|Uplift|\bpriced\b|Adopted dispatch|Perfect price foresight|Hard-limit|economic limit|28-day frequency|Brier baseline|Meter limits|Power rating|No double count|Inputs by 09:00|Constraint checks/;
var JARGON_SEEN = /in-sample|out-of-sample|\bseen\b|unseen|frozen|uplift(?! yet)|\bpriced\b|foresight|hard-limit|economic limit|\binterval\b|\bmean\b/i;
function visible(){ return txt("#ph-ctx") + " | " + txt("#ph-chips") + " | " + $("#s-evidence").textContent; }
function hits(re, s){ var m = s.match(new RegExp(re.source, re.flags + "g")); return m ? m.join(",") : ""; }
/* (b) acronyms: each one used anywhere on the page has its long form in visible text or in a hover note;
   in HTML text it sits inside an element with a hover note; in chart text the plain words come first, the acronym in brackets */
var ACR = [["MAE", /Mean absolute error/i], ["Brier", /How wrong the model's probability of a price spike/], ["CMD", /import contract \(CMD\): Contract maximum demand/i],
           ["FCESS", /grid services \(FCESS\): Frequency control services/i], ["SOC", /state of charge \(SOC\)/i], ["AEMO", /market operator.{0,4}\(AEMO\)/i],
           ["WEM", /Wholesale Electricity Market \(WEM\)/], ["IRCR", /individual reserve capacity requirement/i]];
var ACR_RE = /\b(MAE|Brier|CMD|FCESS|SOC|AEMO|WEM|IRCR)\b/;
function notes(){ return $$("#s-evidence [data-note], #ph-chips [data-note]").map(note).join(" | "); }
function wording(state){
  var html = $("#s-evidence").innerHTML + txt("#ph-ctx") + $("#ph-chips").innerHTML;
  ok(state + ": no internal jargon in labels, notes or aria-labels", !JARGON_HTML.test(html), hits(JARGON_HTML, html));
  ok(state + ": no internal jargon in visible text", !JARGON_SEEN.test(visible()), hits(JARGON_SEEN, visible()));
  var known = visible() + " | " + notes(), used = [], missing = [];
  ACR.forEach(function(a){ if (new RegExp("\\b" + a[0] + "\\b").test(html)) { used.push(a[0]); if (!a[1].test(known)) missing.push(a[0]); } });
  ok(state + ": acronyms MAE, Brier, CMD, FCESS are used and explained", ["MAE", "Brier", "CMD", "FCESS"].every(function(a){ return used.indexOf(a) >= 0; }), used.join());
  ok(state + ": every acronym used has its long form shown or in a hover note", !missing.length, "missing: " + missing.join() + " · used: " + used.join());
  var bare = [], w = document.createTreeWalker($("#s-evidence"), NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { var n = w.currentNode, el = n.parentElement;
    if (!ACR_RE.test(n.textContent)) continue;
    if (el.closest("svg")) { if (!/[a-z]{3,}[^()]*\((MAE|Brier score|CMD|FCESS|SOC|AEMO)\)/.test(el.closest("text").textContent)) bare.push(n.textContent); }
    else if (!el.closest("[data-note]")) bare.push(n.textContent); }
  ok(state + ": every acronym in text carries a hover note (HTML) or its plain words (chart)", !bare.length, bare.join(" | "));
  /* (c) every period label carries its dates and days: shown, or in the same element's hover note */
  var bad = [];
  PKEYS.forEach(function(p){
    var nm = $('#ev-dots text[data-n="' + p + '"]'), dd = $('#ev-dots text[data-d="' + p + '"]');
    if (!nm || nm.textContent !== PER[p][0] || !dd || dd.textContent !== span(p)) bad.push("dot row " + p);
  });
  $$("#ev-matrix thead th").forEach(function(th, i){ var p = PKEYS[i];
    if (colName(th) !== PER[p][0] || !th.querySelector(".ev-d") || th.querySelector(".ev-d").textContent !== PER[p][2] + " d" || !hasDatesDays(note(th), p)) bad.push("matrix column " + p); });
  var sp = S_PER();
  [["#ev-head .ev-per", "headline"], ["#ev-q2a-per .ev-per", "price forecast"]].forEach(function(l){ var el = $(l[0]);
    if (!el || txt(l[0]) !== PER[sp][0] + " · " + PER[sp][2] + " d" || !hasDatesDays(note(el), sp)) bad.push(l[1] + " period"); });
  var at = svgTxt("#ev-abs");
  if (at.indexOf(PER[sp][0]) < 0 || at.indexOf(span(sp)) < 0) bad.push("bars period");
  var ft = $$("#ev-mx-chips .tipc")[1];
  if (!ft || !/114 test days: 107 reviewed, 7 blind/.test(ft.textContent) || !/1 Jul – 22 Oct 2026/.test(note(ft))) bad.push("test-days chip");
  ok(state + ": every period label carries its dates and days", !bad.length, bad.join(", "));
  glossary(state);
}
/* ---- the shared glossary: notes reuse GLOSS wording (page scope is not visible, so a distinctive phrase of each entry is checked) ---- */
var G = {DISPATCHER:"this tool's scheduling software", RUN:"A saved replay of past days with one fixed set of inputs", CMD:"Contract maximum demand: the grid import the factory has contracted for",
         NETBILL:"The charge the network operator bills the factory", FCESS:"Frequency control services: the battery is paid", GRIDRESERVE:"Energy the battery must keep so it can deliver",
         BACKUP:"Energy kept so the factory can ride through a grid outage", SOC:"How much energy the battery holds at a moment", MAE:"Mean absolute error: the average gap",
         BRIER:"How wrong the model's probability of a price spike", TRAIN:"the data used to build and choose the method", TREV:"looked at repeatedly while refining it",
         TBLIND:"never looked at before the method was locked", HOTSEASON:"1 Dec – 31 Mar in the WA market: when system demand peaks", DECISION:"The plan is made at 09:00 each trading day",
         AEMO:"The Australian Energy Market Operator runs the wholesale market", HINDSIGHT:"Known only after the fact."};
var PG = {ins:"TRAIN", tseen:"TREV", tnew:"TBLIND"};
function has(el, keys){ var n = note(el); return !!el && keys.every(function(k){ return n.indexOf(G[k]) >= 0; }); }
var BANNED = /frozen|out-of-sample|in-sample|unseen/i;
function glossary(state){
  var all = $$("#s-evidence [data-note], #ph-chips [data-note]"), sp = S_PER(), bad = [];
  /* (a) no note uses the words the glossary avoids */
  var banned = all.filter(function(el){ return BANNED.test(note(el)); }).map(function(el){ return hits(BANNED, note(el)); });
  ok(state + ": no note says frozen, out-of-sample, in-sample or unseen", !banned.length, banned.join(" | "));
  /* (b) every acronym, period name and 'dispatcher' label takes its note from GLOSS */
  if (!has($("#ev-head .ev-def"), ["DISPATCHER"])) bad.push("headline dispatcher");
  ["#ev-head .ev-per", "#ev-q2a-per .ev-per"].forEach(function(l){ if (!has($(l), [PG[sp]])) bad.push(l); });
  $$("#ev-matrix thead th").forEach(function(th, i){ if (!has(th, [PG[PKEYS[i]]])) bad.push("matrix column " + i); });
  if (!has($("#ev-dots .ev-ov"), ["TRAIN"])) bad.push("training chip in the dot plot");
  $$("#ev-chips .ev-cav").forEach(function(c){ if (!has(c, [PG[sp]])) bad.push("caveat chip " + c.textContent); });
  var dc = $$("#ev-chips .tipc:not(.ev-cav)");
  if (!has(dc[0], ["NETBILL", "CMD"]) || !/import stays under the 9 MW contract/.test(note(dc[0]))) bad.push("network bill chip");
  if (!has(dc[1], ["RUN"])) bad.push("run chip");
  if (!has($("#ev-viol .ck"), ["CMD"])) bad.push("import contract tile");
  var qc = $$("#ev-q2a-chips .tipc");
  if (!has(qc[0], ["MAE"]) || !has(qc[2], ["BRIER"])) bad.push("price forecast chips");
  var mr = $$("#ev-matrix tbody th");
  [["SOC"], [], ["GRIDRESERVE", "BACKUP"], [], ["FCESS"], ["DECISION", "AEMO"], ["CMD"]].forEach(function(k, i){ if (!has(mr[i], k)) bad.push("matrix row " + i); });
  var mc = $$("#ev-mx-chips .tipc");
  if (!has(mc[0], ["DECISION"]) || !has(mc[1], ["TREV", "TBLIND", "HOTSEASON"])) bad.push("matrix chips");
  var ah = $$("#ev-abs .ev-hot");
  if (ah.length !== 3 || !has(ah[0], ["DISPATCHER"]) || !has(ah[1], ["DISPATCHER"]) || !has(ah[2], ["HINDSIGHT"])) bad.push("bar names");
  ok(state + ": acronyms, periods and 'dispatcher' take their notes from the glossary", !bad.length, bad.join(", "));
  ok(state + ": every matrix row note says why it is a check", mr.length === 7 && mr.every(function(th){ return /Why it is a check: \w/.test(note(th)); }));
  /* (c) the forecast change reads as an improvement, not a bare minus */
  var qt = svgTxt("#ev-q2a");
  ok(state + ": price forecast says 'lower error', no bare minus percentage", (qt.match(/\d+\.\d% lower error/g) || []).length === 3 && !/[−-]\d+(\.\d+)?%/.test(qt), qt.slice(0, 200));
  /* (d) a reserve is always named: grid-service reserve (factory backup is its own word) */
  var words = visible() + " | " + svgTxt("#ev-dots") + " | " + svgTxt("#ev-abs") + " | " + svgTxt("#ev-q2a") + " | " + notes(), bare = [], re = /(.{0,14})\breserves?\b/gi, m;
  while ((m = re.exec(words))) if (!/grid-service $/i.test(m[1])) bare.push(m[0]);
  ok(state + ": no bare 'reserve' (always grid-service reserve or factory backup)", !bare.length, bare.join(" | "));
}
/* the selected period, read from the dot plot */
function S_PER(){ return selRow() || "tseen"; }

/* default: Test · reviewed */
ok("page opens alone", $("#s-evidence").classList.contains("on") && $$(".screen.on").length === 1);
ok("header chip 2 · Prove only", txt("#ph-chips") === "2 · Prove", txt("#ph-chips"));
ok("header context ≤ 12 words, plain", txt("#ph-ctx").split(/\s+/).length <= 12 && /training period/.test(txt("#ph-ctx")), txt("#ph-ctx"));
ok("headline", /^\+A\$350 \/day vs the previous dispatcher \(v1\)/.test(txt("#ev-head")), txt("#ev-head"));
ok("headline small: +20.6% and the period", txt("#ev-head small") === "+20.6% · Test · reviewed · 107 d", txt("#ev-head small"));
ok("headline number green, not grey", !$("#ev-head .ev-num").classList.contains("zero"));
ok("previous dispatcher: a glossary chip on hover", noted($("#ev-head .ev-def")) && $("#ev-head .ev-def").classList.contains("tipc") && $("#ev-head .ev-def").textContent === "the previous dispatcher (v1)" && /previous version \(v1\) is kept unchanged as the baseline/.test(note($("#ev-head .ev-def"))));
ok("headline period: dates on hover", noted($("#ev-head .ev-per")) && /1 Jul – 15 Oct 2026, 107 days/.test(note($("#ev-head .ev-per"))) && /looked at repeatedly/.test(note($("#ev-head .ev-per"))));
var chips = $$("#ev-chips .tipc");
ok("decision chips: network saving + run with the trust point", chips.map(function(c){ return c.textContent; }).join("|") === "includes the factory's network bill saving|saved run R‑0142 · illustrative: all numbers on this page are placeholders until the engine's results are loaded", chips.map(function(c){ return c.textContent; }).join("|"));
ok("decision chips carry notes", chips.every(noted) && /synthetic/.test(note(chips[0])) && /placeholders/.test(note(chips[1])));
ok("no caveat chip by default", !$("#ev-chips .ev-cav"));
ok("limits broken: caption and 0 of 5,136 half-hours", txt("#ev-viol .cap") === "Physical and contract limits broken" && noted($("#ev-viol .cap [data-note]")) && txt("#ev-viol .dv") === "0 of 5,136 half-hours" && $("#ev-viol .dv").classList.contains("ok"), txt("#ev-viol .dv"));
ok("import contract tile: exceeded · cost counted, with note", $("#ev-viol .ck.w") && txt("#ev-viol .ck") === "!9 MW import contract exceeded · cost counted" && noted($("#ev-viol .ck")) && /^Contract maximum demand: /.test(note($("#ev-viol .ck"))) && /Exceeding it is allowed; the extra demand charge is counted in the result/.test(note($("#ev-viol .ck"))) && /at times/.test(note($("#ev-viol .ck"))), txt("#ev-viol"));

/* dot plot */
var dsvg = $("#ev-dots svg"), dt = svgTxt("#ev-dots");
ok("dot plot is an image with a label", dsvg && dsvg.getAttribute("role") === "img" && dsvg.getAttribute("aria-label").length > 20);
ok("three rows, Test · reviewed selected", rows().length === 3 && selRow() === "tseen", selRow());
["Extra A$ per day vs the previous dispatcher · dot = average, line = 95% confidence range", "Training period", "Test · reviewed", "Test · blind", "1 Apr 2025 – 30 Jun 2026 · 456 d", "1 Jul – 15 Oct 2026 · 107 d", "16–22 Oct 2026 · 7 d",
 "+A$410 average", "+A$350 average", "+A$70 average", "+A$190", "+A$620 · 95% range", "+A$140", "+A$560 · 95% range", "−A$20", "+A$160 · 95% range"].forEach(function(w){ ok("dot plot label: " + w, dt.indexOf(w) >= 0); });
ok("'includes 0 · no clear gain' only on the 7-day row", (dt.match(/includes 0 · no clear gain/g) || []).length === 1 && (dt.match(/includes 0/g) || []).length === 1);
var zh = $$("#ev-dots .ev-hot");
ok("'includes 0' opens its note on hover", zh.length === 1 && noted(zh[0]) && note(zh[0]) === "The 95% range crosses zero: we cannot tell this period's gain from luck.", zh.length);
ok("zero line drawn", dsvg.querySelectorAll('line[style*="axis-strong"]').length === 1);
var dots = dsvg.querySelectorAll("circle");
ok("three dots: green, green, grey; training period hollow", dots.length === 3 && /--ok/.test(dots[0].getAttribute("style")) && /fill:var\(--surface\)/.test(dots[0].getAttribute("style")) && /fill:var\(--ok\)/.test(dots[1].getAttribute("style")) && /--text-4/.test(dots[2].getAttribute("style")));
var ins = $("#ev-dots .ev-ov");
ok("training-period row carries 'training data · not a test' with the caveat", ins && ins.textContent === "training data · not a test" && noted(ins) && /cannot show how the method does on new data/.test(note(ins)) && /1 Apr 2025 – 30 Jun 2026/.test(note(ins)));
/* the chip sits after the row name and ends before the zero line (it may run into the plot's empty left edge) */
var nmr = $('#ev-dots text[data-n="ins"]').getBoundingClientRect(), ir = ins.getBoundingClientRect(), zr = dsvg.querySelector('line[style*="axis-strong"]').getBoundingClientRect();
ok("'training data · not a test' between the name and the zero line", ir.left > nmr.right && ir.right < zr.left - 4, Math.round(nmr.right) + " < " + Math.round(ir.left) + "…" + Math.round(ir.right) + " < " + Math.round(zr.left));
ok("rows are keyboard buttons", rows().every(function(r){ return r.getAttribute("role") === "button" && r.getAttribute("tabindex") === "0" && r.getAttribute("aria-label").length > 30; }));
ok("row names say the dates in their aria-label", /^Test · reviewed, 1 Jul – 15 Oct 2026, 107 days: extra \+A\$350 per day/.test(rows()[1].getAttribute("aria-label")), rows()[1].getAttribute("aria-label"));

/* absolute bars for the selected period */
var at = svgTxt("#ev-abs");
["Test · reviewed", "Profit per day, A$ · energy + grid services + network bill saving − battery wear", "1 Jul – 15 Oct 2026 · 107 d · average, same days and prices", "Previous dispatcher (v1)", "A$1,700", "This dispatcher", "A$2,050", "If prices were known in advance", "A$3,300", "ceiling · unattainable: prices are not known in advance"].forEach(function(w){ ok("bars label: " + w, at.indexOf(w) >= 0); });
ok("bars: names end before the bars start", $$("#ev-abs text.ann").every(function(t){ return t.getBBox().x + t.getBBox().width < +$("#ev-abs rect[rx]").getAttribute("x") - 6; }));
ok("'if prices were known' hatched, the only hatch", $$("#ev-abs rect").filter(function(r){ return /^url\(#hEv\)/.test(r.getAttribute("fill") || ""); }).length === 1 && $$("#s-evidence pattern").length === 1);

/* price forecast */
var qt = svgTxt("#ev-q2a");
["Wholesale price, all day", "Evening 17:00–21:00 price", "Spike ≥ A$200", "average error, A$/MWh", "spike forecast error (Brier score)", "33 · model", "45 · yesterday's prices", "26.7% lower error", "71 · model", "99 · yesterday's prices", "28.3% lower error", "0.064 · model", "0.077 · spike rate, last 28 days", "16.9% lower error"].forEach(function(w){ ok("price forecast label: " + w, qt.indexOf(w) >= 0, w); });
var qx = +$("#ev-q2a rect").getAttribute("x");
ok("price forecast: metric names end before the bars start", $$("#ev-q2a text.lt, #ev-q2a text.lu").every(function(t){ return t.getBBox().x + t.getBBox().width < qx - 4; }));
var qh = $$("#ev-q2a .ev-hot");
ok("evening price label says why it matters on hover", qh.length === 1 && noted(qh[0]) && /evening peak/.test(note(qh[0])) && /battery discharges/.test(note(qh[0])));
ok("price forecast follows the period", txt("#ev-q2a-per") === "Test · reviewed · 107 d", txt("#ev-q2a-per"));
var qc = $$("#ev-q2a-chips .tipc");
ok("price forecast chips", qc.map(function(c){ return c.textContent; }).join("|") === "average error (MAE), A$/MWh · lower is better|simple baseline: repeat yesterday's prices|spike baseline: how often spikes happened in the last 28 days", qc.map(function(c){ return c.textContent; }).join("|"));
ok("price forecast chip notes", qc.every(noted) && /^Mean absolute error: /.test(note(qc[0])) && /not an accuracy percentage/.test(note(qc[0])) && /half-hourly wholesale electricity price/.test(note(qc[0])) && !/Energy price/.test(note(qc[0])) && /^How wrong the model's probability of a price spike/.test(note(qc[2])) && /28 days/.test(note(qc[2])));

/* limits matrix */
var mr = $$("#ev-matrix tbody tr");
ok("matrix: 7 rows × 3 periods", mr.length === 7 && mr.every(function(r){ return r.querySelectorAll("td").length === 3; }) && $$("#ev-matrix thead th").length === 3);
ok("matrix columns: names and days", $$("#ev-matrix thead th").map(function(t){ return colName(t) + "/" + t.querySelector(".ev-d").textContent; }).join() === "Training period/456 d,Test · reviewed/107 d,Test · blind/7 d", $$("#ev-matrix thead th").map(function(t){ return t.textContent; }).join());
ok("matrix row names", mr.map(function(r){ return r.querySelector("th").textContent; }).join("|") === "Energy balance holds|Within 10 MW rating|Grid-service reserve + factory backup|Grid import and export limits|Nothing sold twice|Only data known at 09:00|Import contract 9 MW a cost, not a hard limit", mr.map(function(r){ return r.querySelector("th").textContent; }).join("|"));
ok("row names carry the definitions", mr.every(function(r){ return noted(r.querySelector("th")); }) && /SOC\(t\+1\)/.test(note(mr[0].querySelector("th"))) && /never share the same MW or MWh/.test(note(mr[4].querySelector("th"))) && /^Contract maximum demand: /.test(note(mr[6].querySelector("th"))) && /grid-service reserve plus the factory backup/.test(note(mr[2].querySelector("th"))));
ok("six hard limits: ✓ everywhere", mr.slice(0, 6).every(function(r){ return Array.prototype.every.call(r.querySelectorAll("td"), function(c){ return c.querySelector(".ci.ok") && c.textContent.trim() === "✓"; }); }));
var cmd = mr[6].querySelectorAll("td");
ok("import contract row: cost counted, cost counted, ✓", cmd[0].querySelector(".ci.w") && cmd[0].textContent === "!cost counted" && cmd[1].querySelector(".ci.w") && cmd[1].textContent === "!cost counted" && cmd[2].querySelector(".ci.ok"), Array.prototype.map.call(cmd, function(c){ return c.textContent; }).join());
var cells = $$("#ev-matrix td.c");
ok("every cell has a hover note", cells.length === 21 && cells.every(noted));
ok("cell notes: half-hour counts", /0 of 21,888 half-hours/.test(note(mr[0].querySelectorAll("td")[0])) && /0 of 5,136 half-hours/.test(note(mr[0].querySelectorAll("td")[1])) && /0 of 336 half-hours/.test(note(mr[0].querySelectorAll("td")[2])));
ok("import contract cell note says the charge is counted", /counted in the result/.test(note(cmd[0])));
ok("selected column shaded", colOn() === "Test · reviewed", colOn());
ok("matrix fits its block (no column pushed out)", $("#ev-matrix").getBoundingClientRect().right <= $("#ev-lim").getBoundingClientRect().right + 1);
var mc = $$("#ev-mx-chips .tipc");
ok("matrix chips", mc.length === 2 && mc[0].textContent === "every input was public by 09:00" && /pre-dispatch/.test(note(mc[0])) && mc[1].textContent === "114 test days: 107 reviewed, 7 blind · no Hot Season (Dec–Mar) yet" && /1 Dec – 31 Mar/.test(note(mc[1])) && /peak prices and the capacity charge happen then/.test(note(mc[1])) && mc.every(noted), mc.map(function(c){ return c.textContent; }).join("|"));

/* round-4 pieces are gone */
ok("old blocks gone", !$("#cmp-hero") && !$("#sv-tiles") && !$("#val-note") && !$("#val-checks") && !$("#cmp-q2a") && !$("#s-evidence .vt-tiles") && !$("#s-evidence .scope"));
ok("no period toolbar", !$("[data-per]") && !$("[data-cper]") && !$("#s-evidence .toolbar"));
/* text outside the charts: chart labels may name the range, sentences may not */
var cl = $("#s-evidence").cloneNode(true); Array.prototype.forEach.call(cl.querySelectorAll("svg"), function(g){ g.remove(); }); var plain = cl.textContent;
ok("no Scope / footnote / range sentences", !/Scope|Lower is better|95% interval|95% confidence range|bootstrap/.test(plain));
wording("default");

/* hover a row: the table names the period with its dates */
var r0 = rows()[0], bb = r0.getBoundingClientRect();
r0.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:bb.left + 40, clientY:bb.top + 10}));
var tip = $(".ctip");
ok("row hover: table with dates and range", tip && tip.style.display === "block" && /Training period · 1 Apr 2025 – 30 Jun 2026 · 456 days/.test(tip.textContent) && /\+A\$190 to \+A\$620/.test(tip.textContent) && /build and choose the method/.test(tip.textContent) && /Previous dispatcher \(v1\)/.test(tip.textContent), tip && tip.textContent.slice(0, 120));
document.body.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:5, clientY:5}));

/* click Training period */
click(rows()[0]);
ok("training period: headline follows", /^\+A\$410 \/day/.test(txt("#ev-head")) && txt("#ev-head small") === "+15.5% · Training period · 456 d", txt("#ev-head"));
ok("training period: selected row", selRow() === "ins", selRow());
var cav = $("#ev-chips .ev-cav");
ok("training period: caveat chip leads the decision chips", cav && cav === $("#ev-chips .tipc") && cav.textContent === "training period · not a test" && noted(cav) && /cannot show how the method does on new data/.test(note(cav)) && !$("#ev-chips .ev-zc"));
at = svgTxt("#ev-abs");
ok("training period: bars follow", /Training period/.test(at) && /A\$2,640/.test(at) && /A\$3,050/.test(at) && /A\$4,900/.test(at), at);
qt = svgTxt("#ev-q2a");
ok("training period: price forecast follows", /41 · model/.test(qt) && /58 · yesterday's prices/.test(qt) && /0\.081 · model/.test(qt) && txt("#ev-q2a-per") === "Training period · 456 d", qt);
ok("training period: matrix column", colOn() === "Training period");
ok("training period: 21,888 half-hours", /of 21,888 half-hours/.test(txt("#ev-viol .dv")));
wording("training period");

/* click Test · blind: the range includes 0 */
click(rows()[2]);
ok("blind: headline +A$70, grey", /^\+A\$70 \/day/.test(txt("#ev-head")) && $("#ev-head .ev-num").classList.contains("zero") && txt("#ev-head small") === "+5.3% · Test · blind · 7 d", txt("#ev-head"));
var zc = $("#ev-chips .ev-zc");
ok("blind: 'includes 0 · no clear gain yet' chip", zc && zc.textContent === "includes 0 · no clear gain yet" && noted(zc) && /^The 95% range crosses zero: we cannot tell this period's gain from luck/.test(note(zc)) && /−A\$20 to \+A\$160/.test(note(zc)) && /too short to prove anything/.test(note(zc)) && $$("#ev-chips .ev-cav").length === 1);
at = svgTxt("#ev-abs");
ok("blind: bars follow", /Test · blind/.test(at) && /A\$1,330/.test(at) && /A\$1,400/.test(at) && /A\$2,100/.test(at), at);
ok("blind: price forecast follows", /22 · model/.test(svgTxt("#ev-q2a")) && /27 · yesterday's prices/.test(svgTxt("#ev-q2a")));
ok("blind: import contract held, 336 half-hours", $("#ev-viol .ck.ok") && txt("#ev-viol .ck") === "✓9 MW import contract held" && /^Contract maximum demand: /.test(note($("#ev-viol .ck"))) && /of 336 half-hours/.test(txt("#ev-viol .dv")), txt("#ev-viol"));
ok("blind: matrix column", colOn() === "Test · blind");
ok("blind: still one 'includes 0' row", (svgTxt("#ev-dots").match(/includes 0/g) || []).length === 1);
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
