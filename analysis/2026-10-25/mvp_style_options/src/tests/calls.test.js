/* Tonight's call (round 5, terminology pass of ROUND5.md section 7, then the shared glossary and the PM review items 47-60).
   Run with src/tests/run.py calls. */
goTo("calls");
/* the shared glossary's notes (GLOSS in engine.js, what + why) for the keys this page uses. The page scope is not
   visible from a test, so they are copied here: a paraphrase on the page, or an edit to GLOSS, fails the checks. */
var GLC = {
  TRAIN:"1 Apr 2025 – 30 Jun 2026, 456 days: the data used to build and choose the method. Results here cannot show how the method does on new data.",
  HOTSEASON:"1 Dec – 31 Mar in the WA market: when system demand peaks. The capacity charge for the following year is set by the 12 peak half-hours of this season.",
  HINDSIGHT:"Known only after the fact. It was not available at 09:00 when the decision was made, so it can judge a decision but never inform it.",
  CALL:"Asking customers' home batteries to discharge together for a set evening window, for a payment. Each call uses one of a limited number per contract year.",
  TRANSFER:"Money moved from Zenith to customers for responding to a call. It changes who gains, not the total value created, so it sums to zero across parties.",
  RESPONSE:"On average 90% of enrolled homes respond to a call. 3,000 enrolled homes is an assumption. No home is obliged to respond, so this is an average, not guaranteed capacity.",
  ENROLLED:"Homes that signed up to the offer (3,000 assumed). Offered: homes the offer was shown to. Responding: enrolled homes that actually respond to a call (90% on average). Payments are per responding home; fixed costs are per enrolled home.",
  HOLDCHARGE:"Before a call, home batteries stop discharging and keep their energy for the event window. Without it the batteries would be empty by the evening peak.",
  CUSTFLOOR:"The 20% of each home battery that is never discharged during a call. It protects the customer's own evening use.",
  FLAGGED:"A day the rule marks as a likely peak day at 09:00: the forecast peak is within 85 MW of the 4th-highest peak seen so far this season. Flagged days are called while calls remain; the 85 MW margin is set from past seasons.",
  IRCR:"Each retailer's share of the cost of keeping enough generation available, set by its customers' demand in the 12 peak half-hours of the Hot Season. Lowering demand in those 12 half-hours lowers the charge for a whole year; that is the main value of calling home batteries.",
  CONTRACTYEAR:"1 Jul 2025 – 30 Jun 2026: the year the customer offer's call cap and payments are counted over. It lies inside the training period, so its results are not a blind test.",
  AEMO:"The Australian Energy Market Operator runs the wholesale market and publishes the demand forecasts and prices used here. Only AEMO data published before 09:00 may be used for the 09:00 decision.",
  PEAKDAY:"One of the 4 Hot Season days that contain the 12 peak half-hours. They are known only after the season. A call on a peak day lowers the capacity charge; a call on any other day only costs the payment."
};
function GL(k){ return GLC[k]; }
function svgText(sel){ var s = $(sel + " svg"); return s ? Array.prototype.map.call(s.querySelectorAll("text"), function(t){ return t.textContent; }).join(" | ") : ""; }
function chips(sel){ return $$(sel + " .tipc"); }
function chipTxt(sel){ return chips(sel).map(function(c){ return c.textContent; }).join(" | "); }
function cells(cls){ return $$("#call-budget .cl-cells i" + (cls ? "." + cls : "")).length; }
function budgetLab(){ return $$("#call-budget .cl-bl > span").map(function(s){ return s.textContent; }).join(" | "); }
function solidActual(){ return $$("#act-dem svg path").filter(function(p){ return p.getAttribute("stroke-width") === "2" && !p.getAttribute("stroke-dasharray"); }).length; }
function note(el){ return el ? el.getAttribute("data-note") || "" : ""; }
function hdrChip(re){ return $$("#ph-chips .chip").filter(function(c){ return re.test(c.textContent); })[0]; }

