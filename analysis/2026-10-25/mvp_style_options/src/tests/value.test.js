/* Value · customer fleet (round 5). Run with src/tests/run.py value. Expected numbers are evalOffer's outputs for the
   saved run R‑0142 (S3: custom 327,184 − reference 149,251; S2b: 1,287,528 − 735,802). The headline is also checked
   against the chart's own bar values, so a headline computed the wrong way round fails even if a number happened to match. */
goTo("value");
function notes(el){ return el ? el.getAttribute("data-note") || "" : ""; }
function svgTxt(el){ return el ? el.textContent.replace(/\s+/g, " ") : ""; }
function barOf(row, s){ return row.querySelector('rect.vbar[data-s="' + s + '"]'); }
function zeroX(svg){ return +svg.querySelector(".v-zero").getAttribute("x1"); }
function chartGain(){ var bz = function(k){ return +barOf($('#v-opts .vrow[data-k="' + k + '"]'), "z").getAttribute("data-v"); }; return bz("custom") - bz("ref"); }
function headNum(){ var t = txt("#v-head .v-n"); return (t.charAt(0) === "−" ? -1 : 1) * +t.replace(/[^\d]/g, ""); }
/* ROUND5.md section 7: no internal project names anywhere in the page, labels and hover notes included */
var JARGON = /Synergy TPA|Peak IRCR|in-sample|not chronological|Ledger by party|Hot Season holdouts/;
function jargon(){ var m = $("#s-value").innerHTML.match(JARGON); return m ? m[0] : ""; }
/* vocabulary decisions (2026-10-27): call not activation, customer floor not customer reserve, hold charge not standby;
   no research labels. Checked in the page (labels and data-note) and in every chart tooltip. */
