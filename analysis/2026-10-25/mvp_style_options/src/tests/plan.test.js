/* Today's plan (round 5). Run with src/tests/run.py. */
goTo("plan");
function S_rev(){ return $('[data-po="outcome"]').classList.contains("on"); }
ok("status: executable", $("#plan-status").classList.contains("ok") && /Plan executable/.test(txt("#plan-status")), txt("#plan-status"));
ok("headline", txt("#plan-head") === "Discharge 7.5 MW · 17:30–20:00", txt("#plan-head"));
ok("net value A$ 3,117", txt("#plan-val .dv") === "A$ 3,117", txt("#plan-val .dv"));
ok("value chips", /forecast made at 09:00/.test(txt("#plan-val")) && /scheduled actions only/.test(txt("#plan-val")), txt("#plan-val"));
ok("header context short", txt("#ph-ctx").split(/\s+/).length <= 12, txt("#ph-ctx"));
var svgs = $$("#sch-chart svg");
ok("two charts on one axis", svgs.length === 2 && svgs[0].getAttribute("data-tip") === "day" && svgs[1].getAttribute("data-tip") === "soc" && svgs.every(function(s){ return s.getAttribute("data-xg") === "day"; }), svgs.length);
ok("48 hit columns per chart", svgs.every(function(s){ return s.querySelectorAll(".hit").length === 48; }));
ok("same x scale in both charts", svgs[0].getAttribute("data-iw") === svgs[1].getAttribute("data-iw") && svgs[0].getAttribute("viewBox").split(" ")[2] === svgs[1].getAttribute("viewBox").split(" ")[2]);
var lanesTxt = Array.prototype.map.call(svgs[0].querySelectorAll("text"), function(t){ return t.textContent; }).join(" | ");
["Plan", "Sell more if", "Grid services", "sold 4 of 5 · A$ 216", "earns A$ 503", "Cut import 0.4 MW", "Discharge 7.5 MW", "Hold", "Recharge 4.2 MW", "peak A$230", "low A$75", "threshold A$300→150", "A$640", "Price, A$/MWh", "PLAN MADE 09:00"].forEach(function(w){ ok("lane label: " + w, lanesTxt.indexOf(w) >= 0); });
ok("no captions on the day chart: only lane names, axis, actions, mark", (function(){ var t = Array.prototype.map.call(svgs[0].querySelectorAll("text"), function(e){ return e.lastChild.textContent; }).filter(function(x){ return !/^(\d+|\d\d:\d\d|Plan|Sell more if|Grid services|Price, A\$\/MWh|PLAN MADE 09:00|Hold|Cut import [\d.]+ MW|Discharge [\d.]+ MW|Recharge [\d.]+ MW|peak A\$\d+|low A\$\d+|threshold A\$\d+→\d+|A\$[\d,]+|sold \d of \d · A\$ [\d,]+|earns A\$ [\d,]+)$/.test(x); }); return t.length === 0; })(), Array.prototype.map.call(svgs[0].querySelectorAll("text"), function(e){ return e.textContent; }).join(" | "));
ok("no label says the offer sold at A$150", lanesTxt.indexOf("offer price") < 0);
ok("sold half-hours filled solid in the offer lane", svgs[0].querySelectorAll("rect.osold").length >= 1);
ok("threshold drawn as five falling steps", (function(){ var ys = $$("#sch-chart svg line.othr").map(function(l){ return +l.getAttribute("y1"); }); return ys.length === 5 && ys.every(function(y, i){ return !i || y > ys[i - 1]; }); })(), $$("#sch-chart svg line.othr").length);
ok("17:30 not sold: the threshold was above the actual price", !svgs[0].querySelector('rect.osold[x="' + svgs[0].querySelector('.hit[data-i="19"]').getAttribute("x") + '"]'));
ok("offer window is square, sold columns fill its full height", !svgs[0].querySelector('rect.osold[rx]') && svgs[0].querySelectorAll("rect.osold").length >= 1 && +svgs[0].querySelector("rect.osold").getAttribute("height") === 16);
ok("no 'actual price' caption on the chart; the red line and the toggle say it", !/actual price · known only afterwards/.test($("#sch-chart").textContent));
ok("likely price range has its own colour and is visible", (function(){ var b = $("#sch-chart svg path.band"); return b && /--range/.test(b.getAttribute("style")) && +b.getAttribute("opacity") >= 0.25; })());
ok("actual price and actual stored energy share one colour, distinct from the forecast", $$("#sch-chart svg path.actual").length === 2 && $$("#sch-chart svg path.actual").every(function(a){ return /--actual/.test(a.getAttribute("style")); }));
ok("lane names fit the left margin", $$("#sch-chart svg text.lnm").filter(function(e){ return e.getAttribute("text-anchor") === "end"; }).every(function(e){ return e.getBBox().x >= 0; }));
ok("lane names explain themselves on hover (native title from the glossary)", $$("#sch-chart svg text.lnm title").length === 3 && /sells only in the half-hours/.test($$("#sch-chart svg text.lnm title")[1].textContent));
ok("no two labels overlap in the price panel", (function(){ var ts = $$("#sch-chart svg:first-child text").filter(function(e){ return !/^(0|250|500)$/.test(e.textContent) && !e.classList.contains("mk9"); }).map(function(e){ var b = e.getBBox(); return {t:e.textContent, x:b.x, y:b.y, r:b.x + b.width, b:b.y + b.height}; }), bad = [];
  for (var i = 0; i < ts.length; i++) for (var j = i + 1; j < ts.length; j++) { var p = ts[i], q = ts[j]; if (p.x < q.r && q.x < p.r && p.y < q.b && q.y < p.b) bad.push(p.t + " × " + q.t); }
  return bad.length === 0; })(), function(){ var ts = $$("#sch-chart svg:first-child text").filter(function(e){ return !/^(0|250|500)$/.test(e.textContent) && !e.classList.contains("mk9"); }).map(function(e){ var b = e.getBBox(); return {t:e.textContent, x:b.x, y:b.y, r:b.x + b.width, b:b.y + b.height}; }), bad = []; for (var i = 0; i < ts.length; i++) for (var j = i + 1; j < ts.length; j++) { var p = ts[i], q = ts[j]; if (p.x < q.r && q.x < p.r && p.y < q.b && q.y < p.b) bad.push(p.t + " × " + q.t); } return bad.join("; "); }());