/* the page as a reader meets it: the header, the call page, its hover notes and the words drawn in its charts */
var ROOTS = ["#ph-chips", "#ph-ctx", "#s-calls"];
function shownText(){
  var out = [];
  ROOTS.forEach(function(r){ var root = $(r), w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) { var n = w.currentNode, t = n.textContent.trim(); if (t && n.parentElement.getClientRects().length) out.push({t:t, el:n.parentElement}); } });
  return out;
}
function allNotes(){ var n = []; ROOTS.forEach(function(r){ var root = $(r); if (root.hasAttribute("data-note")) n.push(root); n = n.concat($$(r + " [data-note]")); }); return n.map(note); }
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
function termChecks(where){
  ok(where + ": no internal jargon", !JARGON.test($("#s-calls").innerHTML) && !JARGON.test($("#ph-chips").innerHTML + $("#ph-ctx").innerHTML), ($("#s-calls").innerHTML.match(JARGON) || [""])[0]);
  var ac = acronymProblems();
  ok(where + ": every acronym spelled out and noted", ac.bad.length === 0 && ac.found.indexOf("AEMO") >= 0, ac.bad.join("; ") || ac.found.join(","));
  ok(where + ": named periods in notes carry their dates", periodProblems().length === 0, periodProblems().join(" || "));
  /* the three vocabulary decisions: hold charge, customer floor, call (never standby, bare reserve or activation) */
  var corpusV = $("#s-calls").innerHTML + " " + allNotes().join(" ") + " " + $("#ph-chips").innerHTML + " " + $("#ph-ctx").textContent, mv = corpusV.match(/standby|activation|\breserve\b(?! floor)/i);
  ok(where + ": no standby, activation or bare reserve", !mv, mv && corpusV.slice(Math.max(0, corpusV.search(/standby|activation|\breserve\b(?! floor)/i) - 40), corpusV.search(/standby|activation|\breserve\b(?! floor)/i) + 40));
  ok(where + ": every note opens by keyboard", $$("#s-calls [data-note], #ph-chips [data-note]").every(function(e){ return e.tabIndex === 0 && note(e).length > 15; }));
}

/* page header */
ok("header title and chips", txt("#ph-title") === "Tonight's call" && $$("#ph-chips .chip").map(function(c){ return c.textContent; }).join("|") === "1 · Decide|training-period day|Hot Season 2025-26 (Dec–Mar)", $$("#ph-chips .chip").map(function(c){ return c.textContent; }).join("|"));
ok("header: training-period note names the day and reuses the glossary", /^14 Jan 2026 lies in the training period/.test(note(hdrChip(/training-period/))) && note(hdrChip(/training-period/)).indexOf(GL("TRAIN")) >= 0, note(hdrChip(/training-period/)));
ok("header: Hot Season chip note is the glossary entry plus the dates", note(hdrChip(/Hot Season/))  .indexOf(GL("HOTSEASON")) === 0 && /1 Dec 2025 – 31 Mar 2026/.test(note(hdrChip(/Hot Season/))), note(hdrChip(/Hot Season/)));
ok("header context ≤ 12 words, names the 09:00 decision and the model suggestion", txt("#ph-ctx").split(/\s+/).length <= 12 && /09:00/.test(txt("#ph-ctx")) && /85 c\/kWh \(model suggestion\)/.test(txt("#ph-ctx")), txt("#ph-ctx"));

/* view switch: plain names, same hooks */
ok("view switch labels", txt('[data-view="decision"]') === "As known at 09:00" && txt('[data-view="review"]') === "Whole season · with hindsight" && note($('[data-view="review"]')) === GL("HINDSIGHT") && $('[data-view="decision"]').classList.contains("on"), txt('[data-view="decision"]') + " / " + txt('[data-view="review"]'));

