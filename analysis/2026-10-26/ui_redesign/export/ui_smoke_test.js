(function(){
  var out = [], $ = function(s){ return document.querySelector(s); }, $$ = function(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); };
  function ok(name, cond){ out.push((cond ? "PASS " : "FAIL ") + name); }
  function click(s){ var e = $(s); if (!e) { out.push("MISSING " + s); return; } e.click(); }
  function set(id, v){ var e = document.getElementById(id); e.value = v; e.dispatchEvent(new Event("change")); }
  function txt(s){ var e = $(s); return e ? e.textContent : ""; }
  function go(n){ click('[data-go="' + n + '"]'); }
  function hover(s){ var e = $(s); if (!e) { out.push("MISSING " + s); return; } e.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:400, clientY:300})); }
  try {
  /* layer 1 on the in-sample day */
  go("today");
  ok("data loaded: two site days, summary, fleet", window.GS_DATA.site_days.length === 2 && !!window.GS_DATA.site_summary && !!window.GS_DATA.fleet);
  ok("status PASS with 2 not checked", /PASS/.test(txt("#t-status")) && /2 not checked/.test(txt("#t-status")));
  ok("decision: discharge 9.2 MW for the IRCR peak 17:00–20:00", /Discharge 9\.2 MW · IRCR peak · 17:00–20:00/.test(txt("#t-head")));
  ok("expected value A$5,454 with stored energy and scope chips", /A\$5,454/.test(txt("#t-val")) && /incl\. A\$1,799 stored/.test(txt("#t-val")) && /scheduled only/.test(txt("#t-val")));
  ok("action chips: charge, CMD shave, IRCR discharge, conditional", $$("#t-acts .act").length === 4 && /Conditional/.test(txt("#t-acts")) && /CMD/.test(txt("#t-acts")));
  var sv = $$("#t-chart svg");
  ok("two charts on one axis: lanes + price, energy", sv.length === 2 && /Scheduled/.test(sv[0].textContent) && /Conditional/.test(sv[0].textContent) && /FCESS/.test(sv[0].textContent) && /Energy held, MWh/.test(sv[1].textContent));
  ok("price panel: forecast, trigger, P10–P90", /forecast/.test(sv[0].textContent) && /trigger/.test(sv[0].textContent) && /P10–P90/.test(sv[0].textContent));
  ok("no actual values before reveal", !/actual/.test(sv[0].textContent) && !/Actual/.test(sv[1].textContent) && /Outcome hidden/.test(txt("#t-panel")));
  ok("panel opens on the main action", /SCHEDULED/.test(txt("#t-panel")) && /IRCR peak/.test(txt("#t-panel")));
  ok("14 check tiles, 2 not checked, none failing", $$("#t-safe .ck").length === 14 && $$("#t-safe .ck.u").length === 2 && !$("#t-safe .ck.b"));
  ok("performance: three periods with real uplift", $$("#t-perf .cbr").length === 3 && /\+A\$407/.test(txt("#t-perf")) && /\+A\$398/.test(txt("#t-perf")) && /\+A\$35/.test(txt("#t-perf")));
  ok("downside table: worst in-sample day 28 Apr 2026", /28 Apr 2026/.test(txt("#t-safe")) && /−A\$11,178/.test(txt("#t-safe")));
  hover('#t-chart svg .hit[data-i="20"]');
  ok("hover table: scheduled, conditional, spike chance", /Scheduled/.test(txt(".ctip")) && /Conditional/.test(txt(".ctip")) && /Chance of A\$200 or more/.test(txt(".ctip")));
  document.body.dispatchEvent(new MouseEvent("mousemove", {bubbles:true, clientX:5, clientY:5}));
  /* a conditional half-hour */
  click('#t-acts .act.cond');
  ok("conditional panel: trigger, extra power, if it clears", /CONDITIONAL/.test(txt("#t-panel")) && /Trigger/.test(txt("#t-panel")) && /If it all clears/.test(txt("#t-panel")) && /Tranche/.test(txt("#t-panel")));
  ok("energy chart shows the if-it-clears step only for the selection", /if it clears/.test($$("#t-chart svg")[1].textContent));
  /* reveal */
  click('[data-po="outcome"]');
  ok("outcome vs fixed-code baseline: A$18,272 vs A$16,727, +A$1,545", /A\$18,272/.test(txt("#t-val")) && /A\$16,727/.test(txt("#t-val")) && /\+A\$1,545/.test(txt("#t-val")));
  ok("revealed: actual price and energy", /actual/.test($$("#t-chart svg")[0].textContent) && /Actual/.test($$("#t-chart svg")[1].textContent));
  click('[data-po="plan"]');
  /* the test-period day */
  click('[data-di="1"]');
  ok("test day: charge 1.0 MW, value A$3,469, period chip", /Charge 1\.0 MW · 11:00–15:00/.test(txt("#t-head")) && /A\$3,469/.test(txt("#t-val")) && /Test, unseen/.test(txt("#ph-chips")));
  ok("test day: no P10–P90 band (not in the export)", !/P10–P90/.test($$("#t-chart svg")[0].textContent) && /no P10–P90 range/.test(txt("#t-safe")));
  click('[data-po="outcome"]');
  ok("test day outcome below baseline: −A$583", /−A\$583/.test(txt("#t-val")));
  click('[data-po="plan"]');
  click('[data-di="0"]');
  /* blocked by a lower export limit */
  go("config"); set("c-exp", "3"); go("today");
  ok("export 3 MW blocks the plan", /BLOCKED/.test(txt("#t-status")) && /Meter export ≤ 3 MW/.test(txt("#t-status")) && /Resolve meter export before execution/.test(txt("#t-head")));
  ok("blocked value is simulation only", /Simulation only/.test(txt("#t-val")) && !!$("#t-val .bigv.off"));
  ok("chart marks the half-hours over the limit", /Export above 3 MW/.test($$("#t-chart svg")[0].textContent) && !!$("#t-safe .ck.b"));
  ok("run status stale, separate from plan status", /Results stale/.test(txt("#run")) && document.body.classList.contains("stale") && document.body.classList.contains("plan-blocked"));
  click("#t-status [data-inspect]");
  ok("inspect selects the first half-hour over the limit", /SCHEDULED/.test(txt("#t-panel")) && /Export if raise is called/.test(txt("#t-panel")));
  click("#revert");
  ok("revert: PASS and up to date again", /PASS/.test(txt("#t-status")) && /Results up to date/.test(txt("#run")) && !document.body.classList.contains("stale"));
  /* site validation */
  go("svalid");
  ok("Q2A: three periods, MAE 20.1 vs 27.6 in-sample", $$("#sv-q2a .mrow").length === 3 && /20\.1/.test(txt("#sv-q2a")) && /27\.6/.test(txt("#sv-q2a")) && /not an accuracy/.test(txt("#sv-q2a")));
  ok("Q2B: three periods, in-sample interval, combined test interval", $$("#sv-q2b .cbr").length === 3 && /95% \+A\$205/.test(txt("#sv-q2b")) && /114 test days/.test(txt("#sv-q2b")));
  ok("constraints by period: CMD 627 / 116 / 10, power 0", /627/.test(txt("#sv-lim")) && /116/.test(txt("#sv-lim")) && /3 in-sample days over 10 MW/.test(txt("#sv-lim")));
  ok("own-battery value and IRCR saving", /A\$4,669/.test(txt("#sv-risk")) && /A\$2,116,716/.test(txt("#sv-risk")));
  /* customer fleet */
  go("calls");
  ok("call on 2 Feb 2026, custom S3, 20 of 45 used before", /CALL/.test(txt("#c-dec")) && /Mon 2 Feb 2026/.test(txt("#day-label")) && /20/.test(txt("#c-dec .bigv")) && /\/ 45/.test(txt("#c-dec .bigv")));
  ok("season chart in decision view hides the future", /Not yet known at 09:00/.test(txt("#c-season")) && !/★/.test(txt("#c-season svg")));
  click('[data-view="review"]');
  ok("review: official peak day covered, hindsight label", /Official peak day/.test(txt("#c-dec")) && /Hindsight/.test(txt("#c-season")));
  click('[data-offer="ref"]'); click('[data-vscen="S2b"]');
  ok("reference S2b: 30 calls cap, 4 of 4 peak days", /\/ 30/.test(txt("#c-dec .bigv")) && /4 of 4/.test(txt("#c-budget")));
  click('[data-view="decision"]'); click('[data-offer="custom"]'); click('[data-vscen="S3"]');
  go("value");
  ok("value flow: two offers, transfers apart", $$("#v-flow .fl-row").length === 2 && /transfers inside it/.test(txt("#v-flow")));
  click('[data-vscen="S2b"]');
  ok("S2b 2025-26: custom Zenith A$2.08M, reference A$1.18M", /A\$2\.08M/.test(txt("#v-flow")) && /A\$1\.18M/.test(txt("#v-flow")));
  click('[data-vunit="home"]');
  ok("per enrolled home: reference ahead of custom (A$527 vs A$465)", /A\$527/.test(txt("#v-flow")) && /A\$465/.test(txt("#v-flow")));
  click('[data-vunit="fleet"]');
  ok("one call: 116.1 c per billed kWh for custom S2b", /116\.1 c/.test(txt("#v-unit")) && /3\.41 kW/.test(txt("#v-unit")));
  click('[data-vper="test-seen"]');
  ok("test-seen: no calls, operating cost only", /no Hot Season/.test(txt("#v-cmp")));
  click('[data-vscen="S3"]');
  go("fvalid");
  ok("holdouts: three seasons, 2025-26 chronological", $$("#fv-hold .mrow").length === 3 && /chronological/.test(txt("#fv-hold")));
  ok("constraints: all pass", $$("#fv-chk .cell.ok").length >= 10 && !$("#fv-chk .cell.b"));
  ok("stress tests and the failed call signal", $$("#fv-notes .ck").length >= 20 && /failed its pre-set test/.test(txt("#fv-notes")));
  /* old links */
  location.hash = "";
  ok("old link #compare opens validation", (function(){ var b = document.createElement("button"); b.setAttribute("data-go", "compare"); document.body.appendChild(b); b.click(); b.remove(); return document.getElementById("s-svalid").classList.contains("on"); })());
  } catch (e) { out.push("FAIL exception " + e.message + " " + (e.stack || "").split("\n")[1]); }
  var pre = document.createElement("pre"); pre.id = "smoke-out"; pre.textContent = out.join("\n"); document.body.appendChild(pre);
})();
