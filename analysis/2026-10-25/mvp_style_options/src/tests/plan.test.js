/* Today's plan (round 7: the model's own output for 2026-02-02, analysis/2026-10-26/ui_redesign/data/site_days.json).
   Expected numbers were computed from that export independently of the page. Run with src/tests/run.py. */
goTo("plan");
function S_rev(){ return $('[data-po="outcome"]').classList.contains("on"); }
ok("status: executable, 12 pass and 2 not checked", $("#plan-status").classList.contains("ok") && /Plan executable/.test(txt("#plan-status")) && /12 checks pass · 2 not checked/.test(txt("#plan-status")), txt("#plan-status"));
ok("headline: the model's main action", txt("#plan-head") === "Discharge 9.2 MW · IRCR peak · 17:00–20:00", txt("#plan-head"));
ok("expected value A$ 5,454 (the engine's own value at 09:00)", txt("#plan-val .dv") === "A$ 5,454", txt("#plan-val .dv"));
ok("value chips: forecast, stored energy, scheduled only", /forecast made at 09:00/.test(txt("#plan-val")) && /incl\. A\$ 1,799 stored/.test(txt("#plan-val")) && /scheduled actions only/.test(txt("#plan-val")), txt("#plan-val"));
ok("header context: the real trading day", txt("#ph-ctx") === "Mon 2 Feb 2026 · trading day 08:00–08:00 AWST · plan 09:00", txt("#ph-ctx"));
ok("header chip says model output, with the source on hover", /model output/.test(txt("#ph-chips")) && /site_days\.json/.test($$("#ph-chips [data-note]").map(function(c){ return c.getAttribute("data-note"); }).join(" ")));
ok("banner: plan is model output, other pages illustrative", /model's own output/.test(txt(".proto")) && /illustrative/.test(txt(".proto")));
var svgs = $$("#sch-chart svg");
ok("two charts on one axis", svgs.length === 2 && svgs[0].getAttribute("data-tip") === "day" && svgs[1].getAttribute("data-tip") === "soc" && svgs.every(function(s){ return s.getAttribute("data-xg") === "day"; }), svgs.length);
ok("48 hit columns per chart", svgs.every(function(s){ return s.querySelectorAll(".hit").length === 48; }));
ok("same x scale in both charts", svgs[0].getAttribute("data-iw") === svgs[1].getAttribute("data-iw") && svgs[0].getAttribute("viewBox").split(" ")[2] === svgs[1].getAttribute("viewBox").split(" ")[2]);
var lanesTxt = Array.prototype.map.call(svgs[0].querySelectorAll("text"), function(t){ return t.textContent; }).join(" | ");
["Plan", "Sell more if", "Grid services", "sold A$ 5,720", "+10 MW", "+5.8 MW", "earns A$ 1,430", "Cut import 0.2 MW", "Discharge 9.2 MW", "Hold", "peak A$143", "low A$78", "trigger A$159", "A$900", "Price, A$/MWh", "PLAN MADE 09:00"].forEach(function(w){ ok("lane label: " + w, lanesTxt.indexOf(w) >= 0); });
ok("no captions on the day chart: only lane names, axis, actions, mark", (function(){ var t = Array.prototype.map.call(svgs[0].querySelectorAll("text"), function(e){ return e.lastChild.textContent; }).filter(function(x){ return !/^(\d+|\d\d:\d\d|Plan|Sell more if|Grid services|Price, A\$\/MWh|PLAN MADE 09:00|Hold|Cut import [\d.]+ MW|Discharge [\d.]+ MW|Charge [\d.]+ MW|[\d.]+ MW|peak A\$\d+|low A\$\d+|trigger A\$\d+|\+[\d.]+ MW|A\$[\d,]+|sold A\$ [\d,]+|earns A\$ [\d,]+)$/.test(x); }); return t.length === 0; })(), Array.prototype.map.call(svgs[0].querySelectorAll("text"), function(e){ return e.lastChild.textContent; }).join(" | "));
ok("sold half-hours filled solid in the offer lane", svgs[0].querySelectorAll("rect.osold").length >= 1);
ok("trigger: one amber stepped line from 09:00 to the end of the day", (function(){ var p = $$("#sch-chart svg path.othr"), h2 = svgs[0].querySelector('.hit[data-i="2"]'), h47 = svgs[0].querySelector('.hit[data-i="47"]'); if (p.length !== 1) return false; var b = p[0].getBBox(); return /--cond/.test(p[0].getAttribute("style")) && Math.abs(b.x - +h2.getAttribute("x")) < 1 && Math.abs(b.x + b.width - (+h47.getAttribute("x") + +h47.getAttribute("width"))) < 1; })(), $$("#sch-chart svg path.othr").length);
function soldAt(i){ return !!svgs[0].querySelector('rect.osold[x="' + svgs[0].querySelector('.hit[data-i="' + i + '"]').getAttribute("x") + '"]'); }
ok("sold exactly 17:30–20:00, the five half-hours the replay cleared", [19, 20, 21, 22, 23].every(soldAt) && $$("#sch-chart svg rect.osold").length === 5);
ok("sold columns are square, height by MW and at least 4 px", !svgs[0].querySelector('rect.osold[rx]') && $$("#sch-chart svg rect.osold").every(function(r){ var h = +r.getAttribute("height"); return h >= 4 && h <= 20; }));
ok("no 'actual price' caption on the chart; the red line and the toggle say it", !/actual price · known only afterwards/.test($("#sch-chart").textContent));
ok("likely price range has its own colour and is visible", (function(){ var b = $("#sch-chart svg path.band"); return b && /--range/.test(b.getAttribute("style")) && +b.getAttribute("opacity") >= 0.25; })());
ok("actual price and actual stored energy share one colour, distinct from the forecast", $$("#sch-chart svg path.actual").length === 2 && $$("#sch-chart svg path.actual").every(function(a){ return /--actual/.test(a.getAttribute("style")); }));
ok("lane names fit the left margin", $$("#sch-chart svg text.lnm").filter(function(e){ return e.getAttribute("text-anchor") === "end"; }).every(function(e){ return e.getBBox().x >= 0; }));
ok("lane names explain themselves on hover; the offer note says the trigger is an expected value with spikes included", $$("#sch-chart svg text.lnm title").length === 3 && /sells only if the price reaches its trigger/.test($$("#sch-chart svg text.lnm title")[1].textContent) && /expected value/.test($$("#sch-chart svg text.lnm title")[1].textContent) && /spike/.test($$("#sch-chart svg text.lnm title")[1].textContent));
function noOverlap(svg, skip){ var ts = Array.prototype.filter.call(svg.querySelectorAll("text"), function(e){ return !skip.test(e.textContent) && !e.classList.contains("mk9"); }).map(function(e){ var b = e.getBBox(); return {t:e.lastChild.textContent, x:b.x, y:b.y, r:b.x + b.width, b:b.y + b.height}; }), bad = [];
  for (var i = 0; i < ts.length; i++) for (var j = i + 1; j < ts.length; j++) { var p = ts[i], q = ts[j]; if (p.x < q.r && q.x < p.r && p.y < q.b && q.y < p.b) bad.push(p.t + " × " + q.t); } return bad; }
ok("no two labels overlap in the day chart", noOverlap(svgs[0], /^(0|200|400)$/).length === 0, noOverlap(svgs[0], /^(0|200|400)$/).join("; "));
ok("no two labels overlap in the stored-energy chart", noOverlap(svgs[1], /^(0|10|20|30|40)$/).length === 0, noOverlap(svgs[1], /^(0|10|20|30|40)$/).join("; "));
ok("offer drawn as a dashed amber area, not hatched", !!svgs[0].querySelector('path.oarea[stroke-dasharray="3 2"]') && /--cond/.test(svgs[0].querySelector("path.oarea").getAttribute("style")) && !svgs[0].querySelector("pattern#cdP"));
ok("hatch only before 09:00", svgs[0].querySelectorAll('rect[fill^="url(#hDy)"]').length === 1);
var socTxt = svgs[1].textContent;
["Energy stored, MWh", "Minimum", "Full · 40 MWh", "Mon 2 Feb", "Tue 3 Feb"].forEach(function(w){ ok("SoC label: " + w, socTxt.indexOf(w) >= 0); });
ok("stored-energy chart: key levels as numbers only", /16\.3 MWh/.test(socTxt) && !/Planned|Actual|Lowest|tomorrow/.test(socTxt), socTxt);
ok("round-4 blocks gone", !$("#plan-why") && !$("#plan-timeline") && !$("#k-net") && !$("#k-dis") && !$("#k-con"));
ok("what happened shown by default", S_rev() && $$("#sch-chart svg path.actual").length === 2 && $$("#plan-vbar .vb").length === 2, $$("#plan-vbar .vb").length);
click($('[data-po="plan"]'));
ok("Plan only hides what happened", !$("#sch-chart svg").querySelector("path.actual") && !$("#sch-chart rect.osold"));
ok("default selection 18:00: IRCR peak discharge", /18:00–18:30/.test(txt("#sch-rail")) && /Discharging 6\.5 MW/.test(txt("#sch-rail")) && /capacity-charge \(IRCR\) peak day/.test(txt("#sch-rail")), txt("#sch-rail").slice(0, 80));
var vb = $$("#plan-vbar .vb");
ok("value waterfall: one bar before reveal, the engine's parts", vb.length === 1 && /Energy 2,853/.test(vb[0].innerHTML) && /Energy kept, valued 1,799/.test(vb[0].innerHTML) && /Grid services 1,430/.test(vb[0].innerHTML) && /Wear −628/.test(vb[0].innerHTML) && /Net A\$ 5,454/.test(vb[0].textContent) && !/Network bill/.test(vb[0].innerHTML), vb.length && vb[0].innerHTML.slice(0, 200));
ok("breakdown table folded; says what each column leaves out", !$("#sch-money").closest("details").open && /Net for the trading day/.test(txt("#sch-money")) && /not priced at 09:00/.test(txt("#sch-money")) && /IRCR/.test(txt("#sch-money")));
var ck = $$("#sch-checks .ck");
ok("14 check tiles from the export: 12 pass, 2 not checked", ck.length === 14 && ck.filter(function(c){ return c.classList.contains("ok"); }).length === 12 && ck.filter(function(c){ return c.classList.contains("n"); }).length === 2, ck.map(function(c){ return c.textContent; }).join(","));
ok("not-checked tiles: contracts and market registration", /Contracts/.test(ck.filter(function(c){ return c.classList.contains("n"); }).map(function(c){ return c.textContent; }).join()) && /Market registration/.test(ck.filter(function(c){ return c.classList.contains("n"); }).map(function(c){ return c.textContent; }).join()));
ok("check tiles: no label cut off", ck.every(function(c){ return c.scrollWidth <= c.clientWidth + 1; }), ck.filter(function(c){ return c.scrollWidth > c.clientWidth + 1; }).map(function(c){ return c.textContent; }).join(","));
ok("value waterfall: no segment text cut off (narrow ones show the number, full text on hover)", $$("#plan-vbar .vb-row i span").every(function(sp){ return sp.scrollWidth <= sp.parentElement.clientWidth + 1; }) && $$("#plan-vbar .vb-row i:not(.vb-net)").every(function(i){ return i.title; }), $$("#plan-vbar .vb-row i span").filter(function(sp){ return sp.scrollWidth > sp.parentElement.clientWidth + 1; }).map(function(sp){ return sp.textContent + " " + sp.scrollWidth + "/" + sp.parentElement.clientWidth; }).join(","));
ok("check tiles carry the full wording", ck.every(function(c){ return c.getAttribute("data-note").length > 15 && c.tabIndex === 0; }));
ok("no internal jargon on the plan page", !/Reserve floor|SoC ≥|CMD 9 MW<|In-sample|No double count/.test($("#s-plan").innerHTML + $("#ph-chips").innerHTML), "");
ok("header chip explains the period", /training-period day/.test(txt("#ph-chips")) && /456 days/.test($$("#ph-chips [data-note]").map(function(c){ return c.getAttribute("data-note"); }).join(" ")));
ok("acronyms carry their long form", /contract maximum demand/i.test($$("#sch-checks .ck").map(function(c){ return c.getAttribute("data-note"); }).join(" ")));

/* hover every half-hour from 09:00: the trigger sits above the 09:00 forecast (else the plan would have sold it) */
(function(){ var below = [];
  for (var i = 2; i < 48; i++) { var hit = $("#sch-chart svg").querySelector('.hit[data-i="' + i + '"]'), b = hit.getBoundingClientRect();
    hit.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:b.left + 2, clientY:b.top + 10}));
    var t = $(".ctip").textContent, mt = t.match(/≥ A\$([\d,]+)/), mf = t.match(/ForecastA\$([\d,]+)/);
    if (!mt || !mf || +mt[1].replace(/,/g, "") <= +mf[1].replace(/,/g, "")) below.push(i + ":" + t.slice(0, 60)); }
  document.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:2, clientY:2}));
  ok("every half-hour from 09:00 has an offer whose trigger is above the forecast", below.length === 0, below.join(" / ")); })();