/* default day: Wed 14 Jan 2026, a call */
ok("default day", txt("#day-label") === "Wed 14 Jan 2026", txt("#day-label"));
ok("CALL pill", txt("#call-head .dec-pill") === "CALL" && !$("#call-head .dec-pill").classList.contains("no"), txt("#call-head"));
ok("the headline says what a call is, in plain words", /Home batteries discharge 17:30–19:30/.test(txt("#call-head")), txt("#call-head"));
ok("CALL pill note is the glossary entry", note($("#call-head .dec-pill")).indexOf(GL("CALL")) >= 0 && /discharge together/.test(note($("#call-head .dec-pill"))) && $("#call-head .dec-pill").tabIndex === 0, note($("#call-head .dec-pill")));
var wn = $("#call-head .nt");
ok("call window note: a model assumption, not a market rule", !!wn && /^17:30–19:30 is an assumption of this model, not a market rule; the official peak half-hours have fallen in this window in past seasons\.$/.test(note(wn)), note(wn));
ok("payment to customers A$ 20,655", txt("#call-val .cap") === "Expected payment to customers for this call" && txt("#call-val .dv") === "A$ 20,655" && !$("#call-val .dv").classList.contains("off"), txt("#call-val .cap") + " " + txt("#call-val .dv"));
var tr = chips("#call-val").filter(function(c){ return c.textContent === "payment to customers · transfer"; })[0];
ok("transfer chip: glossary note plus the billing basis", !!tr && note(tr).indexOf(GL("TRANSFER")) === 0 && /sums to zero/.test(note(tr)) && /billed event net export: 85 c\/kWh/.test(note(tr)), note(tr));
var rp = chips("#call-val").filter(function(c){ return /respond/.test(c.textContent); })[0];
ok("response chip: enrolled homes, average, not guaranteed", !!rp && rp.textContent === "2,700 of 3,000 enrolled homes expected to respond · average, not guaranteed" && note(rp) === GL("RESPONSE") + " " + GL("ENROLLED") && /Offered: .* Responding: /.test(note(rp)), rp && rp.textContent);
ok("left chips: hold charge, customer floor, spikes", /hold charge 15:30–17:30 · 20% customer floor kept/.test(chipTxt("#call-chips")) && /price spikes don't trigger calls/.test(chipTxt("#call-chips")), chipTxt("#call-chips"));
var sb = chips("#call-chips").filter(function(c){ return /^hold charge/.test(c.textContent); })[0];
ok("hold-charge chip note: glossary hold charge and customer floor", !!sb && note(sb) === GL("HOLDCHARGE") + " " + GL("CUSTFLOOR"), note(sb));
var sp = chips("#call-chips").filter(function(c){ return /price spikes/.test(c.textContent); })[0];
ok("spike chip says what triggers a call and why", !!sp && /Price spikes only drive the site battery's offers\. What triggers a call is the peak-day rule: /.test(note(sp)) && note(sp).indexOf(GL("FLAGGED")) >= 0 && note(sp).indexOf(GL("IRCR")) >= 0, note(sp));
/* (b) the four key notes carry the glossary's distinctive phrase */
ok("glossary phrases: call, flagged, capacity charge, transfer", /discharge together/.test(note($("#call-head .dec-pill"))) && /4th-highest/.test(note(sp)) && /12 peak half-hours/.test(note(sp)) && /sums to zero/.test(note(tr)), "");
ok("decision row: at most 4 chips", chips("#call-dec").length <= 4, chips("#call-dec").length);
ok("no outcome before hindsight", !$("#call-dec .cl-oc") && !$("#call-dec .hind"));
termChecks("as known at 09:00");
ok("Hot Season dates on the page", /Hot Season 2025-26 \(Dec–Mar\)/.test(txt("#act-h")) && /1 Dec – 31 Mar/.test(note($("#act-h"))) && $("#act-h").tabIndex === 0, txt("#act-h") + " / " + note($("#act-h")));

/* the call budget as one bar */
ok("budget: 45 cells, 9 used, tonight hatched", cells() === 45 && cells("u") === 9 && cells("t") === 1, cells() + "/" + cells("u") + "/" + cells("t"));
ok("budget label", budgetLab() === "10 of 45 calls used this contract year | 35 left | 77 Hot Season days remain", budgetLab());
var bn = $("#call-budget .cl-bcap");
ok("budget caption is the cap, note names both denominators with dates", !!bn && txt("#call-budget .cl-bcap") === "Cap: 45 calls" && /per contract year 2025-26 \(1 Jul 2025 – 30 Jun 2026\)/.test(note(bn)) && /Hot Season 2025-26 \(1 Dec 2025 – 31 Mar 2026\)/.test(note(bn)) && /not yet happened/.test(note(bn)), note(bn));
var bls = $$("#call-budget .cl-bl > span");
ok("budget counts: contract year and Hot Season notes from the glossary", note(bls[0]).indexOf(GL("CONTRACTYEAR")) >= 0 && note(bls[2]).indexOf(GL("HOTSEASON")) === 0 && bls[0].tabIndex === 0 && bls[2].tabIndex === 0, note(bls[0]));
ok("budget bar has an accessible name", /9 calls used before today, tonight's call not yet happened, 35 left of the cap of 45/.test($("#call-budget .cl-cells").getAttribute("aria-label")));

/* the demand chart carries the call rule */
var dem = $("#act-dem svg"), dt = svgText("#act-dem");
ok("demand chart is an image with a name that explains the threshold", dem.getAttribute("role") === "img" && /call threshold 3,820 MW, the 4th-highest daily peak so far \(3,905\) minus a 85 MW margin/.test(dem.getAttribute("aria-label")) && dem.getAttribute("aria-label").indexOf(GL("FLAGGED")) >= 0 && dem.getAttribute("aria-label").indexOf(GL("PEAKDAY")) >= 0 && /a Hot Season has 4 official peak days/.test(dem.getAttribute("aria-label")), dem.getAttribute("aria-label"));
var ttl = $$("#act-dem svg title").map(function(t){ return t.textContent; });
ok("demand: threshold and call window carry hover titles", ttl.some(function(t){ return t.indexOf(GL("FLAGGED")) >= 0 && /4 official peak days/.test(t); }) && ttl.some(function(t){ return /not a market rule/.test(t); }) && ttl.some(function(t){ return t === GL("HOLDCHARGE") + " " + GL("CUSTFLOOR"); }), ttl.join(" || ").slice(0, 160));
ok("demand source: AEMO, spelled out on hover", txt("#act-dem-sub") === "AEMO forecast, before 09:00" && /^market operator \(AEMO\): /.test(note($("#act-dem-sub"))) && note($("#act-dem-sub")).indexOf(GL("AEMO")) >= 0 && $("#act-dem-sub").tabIndex === 0, txt("#act-dem-sub"));
["Call threshold 3,820 MW", "= 4th-highest daily peak so far (3,905) − 85 MW margin", "Forecast peak 3,870 MW · above threshold", "Call 17:30–19:30", "Hold charge", "09:00 DECISION", "Whole-system demand, MW"].forEach(function(w){ ok("demand label: " + w, dt.indexOf(w) >= 0, dt.slice(0, 120)); });
/* the narrower plot (X0 92): the rule annotation stays inside the chart and clear of the peak label */
var vwD = +dem.getAttribute("viewBox").split(" ")[2], tA = $$("#act-dem svg text").filter(function(t){ return /^= 4th-highest/.test(t.textContent); })[0], tP = $$("#act-dem svg text").filter(function(t){ return /^Forecast peak/.test(t.textContent); })[0];
var bA = tA && tA.getBBox(), bP = tP && tP.getBBox();
ok("demand: rule annotation inside the chart, clear of the peak label", !!bA && !!bP && bA.x >= 92 && bA.x + bA.width <= vwD && (bP.x + bP.width < bA.x || bA.y + bA.height < bP.y || bP.y + bP.height < bA.y), bA && [bA.x.toFixed(0), bA.width.toFixed(0), vwD, bP.x.toFixed(0), bP.width.toFixed(0)].join());
ok("demand: forecast dashed, no actual before hindsight", dem.querySelectorAll('path[stroke-dasharray="5 3"]').length === 1 && solidActual() === 0 && dt.indexOf("Actual") < 0);
ok("demand: no legend", !$("#act-dem .lgd") && !$("#s-calls .lgd"));
var hit = dem.querySelector('.hit[data-i="20"]');
hit.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:400, clientY:400}));
var tip = $(".ctip");
ok("demand hover table", !!tip && tip.style.display === "block" && /18:00–18:30/.test(tip.textContent) && /Call threshold/.test(tip.textContent) && /3,820 MW/.test(tip.textContent) && /called/.test(tip.textContent) && !/standby/i.test(tip.textContent), tip && tip.textContent);
document.body.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:5, clientY:5}));

