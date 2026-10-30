/* Tonight's call (round 6: cut to the plan page's standard, ROUND6.md; the terminology and glossary checks of round 5 stay).
   Run with src/tests/run.py calls. */
goTo("calls");
/* the shared glossary's notes (GLOSS in engine.js, what + why) for the keys this page uses. The page scope is not
   visible from a test, so they are copied here: a paraphrase on the page, or an edit to GLOSS, fails the checks. */
var GLC = {
  TRAIN:"1 Apr 2025 – 30 Jun 2026, 456 days: the data used to build and choose the method. Results here cannot show how the method does on new data.",
  HOTSEASON:"1 Dec – 31 Mar in the WA market: when system demand peaks. The capacity charge for the following year is set by the 12 peak half-hours of this season.",
  HINDSIGHT:"Known only after the fact. It was not available at 09:00 when the decision was made, so it can judge a decision but never inform it.",
  CALL:"Asking customers' batteries to discharge together for a set evening window, for a payment. Each call uses one of a limited number per contract year.",
  TRANSFER:"Money moved from Zenith to customers for responding to a call. It changes who gains, not the total value created, so it sums to zero across parties.",
  RESPONSE:"On average 90% of enrolled customers respond to a call. 3,000 enrolled customers is an assumption. No customer is obliged to respond, so this is an average, not guaranteed capacity.",
  ENROLLED:"Customers that signed up to the offer (3,000 assumed). Offered: customers the offer was shown to. Responding: enrolled customers that actually respond to a call (90% on average). Payments are per responding customer; fixed costs are per enrolled customer.",
  HOLDCHARGE:"Before a call, customer batteries stop discharging and keep their energy for the event window. Without it the batteries would be empty by the evening peak.",
  CUSTFLOOR:"The 20% of each customer battery that is never discharged during a call. It protects the customer's own evening use.",
  FLAGGED:"A day the rule marks as a likely peak day at 09:00: the forecast peak is within 85 MW of the 4th-highest peak seen so far this season. Flagged days are called while calls remain; the 85 MW margin is set from past seasons.",
  IRCR:"Each retailer's share of the cost of keeping enough generation available, set by its customers' demand in the 12 peak half-hours of the Hot Season. Lowering demand in those 12 half-hours lowers the charge for a whole year; that is the main value of calling customer batteries.",
  CONTRACTYEAR:"1 Jul 2025 – 30 Jun 2026: the year the customer offer's call cap and payments are counted over. It lies inside the training period, so its results are not a blind test.",
  AEMO:"The Australian Energy Market Operator runs the wholesale market and publishes the demand forecasts and prices used here. Only AEMO data published before 09:00 may be used for the 09:00 decision.",
  PEAKDAY:"One of the 4 Hot Season days that contain the 12 peak half-hours. They are known only after the season. A call on a peak day lowers the capacity charge; a call on any other day only costs the payment.",
  CUSTOMOFFER:"The terms this tool suggests (85 c/kWh, 45 calls a year), found by searching the training period. A suggestion to test in a pilot, not a contract."
};
function GL(k){ return GLC[k]; }
function svgText(sel){ var s = $(sel + " svg"); return s ? Array.prototype.map.call(s.querySelectorAll("text"), function(t){ return t.textContent; }).join(" | ") : ""; }
function svgTexts(sel){ var s = $(sel + " svg"); return s ? Array.prototype.map.call(s.querySelectorAll("text"), function(t){ return t.textContent; }) : []; }
function chips(sel){ return $$(sel + " .tipc"); }
function chipTxt(sel){ return chips(sel).map(function(c){ return c.textContent; }).join(" | "); }
function cells(cls){ return $$("#call-budget .cl-cells i" + (cls ? "." + cls : "")).length; }
function budgetLab(){ return $$("#call-budget .cl-bl > span").map(function(s){ return s.textContent; }).join(" | "); }
function solidActual(){ return $$("#act-dem svg path").filter(function(p){ return p.getAttribute("stroke-width") === "2" && !p.getAttribute("stroke-dasharray"); }).length; }
function note(el){ return el ? el.getAttribute("data-note") || "" : ""; }
function hdrChip(re){ return $$("#ph-chips .chip").filter(function(c){ return re.test(c.textContent); })[0]; }
/* words with letters in them: numbers and separators are not words (ROUND6 §1: fewer words, not fewer numbers) */
function letterWords(t){ return t.trim().split(/\s+/).filter(function(w){ return /[A-Za-z]/.test(w); }).length; }
/* the first screen as run.py --words counts it: text in #main above 900 px, outside SVGs and closed folds */
function firstScreen(){
  var tot = 0, blocks = 0, seen = [], walk = document.createTreeWalker(document.getElementById("main"), NodeFilter.SHOW_TEXT);
  window.scrollTo(0, 0);
  while (walk.nextNode()) {
    var n = walk.currentNode, el = n.parentElement, t = n.textContent.trim();
    if (!t || el.closest("svg") || el.closest(".screen:not(.on)") || el.closest("details:not([open])") && !el.closest("summary")) continue;
    var r = el.getBoundingClientRect(); if (!r.width || r.top >= 900 || r.bottom <= 80) continue;
    var cs = getComputedStyle(el); if (cs.visibility === "hidden" || cs.display === "none") continue;
    tot += t.split(/\s+/).length;
    if (el.closest(".tipc,.ck")) continue;
    var blk = el.closest("li,p,td,th,h1,h2,h3,div,span,summary,button,label");
    if (seen.indexOf(blk) < 0) { seen.push(blk); if (blk.textContent.trim().split(/\s+/).length >= 8) blocks++; }
  }
  return {words:tot, blocks:blocks};
}
/* a hover table: at most five rows, labels of one or two words, no subtitle sentence (the plan page's standard) */
function tipCheck(where, hit, cx, cy, head){
  hit.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:cx, clientY:cy}));
  var tip = $(".ctip"), rows = $$(".ctip tr"), labels = $$(".ctip td:nth-child(2)").map(function(l){ return l.textContent.trim(); });
  ok(where + ": hover table shows", !!tip && tip.style.display === "block" && head.test(tip.textContent), tip && tip.textContent);
  ok(where + ": hover table ≤ 5 rows, labels ≤ 2 words, no subtitle", rows.length <= 5 && rows.length >= 1 && labels.every(function(l){ return l.split(/\s+/).length <= 2; }) && !$(".ctip .ct-h span"), rows.length + " rows: " + labels.join(","));
  var t = tip ? tip.textContent : "";
  document.body.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:5, clientY:5}));
  return t;
}