/* hover a half-hour: the tooltip's label column must stay on one line and the box keep its width */
(function(){ var hit = $("#sch-chart svg").querySelector('.hit[data-i="20"]'), b = hit.getBoundingClientRect();
  hit.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:b.left + 2, clientY:b.top + 10}));
  var tip = $(".ctip"), labels = $$(".ctip td:nth-child(2)"), rows = $$(".ctip tr");
  ok("tooltip shows for 18:00–18:30", tip && tip.style.display === "block" && /18:00–18:30/.test(tip.textContent), tip && tip.textContent.slice(0, 60));
  ok("tooltip labels on one line each", labels.length >= 3 && labels.every(function(l){ return l.getBoundingClientRect().height <= 24; }), labels.map(function(l){ return l.textContent + ":" + Math.round(l.getBoundingClientRect().height); }).join(", "));
  ok("tooltip rows each ≤ 2 lines", rows.every(function(l){ return l.getBoundingClientRect().height < 40; }));
  ok("tooltip width fixed at 230", Math.round(tip.getBoundingClientRect().width) === 230, tip.getBoundingClientRect().width);
  ok("tooltip offer row: size and trigger from the 09:00 submission", /Sell more\+3\.5 MW ≥ A\$162/.test(tip.textContent) && /ForecastA\$128/.test(tip.textContent) && !/Sold/.test(tip.textContent), tip.textContent);
  ok("tooltip: one word per label, no sentence", $$(".ctip td:nth-child(2)").every(function(l){ return l.textContent.trim().split(/\s+/).length <= 2; }) && !$(".ctip .ct-h span"), $$(".ctip td:nth-child(2)").map(function(l){ return l.textContent; }).join(","));
  document.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:2, clientY:2}));
})();
/* click a half-hour */
click(svgs[0].querySelector('.hit[data-i="2"]'));
ok("click 09:00 → charging detail", /09:00–09:30/.test(txt("#sch-rail")) && /Charging 1\.0 MW/.test(txt("#sch-rail")) && /Sell more\+10\.0 MW if ≥ A\$159–162/.test(txt("#sch-rail")), txt("#sch-rail").slice(0, 400));
click($('[data-po="outcome"]'));
(function(){ var hit = $("#sch-chart svg").querySelector('.hit[data-i="20"]'), b = hit.getBoundingClientRect();
  hit.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:b.left + 2, clientY:b.top + 10}));
  ok("tooltip after reveal: Sold and Actual rows", /Sold1\.8 MW/.test($(".ctip").textContent) && /ActualA\$869/.test($(".ctip").textContent), $(".ctip").textContent);
  document.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:2, clientY:2})); })();