/* the season chart: future hatched and labelled, a compact legend, CSV */
var st = svgText("#act-chart");
ok("season: future hatched, with the switch named as it reads", $$('#act-chart rect[fill="url(#aF)"]').length === 1 && st.indexOf("Not yet known at the 09:00 decision") >= 0 && st.indexOf("Whole season · with hindsight") >= 0, st.slice(-160));
ok("season: call threshold today", st.indexOf("call threshold today 3,820 MW") >= 0, st.slice(0, 80));
ok("season: compact legend", txt("#act-legend") === "Daily peakFlagged by the call ruleCalled", txt("#act-legend"));
/* (c) flagged and called are different things, and the legend notes say how */
var lg = $$("#act-legend [data-note]"), lgF = lg.filter(function(e){ return /Flagged/.test(e.textContent); })[0], lgC = lg.filter(function(e){ return /^Called/.test(e.textContent); })[0];
ok("legend: flagged and called notes differ and say how", !!lgF && !!lgC && note(lgF) !== note(lgC) && note(lgF).indexOf(GL("FLAGGED")) >= 0 && /^Called: flagged and a call was still available/.test(note(lgC)) && /not always called/.test(note(lgF)), lgC && note(lgC));
ok("season: no stars before hindsight", st.indexOf("★") < 0);
ok("CSV download control", !!$('#s-calls [data-csvdl="act"]'));
var hs = $('#act-chart .hit[data-d="40"]');
hs.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:300, clientY:820}));
tip = $(".ctip");
ok("season hover names the call plainly", !!tip && /Flagged by the call rule/.test(tip.textContent) && /Customer batteries called/.test(tip.textContent), tip && tip.textContent);
document.body.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:5, clientY:5}));

