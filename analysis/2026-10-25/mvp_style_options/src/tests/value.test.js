/* Value · customer fleet (round 6b). Run with src/tests/run.py value. One scenario, S2b (assumed 2027 capacity rule):
   expected numbers are evalOffer's outputs for the saved run R‑0142 (custom 1,287,528 − reference 735,802 = 551,726).
   The headline is also checked against the chart's own bar values, so a headline computed the wrong way round fails
   even if a number happened to match.
   Round 6 (ROUND6.md 1, 2.3, 4): chart text is a whitelist of row names, numbers and "number + one word"; the row hover
   table has ≤ 5 rows with one- or two-word labels and no subtitle; the decision row has two chips of ≤ 4 words; every
   cut sentence is still reachable in a hover note or a fold; the first screen stays within the word budget.
   Round 6b (user, 2026-10-27): the fleet is Zenith's own business customers' batteries; Synergy is only the source of
   the reference terms (never a party, a bar, a button or a chip); S3 and the scenario toggle are gone; "homes" are
   "customers". */
goTo("value");
function notes(el){ return el ? el.getAttribute("data-note") || "" : ""; }
function svgTxt(el){ return el ? el.textContent.replace(/\s+/g, " ") : ""; }
function barOf(row, s){ return row.querySelector('rect.vbar[data-s="' + s + '"]'); }
function zeroX(svg){ return +svg.querySelector(".v-zero").getAttribute("x1"); }
function chartGain(){ var bz = function(k){ return +barOf($('#v-opts .vrow[data-k="' + k + '"]'), "z").getAttribute("data-v"); }; return bz("custom") - bz("ref"); }
function headNum(){ var t = txt("#v-head .v-n"); return (t.charAt(0) === "−" ? -1 : 1) * +t.replace(/[^\d]/g, ""); }
function words(s){ return s.trim().split(/\s+/).length; }
/* ROUND5.md section 7 and round 6b: no internal project names anywhere in the page, labels and hover notes included */
var JARGON = /Synergy TPA|Peak IRCR|in-sample|not chronological|Ledger by party|Hot Season holdouts|\bS3\b|\bTPA\b/;
function jargon(){ var m = $("#s-value").innerHTML.match(JARGON); return m ? m[0] : ""; }
/* vocabulary decisions (2026-10-27): call not activation, customer floor not customer reserve, hold charge not standby;
   no research labels; customers, not homes. Checked in the page (labels and data-note) and in every chart tooltip. */