/* the page as a reader meets it: the header, the call page, its hover notes and the words drawn in its charts */
var ROOTS = ["#ph-chips", "#ph-ctx", "#s-calls"];
function shownText(){
  var out = [];
  ROOTS.forEach(function(r){ var root = $(r), w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) { var n = w.currentNode, t = n.textContent.trim(); if (t && n.parentElement.getClientRects().length) out.push({t:t, el:n.parentElement}); } });
  return out;
}
function allNotes(){ var n = []; ROOTS.forEach(function(r){ var root = $(r); if (root.hasAttribute("data-note")) n.push(root); n = n.concat($$(r + " [data-note]")); }); return n.map(note); }
function allTitles(){ return $$("#s-calls svg title").map(function(t){ return t.textContent; }); }
/* every acronym needs its long form somewhere a reader can see it (text or note), and a hover note wherever it is shown */
var LONG = {AEMO:/Australian Energy Market Operator \(AEMO\)|market operator \(AEMO\)/, IRCR:/Individual reserve capacity requirement \(IRCR\)|capacity charge \(IRCR\)/i, WEM:/Wholesale Electricity Market \(WEM\)/};
var NOT_ACRONYM = ["CALL", "NO", "DECISION", "AWST"];   /* the call pill, the 09:00 DECISION marker, the time zone (as on the plan page) */
function acronymProblems(){
  var texts = shownText(), notes = allNotes(), corpus = texts.map(function(x){ return x.t; }).concat(notes).join(" \n "), bad = [], found = {};
  (corpus.match(/\b[A-Z][A-Z0-9]{2,}\b/g) || []).forEach(function(a){ if (NOT_ACRONYM.indexOf(a) < 0) found[a] = 1; });
  Object.keys(found).forEach(function(a){
    if (!LONG[a]) bad.push(a + ": no long form known to the test");
    else if (!LONG[a].test(corpus)) bad.push(a + ": long form missing");
  });
  texts.forEach(function(x){ (x.t.match(/\b[A-Z][A-Z0-9]{2,}\b/g) || []).forEach(function(a){
    if (NOT_ACRONYM.indexOf(a) < 0 && !x.el.closest("[data-note]") && !x.el.closest("svg")) bad.push(a + ": shown without a hover note in '" + x.t.slice(0, 40) + "'"); }); });
  return {bad:bad, found:Object.keys(found)};
}
/* every named period in a note carries its dates */
var PERIODS = [[/contract year/, /1 Jul 2025 – 30 Jun 2026/], [/Hot Season 2025-26/, /1 Dec 2025 – 31 Mar 2026/], [/training period/, /1 Apr 2025 – 30 Jun 2026/]];
function periodProblems(){ var bad = []; allNotes().forEach(function(n){ PERIODS.forEach(function(p){ if (p[0].test(n) && !p[1].test(n)) bad.push(n.slice(0, 70)); }); }); return bad; }
var JARGON = /Decision view|Season review|Rule flagged|Expected compensation|In-sample|in-sample|never trigger|not firm\b/;
/* chart text as the plan page allows it: axis numbers and times, dates, the unit, the mark, action names, and
   "number + one word" labels; nothing else (ROUND6 §1) */