var OLDWORDS = /activation|customer reserve|standby|policy C|sweep|in-sample/i;
function tipText(i){ var h = $('#v-opts .hit[data-i="' + i + '"]'); h.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:400, clientY:400})); var t = $(".ctip") ? $(".ctip").textContent : ""; document.body.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:5, clientY:5})); return t; }
function oldWords(){ var t = $("#s-value").innerHTML + " " + [0, 1, 2, 3].map(tipText).join(" "), m = t.match(OLDWORDS); return m ? m[0] : ""; }
/* visible text nodes of the page in reading order (SVG included, closed folds skipped) */
function visText(){ var out = [], w = document.createTreeWalker($("#s-value"), NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { var n = w.currentNode, el = n.parentElement; if (!n.textContent.trim() || el.closest("details:not([open])") && !el.closest("summary") || !el.getClientRects().length) continue; out.push(n); }
  return out; }
/* every acronym is spelled out somewhere on the page (text or hover note), and every visible use either spells it out
   or sits inside an element with a hover note */
var ACR = {IRCR:/capacity charge \(IRCR\)|reserve capacity requirement \(IRCR\)/i, TPA:/access through Synergy \(TPA\): A third-party access arrangement/, AEMO:/market operator \(AEMO\)/, DEBS:/The Distributed Energy Buyback Scheme/, WEM:/wholesale electricity market \(WEM\)/};
function acronymProblems(){ var bad = [], all = $("#s-value").textContent + " " + $$("#s-value [data-note]").map(function(e){ return e.getAttribute("data-note"); }).join(" ");
  Object.keys(ACR).forEach(function(a){ var re = new RegExp("\\b" + a + "\\b");
    if (re.test(all) && !ACR[a].test(all)) bad.push(a + ": never spelled out");
    visText().forEach(function(n){ var el = n.parentElement; if (re.test(n.textContent) && !ACR[a].test(n.textContent) && !el.closest("[data-note]")) bad.push(a + " bare in “" + n.textContent.trim().slice(0, 40) + "”"); }); });
  return bad; }

ok("page opens alone", $("#s-value").classList.contains("on") && $$(".screen.on").length === 1);
ok("header: title, step chip, context ≤ 12 words", txt("#ph-title") === "Value by party" && txt("#ph-chips") === "2 · Improve" && txt("#ph-ctx") === "Customers' home batteries · custom offer vs reference terms" && txt("#ph-ctx").split(/\s+/).length <= 12, txt("#ph-ctx"));

/* ---- default: S3 ---- */
ok("S3 selected", $('[data-vscen="S3"]').classList.contains("on") && !$('[data-vscen="S2b"]').classList.contains("on"));
var sb = [$('[data-vscen="S3"]'), $('[data-vscen="S2b"]')];
ok("scenario buttons in plain words", sb[0].textContent === "S3 · via Synergy (hypothetical)" && sb[1].textContent === "S2b · assumed 2027 capacity rule", sb.map(function(b){ return b.textContent; }).join(" | "));
ok("scenario buttons carry the glossary notes (S3 with the TPA note), no duplicate title", /keeps an assumed 50% share; the energy value stays with Synergy\. Hypothetical: no such arrangement exists today\. access through Synergy \(TPA\): A third-party access arrangement/.test(notes(sb[0])) &&
  /an assumed future rule in which export counts against the peak\. Not current revenue/.test(notes(sb[1])) && sb.every(function(b){ return !b.hasAttribute("title"); }), notes(sb[0]) + " | " + notes(sb[1]));
var nos = $("#v-noscen");
ok("toolbar chip: today's rules (S1, S2a): no business case, both glossary notes on hover", txt("#v-noscen") === "today's rules (S1, S2a): no business case" && nos.tabIndex === 0 &&
  /^Zenith only sells the fleet's extra export at the wholesale price; no capacity value\. No call pays for itself at 09:00/.test(notes(nos)) && /a home's export counts as zero demand, not negative\. Normal self-use already removes the peak import/.test(notes(nos)), notes(nos));
ok("Synergy named in the scenario area with its note", txt("#v-syn") === "Synergy" && /incumbent retailer/.test(notes($("#v-syn .tipc"))));
ok("headline +A$177,933", txt("#v-head") === "+A$177,933 extra Zenith profit vs reference terms", txt("#v-head"));
ok("headline = custom − reference, read from the chart", headNum() === chartGain() && chartGain() === 177933, headNum() + " vs " + chartGain());
ok("headline number green", $("#v-head .v-n").classList.contains("pos") && getComputedStyle($("#v-head .v-n")).color === getComputedStyle($("#v-pill b")).color);
ok("verdict pill POSITIVE IN S3", $("#v-pill").classList.contains("ok") && txt("#v-pill") === "Positive in S3" && getComputedStyle($("#v-pill b")).textTransform === "uppercase", txt("#v-pill"));
ok("verdict pill states the rule on hover", /no Hot Season losing money/.test(notes($("#v-pill b"))) && $("#v-pill b").tabIndex === 0);
var ch = $$("#v-chips .tipc");
ok("four chips", ch.length === 4, ch.map(function(c){ return c.textContent; }).join(" | "));
ok("chip 1: replay of contract year 2025-26 with dates, contract-year note", ch[0].textContent === "replay of contract year 2025-26 (1 Jul 2025 – 30 Jun 2026)" && /It lies inside the training period, so its results are not a blind test/.test(notes(ch[0])) && /365 days/.test(notes(ch[0])), notes(ch[0]));
ok("chip 2: S3 · via Synergy (hypothetical), hypothesis style, S3 + TPA notes", ch[1].textContent === "S3 · via Synergy (hypothetical)" && ch[1].classList.contains("hyp") && /keeps an assumed 50% share/.test(notes(ch[1])) && /The 50% share is an assumption, not a contract/.test(notes(ch[1])));
ok("chip 3: blind test: Hot Season 2026-27 (Dec–Mar)", ch[2].textContent === "blind test: Hot Season 2026-27 (Dec–Mar)" && /16–22 Oct 2026, 7 days: never looked at before the method was locked/.test(notes(ch[2])) && /first chance: Hot Season 2026-27, 1 Dec 2026 – 31 Mar 2027 \(121 days\)/.test(notes(ch[2])) && /when system demand peaks/.test(notes(ch[2])), notes(ch[2]));
ok("chip 4: saved run R‑0142, placeholder numbers on hover", ch[3].textContent === "saved run R‑0142" && /placeholders from the design prototype/.test(notes(ch[3])));
var hs = visText().filter(function(n){ return /Hot Season/.test(n.textContent); })[0];
ok("first visible Hot Season carries its months and a note", !!hs && /Hot Season 2026-27 \(Dec–Mar\)/.test(hs.textContent) && /when system demand peaks/.test(notes(hs.parentElement.closest("[data-note]"))), hs && hs.textContent);
ok("chips focusable, open right", ch.every(function(c){ return c.tabIndex === 0 && c.classList.contains("r"); }));

/* four status items under the headline */
var st = $$("#v-status .ck");
ok("four status items", st.length === 4, st.map(function(c){ return c.textContent; }).join(" | "));
ok("status 1 ✓ customers better off", st[0].classList.contains("ok") && st[0].querySelector(".ci.ok").textContent === "✓" && /Customers better off/.test(st[0].textContent) && /normal self-use: \+A\$ 140.45 per enrolled home per year/.test(notes(st[0])), notes(st[0]));
ok("status 2 ✓ customer limits held (cap, control time, reserve)", st[1].classList.contains("ok") && st[1].textContent === "✓Customer limits held" && /yearly call cap/.test(notes(st[1])) && /max hours Zenith controls a battery per call \(hold charge \+ call ≤ 6 h\)/.test(notes(st[1])) && /customer floor/.test(notes(st[1])) && /1 Jul 2025 – 30 Jun 2026, 365 days/.test(notes(st[1])), st[1].textContent);
ok("status 3 ! Zenith keeps 50% of the capacity value · assumption", st[2].classList.contains("w") && st[2].querySelector(".ci.w").textContent === "!" && st[2].textContent === "!Zenith keeps 50% of the capacity value · assumption" && /50% share is an assumption/.test(notes(st[2])) && !/\bs = |share s/.test(st[2].textContent), st[2].textContent);
ok("status 4 – not yet tested blind: blind-test, first-chance and Hot Season notes", st[3].classList.contains("u") && st[3].querySelector(".ci.u").textContent === "–" && st[3].textContent === "–Not yet tested blind" && /never looked at before the method was locked/.test(notes(st[3])) && /first chance: Hot Season 2026-27, 1 Dec 2026 – 31 Mar 2027/.test(notes(st[3])) && /1 Dec – 31 Mar in the WA market/.test(notes(st[3])), st[3].textContent);
ok("four status items on one row", st.every(function(c){ return Math.abs(c.getBoundingClientRect().top - st[0].getBoundingClientRect().top) < 1; }), st.map(function(c){ return Math.round(c.getBoundingClientRect().top); }).join());
ok("status items focusable with notes", st.every(function(c){ return c.tabIndex === 0 && notes(c).length > 30; }));

/* the option chart */
var svg = $("#v-opts svg");
ok("chart: one svg, role img, labelled", $$("#v-opts svg").length === 1 && svg.getAttribute("role") === "img" && /reference terms and the custom offer/.test(svg.getAttribute("aria-label")));
var rows = $$("#v-opts .vrow");
ok("chart: three rows No calls / Reference / Custom", rows.map(function(r){ return r.getAttribute("data-k"); }).join() === "none,ref,custom");
var r0 = svgTxt(rows[0]), r1 = svgTxt(rows[1]), r2 = svgTxt(rows[2]);
ok("row No calls: baseline at A$ 0", /No calls/.test(r0) && /A\$ 0/.test(r0) && /Baseline/.test(r0) && !rows[0].querySelector("rect.vbar"), r0);
ok("row Reference: terms, calls, cap date, peak days", /Reference terms/.test(r1) && /70 c\/kWh · 30 calls/.test(r1) && /30 of 30 calls used · cap used up by 20 Feb/.test(r1) && /3 of 4 official peak days covered · known only afterwards/.test(r1), r1);
ok("row Reference: A$ 149,251 and worst season −A$ 406,000, No business case", /Zenith extra profit A\$ 149,251 · contract year 2025-26/.test(r1) && /−A\$ 406,000 · worst season \(2023-24\) · tested on an earlier season/.test(r1) && /No business case/.test(r1) && rows[1].querySelector(".vo-pill.bad"), r1);
ok("row Custom: terms, calls, A$ 327,184, worst A$ 40,000, Positive in S3", /85 c\/kWh · 45 calls/.test(r2) && /24 of 45 calls used/.test(r2) && /4 of 4 official peak days covered · known only afterwards/.test(r2) && /Zenith extra profit A\$ 327,184 · contract year 2025-26/.test(r2) && /A\$ 40,000 · worst season \(2023-24\) · tested on an earlier season/.test(r2) && /Positive in S3/.test(r2) && rows[2].querySelector(".vo-pill.ok"), r2);
ok("rows carry customer net per enrolled home per year", /customers \+A\$ 139.65 per enrolled home per year/.test(r1) && /customers \+A\$ 140.45 per enrolled home per year/.test(r2), r1);
var zx = zeroX(svg), wb = barOf(rows[1], "worst"), cz = barOf(rows[2], "z");
ok("reference worst season drawn left of zero, in red", +wb.getAttribute("x") + +wb.getAttribute("width") <= zx + 0.01 && +wb.getAttribute("width") > 100 && /var\(--bad\)/.test(wb.getAttribute("style")), wb.getAttribute("x") + " " + wb.getAttribute("width") + " zero " + zx);
ok("positive bars start at zero and run right", [barOf(rows[1], "z"), cz, barOf(rows[2], "worst")].every(function(b){ return Math.abs(+b.getAttribute("x") - zx) < 0.01 && !/--bad/.test(b.getAttribute("style")); }));
ok("one scale: bar lengths in proportion", Math.abs(+wb.getAttribute("width") / +cz.getAttribute("width") - 406000 / 327184) < 0.005);
var minBar = Math.min.apply(null, $$("#v-opts rect.vbar").map(function(b){ return +b.getAttribute("x"); }));
var lab = $$('#v-opts .vrow text').filter(function(t){ return +t.getAttribute("x") === 12; }).map(function(t){ var b = t.getBBox(); return b.x + b.width; });
ok("row labels end before the bars start", lab.length >= 9 && Math.max.apply(null, lab) < minBar - 8, Math.round(Math.max.apply(null, lab)) + " vs " + Math.round(minBar));
ok("all bars inside the plot", $$("#v-opts rect.vbar").every(function(b){ return +b.getAttribute("x") >= 290 && +b.getAttribute("x") + +b.getAttribute("width") <= +svg.getAttribute("viewBox").split(" ")[2] - 150; }));
ok("no legend, no hatching", !$("#s-value .lgd") && !$("#s-value .legend") && !$("#s-value pattern") && !/url\(#/.test($("#s-value").innerHTML));
ok("axis title: extra Zenith profit vs Zenith without the fleet", /Extra Zenith profit, A\$ · vs Zenith without the fleet/.test(svgTxt(svg)));
ok("holdout basis written on the chart", /Worst season: each Hot Season \(2023-24, 2024-25, 2025-26\) left out in turn and tested with a rule tuned on the others; only 2025-26 is a forward test, tuned on earlier seasons only\./.test(svgTxt(svg)), svgTxt(svg).slice(-200));
var capR = $$("#v-opts text.vo-t").pop().getBBox();
ok("holdout caption fits inside the chart", capR.x + capR.width <= +svg.getAttribute("viewBox").split(" ")[2], Math.round(capR.x + capR.width));
/* hover a row: the full numbers */
$('#v-opts .hit[data-i="1"]').dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:400, clientY:400}));
var tip = $(".ctip"), tt = tip ? tip.textContent : "";
ok("hover reference row: full numbers", tip && tip.style.display === "block" && /Reference terms · 70 c\/kWh · 30 calls/.test(tt) && /Customer net per enrolled home per year\s*A\$ 139.65/.test(tt) && /−A\$ 406,000/.test(tt) && /Not a Zenith tariff/.test(tt) && /Enrolled homes\s*2,000/.test(tt), tt);
ok("hover reference row: periods with dates, how the worst season was tested", /Contract year 2025-26: 1 Jul 2025 – 30 Jun 2026, 365 days\./.test(tt) && /Worst season: Hot Season 2023-24, 1 Dec 2023 – 31 Mar 2024, 122 days, tested on an earlier season: The rule was tuned on later seasons/.test(tt) && /Official peak days: One of the 4 Hot Season days/.test(tt) && /Zenith · worst season \(2023-24\)\s*−A\$ 406,000/.test(tt) && /Official peak days covered · known only afterwards/.test(tt), tt);
$('#v-opts .hit[data-i="2"]').dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:400, clientY:500}));
tt = $(".ctip").textContent;
$('#v-opts .hit[data-i="3"]').dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:400, clientY:500}));
tt = $(".ctip").textContent;
ok("hover the basis line: both kinds of test explained, each season dated", /Each Hot Season \(1 Dec – 31 Mar\) is left out in turn/.test(tt) && /Forward test: The rule was tuned on earlier seasons only/.test(tt) && /Tested on an earlier season: The rule was tuned on later seasons/.test(tt) &&
  /2023-24 · tested on an earlier season\s*122 days/.test(tt) && /2025-26 · forward test\s*121 days/.test(tt), tt);