click($("#sch-chart svg").querySelector('.hit[data-i="21"]'));
ok("rail 18:30: offer, what the replay sold and its money", /Sell more\+4\.3 MW if ≥ A\$165–168/.test(txt("#sch-rail")) && /3\.5 MW sold → A\$ 1,594/.test(txt("#sch-rail")) && /A\$900\/MWh/.test(txt("#sch-rail")), txt("#sch-rail"));
click($("#sch-chart svg").querySelector('.hit[data-i="36"]'));
ok("rail 02:00: holding overnight, still offering 10 MW at a higher trigger, not sold", /Holding 16\.3 MWh/.test(txt("#sch-rail")) && /Sell more\+10\.0 MW if ≥ A\$185–188/.test(txt("#sch-rail")) && /not sold/.test(txt("#sch-rail")), txt("#sch-rail"));
click($('[data-po="plan"]')); click($("#sch-chart svg").querySelector('.hit[data-i="34"]'));
var s0 = $("#sch-chart svg"), mk = s0.querySelector("rect.selm"), hit = s0.querySelector('.hit[data-i="34"]');
ok("selection mark moves", mk && Math.abs(+mk.getAttribute("x") - +hit.getAttribute("x")) < 0.01);
document.dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowRight", bubbles:true}));
ok("arrow key moves one half-hour", /01:30–02:00/.test(txt("#sch-rail")), txt("#sch-rail").slice(0, 30));