var DEM_OK = /^(\d[\d,]*|\d\d:\d\d|[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2}|Demand, MW|09:00 DECISION|Hold charge|Call \d\d:\d\d–\d\d:\d\d|(peak|forecast|actual|threshold) [\d,]+ MW)$/;
var ACT_OK = /^(\d[\d,]*|Daily peak, MW|Calls|Today|threshold [\d,]+ MW|Dec 2025|Jan 2026|Feb|Mar|★)$/;
var BANNED = /4th-highest daily peak so far|Not yet known|Switch to|above threshold|below threshold|Forecast peak|Actual peak|Whole-system|call threshold today/;
function chartChecks(where){
  var dOff = svgTexts("#act-dem").filter(function(t){ return !DEM_OK.test(t); }), aOff = svgTexts("#act-chart").filter(function(t){ return !ACT_OK.test(t); });
  ok(where + ": demand chart text is only axis, unit, mark, actions and number+word labels", dOff.length === 0, dOff.join(" | "));
  ok(where + ": season chart text is only axis, unit, Today, threshold and months", aOff.length === 0, aOff.join(" | "));
  ok(where + ": no explanation sentences on either chart", !BANNED.test(svgText("#act-dem") + " " + svgText("#act-chart")), ((svgText("#act-dem") + " " + svgText("#act-chart")).match(BANNED) || [""])[0]);
  /* no two labels overlap in the demand chart (axis numbers excluded) */
  var ts = $$("#act-dem svg text").filter(function(e){ return !/^\d[\d,]*$/.test(e.textContent); }).map(function(e){ var b = e.getBBox(); return {t:e.textContent, x:b.x, y:b.y, r:b.x + b.width, b:b.y + b.height}; }), bad = [];
  for (var i = 0; i < ts.length; i++) for (var j = i + 1; j < ts.length; j++) { var p = ts[i], q = ts[j]; if (p.x < q.r && q.x < p.r && p.y < q.b && q.y < p.b) bad.push(p.t + " × " + q.t); }
  ok(where + ": no two labels overlap in the demand chart", bad.length === 0, bad.join("; "));
  var vw = +$("#act-dem svg").getAttribute("viewBox").split(" ")[2];
  ok(where + ": demand labels inside the chart", $$("#act-dem svg text").every(function(e){ var b = e.getBBox(); return b.x >= 0 && b.x + b.width <= vw + 0.5; }));
}
function termChecks(where){
  ok(where + ": no internal jargon", !JARGON.test($("#s-calls").innerHTML) && !JARGON.test($("#ph-chips").innerHTML + $("#ph-ctx").innerHTML), ($("#s-calls").innerHTML.match(JARGON) || [""])[0]);
  var ac = acronymProblems();
  ok(where + ": every acronym spelled out and noted", ac.bad.length === 0 && ac.found.indexOf("AEMO") >= 0, ac.bad.join("; ") || ac.found.join(","));
  ok(where + ": named periods in notes carry their dates", periodProblems().length === 0, periodProblems().join(" || "));
  /* the three vocabulary decisions: hold charge, customer floor, call (never standby, bare reserve or activation) */
  var corpusV = $("#s-calls").innerHTML + " " + allNotes().join(" ") + " " + $("#ph-chips").innerHTML + " " + $("#ph-ctx").textContent, mv = corpusV.match(/standby|activation|\breserve\b(?! floor)/i);
  ok(where + ": no standby, activation or bare reserve", !mv, mv && corpusV.slice(Math.max(0, corpusV.search(/standby|activation|\breserve\b(?! floor)/i) - 40), corpusV.search(/standby|activation|\breserve\b(?! floor)/i) + 40));
  ok(where + ": every note opens by keyboard", $$("#s-calls [data-note], #ph-chips [data-note]").every(function(e){ return e.tabIndex === 0 && note(e).length > 15; }));
  /* the decision row: short chips, their meaning on hover */
  var cs = chips("#call-dec"), longc = cs.filter(function(c){ return letterWords(c.textContent) > 4; });
  ok(where + ": decision chips ≤ " + (S_review() ? 5 : 4) + ", each ≤ 4 words with a note", cs.length <= (S_review() ? 5 : 4) && longc.length === 0 && cs.every(function(c){ return note(c).length > 30; }), cs.length + ": " + cs.map(function(c){ return c.textContent; }).join(" | "));
  chartChecks(where);
  /* the cut sentences are still reachable: the rule behind the threshold (title + hover table), the call window as a
     model assumption (title + note), average response not guaranteed (note), price spikes (note) */
  var tt = allTitles().join(" || "), nn = allNotes().join(" || ");
  ok(where + ": threshold rule in an SVG title", /4th-highest daily peak so far \(3,\d{3}\) − 85 MW margin/.test(tt) && tt.indexOf(GL("FLAGGED")) >= 0 && /a Hot Season has 4 official peak days/.test(tt), tt.slice(0, 120));
  ok(where + ": spikes, response and window caveats in notes", /price spikes only drive the site battery's offers/.test(nn) && (!S_called() || (/not guaranteed capacity/.test(nn) && /not a market rule/.test(nn + tt))), "");
}
function S_review(){ return $('[data-view="review"]').classList.contains("on"); }
function S_called(){ return txt("#call-head .dec-pill") === "CALL"; }

/* page header */
ok("header title and chips", txt("#ph-title") === "Tonight's call" && $$("#ph-chips .chip").map(function(c){ return c.textContent; }).join("|") === "1 · Decide|training-period day|model suggestion", $$("#ph-chips .chip").map(function(c){ return c.textContent; }).join("|"));
ok("header: training-period note names the day and reuses the glossary", /^14 Jan 2026 lies in the training period/.test(note(hdrChip(/training-period/))) && note(hdrChip(/training-period/)).indexOf(GL("TRAIN")) >= 0, note(hdrChip(/training-period/)));
ok("header: the offer is a model suggestion, not a contract (chip note, training period dated)", note(hdrChip(/model suggestion/)) === GL("CUSTOMOFFER") + " Training period: 1 Apr 2025 – 30 Jun 2026.", note(hdrChip(/model suggestion/)));
ok("header context: the offer's numbers only", txt("#ph-ctx") === "85 c/kWh · 45 calls/year", txt("#ph-ctx"));

/* view switch: plain names, same hooks */
ok("view switch labels", txt('[data-view="decision"]') === "Known at 09:00" && txt('[data-view="review"]') === "Whole season · hindsight" && note($('[data-view="review"]')) === GL("HINDSIGHT") && $('[data-view="decision"]').classList.contains("on"), txt('[data-view="decision"]') + " / " + txt('[data-view="review"]'));

/* default day: Wed 14 Jan 2026, a call */
ok("default day", txt("#day-label") === "Wed 14 Jan 2026", txt("#day-label"));
ok("CALL pill", txt("#call-head .dec-pill") === "CALL" && !$("#call-head .dec-pill").classList.contains("no"), txt("#call-head"));
ok("the headline says what a call is, in plain words", /Customer batteries discharge 17:30–19:30/.test(txt("#call-head")), txt("#call-head"));
ok("CALL pill note is the glossary entry", note($("#call-head .dec-pill")).indexOf(GL("CALL")) >= 0 && /discharge together/.test(note($("#call-head .dec-pill"))) && $("#call-head .dec-pill").tabIndex === 0, note($("#call-head .dec-pill")));
var wn = $("#call-head .nt");
ok("call window note: a model assumption, not a market rule", !!wn && /^17:30–19:30 is an assumption of this model, not a market rule; the official peak half-hours have fallen in this window in past seasons\.$/.test(note(wn)), note(wn));
ok("payment to customers A$ 20,655 under a 3-word caption", txt("#call-val .cap") === "Expected customer payment" && txt("#call-val .dv") === "A$ 20,655" && !$("#call-val .dv").classList.contains("off"), txt("#call-val .cap") + " " + txt("#call-val .dv"));
var tr = chips("#call-val").filter(function(c){ return c.textContent === "transfer payment"; })[0];
ok("transfer chip: glossary note plus the billing basis", !!tr && note(tr).indexOf(GL("TRANSFER")) === 0 && /sums to zero/.test(note(tr)) && /billed event net export: 85 c\/kWh/.test(note(tr)) && /not settled/.test(note(tr)), note(tr));
var rp = chips("#call-val").filter(function(c){ return /respond/.test(c.textContent); })[0];
ok("response chip: numbers on the chip, average not guaranteed in the note", !!rp && rp.textContent === "2,700 of 3,000 respond · average" && note(rp).indexOf(GL("RESPONSE") + " " + GL("ENROLLED")) === 0 && /not guaranteed capacity/.test(note(rp)) && /Offered: .* Responding: /.test(note(rp)), rp && rp.textContent);
ok("left chips: hold charge, the demand rule", chipTxt("#call-chips") === "hold charge 15:30–17:30 | demand rule, not price", chipTxt("#call-chips"));
var sb = chips("#call-chips").filter(function(c){ return /^hold charge/.test(c.textContent); })[0];
ok("hold-charge chip note: glossary hold charge and the 20% customer floor", !!sb && note(sb) === GL("HOLDCHARGE") + " " + GL("CUSTFLOOR") && /20% of each customer battery/.test(note(sb)), note(sb));
var sp = chips("#call-chips").filter(function(c){ return /demand rule/.test(c.textContent); })[0];
ok("rule chip says what triggers a call and why", !!sp && /not price spikes/.test(note(sp)) && /Price spike risk today: (high|low); price spikes only drive the site battery's offers\. What triggers a call is the peak-day rule: /.test(note(sp)) && note(sp).indexOf(GL("FLAGGED")) >= 0 && note(sp).indexOf(GL("IRCR")) >= 0, note(sp));
/* the four key notes carry the glossary's distinctive phrase */
ok("glossary phrases: call, flagged, capacity charge, transfer", /discharge together/.test(note($("#call-head .dec-pill"))) && /4th-highest/.test(note(sp)) && /12 peak half-hours/.test(note(sp)) && /sums to zero/.test(note(tr)), "");
ok("no outcome before hindsight", !$("#call-dec .cl-oc") && !$("#call-dec .hind"));
ok("demand heading: two words, the unit on the chart", txt("#act-dem-h") === "Demand forecast", txt("#act-dem-h"));
ok("season block: no click hint; days show a pointer", !$("#s-calls .blk-h .sub:not(#act-dem-sub)") && getComputedStyle($('#act-chart .hit[data-d="40"]')).cursor === "pointer");
termChecks("as known at 09:00");
ok("Hot Season named once, at the season heading, dates in its note", /Hot Season 2025-26 \(Dec–Mar\)/.test(txt("#act-h")) && /1 Dec 2025 – 31 Mar 2026/.test(note($("#act-h"))) && $("#act-h").tabIndex === 0 && !hdrChip(/Hot Season/), txt("#act-h") + " / " + note($("#act-h")));
var fs = firstScreen();
ok("first screen ≤ 75 words, ≤ 1 block of 8+ words", fs.words <= 75 && fs.blocks <= 1, fs.words + " words, " + fs.blocks + " blocks");

/* the call budget as one bar */
ok("budget: 45 cells, 9 used, tonight hatched", cells() === 45 && cells("u") === 9 && cells("t") === 1, cells() + "/" + cells("u") + "/" + cells("t"));
ok("budget label: two counts, two denominators", budgetLab() === "10 of 45 used | 77 season days left", budgetLab());
ok("budget: no caption; the bar fills the row", !$("#call-budget .cl-bcap") && $("#call-budget .cl-cells").getBoundingClientRect().width > 500, $("#call-budget .cl-cells").getBoundingClientRect().width);
var bls = $$("#call-budget .cl-bl > span");
ok("budget counts: calls left, the cap and the contract year in one note; the Hot Season in the other", /^35 left in the contract year, counting tonight's call\. The cap: at most 45 calls per contract year 2025-26 \(1 Jul 2025 – 30 Jun 2026\); one cell per call\./.test(note(bls[0])) && /not yet happened at 09:00/.test(note(bls[0])) && note(bls[0]).indexOf(GL("CONTRACTYEAR")) >= 0 && /^Days left in Hot Season 2025-26 \(1 Dec 2025 – 31 Mar 2026\), a different count/.test(note(bls[1])) && note(bls[1]).indexOf(GL("HOTSEASON")) >= 0 && bls[0].tabIndex === 0 && bls[1].tabIndex === 0, note(bls[0]) + " || " + note(bls[1]));
ok("budget bar has an accessible name", /9 calls used before today, tonight's call not yet happened, 35 left of the cap of 45/.test($("#call-budget .cl-cells").getAttribute("aria-label")));

/* the demand chart carries the call rule */
var dem = $("#act-dem svg"), dt = svgText("#act-dem");
ok("demand chart is an image with a name that explains the threshold", dem.getAttribute("role") === "img" && /forecast peak 3,870 MW, above the threshold\. Call threshold 3,820 MW = 4th-highest daily peak so far \(3,905\) − 85 MW margin/.test(dem.getAttribute("aria-label")) && dem.getAttribute("aria-label").indexOf(GL("FLAGGED")) >= 0 && dem.getAttribute("aria-label").indexOf(GL("PEAKDAY")) >= 0, dem.getAttribute("aria-label"));
var ttl = $$("#act-dem svg title").map(function(t){ return t.textContent; });
ok("demand: threshold and call window carry hover titles", ttl.some(function(t){ return /^Call threshold 3,820 MW = 4th-highest/.test(t) && t.indexOf(GL("FLAGGED")) >= 0 && /4 official peak days/.test(t); }) && ttl.some(function(t){ return /not a market rule/.test(t); }) && ttl.some(function(t){ return t === GL("HOLDCHARGE") + " " + GL("CUSTFLOOR"); }), ttl.join(" || ").slice(0, 160));
ok("demand source: two words, AEMO spelled out on hover", txt("#act-dem-sub") === "AEMO · before 09:00" && /^market operator \(AEMO\): /.test(note($("#act-dem-sub"))) && note($("#act-dem-sub")).indexOf(GL("AEMO")) >= 0 && $("#act-dem-sub").tabIndex === 0, txt("#act-dem-sub"));
["threshold 3,820 MW", "peak 3,870 MW", "Call 17:30–19:30", "Hold charge", "09:00 DECISION", "Demand, MW"].forEach(function(w){ ok("demand label: " + w, dt.indexOf(w) >= 0, dt.slice(0, 120)); });
ok("demand: forecast dashed, no actual before hindsight", dem.querySelectorAll('path[stroke-dasharray="5 3"]').length === 1 && solidActual() === 0 && dt.indexOf("actual") < 0);
ok("demand: no legend", !$("#act-dem .lgd") && !$("#s-calls .lgd"));
var tipT = tipCheck("demand 18:00", dem.querySelector('.hit[data-i="20"]'), 400, 400, /18:00–18:30/);
ok("demand hover: forecast, threshold, how it is set, batteries called", /Forecast3,\d{3} MW/.test(tipT) && /Threshold3,820 MW/.test(tipT) && /4th-highest3,905 − 85 MW/.test(tipT) && /Batteriescalled/.test(tipT) && !/Actual/.test(tipT), tipT);
tipT = tipCheck("demand 16:00", dem.querySelector('.hit[data-i="16"]'), 380, 400, /16:00–16:30/);
ok("demand hover at 16:00: batteries hold charge", /Batterieshold charge/.test(tipT), tipT);

/* the season chart: future hatched (nothing written on it), a compact legend, CSV */
var st = svgText("#act-chart");
ok("season: future hatched, with no caption on the hatch", $$('#act-chart rect[fill="url(#aF)"]').length === 1 && st.indexOf("Not yet known") < 0 && st.indexOf("Switch to") < 0, st.slice(-160));
ok("season: threshold today as number + word", st.indexOf("threshold 3,820 MW") >= 0 && st.indexOf("call threshold today") < 0, st.slice(0, 80));
ok("season: compact legend", txt("#act-legend") === "Daily peakFlaggedCalled", txt("#act-legend"));
/* flagged and called are different things, and the legend notes say how */
var lg = $$("#act-legend [data-note]"), lgF = lg.filter(function(e){ return /Flagged/.test(e.textContent); })[0], lgC = lg.filter(function(e){ return /^Called/.test(e.textContent); })[0];
ok("legend: flagged and called notes differ and say how", !!lgF && !!lgC && note(lgF) !== note(lgC) && note(lgF).indexOf(GL("FLAGGED")) >= 0 && /^Called: flagged and a call was still available/.test(note(lgC)) && /not always called/.test(note(lgF)), lgC && note(lgC));
ok("season: no stars before hindsight", st.indexOf("★") < 0);
ok("CSV download control", !!$('#s-calls [data-csvdl="act"]'));
tipT = tipCheck("season 10 Jan", $('#act-chart .hit[data-d="40"]'), 300, 820, /Sat 10 Jan 2026/);
ok("season hover: peak, flagged, called", /Peak3,\d{3} MW/.test(tipT) && /Flaggedyes/.test(tipT) && /Calledyes/.test(tipT) && !/Peak day/.test(tipT), tipT);
tipT = tipCheck("season future day", $('#act-chart .hit[data-d="100"]'), 900, 820, /Mar 2026/);
ok("season hover on a future day says it is not yet known", /Peaknot yet known at 09:00/.test(tipT), tipT);

/* round-4 blocks are gone */
ok("round-4 blocks gone", !$("#act-budget") && !$(".ac-why") && !$(".ac-exp") && !$("#act-card") && !$("#s-calls .foot") && !$("#s-calls .wl"));

/* step to a day without a call */
click($("#day-prev"));
ok("prev: Tue 13 Jan", txt("#day-label") === "Tue 13 Jan 2026", txt("#day-label"));
ok("prev: header note follows the day", /^13 Jan 2026 lies in the training period/.test(note(hdrChip(/training-period/))), note(hdrChip(/training-period/)));
ok("prev: NO CALL", txt("#call-head .dec-pill") === "NO CALL" && $("#call-head .dec-pill").classList.contains("no") && /Normal self-use/.test(txt("#call-head")), txt("#call-head"));
ok("prev: nothing paid", txt("#call-val .dv") === "A$ 0" && $("#call-val .dv").classList.contains("off") && !/transfer|respond/.test(chipTxt("#call-val")) && /no call used/.test(chipTxt("#call-val")), txt("#call-val"));
ok("prev: no event chips", !/hold charge/.test(chipTxt("#call-chips")) && /demand rule/.test(chipTxt("#call-chips")), chipTxt("#call-chips"));
ok("prev: budget 9 used, nothing hatched", cells("u") === 9 && cells("t") === 0 && budgetLab() === "9 of 45 used | 78 season days left", budgetLab());
dt = svgText("#act-dem");
ok("prev: no event window, the peak still labelled", dt.indexOf("Call 17:30") < 0 && dt.indexOf("Hold charge") < 0 && /peak 3,\d{3} MW/.test(dt) && /below the threshold/.test($("#act-dem svg").getAttribute("aria-label")), dt.slice(0, 160));
termChecks("no-call day");
click($("#day-next"));
ok("next: back to the call", txt("#day-label") === "Wed 14 Jan 2026" && txt("#call-head .dec-pill") === "CALL");
document.dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowRight", bubbles:true}));
ok("arrow key steps a day", txt("#day-label") === "Thu 15 Jan 2026" && cells("u") === 10 && cells("t") === 1, txt("#day-label"));
document.dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowLeft", bubbles:true}));

/* click a day on the season chart (as known at 09:00) */
click($('#act-chart .hit[data-d="40"]'));
ok("season click selects the day", txt("#day-label") === "Sat 10 Jan 2026" && txt("#call-head .dec-pill") === "CALL" && budgetLab() === "9 of 45 used | 81 season days left", txt("#day-label") + " " + budgetLab());
/* late in the season the threshold label stays inside the chart and clear of "Today" */
click($('#act-chart .hit[data-d="110"]'));
var sv = $("#act-chart svg"), vw = +sv.getAttribute("viewBox").split(" ")[2];
var tlab = $$("#act-chart svg text").filter(function(t){ return /^threshold/.test(t.textContent); })[0], tday = $$("#act-chart svg text").filter(function(t){ return t.textContent === "Today"; })[0];
var b1 = tlab && tlab.getBBox(), b2 = tday && tday.getBBox();
ok("late season: threshold label inside, clear of Today", !!b1 && !!b2 && b1.x >= 0 && b1.x + b1.width <= vw && (b1.x + b1.width < b2.x || b2.x + b2.width < b1.x || b1.y + b1.height < b2.y || b2.y + b2.height < b1.y), b1 && [b1.x, b1.width, vw, b2.x].join());
chartChecks("late season");
click($('#act-chart .hit[data-d="44"]'));

/* whole season: hindsight */
click($('[data-view="review"]'));
ok("review: switch on", $('[data-view="review"]').classList.contains("on") && !$('[data-view="decision"]').classList.contains("on"));
var oc = $("#call-dec .cl-oc");
ok("review: 14 Jan not an official peak day", !!oc && oc.classList.contains("mu") && oc.textContent.replace(/^[–!✓]/, "") === "Not a peak day" && /Known only after/.test(note(oc)) && /without capacity value/.test(note(oc)), oc && oc.textContent);
var hd = $("#call-dec .hind");
ok("review: hindsight tag next to the outcome, with its note", !!hd && /hindsight/i.test(hd.textContent) && note(hd) === GL("HINDSIGHT") && hd.tabIndex === 0, note(hd));
ok("review: actual line with its name, number + word", solidActual() === 1 && /actual 3,840 MW/.test(svgText("#act-dem")) && /forecast 3,870 MW/.test(svgText("#act-dem")), svgText("#act-dem").slice(0, 160));
ok("review: future hatch gone", $$('#act-chart rect[fill="url(#aF)"]').length === 0 && svgText("#act-chart").indexOf("Not yet known") < 0);
var lp = $$("#act-legend [data-note]").filter(function(e){ return /Official peak day/.test(e.textContent); })[0];
ok("review: four official peak days starred, legend says what they set on hover", (svgText("#act-chart").match(/★/g) || []).length === 4 && txt("#act-legend") === "Daily peakFlaggedCalled★Official peak day" && !!lp && /^Official peak day · sets the capacity charge\. /.test(note(lp)) && note(lp).indexOf(GL("PEAKDAY")) >= 0 && note(lp).indexOf(GL("IRCR")) >= 0 && lp.tabIndex === 0, txt("#act-legend"));
ok("review: budget shows the season total", cells("u") === 24 && cells("t") === 0 && budgetLab() === "24 of 45 used | whole season · hindsight" && $$("#call-budget .cl-cells i.cur").length === 1, budgetLab());
ok("review: chart titles say hindsight", /Hindsight/i.test(txt("#act-tag")) && note($("#act-tag .hind")) === note(hd) && txt("#act-dem-sub") === "AEMO + actual · hindsight" && /known only after the fact/.test(note($("#act-dem-sub"))) && txt("#act-dem-h") === "Demand", txt("#act-dem-sub") + " / " + txt("#act-dem-h"));
termChecks("whole season");
tipT = tipCheck("review demand 18:00", $('#act-dem svg .hit[data-i="20"]'), 400, 400, /18:00–18:30/);
ok("review hover: actual row added, still ≤ 5 rows", /Actual3,\d{3} MW/.test(tipT) && /Forecast3,\d{3} MW/.test(tipT) && /Batteriescalled/.test(tipT), tipT);
tipT = tipCheck("review season 15 Jan", $('#act-chart .hit[data-d="45"]'), 320, 820, /Thu 15 Jan 2026/);
ok("review season hover: peak day row", /Peak dayyes/.test(tipT) && /Calledyes/.test(tipT), tipT);

/* review: click the season chart */
click($('#act-chart .hit[data-d="45"]'));
oc = $("#call-dec .cl-oc");
ok("review click: 15 Jan covered", txt("#day-label") === "Thu 15 Jan 2026" && !!oc && oc.classList.contains("ok") && /Official peak day · covered/.test(oc.textContent) && /capacity charge \(IRCR\)/.test(note(oc)), oc && oc.textContent);
termChecks("whole season, peak day");
click($('#act-chart .hit[data-d="50"]'));
oc = $("#call-dec .cl-oc");
ok("review click: 20 Jan no call, not a peak", txt("#day-label") === "Tue 20 Jan 2026" && txt("#call-head .dec-pill") === "NO CALL" && !!oc && oc.classList.contains("mu") && /Not a peak day/.test(oc.textContent) && !/without capacity value/.test(note(oc)), txt("#day-label") + " " + (oc && oc.textContent));
click($('#act-chart .hit[data-d="63"]'));
oc = $("#call-dec .cl-oc");
ok("review click: 2 Feb covered", /2 Feb 2026/.test(txt("#day-label")) && oc.classList.contains("ok") && txt("#call-head .dec-pill") === "CALL", txt("#day-label"));

/* back to the view as known at 09:00 */
click($('[data-view="decision"]'));
ok("as known at 09:00 again: outcome hidden, hatch back", !$("#call-dec .cl-oc") && $$('#act-chart rect[fill="url(#aF)"]').length === 1 && solidActual() === 0 && txt("#act-dem-sub") === "AEMO · before 09:00");
click($('#act-chart .hit[data-d="44"]'));
ok("reset to 14 Jan", txt("#day-label") === "Wed 14 Jan 2026");
goTo("plan");
done();