$('#v-opts .hit[data-i="2"]').dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:400, clientY:500}));
tt = $(".ctip").textContent;
ok("hover custom row: custom-offer note, customers, Synergy, verdict", /Custom offer · 85 c\/kWh · 45 calls/.test(tt) && /A suggestion to test in a pilot, not a contract/.test(tt) && /Customers · vs normal self-use\s*A\$ 421,360/.test(tt) && /Synergy\s*A\$ 944,344/.test(tt) && /Verdict\s*Positive in S3/.test(tt), tt);
document.body.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:5, clientY:5}));
ok("tooltip hides off the chart", $(".ctip").style.display === "none");

/* who gets what */
var pc = $$("#v-pchips .tipc");
ok("party chips: the fleet defined, then both baselines", pc.length === 3 && pc[0].textContent === "the fleet = customers' home batteries" && /virtual power plant/.test(notes(pc[0])) && pc[1].textContent === "Zenith: vs Zenith without the fleet" && pc[2].textContent === "Customers: vs their normal self-use" && /Profit\(Zenith \+ the fleet\) − Profit\(Zenith without the fleet\)/.test(notes(pc[1])) && /1 Jul 2025 – 30 Jun 2026, 365 days/.test(notes(pc[1])) && /same battery in normal self-use, not “no battery”/.test(notes(pc[2])) && /sums to zero across parties/.test(notes(pc[2])), pc.map(function(c){ return c.textContent; }).join(" | "));
ok("Synergy chip beside the parties: what Synergy keeps under S3, S3 note", txt("#v-pnote") === "Synergy keeps the energy value and 50% of the capacity value under S3 · assumption" && /Hypothetical: no such arrangement exists today/.test(notes($("#v-pnote .tipc"))), txt("#v-pnote"));
var pr = $$("#v-parties .vparty");
ok("three parties", pr.length === 3 && pr.map(function(p){ return p.getAttribute("data-p"); }).join() === "z,c,o");
ok("Zenith A$ 327,184 · A$ 109.06 per enrolled home", /Zenith/.test(svgTxt(pr[0])) && /A\$ 327,184 · A\$ 109.06 per enrolled home per year/.test(svgTxt(pr[0])), svgTxt(pr[0]));
ok("Customers A$ 421,360 · A$ 140.45 per enrolled home", /Customers/.test(svgTxt(pr[1])) && /A\$ 421,360 · A\$ 140.45 per enrolled home per year/.test(svgTxt(pr[1])), svgTxt(pr[1]));
ok("Synergy A$ 944,344 · A$ 314.78 per enrolled home", /Synergy/.test(svgTxt(pr[2])) && /A\$ 944,344 · A\$ 314.78 per enrolled home per year/.test(svgTxt(pr[2])), svgTxt(pr[2]));
var pz = zeroX($("#v-parties svg"));
ok("parties: three separate bars from one zero, not stacked", pr.every(function(p){ var b = p.querySelector("rect.vbar"); return Math.abs(+b.getAttribute("x") - pz) < 0.01; }) && $$("#v-parties rect.vbar").length === 3);