/* round-4 blocks are gone */
ok("round-4 blocks gone", !$("#act-budget") && !$(".ac-why") && !$(".ac-exp") && !$("#act-card") && !$("#s-calls .foot") && !$("#s-calls .wl"));

/* step to a day without a call */
click($("#day-prev"));
ok("prev: Tue 13 Jan", txt("#day-label") === "Tue 13 Jan 2026", txt("#day-label"));
ok("prev: header note follows the day", /^13 Jan 2026 lies in the training period/.test(note(hdrChip(/training-period/))), note(hdrChip(/training-period/)));
ok("prev: NO CALL", txt("#call-head .dec-pill") === "NO CALL" && $("#call-head .dec-pill").classList.contains("no") && /Normal self-use/.test(txt("#call-head")), txt("#call-head"));
ok("prev: nothing paid", txt("#call-val .dv") === "A$ 0" && $("#call-val .dv").classList.contains("off") && !/transfer|respond/.test(chipTxt("#call-val")) && /no call used/.test(chipTxt("#call-val")), txt("#call-val"));
ok("prev: no event chips", !/hold charge/.test(chipTxt("#call-chips")) && /price spikes/.test(chipTxt("#call-chips")), chipTxt("#call-chips"));
ok("prev: budget 9 used, nothing hatched", cells("u") === 9 && cells("t") === 0 && budgetLab() === "9 of 45 calls used this contract year | 36 left | 78 Hot Season days remain", budgetLab());
dt = svgText("#act-dem");
ok("prev: no event window, peak below threshold", dt.indexOf("Call 17:30") < 0 && dt.indexOf("Hold charge") < 0 && /below threshold/.test(dt), dt.slice(0, 160));
termChecks("no-call day");
click($("#day-next"));
ok("next: back to the call", txt("#day-label") === "Wed 14 Jan 2026" && txt("#call-head .dec-pill") === "CALL");
document.dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowRight", bubbles:true}));
ok("arrow key steps a day", txt("#day-label") === "Thu 15 Jan 2026" && cells("u") === 10 && cells("t") === 1, txt("#day-label"));
document.dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowLeft", bubbles:true}));

/* click a day on the season chart (as known at 09:00) */
click($('#act-chart .hit[data-d="40"]'));
ok("season click selects the day", txt("#day-label") === "Sat 10 Jan 2026" && txt("#call-head .dec-pill") === "CALL" && budgetLab() === "9 of 45 calls used this contract year | 36 left | 81 Hot Season days remain", txt("#day-label") + " " + budgetLab());
/* late in the season the threshold label stays inside the chart and clear of "Today" */
click($('#act-chart .hit[data-d="110"]'));
var sv = $("#act-chart svg"), vw = +sv.getAttribute("viewBox").split(" ")[2];
var tlab = $$("#act-chart svg text").filter(function(t){ return /threshold today/.test(t.textContent); })[0], tday = $$("#act-chart svg text").filter(function(t){ return t.textContent === "Today"; })[0];
var b1 = tlab && tlab.getBBox(), b2 = tday && tday.getBBox();
ok("late season: threshold label inside, clear of Today", !!b1 && !!b2 && b1.x >= 0 && b1.x + b1.width <= vw && (b1.x + b1.width < b2.x || b2.x + b2.width < b1.x || b1.y + b1.height < b2.y || b2.y + b2.height < b1.y), b1 && [b1.x, b1.width, vw, b2.x].join());
click($('#act-chart .hit[data-d="44"]'));