ok("offer drawn as dashed outline, not hatched", !!svgs[0].querySelector('rect[stroke-dasharray="3 2"]') && !svgs[0].querySelector("pattern#cdP"));
ok("hatch only before 09:00", svgs[0].querySelectorAll('rect[fill^="url(#hDy)"]').length === 1);
var socTxt = svgs[1].textContent;
["Energy stored, MWh", "Minimum", "Full · 40 MWh"].forEach(function(w){ ok("SoC label: " + w, socTxt.indexOf(w) >= 0); });
ok("stored-energy chart: key levels as numbers only", /6\.6 MWh/.test(socTxt) && /29\.9 MWh/.test(socTxt) && !/Planned|Actual|Lowest|tomorrow/.test(socTxt), socTxt);
ok("round-4 blocks gone", !$("#plan-why") && !$("#plan-timeline") && !$("#k-net") && !$("#k-dis") && !$("#k-con"));
ok("what happened shown by default", S_rev() && $$("#sch-chart svg path.actual").length === 2 && $$("#plan-vbar .vb").length === 2, $$("#plan-vbar .vb").length);
click($('[data-po="plan"]'));
ok("Plan only hides what happened", !$("#sch-chart svg").querySelector("path.actual") && !$("#sch-chart rect.osold"));
ok("default selection 18:00", /18:00–18:30/.test(txt("#sch-rail")) && /Discharging 7.5 MW/.test(txt("#sch-rail")), txt("#sch-rail").slice(0, 60));
var vb = $$("#plan-vbar .vb");
ok("value waterfall: one bar before reveal", vb.length === 1 && /Energy 2,361/.test(vb[0].textContent) && /Grid services 503/.test(vb[0].textContent) && /Network bill saving 900/.test(vb[0].textContent) && /Wear −647/.test(vb[0].textContent) && /Net A\$ 3,117/.test(vb[0].textContent), vb.length && vb[0].textContent);
ok("breakdown table folded", !$("#sch-money").closest("details").open && /Net for the trading day/.test(txt("#sch-money")));
var ck = $$("#sch-checks .ck");
ok("7 check tiles, all pass", ck.length === 7 && ck.every(function(c){ return c.classList.contains("ok"); }), ck.map(function(c){ return c.textContent; }).join(","));
ok("check tiles carry the full wording", ck.every(function(c){ return c.getAttribute("data-note").length > 15 && c.tabIndex === 0; }));
ok("no internal jargon on the plan page", !/Reserve floor|SoC ≥|CMD 9 MW<|In-sample|No double count/.test($("#s-plan").innerHTML + $("#ph-chips").innerHTML), "");
ok("header chip explains the period", /training-period day/.test(txt("#ph-chips")) && $("#ph-chips [data-note]") !== null);
ok("acronyms carry their long form", /contract maximum demand/i.test($$("#sch-checks .ck").map(function(c){ return c.getAttribute("data-note"); }).join(" ")));