/* round-4 pieces are gone */
ok("old elements gone", !$("#v-cmp") && !$("#fv-tiles") && !$("#s-value .scope") && !$("#v-cmp-foot") && !$("#s-value .verdict p") && !$("#v-hero") && !$("#v-verdict") && !$("#fv-checks"));

/* folds */
var folds = $$("#s-value details.fold.sm");
ok("five folds, closed", folds.length === 5 && folds.every(function(f){ return !f.open; }) && folds.map(function(f){ return f.querySelector("summary").textContent; }).join("|") === "Full accounts by party|How the capacity charge (IRCR) saving is calculated|Zenith's own battery|Season-by-season tests|Customer limits", folds.map(function(f){ return f.querySelector("summary").textContent; }).join("|"));
ok("IRCR fold: glossary note on hover", /^Each retailer's share of the cost of keeping enough generation available, set by its customers' demand in the 12 peak half-hours/.test(notes($("#v-f-ircr > summary"))));
folds.forEach(function(f){ f.open = true; });
var lr = $$("#v-ledger tr.x"), tot = $$("#v-ledger tr.tot td").map(function(td){ return td.textContent; });
ok("ledger: 10 items, 4 transfers, net matches the parties", lr.length === 10 && $$("#v-ledger .trt").length === 4 && tot.join("|") === "Net|A$ 327,184|A$ 421,360|A$ 944,344|A$ 1,692,888", tot.join("|"));
ok("ledger: party column named Synergy", /Synergy/.test(txt("#v-ledger tr:first-child")));
ok("ledger: period with dates, scenario in plain words", txt("#v-ledger-sub") === "Custom offer · replay of contract year 2025-26 (1 Jul 2025 – 30 Jun 2026, 365 days), inside the training period · S3 · via Synergy (hypothetical) · A$, whole fleet", txt("#v-ledger-sub"));
ok("ledger: hold-charge row name, retail row after calls", /Hold-charge offset credits/.test(txt("#v-ledger")) && /Retail bill change after calls/.test(txt("#v-ledger")) && /the retailer earns the margin on that extra energy/.test(txt("#v-ledger")), txt("#v-ledger").slice(0, 300));
var trt = $$("#v-ledger .trt");
ok("ledger: transfer payment tags with a note", trt.every(function(t){ return t.textContent === "TRANSFER PAYMENT" && /sums to zero across parties/.test(notes(t)); }), trt.map(function(t){ return t.textContent; }).join());
ok("ledger: IRCR and DEBS rows carry the glossary notes", /12 peak half-hours/.test(notes($("#v-ledger .v-rn"))) && $$("#v-ledger .v-rn").some(function(e){ return /Distributed Energy Buyback Scheme/.test(notes(e)); }));
ok("ledger: capacity row points at the renamed fold, no variable names", /Zenith keeps 50% of the capacity value \(assumption\)/.test(txt('#v-ledger tr[data-dt="0"]')) && /See “How the capacity charge \(IRCR\) saving is calculated” below\./.test(txt('#v-ledger tr[data-dt="0"]')) && !/Seasonal IRCR effect|share s =/.test(txt("#v-ledger")), txt('#v-ledger tr[data-dt="0"]'));
click(lr[2]);
ok("ledger row opens its basis", lr[2].classList.contains("open") && $('#v-ledger tr[data-dt="2"]').classList.contains("show") && /85 c\/kWh × 9.0 kWh × 2,700 responding homes × 24 calls/.test(txt('#v-ledger tr[data-dt="2"]')));
click(lr[2]);
ok("ledger row closes again", !lr[2].classList.contains("open") && !$('#v-ledger tr[data-dt="2"]').classList.contains("show"));
ok("IRCR fold: median, MW, price, Zenith's part, 12 intervals", /−?-?1.55 kWh/.test(txt("#v-ircr")) && /216,091.78/.test(txt("#v-ircr")) && /A\$ 1,808,688/.test(txt("#v-ircr")) && /Zenith keeps 50%, assumption/.test(txt("#v-ircr")) && /12 peak half-hours/.test(txt("#v-ircr")) && /One of the 4 Hot Season days/.test(notes($("#v-ircr .foot .tipc"))) && /A\$ 904,344/.test(txt("#v-ircr")) && $$("#v-ircr details tr").length === 13 && /not guaranteed/.test(txt("#v-ircr")) && /market operator \(AEMO\) ratio/.test(txt("#v-ircr")), txt("#v-ircr").slice(0, 200));
ok("own battery fold: separate profit first, then alone, fleet, both", /^The site battery's profit is separate and adds to the fleet's; both are Zenith's\./.test(txt("#v-own")) && /A\$ 1,113,250/.test(txt("#v-own")) && /A\$ 327,184/.test(txt("#v-own")) && /A\$ 1,440,434/.test(txt("#v-own")) && /training-period average \(1 Apr 2025 – 30 Jun 2026, 456 days\)/.test(txt("#v-own")), txt("#v-own"));
var hr = $$("#v-hold tbody tr");
ok("holdouts: 3 seasons", hr.map(function(r){ return r.getAttribute("data-season"); }).join() === "2023-24,2024-25,2025-26");
ok("holdouts: only 2025-26 a forward test", hr.map(function(r){ return r.querySelector(".tipc").textContent; }).join() === "tested on an earlier season,tested on an earlier season,forward test" && hr.every(function(r){ return r.querySelector(".tipc").tabIndex === 0; }) &&
  /tuned on later seasons and then tested on this earlier one/.test(notes(hr[0].querySelector(".tipc"))) && /tuned on earlier seasons only and then tested on the next one/.test(notes(hr[2].querySelector(".tipc"))));
ok("holdouts: caption from the glossary, both kinds of test as chips", /^Each Hot Season \(Dec–Mar\) left out in turn and tested with a rule tuned on the others/.test(txt("#v-hold-sub")) && $$("#v-hold-sub .tipc").map(function(c){ return c.textContent; }).join() === "tested on an earlier season,forward test" && /One of the 4 Hot Season days/.test(notes($("#v-hold thead .tipc"))), txt("#v-hold-sub"));
ok("holdouts: each season dated", /1 Dec 2023 – 31 Mar 2024 · 122 days/.test(hr[0].textContent) && /1 Dec 2024 – 31 Mar 2025 · 121 days/.test(hr[1].textContent) && /1 Dec 2025 – 31 Mar 2026 · 121 days/.test(hr[2].textContent));
ok("holdouts: custom and reference results", /A\$ 40,000/.test(hr[0].textContent) && /−A\$ 406,000/.test(hr[0].textContent) && hr[0].querySelector("td.neg") && /A\$ 327,184/.test(hr[2].textContent) && /A\$ 149,251/.test(hr[2].textContent), hr[0].textContent);
var cs = $$("#v-cons .ck");
ok("constraints: plain names", cs.map(function(c){ return c.textContent.slice(1); }).join() === "Yearly call cap,Max hours Zenith controls a battery per call,Customer floor kept,Accounts add up", cs.map(function(c){ return c.textContent; }).join());
ok("acronyms spelled out; every visible use has a note (folds open)", acronymProblems().length === 0, acronymProblems().join("; "));
ok("constraints: 4 tiles, all pass, full wording on hover", cs.length === 4 && cs.every(function(c){ return c.classList.contains("ok") && c.tabIndex === 0 && notes(c).length > 20; }) && /45 calls for the custom offer, 30 for reference terms/.test(notes(cs[0])) && /^Hold charge \+ call ≤ 6 h/.test(notes(cs[1])) && /The 20% of each home battery/.test(notes(cs[2])) && /sums to zero across parties/.test(notes(cs[3])), cs.map(function(c){ return c.textContent; }).join(","));
folds.forEach(function(f){ f.open = false; });
ok("no internal jargon (S3, labels and notes)", jargon() === "", jargon());
ok("vocabulary: no activation, customer reserve, standby, policy C, sweep or in-sample (S3, page and tooltips)", oldWords() === "", oldWords());
/* glossary notes reach the page: one distinctive phrase per key */
var allNotes = $$("#s-value [data-note]").map(notes).join(" ");
ok("glossary notes used: S3, IRCR, TRANSFER, DEBS, EARLIERSEASON", ["assumed 50% share", "12 peak half-hours", "sums to zero", "Distributed Energy Buyback", "tuned on later seasons"].every(function(p){ return allNotes.indexOf(p) >= 0; }),
  ["assumed 50% share", "12 peak half-hours", "sums to zero", "Distributed Energy Buyback", "tuned on later seasons"].filter(function(p){ return allNotes.indexOf(p) < 0; }).join(", "));
ok("every per-home figure says per year", ($("#s-value").textContent.match(/per enrolled home(?! per year)(?! a year)/g) || []).length === 0 && /per enrolled home per year/.test($("#s-value").textContent), ($("#s-value").textContent.match(/.{30}per enrolled home(?! per year).{10}/g) || []).join(" | "));
ok("acronyms spelled out; every visible use has a note (folds closed)", acronymProblems().length === 0, acronymProblems().join("; "));

/* ---- switch to S2b ---- */
click($('[data-vscen="S2b"]'));
ok("S2b selected", $('[data-vscen="S2b"]').classList.contains("on") && !$('[data-vscen="S3"]').classList.contains("on") && $("#s-value").classList.contains("on"));
ok("S2b headline +A$551,726", txt("#v-head") === "+A$551,726 extra Zenith profit vs reference terms", txt("#v-head"));
ok("S2b headline = custom − reference, read from the chart", headNum() === chartGain() && chartGain() === 551726, headNum() + " vs " + chartGain());
ok("S2b pill POSITIVE IN S2B", $("#v-pill").classList.contains("ok") && txt("#v-pill") === "Positive in S2b");
ch = $$("#v-chips .tipc");
ok("S2b chip: S2b · assumed 2027 capacity rule, future style", ch.length === 4 && ch[1].textContent === "S2b · assumed 2027 capacity rule" && ch[1].classList.contains("fut") && !ch[1].classList.contains("hyp") && /Not current revenue: the rule text is not yet confirmed/.test(notes(ch[1])), ch[1] && ch[1].textContent);
st = $$("#v-status .ck");
ok("S2b status 3: ! assumed future rule", st.length === 4 && st[2].classList.contains("w") && /Assumed future rule/.test(st[2].textContent) && /export counts against the peak/.test(notes(st[2])) && /not yet confirmed/.test(notes(st[2])));
ok("S2b pill note: assumed capacity rule in plain words", /Depends on an assumed future rule for the capacity charge \(IRCR\) from 2027; not current revenue/.test(notes($("#v-pill b"))), notes($("#v-pill b")));
ok("no internal jargon (S2b)", jargon() === "", jargon());
ok("S2b: no Synergy chip beside the parties", txt("#v-pnote") === "");
rows = $$("#v-opts .vrow"); r1 = svgTxt(rows[1]); r2 = svgTxt(rows[2]);
ok("S2b chart: reference A$ 735,802, worst −A$ 372,000, No business case", /A\$ 735,802/.test(r1) && /−A\$ 372,000/.test(r1) && /No business case/.test(r1), r1);
ok("S2b chart: custom A$ 1,287,528, worst A$ 670,000, Positive in S2b", /A\$ 1,287,528/.test(r2) && /A\$ 670,000/.test(r2) && /Positive in S2b/.test(r2), r2);
svg = $("#v-opts svg"); wb = barOf(rows[1], "worst");
ok("S2b: reference loss still left of zero", +wb.getAttribute("x") + +wb.getAttribute("width") <= zeroX(svg) + 0.01 && /--bad/.test(wb.getAttribute("style")));
pr = $$("#v-parties .vparty");
ok("S2b parties: Zenith A$ 1,287,528, Other parties −A$ 16,000 in red", /A\$ 1,287,528/.test(svgTxt(pr[0])) && /Other parties/.test(svgTxt(pr[2])) && /−A\$ 16,000 · −A\$ 5.33 per enrolled home/.test(svgTxt(pr[2])) && /--bad/.test(pr[2].querySelector("rect.vbar").getAttribute("style")) && pr[2].querySelector(".vo-neg"), svgTxt(pr[2]));
folds[0].open = true;
ok("S2b ledger re-rendered: Other parties column, Zenith net", /Other parties/.test(txt("#v-ledger tr:first-child")) && /A\$ 1,287,528/.test(txt("#v-ledger tr.tot")));
lr = $$("#v-ledger tr.x"); click(lr[0]);
ok("S2b ledger rows still open", lr[0].classList.contains("open") && /an assumed future rule in which export counts against the peak/.test(txt('#v-ledger tr[data-dt="0"]')));
folds[0].open = false;
ok("S2b holdouts follow the scenario", /A\$ 670,000/.test(txt("#v-hold")) && /S2b · assumed 2027 capacity rule/.test(txt("#v-hold-sub")), txt("#v-hold-sub"));
folds.forEach(function(f){ f.open = true; });
ok("S2b: no internal jargon and acronyms spelled out, folds open", jargon() === "" && acronymProblems().length === 0, jargon() + " " + acronymProblems().join("; "));
ok("S2b vocabulary: no old words (page and tooltips)", oldWords() === "", oldWords());
folds.forEach(function(f){ f.open = false; });

/* ---- back to S3, then the link out ---- */
click($('[data-vscen="S3"]'));
ok("back to S3: headline restored", txt("#v-head") === "+A$177,933 extra Zenith profit vs reference terms" && txt("#v-pill") === "Positive in S3");
$("#v-f-own").open = true;
click($('#v-own [data-go="evidence"]'));
ok("own battery link opens Evidence", $("#s-evidence").classList.contains("on"));
goTo("value");
ok("Value again keeps S3 and renders", $('[data-vscen="S3"]').classList.contains("on") && $$("#v-opts .vrow").length === 3);
goTo("plan");
done();