/* blocked plan: lower the export limit in Settings; checked as if all raise standby were called */
setExp("3"); goTo("plan");
ok("blocked: status", $("#plan-status").classList.contains("bad") && /Plan blocked/.test(txt("#plan-status")) && /28 half-hours above the 3 MW export limit \(18:00–08:00\)/.test(txt("#plan-status")) && !!$("#plan-status [data-inspect]"), txt("#plan-status"));
ok("blocked: headline", txt("#plan-head") === "Resolve the export limit before execution");
ok("blocked: value marked simulation", $("#plan-val .dv").classList.contains("off") && /Simulation only · not executable/.test(txt("#plan-val")));
ok("blocked: waterfall greyed", $("#plan-vbar .vb").classList.contains("off") && /simulation only/.test(txt("#plan-vbar")));
var red = $$("#sch-chart svg:first-child rect").filter(function(r){ return /stroke:var\(--bad\)/.test(r.getAttribute("style") || ""); });
ok("blocked: red outline on the chart", red.length === 1 && /Export above 3 MW/.test($("#sch-chart svg").textContent), red.length);
var bad = $$("#sch-checks .ck.b");
ok("blocked: one failing tile", bad.length === 1 && /Export ≤ 3 MW/.test(bad[0].textContent) && /raise standby/.test(bad[0].getAttribute("data-note")), bad.map(function(c){ return c.textContent; }).join());
ok("stale: results dimmed", document.body.classList.contains("stale") && getComputedStyle($("#plan-vbar").parentElement).opacity === "0.5");
ok("stale + blocked: limit marks and half-hour detail stay readable", getComputedStyle($("#plan-detail")).opacity === "1" && getComputedStyle($("#sch-rail")).opacity === "1");
ok("stale + blocked: decision stays readable", getComputedStyle($("#plan-head").parentElement).opacity === "1");
click($("#plan-status [data-inspect]"));
ok("Inspect jumps to the first bad half-hour", /18:00–18:30/.test(txt("#sch-rail")), txt("#sch-rail").slice(0, 30));
click($("#revert"));
ok("revert: executable again", $("#plan-status").classList.contains("ok") && !document.body.classList.contains("stale"));