/* hover a half-hour: the tooltip's label column must stay on one line and the box keep its width */
(function(){ var hit = $("#sch-chart svg").querySelector('.hit[data-i="20"]'), b = hit.getBoundingClientRect();
  hit.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:b.left + 2, clientY:b.top + 10}));
  var tip = $(".ctip"), labels = $$(".ctip td:nth-child(2)"), rows = $$(".ctip tr");
  ok("tooltip shows for 18:00–18:30", tip && tip.style.display === "block" && /18:00–18:30/.test(tip.textContent), tip && tip.textContent.slice(0, 60));
  ok("tooltip labels on one line each", labels.length >= 3 && labels.every(function(l){ return l.getBoundingClientRect().height <= 24; }), labels.map(function(l){ return l.textContent + ":" + Math.round(l.getBoundingClientRect().height); }).join(", "));
  ok("tooltip rows each ≤ 2 lines", rows.every(function(l){ return l.getBoundingClientRect().height < 40; }));
  ok("tooltip width fixed at 230", Math.round(tip.getBoundingClientRect().width) === 230, tip.getBoundingClientRect().width);
  ok("tooltip offer row: size and threshold", /Sell more≥ A\$280/.test(tip.textContent) && !/Sold/.test(tip.textContent), tip.textContent);
  ok("tooltip: one word per label, no sentence", $$(".ctip td:nth-child(2)").every(function(l){ return l.textContent.trim().split(/\s+/).length <= 2; }) && !$(".ctip .ct-h span"), $$(".ctip td:nth-child(2)").map(function(l){ return l.textContent; }).join(","));
  document.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:2, clientY:2}));
})();
/* click a half-hour */
click(svgs[0].querySelector('.hit[data-i="34"]'));
ok("click 01:00 → charging detail", /01:00–01:30/.test(txt("#sch-rail")) && /Charging 4.2 MW/.test(txt("#sch-rail")), txt("#sch-rail").slice(0, 60));
click($('[data-po="outcome"]')); click($("#sch-chart svg").querySelector('.hit[data-i="21"]'));
(function(){ var hit = $("#sch-chart svg").querySelector('.hit[data-i="20"]'), b = hit.getBoundingClientRect();
  hit.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:b.left + 2, clientY:b.top + 10}));
  ok("tooltip after reveal: Sold and Actual rows", /Sold(A\$ 46|A\$ 47)/.test($(".ctip").textContent) && /ActualA\$310/.test($(".ctip").textContent), $(".ctip").textContent);
  document.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:2, clientY:2})); })();