/* whole season: hindsight */
click($('[data-view="review"]'));
ok("review: switch on", $('[data-view="review"]').classList.contains("on") && !$('[data-view="decision"]').classList.contains("on"));
var oc = $("#call-dec .cl-oc");
ok("review: 14 Jan not an official peak day", !!oc && oc.classList.contains("mu") && /Not an official peak day/.test(oc.textContent) && /Known only after/.test(note(oc)) && /without capacity value/.test(note(oc)), oc && oc.textContent);
var hd = $("#call-dec .hind");
ok("review: hindsight tag next to the outcome, with its note", !!hd && /hindsight/i.test(hd.textContent) && note(hd) === GL("HINDSIGHT") && hd.tabIndex === 0, note(hd));
ok("review: actual line with its name", solidActual() === 1 && /Actual peak 3,840 MW · hindsight/.test(svgText("#act-dem")) && /Forecast peak 3,870 MW/.test(svgText("#act-dem")), svgText("#act-dem").slice(0, 160));
ok("review: future hatch gone", $$('#act-chart rect[fill="url(#aF)"]').length === 0 && svgText("#act-chart").indexOf("Not yet known") < 0);
var lp = $$("#act-legend [data-note]").filter(function(e){ return /Official peak day/.test(e.textContent); })[0];
ok("review: four official peak days starred, legend says what they set", (svgText("#act-chart").match(/★/g) || []).length === 4 && /Official peak day · sets the capacity charge/.test(txt("#act-legend")) && !!lp && note(lp) === GL("PEAKDAY") + " capacity charge (IRCR): " + GL("IRCR") && lp.tabIndex === 0, txt("#act-legend"));
ok("review: budget shows the season total", cells("u") === 24 && cells("t") === 0 && /^24 of 45 calls used \| whole Hot Season · with hindsight$/.test(budgetLab()) && $$("#call-budget .cl-cells i.cur").length === 1, budgetLab());
ok("review: chart titles say hindsight", /Hindsight/i.test(txt("#act-tag")) && note($("#act-tag .hind")) === note(hd) && /hindsight/i.test(txt("#act-dem-sub")) && /AEMO/.test(txt("#act-dem-sub")), txt("#act-dem-sub"));
termChecks("whole season");

/* review: click the season chart */
click($('#act-chart .hit[data-d="45"]'));
oc = $("#call-dec .cl-oc");
ok("review click: 15 Jan covered", txt("#day-label") === "Thu 15 Jan 2026" && !!oc && oc.classList.contains("ok") && /Official peak day · covered/.test(oc.textContent) && /capacity charge \(IRCR\)/.test(note(oc)), oc && oc.textContent);
termChecks("whole season, peak day");
click($('#act-chart .hit[data-d="50"]'));
oc = $("#call-dec .cl-oc");
ok("review click: 20 Jan no call, not a peak", txt("#day-label") === "Tue 20 Jan 2026" && txt("#call-head .dec-pill") === "NO CALL" && !!oc && oc.classList.contains("mu") && /Not an official peak day/.test(oc.textContent) && !/without capacity value/.test(note(oc)), txt("#day-label") + " " + (oc && oc.textContent));
click($('#act-chart .hit[data-d="63"]'));
oc = $("#call-dec .cl-oc");
ok("review click: 2 Feb covered", /2 Feb 2026/.test(txt("#day-label")) && oc.classList.contains("ok") && txt("#call-head .dec-pill") === "CALL", txt("#day-label"));

/* back to the view as known at 09:00 */
click($('[data-view="decision"]'));
ok("as known at 09:00 again: outcome hidden, hatch back", !$("#call-dec .cl-oc") && $$('#act-chart rect[fill="url(#aF)"]').length === 1 && solidActual() === 0 && txt("#act-dem-sub") === "AEMO forecast, before 09:00");
click($('#act-chart .hit[data-d="44"]'));
ok("reset to 14 Jan", txt("#day-label") === "Wed 14 Jan 2026");
goTo("plan");
done();