var OLDWORDS = /activation|customer reserve|standby|policy C|sweep|in-sample|\bhomes?\b/i;
function hover(i){ var h = $('#v-opts .hit[data-i="' + i + '"]'); h.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:400, clientY:400})); }
function unhover(){ document.body.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:5, clientY:5})); }
function tipText(i){ hover(i); var t = $(".ctip") ? $(".ctip").textContent : ""; unhover(); return t; }
function oldWords(){ var t = $("#s-value").innerHTML + " " + [0, 1, 2].map(tipText).join(" "), m = t.match(OLDWORDS); return m ? m[0] : ""; }
/* visible text nodes of the page in reading order (SVG included, closed folds skipped) */
function inClosedFold(el){ var sm = el.closest("summary"), d = el.closest("details:not([open])"); if (!d) return false; if (!sm || sm.parentElement !== d) return true; return !!(d.parentElement && d.parentElement.closest("details:not([open])")); }
function visText(){ var out = [], w = document.createTreeWalker($("#s-value"), NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { var n = w.currentNode, el = n.parentElement; if (!n.textContent.trim() || inClosedFold(el) || !el.getClientRects().length) continue; out.push(n); }
  return out; }
/* Synergy may be named only inside hover notes (the reference terms' source): never in visible text or SVG text */
function synergyVisible(){ return visText().filter(function(n){ return /Synergy/.test(n.textContent); }).map(function(n){ return n.textContent.trim().slice(0, 50); }); }
/* how many line boxes a cell's text takes (the cell itself stretches to the row's height) */
function lines(el){ var r = document.createRange(); r.selectNodeContents(el); return r.getClientRects().length; }
/* every acronym is spelled out somewhere on the page (text or hover note), and every visible use either spells it out
   or sits inside an element with a hover note */
var ACR = {IRCR:/capacity charge \(IRCR\)|reserve capacity requirement \(IRCR\)/i, AEMO:/market operator \(AEMO\)/, DEBS:/The Distributed Energy Buyback Scheme/, WEM:/wholesale electricity market \(WEM\)/};
function acronymProblems(){ var bad = [], all = $("#s-value").textContent + " " + $$("#s-value [data-note]").map(function(e){ return e.getAttribute("data-note"); }).join(" ");
  Object.keys(ACR).forEach(function(a){ var re = new RegExp("\\b" + a + "\\b");
    if (re.test(all) && !ACR[a].test(all)) bad.push(a + ": never spelled out");
    visText().forEach(function(n){ var el = n.parentElement; if (re.test(n.textContent) && !ACR[a].test(n.textContent) && !el.closest("[data-note]")) bad.push(a + " bare in “" + n.textContent.trim().slice(0, 40) + "”"); }); });
  return bad; }
/* ROUND6.md 1: what may be written on the two charts. Each <text> element's whole content must match one pattern */
var OPT_OK = /^(No calls|Reference terms|Custom offer)(baseline|\d+ c\/kWh · \d+ calls)$|^\d+ of \d+ calls$|^\d+ calls$|^\d+ of 4 peak days$|^customers [+−]?A\$ [\d,.]+ \/customer\/yr$|^Extra Zenith profit, A\$$|^−?A\$ [\d,]+( worst season)?$|^A\$ 0 baseline$|^(0|−?A\$[\d.]+[km])$|^(Baseline|No business case|Positive in S2b)$/;
var PTY_OK = /^(Zenith|Customers|Other parties)$|^−?A\$ [\d,]+ · −?A\$ [\d,.]+ \/customer\/yr$/;
function offList(sel, re){ return $$(sel + " text").map(function(t){ return t.textContent.replace(/\s+/g, " ").trim(); }).filter(function(s){ return !re.test(s); }); }
/* the first-screen word count, the way run.py --words counts it (1440×900, no SVG text, closed folds skipped) */
function firstScreen(){ window.scrollTo(0, 0); var tot = 0, blocks = [], seen = [], walk = document.createTreeWalker(document.getElementById("main"), NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) { var n = walk.currentNode, el = n.parentElement, t = n.textContent.trim();
    if (!t || el.closest("svg") || el.closest(".screen:not(.on)") || el.closest("details:not([open])") && !el.closest("summary")) continue;
    var r = el.getBoundingClientRect(); if (!r.width || r.top >= 900 || r.bottom <= 80) continue;
    var cs = getComputedStyle(el); if (cs.visibility === "hidden" || cs.display === "none") continue;
    tot += words(t); if (el.closest(".tipc,.ck")) continue;
    var blk = el.closest("li,p,td,th,h1,h2,h3,div,span,summary,button,label");
    if (seen.indexOf(blk) < 0) { seen.push(blk); if (words(blk.textContent) >= 8) blocks.push(blk.textContent.trim().replace(/\s+/g, " ").slice(0, 60)); } }
  return {words:tot, blocks:blocks}; }

ok("page opens alone", $("#s-value").classList.contains("on") && $$(".screen.on").length === 1);
ok("header: title, step chip, context ≤ 8 words, customer batteries", txt("#ph-title") === "Value by party" && txt("#ph-chips") === "2 · Improve" && txt("#ph-ctx") === "Customer batteries · custom offer vs reference terms" && words(txt("#ph-ctx")) <= 8, txt("#ph-ctx"));
var fs = firstScreen();
ok("first screen ≤ 75 words (ROUND6.md 1)", fs.words <= 75, fs.words);
ok("first screen: at most one text block of 8+ words, the header line", fs.blocks.length <= 1 && (!fs.blocks.length || /^Customer batteries/.test(fs.blocks[0])), fs.blocks.join(" | "));

/* ---- one scenario: no toggle, a chip for today's rule ---- */
ok("no scenario buttons, no segmented control in the toolbar", $$("#s-value [data-vscen]").length === 0 && !$("#s-value .toolbar .seg") && !$("#s-value .toolbar .lbl"), $("#s-value .toolbar").innerHTML.slice(0, 120));
var nos = $("#v-noscen");
ok("toolbar chip: today's rule (S2a): no business case, ≤ 6 words, S2a then S1 glossary notes on hover", txt("#v-noscen") === "today's rule (S2a): no business case" && nos.tabIndex === 0 && words(txt("#v-noscen")) <= 6 &&
  /^Zenith as retailer; under today's rule a customer's export counts as zero demand, not negative\. Normal self-use already removes the peak import/.test(notes(nos)) && /Zenith only sells the fleet's extra export at the wholesale price; no capacity value\. No call pays for itself at 09:00/.test(notes(nos)), notes(nos));
ok("the toolbar holds only that chip", $$("#s-value .toolbar > *").length === 1 && $("#s-value .toolbar > *").id === "v-noscen");
ok("no Synergy chip anywhere, Who is who no longer lists Synergy as a party", !$("#v-syn") && !$("#v-pnote") && !/Synergy/.test(txt("#who-line")), txt("#who-line"));
ok("headline +A$551,726 vs reference terms", txt("#v-head") === "+A$551,726 vs reference terms", txt("#v-head"));
ok("headline = custom − reference, read from the chart", headNum() === chartGain() && chartGain() === 551726, headNum() + " vs " + chartGain());
ok("headline number green", $("#v-head .v-n").classList.contains("pos") && getComputedStyle($("#v-head .v-n")).color === getComputedStyle($("#v-pill b")).color);
ok("headline number explains itself on hover: both profits, the year with dates, the scenario, the fleet baseline", /A\$ 1,287,528 − A\$ 735,802/.test(notes($("#v-head .v-n"))) && /contract year 2025-26 \(1 Jul 2025 – 30 Jun 2026, 365 days\)/.test(notes($("#v-head .v-n"))) && /S2b · assumed 2027 capacity rule/.test(notes($("#v-head .v-n"))) && /profit with the fleet minus Zenith's profit without the fleet/.test(notes($("#v-head .v-n"))) && /inside the training period/.test(notes($("#v-head .v-n"))) && $("#v-head .v-n").tabIndex === 0, notes($("#v-head .v-n")));
ok("“reference terms” opens the glossary (public Synergy terms, not a contract), drawn as words not a pill", /70 c\/kWh, 30 calls a year/.test(notes($("#v-head .v-def"))) && /Not a Zenith tariff and not a contract/.test(notes($("#v-head .v-def"))) && getComputedStyle($("#v-head .v-def")).borderTopWidth === "0px" && getComputedStyle($("#v-head .v-def")).display === "inline", notes($("#v-head .v-def")));
ok("verdict pill POSITIVE IN S2B", $("#v-pill").classList.contains("ok") && txt("#v-pill") === "Positive in S2b" && getComputedStyle($("#v-pill b")).textTransform === "uppercase", txt("#v-pill"));
ok("verdict pill states the rule and the assumed 2027 rule on hover", /no Hot Season losing money/.test(notes($("#v-pill b"))) && /Depends on an assumed future rule for the capacity charge \(IRCR\) from 2027; not current revenue/.test(notes($("#v-pill b"))) && $("#v-pill b").tabIndex === 0, notes($("#v-pill b")));
var ch = $$("#v-chips .tipc");
ok("two chips, each ≤ 4 words", ch.length === 2 && ch.every(function(c){ return words(c.textContent) <= 4; }), ch.map(function(c){ return c.textContent; }).join(" | "));
ok("chip 1: contract year 2025-26; replay, dates, 365 days and the training-period caveat on hover", ch[0].textContent === "contract year 2025-26" && /A replay \(historical simulation\) of contract year 2025-26 \(1 Jul 2025 – 30 Jun 2026, 365 days\)/.test(notes(ch[0])) && /It lies inside the training period, so its results are not a blind test/.test(notes(ch[0])), notes(ch[0]));
ok("chip 2: run R‑0142; placeholders, and the household-size caveat with the rerun for business customers, on hover", ch[1].textContent === "run R‑0142" && /placeholders from the design prototype/.test(notes(ch[1])) && /computed with household-size batteries; they will be recomputed for Zenith's business customers when the engine is rerun/.test(notes(ch[1])), notes(ch[1]));
ok("chips focusable, open right", ch.every(function(c){ return c.tabIndex === 0 && c.classList.contains("r"); }));
ok("no scenario or blind-test chip beside the headline (the pill and the tiles carry them)", !/hypothetical|blind test|S2b|2027/.test(txt("#v-chips")));
var hs = visText().filter(function(n){ return /Hot Season/.test(n.textContent); })[0];
ok("a visible “Hot Season”, if any, carries its months and a note", !hs || (/Hot Season [\d-]+ \(Dec–Mar\)/.test(hs.textContent) && /when system demand peaks/.test(notes(hs.parentElement.closest("[data-note]")))), hs && hs.textContent);

/* four status items under the headline */
var st = $$("#v-status .ck");
ok("four status items", st.length === 4, st.map(function(c){ return c.textContent; }).join(" | "));
ok("status 1 ✓ customers better off, per enrolled customer per year on hover", st[0].classList.contains("ok") && st[0].querySelector(".ci.ok").textContent === "✓" && /Customers better off/.test(st[0].textContent) && /normal self-use: \+A\$ 140.45 per enrolled customer per year/.test(notes(st[0])), notes(st[0]));
ok("status 2 ✓ customer limits held (cap, control time, floor)", st[1].classList.contains("ok") && st[1].textContent === "✓Customer limits held" && /yearly call cap/.test(notes(st[1])) && /max hours Zenith controls a battery per call \(hold charge \+ call ≤ 6 h\)/.test(notes(st[1])) && /customer floor/.test(notes(st[1])) && /1 Jul 2025 – 30 Jun 2026, 365 days/.test(notes(st[1])), st[1].textContent);
ok("status 3 ! Assumed 2027 rule; export counts against the peak, not yet confirmed, on hover", st[2].classList.contains("w") && st[2].querySelector(".ci.w").textContent === "!" && st[2].textContent === "!Assumed 2027 rule" && /Zenith as retailer; an assumed future rule in which export counts against the peak/.test(notes(st[2])) && /not yet confirmed/.test(notes(st[2])), notes(st[2]));
ok("status 4 – not yet tested blind: blind-test, first-chance and Hot Season notes", st[3].classList.contains("u") && st[3].querySelector(".ci.u").textContent === "–" && st[3].textContent === "–Not yet tested blind" && /never looked at before the method was locked/.test(notes(st[3])) && /first chance: Hot Season 2026-27, 1 Dec 2026 – 31 Mar 2027/.test(notes(st[3])) && /1 Dec – 31 Mar in the WA market/.test(notes(st[3])), st[3].textContent);
ok("status items ≤ 4 words each", st.every(function(c){ return words(c.textContent.slice(1)) <= 4; }), st.map(function(c){ return c.textContent; }).join(" | "));
ok("four status items on one row", st.every(function(c){ return Math.abs(c.getBoundingClientRect().top - st[0].getBoundingClientRect().top) < 1; }), st.map(function(c){ return Math.round(c.getBoundingClientRect().top); }).join());
ok("status items focusable with notes", st.every(function(c){ return c.tabIndex === 0 && notes(c).length > 30; }));
ok("no share tile, no 50% anywhere on the page", !/50%|share · assumption/.test($("#s-value").innerHTML));

/* the option chart */
var svg = $("#v-opts svg");
ok("chart: one svg, role img, labelled with the scenario", $$("#v-opts svg").length === 1 && svg.getAttribute("role") === "img" && /reference terms and the custom offer, S2b · assumed 2027 capacity rule/.test(svg.getAttribute("aria-label")), svg.getAttribute("aria-label"));
var rows = $$("#v-opts .vrow");
ok("chart: three rows No calls / Reference / Custom", rows.map(function(r){ return r.getAttribute("data-k"); }).join() === "none,ref,custom");
var r0 = svgTxt(rows[0]), r1 = svgTxt(rows[1]), r2 = svgTxt(rows[2]);
ok("chart text: only row names, numbers and number + one word (whitelist)", offList("#v-opts svg", OPT_OK).length === 0, offList("#v-opts svg", OPT_OK).join(" | "));
ok("row No calls: baseline at A$ 0", /No calls/.test(r0) && /A\$ 0 baseline/.test(r0) && /0 calls/.test(r0) && /customers A\$ 0 \/customer\/yr/.test(r0) && /Baseline/.test(r0) && !rows[0].querySelector("rect.vbar"), r0);
ok("row Reference: terms, calls, peak days, customers per customer", /Reference terms/.test(r1) && /70 c\/kWh · 30 calls/.test(r1) && /30 of 30 calls/.test(r1) && /3 of 4 peak days/.test(r1) && /customers \+A\$ 139.65 \/customer\/yr/.test(r1), r1);
ok("row Reference: A$ 735,802 and −A$ 372,000 worst season, No business case", /A\$ 735,802/.test(r1) && /−A\$ 372,000 worst season/.test(r1) && /No business case/.test(r1) && rows[1].querySelector(".vo-pill.bad"), r1);
ok("row Custom: terms, calls, peak days, A$ 1,287,528, A$ 670,000 worst season, Positive in S2b", /85 c\/kWh · 45 calls/.test(r2) && /24 of 45 calls/.test(r2) && /4 of 4 peak days/.test(r2) && /customers \+A\$ 140.45 \/customer\/yr/.test(r2) && /A\$ 1,287,528/.test(r2) && /A\$ 670,000 worst season/.test(r2) && /Positive in S2b/.test(r2) && rows[2].querySelector(".vo-pill.ok"), r2);
var banned = /Worst season: each Hot Season|the baseline both offers|per enrolled (home|customer) per year|Verdict|known only afterwards|cap used up|contract year 2025-26|tested on an earlier season|Zenith extra profit A|Synergy|\/home\/yr/;
ok("no captions on the charts: the cut sentences, Synergy and /home/yr are gone from the SVG", !banned.test(svgTxt(svg) + " " + svgTxt($("#v-parties svg"))), (svgTxt(svg) + " " + svgTxt($("#v-parties svg"))).match(banned));
ok("no footnote hover area under the chart", !$("#v-opts .vo-fn") && $$("#v-opts .hit").length === 3);
var zx = zeroX(svg), wb = barOf(rows[1], "worst"), cz = barOf(rows[2], "z");
ok("reference worst season drawn left of zero, in red", +wb.getAttribute("x") + +wb.getAttribute("width") <= zx + 0.01 && +wb.getAttribute("width") > 60 && /var\(--bad\)/.test(wb.getAttribute("style")), wb.getAttribute("x") + " " + wb.getAttribute("width") + " zero " + zx);
ok("positive bars start at zero and run right", [barOf(rows[1], "z"), cz, barOf(rows[2], "worst")].every(function(b){ return Math.abs(+b.getAttribute("x") - zx) < 0.01 && !/--bad/.test(b.getAttribute("style")); }));
ok("one scale: bar lengths in proportion", Math.abs(+wb.getAttribute("width") / +cz.getAttribute("width") - 372000 / 1287528) < 0.005);
var minBar = Math.min.apply(null, $$("#v-opts rect.vbar").map(function(b){ return +b.getAttribute("x"); }));
var lab = $$('#v-opts .vrow text').filter(function(t){ return +t.getAttribute("x") === 12; }).map(function(t){ var b = t.getBBox(); return b.x + b.width; });
ok("row labels end before the bars start", lab.length >= 9 && Math.max.apply(null, lab) < minBar - 8, Math.round(Math.max.apply(null, lab)) + " vs " + Math.round(minBar));
ok("all bars inside the plot", $$("#v-opts rect.vbar").every(function(b){ return +b.getAttribute("x") >= 290 && +b.getAttribute("x") + +b.getAttribute("width") <= +svg.getAttribute("viewBox").split(" ")[2] - 150; }));
ok("bar labels end inside the plot", $$("#v-opts text.vo-v").every(function(t){ var b = t.getBBox(); return b.x + b.width <= +svg.getAttribute("viewBox").split(" ")[2] - 150; }));
ok("no two labels overlap in the option chart", (function(){ var ts = $$("#v-opts svg text").map(function(e){ var b = e.getBBox(); return {t:e.textContent, x:b.x, y:b.y, r:b.x + b.width, b:b.y + b.height}; }), bad = [];
  for (var i = 0; i < ts.length; i++) for (var j = i + 1; j < ts.length; j++) { var p = ts[i], q = ts[j]; if (p.x < q.r && q.x < p.r && p.y < q.b && q.y < p.b) bad.push(p.t + " × " + q.t); } return bad.length === 0; })());
ok("no legend, no hatching", !$("#s-value .lgd") && !$("#s-value .legend") && !$("#s-value pattern") && !/url\(#/.test($("#s-value").innerHTML));
ok("axis title: Extra Zenith profit, A$", $$("#v-opts svg text.lnm").map(function(t){ return t.textContent; }).join("|") === "Extra Zenith profit, A$");
/* hover a row: at most five numbers, short labels, no subtitle */
hover(1);
var tip = $(".ctip"), tt = tip ? tip.textContent : "", trs = $$(".ctip tr"), tls = $$(".ctip td:nth-child(2)");
ok("hover reference row: shows", tip && tip.style.display === "block" && /Reference terms · 70 c\/kWh · 30 calls/.test(tt), tt);
ok("hover table ≤ 5 rows, labels ≤ 2 words, no subtitle, width 230", trs.length <= 5 && tls.every(function(l){ return words(l.textContent) <= 2; }) && !$(".ctip .ct-h span") && Math.round(tip.getBoundingClientRect().width) === 230, trs.length + " rows; " + tls.map(function(l){ return l.textContent; }).join(",") + "; " + tip.getBoundingClientRect().width);
ok("hover reference row: Zenith, worst season named in its label, customers, other parties, peak days (hindsight said in the aria-label and the verdict note)", /ZenithA\$ 735,802/.test(tt) && /Worst 2023-24−A\$ 372,000/.test(tt) && /CustomersA\$ 279,300/.test(tt) && /Other parties−A\$ 12,000/.test(tt) && /Peak days3 of 4/.test(tt) && !/Synergy/.test(tt) && /known only after the season \(hindsight\)/.test(svg.getAttribute("aria-label")) && /known only after the season \(hindsight\)/.test(notes($("#v-pill b"))), tt);
ok("hover table: labels on one line, values on one line", tls.every(function(l){ return lines(l) === 1; }) && $$(".ctip td.n").every(function(l){ return lines(l) === 1; }) && trs.every(function(l){ return l.getBoundingClientRect().height < 40; }), tls.concat($$(".ctip td.n")).map(function(l){ return l.textContent + ":" + lines(l); }).join(", "));
hover(2); tt = $(".ctip").textContent;
ok("hover custom row: A$ 1,287,528, worst A$ 670,000 · 2023-24, customers A$ 421,360, other parties −A$ 16,000, 4 of 4", /Custom offer · 85 c\/kWh · 45 calls/.test(tt) && /ZenithA\$ 1,287,528/.test(tt) && /Worst 2023-24A\$ 670,000/.test(tt) && /CustomersA\$ 421,360/.test(tt) && /Other parties−A\$ 16,000/.test(tt) && /Peak days4 of 4/.test(tt) && $$(".ctip tr").length <= 5, tt);
hover(0); tt = $(".ctip").textContent;
ok("hover no-calls row: baseline, two zeros", /No calls · baseline/.test(tt) && /ZenithA\$ 0/.test(tt) && /CustomersA\$ 0/.test(tt) && $$(".ctip tr").length === 2, tt);
unhover();
ok("tooltip hides off the chart", $(".ctip").style.display === "none");

/* who gets what */
var pc = $$("#v-pchips .tipc");
ok("party chips: the two baselines and the third party, ≤ 4 words, full basis on hover (fleet defined, transfer payment, feed-in payer)", pc.length === 3 && pc[0].textContent === "Zenith vs no fleet" && pc[1].textContent === "customers vs self-use" && pc[2].textContent === "other parties: feed-in payer" && pc.every(function(c){ return words(c.textContent) <= 4 && c.tabIndex === 0; }) &&
  /Profit\(Zenith \+ the fleet\) − Profit\(Zenith without the fleet\)/.test(notes(pc[0])) && /1 Jul 2025 – 30 Jun 2026, 365 days/.test(notes(pc[0])) && /business customers' sites, called together as one virtual power plant/.test(notes(pc[0])) && /same battery in normal self-use, not “no battery”/.test(notes(pc[1])) && /sums to zero across parties/.test(notes(pc[1])) &&
  /payer of the feed-in credit \(DEBS\) scheme/.test(notes(pc[2])) && /no other party receives value from the offer/.test(notes(pc[2])) && /Distributed Energy Buyback Scheme/.test(notes(pc[2])), pc.map(function(c){ return c.textContent; }).join(" | "));
var pr = $$("#v-parties .vparty");
ok("three parties: Zenith, Customers, Other parties", pr.length === 3 && pr.map(function(p){ return p.getAttribute("data-p"); }).join() === "z,c,o" && pr.map(function(p){ return p.querySelector("text.vo-n").textContent; }).join("|") === "Zenith|Customers|Other parties");
ok("parties chart text: names and numbers only (whitelist)", offList("#v-parties svg", PTY_OK).length === 0, offList("#v-parties svg", PTY_OK).join(" | "));
ok("Zenith A$ 1,287,528 · A$ 429.18 /customer/yr", /Zenith/.test(svgTxt(pr[0])) && /A\$ 1,287,528 · A\$ 429.18 \/customer\/yr/.test(svgTxt(pr[0])), svgTxt(pr[0]));
ok("Customers A$ 421,360 · A$ 140.45 /customer/yr", /Customers/.test(svgTxt(pr[1])) && /A\$ 421,360 · A\$ 140.45 \/customer\/yr/.test(svgTxt(pr[1])), svgTxt(pr[1]));
ok("Other parties −A$ 16,000 · −A$ 5.33 /customer/yr, in red", /Other parties/.test(svgTxt(pr[2])) && /−A\$ 16,000 · −A\$ 5.33 \/customer\/yr/.test(svgTxt(pr[2])) && /--bad/.test(pr[2].querySelector("rect.vbar").getAttribute("style")) && pr[2].querySelector(".vo-neg"), svgTxt(pr[2]));
var pz = zeroX($("#v-parties svg"));
ok("parties: three separate bars from one zero, not stacked (a loss runs left)", pr.every(function(p){ var b = p.querySelector("rect.vbar"); return Math.abs(+b.getAttribute("x") - pz) < 0.01 || Math.abs(+b.getAttribute("x") + +b.getAttribute("width") - pz) < 0.01; }) && $$("#v-parties rect.vbar").length === 3);
ok("party labels end inside the chart", $$("#v-parties text.vo-v").every(function(t){ var b = t.getBBox(); return b.x + b.width <= +$("#v-parties svg").getAttribute("viewBox").split(" ")[2]; }));
ok("Synergy nowhere in visible text or SVG text (folds closed)", synergyVisible().length === 0, synergyVisible().join(" | "));

/* round-4 pieces are gone */
ok("old elements gone", !$("#v-cmp") && !$("#fv-tiles") && !$("#s-value .scope") && !$("#v-cmp-foot") && !$("#s-value .verdict p") && !$("#v-hero") && !$("#v-verdict") && !$("#fv-checks"));

/* folds */
var folds = $$("#s-value details.fold.sm");
ok("five folds, closed", folds.length === 5 && folds.every(function(f){ return !f.open; }) && folds.map(function(f){ return f.querySelector("summary").textContent; }).join("|") === "Full accounts by party|How the capacity charge (IRCR) saving is calculated|Zenith's own battery|Season-by-season tests|Customer limits", folds.map(function(f){ return f.querySelector("summary").textContent; }).join("|"));
ok("IRCR fold: glossary note on hover", /^Each retailer's share of the cost of keeping enough generation available, set by its customers' demand in the 12 peak half-hours/.test(notes($("#v-f-ircr > summary"))));
folds.forEach(function(f){ f.open = true; });
var lr = $$("#v-ledger tr.x"), tot = $$("#v-ledger tr.tot td").map(function(td){ return td.textContent; });
ok("ledger: 10 items, 4 transfers, net matches the parties", lr.length === 10 && $$("#v-ledger .trt").length === 4 && tot.join("|") === "Net|A$ 1,287,528|A$ 421,360|−A$ 16,000|A$ 1,692,888", tot.join("|"));
ok("ledger: third column named Other parties, not Synergy", /Other parties/.test(txt("#v-ledger tr:first-child")) && !/Synergy/.test(txt("#v-ledger")));
ok("ledger: period with dates, scenario in plain words", txt("#v-ledger-sub") === "Custom offer · replay of contract year 2025-26 (1 Jul 2025 – 30 Jun 2026, 365 days), inside the training period · S2b · assumed 2027 capacity rule · A$, whole fleet", txt("#v-ledger-sub"));
ok("ledger: hold-charge row name, retail row after calls", /Hold-charge offset credits/.test(txt("#v-ledger")) && /Retail bill change after calls/.test(txt("#v-ledger")) && /the retailer earns the margin on that extra energy/.test(txt("#v-ledger")), txt("#v-ledger").slice(0, 300));
var trt = $$("#v-ledger .trt");
ok("ledger: transfer payment tags with a note", trt.every(function(t){ return t.textContent === "TRANSFER PAYMENT" && /sums to zero across parties/.test(notes(t)); }), trt.map(function(t){ return t.textContent; }).join());
ok("ledger: IRCR and DEBS rows carry the glossary notes", /12 peak half-hours/.test(notes($("#v-ledger .v-rn"))) && $$("#v-ledger .v-rn").some(function(e){ return /Distributed Energy Buyback Scheme/.test(notes(e)); }));
ok("ledger: capacity row says Zenith as retailer keeps the whole change, points at the renamed fold, no variable names", /an assumed future rule in which export counts against the peak/.test(txt('#v-ledger tr[data-dt="0"]')) && /Zenith as retailer keeps the whole change/.test(txt('#v-ledger tr[data-dt="0"]')) && /See “How the capacity charge \(IRCR\) saving is calculated” below\./.test(txt('#v-ledger tr[data-dt="0"]')) && !/Seasonal IRCR effect|share s =|keeps 50%/.test(txt("#v-ledger")), txt('#v-ledger tr[data-dt="0"]'));
ok("ledger: energy and retail rows go to Zenith, nothing to other parties but the feed-in credit", /A\$ 0|0/.test($$('#v-ledger tr.x')[1].children[3].textContent) && $$('#v-ledger tr.x')[1].children[1].textContent !== "0" && $$('#v-ledger tr.x').filter(function(r){ return r.children[3].textContent !== "0"; }).length === 1, $$('#v-ledger tr.x').map(function(r){ return r.children[0].textContent.slice(0, 20) + ":" + r.children[3].textContent; }).join(" | "));
click(lr[2]);
ok("ledger row opens its basis, responding customers", lr[2].classList.contains("open") && $('#v-ledger tr[data-dt="2"]').classList.contains("show") && /85 c\/kWh × 9.0 kWh × 2,700 responding customers × 24 calls/.test(txt('#v-ledger tr[data-dt="2"]')), txt('#v-ledger tr[data-dt="2"]'));
click(lr[2]);
ok("ledger row closes again", !lr[2].classList.contains("open") && !$('#v-ledger tr[data-dt="2"]').classList.contains("show"));
ok("IRCR fold: median, MW, price, Zenith's whole part, 12 intervals, per customer", /−?-?1.55 kWh/.test(txt("#v-ircr")) && /216,091.78/.test(txt("#v-ircr")) && /A\$ 1,808,688/.test(txt("#v-ircr")) && /as the customers' retailer it keeps the whole change/.test(txt("#v-ircr")) && !/keeps 50%/.test(txt("#v-ircr")) && /12 peak half-hours/.test(txt("#v-ircr")) && /One of the 4 Hot Season days/.test(notes($("#v-ircr .foot .tipc"))) && $$("#v-ircr details tr").length === 13 && /not guaranteed/.test(txt("#v-ircr")) && /market operator \(AEMO\) ratio/.test(txt("#v-ircr")) && /responding customer, per half-hour/.test(txt("#v-ircr")) && /kWh per half-hour per customer/.test(txt("#v-ircr")), txt("#v-ircr").slice(0, 200));
ok("own battery fold: separate profit first, then alone, fleet, both", /^The site battery's profit is separate and adds to the fleet's; both are Zenith's\./.test(txt("#v-own")) && /A\$ 1,113,250/.test(txt("#v-own")) && /A\$ 1,287,528/.test(txt("#v-own")) && /A\$ 2,400,778/.test(txt("#v-own")) && /training-period average \(1 Apr 2025 – 30 Jun 2026, 456 days\)/.test(txt("#v-own")) && /S2b · assumed 2027 capacity rule/.test(txt("#v-own")), txt("#v-own"));
var hr = $$("#v-hold tbody tr");
ok("holdouts: 3 seasons", hr.map(function(r){ return r.getAttribute("data-season"); }).join() === "2023-24,2024-25,2025-26");
ok("holdouts: only 2025-26 a forward test (the test kind cut from the chart lives here)", hr.map(function(r){ return r.querySelector(".tipc").textContent; }).join() === "tested on an earlier season,tested on an earlier season,forward test" && hr.every(function(r){ return r.querySelector(".tipc").tabIndex === 0; }) &&
  /tuned on later seasons and then tested on this earlier one/.test(notes(hr[0].querySelector(".tipc"))) && /tuned on earlier seasons only and then tested on the next one/.test(notes(hr[2].querySelector(".tipc"))));
ok("holdouts: caption from the glossary, scenario named, both kinds of test as chips", /^Each Hot Season \(Dec–Mar\) left out in turn and tested with a rule tuned on the others · S2b · assumed 2027 capacity rule/.test(txt("#v-hold-sub")) && $$("#v-hold-sub .tipc").map(function(c){ return c.textContent; }).join() === "tested on an earlier season,forward test" && /One of the 4 Hot Season days/.test(notes($("#v-hold thead .tipc"))), txt("#v-hold-sub"));
ok("holdouts: each season dated", /1 Dec 2023 – 31 Mar 2024 · 122 days/.test(hr[0].textContent) && /1 Dec 2024 – 31 Mar 2025 · 121 days/.test(hr[1].textContent) && /1 Dec 2025 – 31 Mar 2026 · 121 days/.test(hr[2].textContent));
ok("holdouts: S2b custom and reference results, peak days known only afterwards", /A\$ 670,000/.test(hr[0].textContent) && /−A\$ 372,000/.test(hr[0].textContent) && hr[0].querySelector("td.neg") && /A\$ 1,287,528/.test(hr[2].textContent) && /A\$ 735,802/.test(hr[2].textContent) && /known only afterwards/.test(txt("#v-hold")), hr[0].textContent);
var cs = $$("#v-cons .ck");
ok("constraints: plain names", cs.map(function(c){ return c.textContent.slice(1); }).join() === "Yearly call cap,Max hours Zenith controls a battery per call,Customer floor kept,Accounts add up", cs.map(function(c){ return c.textContent; }).join());
ok("acronyms spelled out; every visible use has a note (folds open)", acronymProblems().length === 0, acronymProblems().join("; "));
ok("constraints: 4 tiles, all pass, full wording on hover", cs.length === 4 && cs.every(function(c){ return c.classList.contains("ok") && c.tabIndex === 0 && notes(c).length > 20; }) && /45 calls for the custom offer, 30 for reference terms/.test(notes(cs[0])) && /^Hold charge \+ call ≤ 6 h/.test(notes(cs[1])) && /The 20% of each customer battery/.test(notes(cs[2])) && /sums to zero across parties/.test(notes(cs[3])), cs.map(function(c){ return c.textContent; }).join(","));
ok("Synergy nowhere in visible text or SVG text (folds open)", synergyVisible().length === 0, synergyVisible().join(" | "));
ok("Synergy named only as the source of the reference terms, in hover notes", $$("#s-value [data-note]").map(notes).filter(function(n){ return /Synergy/.test(n); }).every(function(n){ return /Synergy Battery Rewards terms/.test(n); }) && /Synergy Battery Rewards terms/.test(notes($("#v-head .v-def"))), $$("#s-value [data-note]").map(notes).filter(function(n){ return /Synergy/.test(n); }).join(" || ").slice(0, 300));
folds.forEach(function(f){ f.open = false; });
ok("no internal jargon (S3, TPA, labels and notes)", jargon() === "", jargon());
ok("vocabulary: no activation, customer reserve, standby, policy C, sweep, in-sample, home or homes (page and tooltips)", oldWords() === "", oldWords());
/* glossary notes reach the page: one distinctive phrase per key */
var allNotes = $$("#s-value [data-note]").map(notes).join(" ");
ok("glossary notes used: S2b, S2a, IRCR, TRANSFER, DEBS, EARLIERSEASON, REFTERMS, VPP", ["export counts against the peak", "export counts as zero demand", "12 peak half-hours", "sums to zero", "Distributed Energy Buyback", "tuned on later seasons", "Not a Zenith tariff", "virtual power plant"].every(function(p){ return allNotes.indexOf(p) >= 0; }),
  ["export counts against the peak", "export counts as zero demand", "12 peak half-hours", "sums to zero", "Distributed Energy Buyback", "tuned on later seasons", "Not a Zenith tariff", "virtual power plant"].filter(function(p){ return allNotes.indexOf(p) < 0; }).join(", "));
ok("per-customer figures: /customer/yr on the charts, per enrolled customer per year in the notes", /\/customer\/yr/.test($("#v-opts svg").textContent) && /\/customer\/yr/.test($("#v-parties svg").textContent) && /per enrolled customer per year/.test(allNotes) && ($("#s-value").textContent.match(/per enrolled customer(?! per year)(?! a year)/g) || []).length === 0);
ok("acronyms spelled out; every visible use has a note (folds closed)", acronymProblems().length === 0, acronymProblems().join("; "));

/* ---- the link out and back ---- */
$("#v-f-own").open = true;
click($('#v-own [data-go="evidence"]'));
ok("own battery link opens Evidence", $("#s-evidence").classList.contains("on"));
goTo("value");
ok("Value again renders the same headline", txt("#v-head") === "+A$551,726 vs reference terms" && $$("#v-opts .vrow").length === 3 && txt("#v-pill") === "Positive in S2b");
goTo("plan");
done();