ok("rail: offer row says sold at the actual price of that half-hour", /sold at A\$640 → A\$ 96/.test(txt("#sch-rail")) && /Why this threshold/.test(txt("#sch-rail")) && /one more hour of forecast A\$230 still ahead/.test(txt("#sch-rail")), txt("#sch-rail"));
click($("#sch-chart svg").querySelector('.hit[data-i="19"]'));
ok("rail: 17:30 not sold, threshold A$300 with its reason", /not sold, price below A\$300/.test(txt("#sch-rail")) && /two more hours of forecast A\$230 still ahead/.test(txt("#sch-rail")), txt("#sch-rail"));
click($('[data-po="plan"]')); click($("#sch-chart svg").querySelector('.hit[data-i="34"]'));
var s0 = $("#sch-chart svg"), mk = s0.querySelector("rect.selm"), hit = s0.querySelector('.hit[data-i="34"]');
ok("selection mark moves", mk && Math.abs(+mk.getAttribute("x") - +hit.getAttribute("x")) < 0.01);
document.dispatchEvent(new KeyboardEvent("keydown", {key:"ArrowRight", bubbles:true}));
ok("arrow key moves one half-hour", /01:30–02:00/.test(txt("#sch-rail")), txt("#sch-rail").slice(0, 30));

/* blocked plan: lower the export limit in Settings */
setExp("3"); goTo("plan");
ok("blocked: status", $("#plan-status").classList.contains("bad") && /Plan blocked/.test(txt("#plan-status")) && /5 half-hours above the 3 MW export limit/.test(txt("#plan-status")) && !!$("#plan-status [data-inspect]"), txt("#plan-status"));
ok("blocked: headline", txt("#plan-head") === "Resolve the export limit before execution");
ok("blocked: value marked simulation", $("#plan-val .dv").classList.contains("off") && /Simulation only · not executable/.test(txt("#plan-val")));
ok("blocked: waterfall greyed", $("#plan-vbar .vb").classList.contains("off") && /simulation only/.test(txt("#plan-vbar")));
var red = $$("#sch-chart svg:first-child rect").filter(function(r){ return /stroke:var\(--bad\)/.test(r.getAttribute("style") || ""); });
ok("blocked: red outline on the chart", red.length === 1 && /Export above 3 MW/.test($("#sch-chart svg").textContent), red.length);
var bad = $$("#sch-checks .ck.b");
ok("blocked: one failing tile", bad.length === 1 && /Connection export ≤ 3 MW/.test(bad[0].textContent), bad.map(function(c){ return c.textContent; }).join());
ok("stale: results dimmed", document.body.classList.contains("stale") && getComputedStyle($("#plan-vbar").parentElement).opacity === "0.5");
ok("stale + blocked: limit marks and half-hour detail stay readable", getComputedStyle($("#plan-detail")).opacity === "1" && getComputedStyle($("#sch-rail")).opacity === "1");
ok("stale + blocked: decision stays readable", getComputedStyle($("#plan-head").parentElement).opacity === "1");
click($("#plan-status [data-inspect]"));
ok("Inspect jumps to the first bad half-hour", /17:30–18:00/.test(txt("#sch-rail")), txt("#sch-rail").slice(0, 30));
click($("#revert"));
ok("revert: executable again", $("#plan-status").classList.contains("ok") && !document.body.classList.contains("stale"));

/* reveal the outcome */
click($('[data-po="outcome"]'));
vb = $$("#plan-vbar .vb");
ok("reveal: second waterfall", vb.length === 2 && /Outcome, replayed/.test(vb[1].textContent) && /Offer cleared/.test(vb[1].textContent), vb.length);
ok("reveal: table outcome column filled", !/hidden/.test(txt("#sch-money")));
ok("reveal: actual price and actual stored energy drawn", $$("#sch-chart svg path.actual").length === 2);
ok("reveal: rail outcome", /REPLAYED/.test(txt("#sch-rail")));
click($('[data-po="plan"]'));

/* links to the evidence page */
click($('#s-plan [data-go="evidence"]'));
ok("Value header link opens Evidence", $("#s-evidence").classList.contains("on"));
goTo("plan");
ok("who is who line under the header: five parties, Synergy not among them", $$("#who-line .tipc").length === 5 && /Zenith/.test(txt("#who-line")) && /AEMO/.test(txt("#who-line")) && !/Synergy/.test(txt("#who-line")));
done();