/* reveal the outcome */
click($('[data-po="outcome"]'));
vb = $$("#plan-vbar .vb");
ok("reveal: second waterfall, the replay's money", vb.length === 2 && /Outcome, replayed/.test(vb[1].textContent) && /Energy 15,919/.test(vb[1].innerHTML) && /Network bill saving 1,527/.test(vb[1].innerHTML) && /Net A\$ 18,272/.test(vb[1].textContent), vb.length);
ok("reveal: table outcome column filled, offers cleared derived, v1 same day", !/hidden/.test(txt("#sch-money")) && /A\$ 5,720/.test(txt("#sch-money")) && /A\$ 16,727/.test(txt("#sch-money")) && /not valued/.test(txt("#sch-money")), txt("#sch-money"));
ok("reveal: actual price and actual stored energy drawn", $$("#sch-chart svg path.actual").length === 2);
ok("reveal: rail outcome", /REPLAYED/.test(txt("#sch-rail")));
click($('[data-po="plan"]'));

/* links to the evidence page */
click($('#s-plan [data-go="evidence"]'));
ok("Value header link opens Evidence", $("#s-evidence").classList.contains("on"));
goTo("plan");
ok("who is who line under the header: five parties, Synergy not among them", $$("#who-line .tipc").length === 5 && /Zenith/.test(txt("#who-line")) && /AEMO/.test(txt("#who-line")) && !/Synergy/.test(txt("#who-line")));
done();
