/* ---------- Evidence, site battery (round 5): "2 · Prove" ----------
   The page answers two questions: how much more this dispatcher earns than the previous dispatcher (v1, kept unchanged
   as the baseline), and whether a physical or contract limit was ever broken. The three periods are drawn at once
   (dot plot, limits matrix), so there is no period toolbar; the row you click (S.cper) drives the headline, the
   absolute bars, the price-forecast pairs and the highlighted matrix column. Every caveat keeps its full wording in a
   hover note (data-note). Hover notes take their wording from the shared glossary (GLOSS, glNote in engine.js) and
   add only page facts (counts, dates). Chart labels that need a note get a transparent hover area over the SVG text
   (evHots), because SVG text cannot open the CSS note.
   This file runs after engine.js and before init.js, inside the same function scope. */
/* each check: name, definition, glossary note(s), why it is a check; its hover note is all four, the matrix shows SITE_SHORT */
var SITE_CHECKS = [
  ["Energy stored adds up in every dispatch interval", "state of charge (SOC) follows SOC(t+1) = SOC(t) + charge × 0.922 − discharge ÷ 0.922 + regulation energy, with the residual below tolerance.",
   GLOSS.SOC[1], "a gap would mean the replay sold energy the battery never held."],
  ["Power stays within the 10 MW rating, energy and grid-service standby together", "discharge + Regulation raise + Contingency raise ≤ 10 MW, and the same on the charge side.",
   "", "a plan above the rating could not be delivered if grid services were called at the same moment."],
  ["Energy stored never goes below the grid-service reserve plus the factory backup", "checked in every dispatch interval.",
   glNote("GRIDRESERVE") + " " + glNote("BACKUP"), "below either, the battery could not deliver the grid services it promised or carry the factory through an outage."],
  ["Grid import and export stay within the connection limits at the meter", "every executed 5-minute dispatch interval is checked.",
   "", "a plan that breaks them could not be carried out at the factory's connection."],
  ["Nothing sold twice", "energy and grid services (FCESS) never share the same MW or MWh.",
   glName("FCESS") + ": " + glNote("FCESS"), "counting the same MW twice would overstate the profit."],
  ["Only data known at 09:00", "every input's publication time is checked against the 09:00 decision time; the 09:00 pre-dispatch forecast of the market operator (AEMO) is left out because it is published after 09:00.",
   glNote("DECISION") + " " + glNote("AEMO"), "data published later would make the replay look better than a real 09:00 decision could be."]];
function siteNote(k){ var c = SITE_CHECKS[k]; return c[0] + ": " + c[1] + (c[2] ? " " + c[2] : "") + " Why it is a check: " + c[3]; }
/* the matrix shows these short names, in SITE_CHECKS order */
var SITE_SHORT = ["Energy balance holds", "Within 10 MW rating", "Grid-service reserve + factory backup", "Grid import and export limits", "Nothing sold twice", "Only data known at 09:00"];
/* the round-4 replay reports one count of hard-limit violations; it was shown against the meter limits, so it stays there */
var HC_ROW = 3;
/* period names (ROUND5.md 7.1) and their glossary keys; the dates and days come from VAL[p].lab, which engine.js owns */
var EV_PER = [["ins", "Training period", "TRAIN"], ["tseen", "Test · reviewed", "TREV"], ["tnew", "Test · blind", "TBLIND"]];
function pct(a, b){ return (a >= b ? "+" : "−") + nf(Math.abs(a - b) / b * 100, 1) + "%"; }
/* a forecast error compared with its baseline, said as an improvement: "26.7% lower error" */
function errChange(mod, base){ return nf(Math.abs(mod - base) / base * 100, 1) + "% " + (mod <= base ? "lower" : "higher") + " error"; }
function evSgn(x){ return (x < 0 ? "−" : "+") + "A$" + nf(Math.abs(x)); }
function evIdx(p){ for (var i = 0; i < EV_PER.length; i++) if (EV_PER[i][0] === p) return i; return 1; }
function evName(p){ return EV_PER[evIdx(p)][1]; }
/* the 95% confidence range of the daily extra profit includes 0: no clear gain in that period */
function evZero(p){ var ci = VAL[p].q2b[2]; return ci[0] <= 0 && ci[1] >= 0; }
function evDates(p){ return VAL[p].lab.split(" · ")[1]; }
/* "1 Jul – 15 Oct 2026 · 107 d": what every period label carries next to its name */
function evSpan(p){ return evDates(p) + " · " + VAL[p].days + " d"; }
/* hover note of a period: the glossary's dates, days and what kind of evidence it is; the test periods add the season */
function evPerNote(p){ return glNote(EV_PER[evIdx(p)][2]) + (p === "ins" ? "" : " No Hot Season (Dec–Mar) in this period."); }
/* the dot plot and the bars under it share the label column, so rows line up; r is the room kept right of the plot */
function evFrame(sel, r){ var W = fitW(sel, 560, 900); return {W:W, x0:214, x1:W - r}; }
var EV_ZERO_NOTE = "The 95% range crosses zero: we cannot tell this period's gain from luck.";
/* SVG text that needs a hover note carries it in data-hot; evHots lays a transparent HTML hover area over each one */
function hot(note){ return ' data-hot="' + String(note).replace(/&/g, "&amp;").replace(/"/g, "&quot;") + '"'; }
function evHots(sel){
  var box = document.querySelector(sel), svg = box && box.querySelector("svg"); if (!svg) return;
  var vb = svg.viewBox.baseVal, h = "";
  Array.prototype.forEach.call(svg.querySelectorAll("[data-hot]"), function(t){
    var b = t.getBBox(); if (!b.width) return;
    h += '<span class="ev-hot' + (b.x > vb.width / 2 ? " r" : "") + '" style="left:' + (b.x / vb.width * 100).toFixed(2) + "%;top:" + (b.y / vb.height * 100).toFixed(2) + "%;width:" + (b.width / vb.width * 100).toFixed(2) + "%;height:" + (b.height / vb.height * 100).toFixed(2) + '%"' + noteAttr(t.getAttribute("data-hot")) + "></span>";
  });
  box.insertAdjacentHTML("beforeend", h);
}

/* the decision: extra profit per day for the selected period, and broken limits in the same period */
function evDecision(){
  var p = S.cper, v = VAL[p], b = v.q2b, up = b[0] - b[1], z = evZero(p), hh = v.days * 48;
  /* the claim and the period are two flex items: when the line is too short, the period moves under the claim whole */
  $("#ev-head").innerHTML = '<span class="ev-hl"><span class="ev-num' + (z ? " zero" : "") + '">' + evSgn(up) + "</span> /day vs " +
    gl("DISPATCHER", "the previous dispatcher (v1)", "ev-def") + "</span>" +
    "<small>" + pct(b[0], b[1]) + ' · <span class="ev-per"' + noteAttr(evPerNote(p)) + ">" + evName(p) + " · " + v.days + " d</span></small>";
  /* a caveat that changes how the headline reads comes first: no clear gain, or numbers from the training period */
  $("#ev-chips").innerHTML = (p === "ins" ? '<span class="tipc ev-cav"' + noteAttr(evPerNote(p)) + ">training period · not a test</span>" : "") +
    (z ? '<span class="tipc ev-cav ev-zc"' + noteAttr(EV_ZERO_NOTE + " 95% range " + evSgn(b[2][0]) + " to " + evSgn(b[2][1]) + " per day, from resampling the " + v.days + " days (bootstrap). " + evPerNote(p)) + ">includes 0 · no clear gain yet</span>" : "") +
    '<span class="tipc"' + noteAttr(glNote("NETBILL") + " " + glName("CMD") + ": " + glNote("CMD") + " How the saving arises: the battery discharges at the factory's peaks so its grid import stays under the 9 MW contract. The factory load is synthetic (simulated, not metered).") + ">includes the factory's network bill saving</span>" +
    gl("RUN", "saved run R‑0142 · illustrative: all numbers on this page are placeholders until the engine's results are loaded");
  $("#ev-viol").innerHTML = '<div class="cap"><span class="ev-capn r"' + noteAttr("Battery rating, grid-service reserve, factory backup and grid connection limits: none may be broken. The 9 MW import contract is a cost, not a hard limit, so it is shown separately below.") + ">Physical and contract limits broken</span></div>" +
    '<div class="dv ' + (v.hc ? "bad" : "ok") + '">' + nf(v.hc) + " <small>of " + nf(hh) + " half-hours</small></div>" +
    (v.cmd ? '<span class="ck w"' + noteAttr(glNote("CMD") + " Exceeding it is allowed; the extra demand charge is counted in the result. In this period actual factory load ran above forecast and import went above 9 MW at times.") + '><span class="ci w">!</span>9 MW import contract exceeded · cost counted</span>'
           : '<span class="ck ok"' + noteAttr(glNote("CMD") + " Site import stayed at or below 9 MW in all " + nf(hh) + " half-hours of this period.") + '><span class="ci ok">&#10003;</span>9 MW import contract held</span>');
}

/* primary visual: average extra profit per day and its 95% confidence range per period, against a zero line;
   rows whose range includes 0 go grey */
function evDots(){
  var f = evFrame("#ev-dots", 64), W = f.W, x0 = f.x0, x1 = f.x1, y0 = 30, RH = 64, yb = y0 + 3 * RH, H = yb + 24, s = [], ov = "";
  var bs = EV_PER.map(function(e){ return VAL[e[0]].q2b; });
  var dmin = Math.min.apply(null, [0].concat(bs.map(function(b){ return b[2][0]; }))), dmax = Math.max.apply(null, bs.map(function(b){ return b[2][1]; }));
  var lo = Math.floor((dmin - 50) / 100) * 100, hi = Math.ceil((dmax + 50) / 100) * 100, t;
  var xa = function(v){ return x0 + (v - lo) / (hi - lo) * (x1 - x0); };
  s.push('<svg viewBox="0 0 ' + W + " " + H + '" class="lanes chart" data-tip="evd" role="img" aria-label="Extra profit per day of this dispatcher over the previous dispatcher (v1), A$, average and 95% confidence range for three periods (illustrative)">');
  s.push(tx(x0, 14, "Extra A$ per day vs the previous dispatcher · dot = average, line = 95% confidence range", "lnm"));
  for (t = lo; t <= hi; t += 100) s.push(ln(xa(t), y0, xa(t), yb, t ? 'class="gl"' : 'style="stroke:var(--axis-strong)" stroke-width="1.3"') +
    tx(xa(t), yb + 15, t ? (t < 0 ? "−" : "+") + nf(Math.abs(t)) : "0", t ? "" : "lt", ' text-anchor="middle"'));
  EV_PER.forEach(function(e, i){
    var p = e[0], b = VAL[p].q2b, m = b[0] - b[1], ci = b[2], z = evZero(p), on = p === S.cper, ry = y0 + i * RH, cy = ry + RH / 2;
    var col = z ? "var(--text-4)" : "var(--ok)", hiLab = evSgn(ci[1]) + " · 95% range";
    if (on) s.push(rc(0, ry + 1, W, RH - 2, 'style="fill:var(--sel)"') + rc(0, ry + 9, 3, RH - 18, 'class="selm"'));
    if (i) s.push(ln(0, ry, W, ry, 'class="gl"'));
    s.push(tx(14, cy - 3, e[1], "lt", ' data-n="' + p + '"') + tx(14, cy + 13, evSpan(p), "lu", ' data-d="' + p + '"'));
    /* the range with end caps: low end at the left, high end and "95% range" at the right */
    s.push(ln(xa(ci[0]), cy, xa(ci[1]), cy, 'style="stroke:' + col + '" stroke-width="2"') +
      ln(xa(ci[0]), cy - 6, xa(ci[0]), cy + 6, 'style="stroke:' + col + '" stroke-width="2"') + ln(xa(ci[1]), cy - 6, xa(ci[1]), cy + 6, 'style="stroke:' + col + '" stroke-width="2"') +
      tx(xa(ci[0]) - 6, cy + 4, evSgn(ci[0]), "lu halo", ' text-anchor="end"') +
      /* a range that includes 0 is named right after its upper end, where the eye already is; its note opens on hover */
      tx(xa(ci[1]) + 6, cy + 4, hiLab + (z ? '<tspan class="vl" dx="12" style="fill:var(--text-3)"' + hot(EV_ZERO_NOTE) + ">includes 0 · no clear gain</tspan>" : ""), "lu halo"));
    /* the training period is drawn hollow: it chose the method, so it is a reference, not a test */
    s.push('<circle cx="' + xa(m).toFixed(1) + '" cy="' + cy + '" r="6" style="fill:' + (p === "ins" ? "var(--surface)" : col) + ";stroke:" + col + '" stroke-width="2"/>' +
      tx(xa(m), cy - 11, evSgn(m) + " average", "vl halo", ' text-anchor="middle" style="fill:' + (z ? "var(--text-3)" : "var(--ok)") + '"'));
    /* "training data · not a test" sits next to the training period's name as a chip (HTML over the chart, so it can
       open its note); evPlaceChip moves it to the measured end of the name once the chart is on screen */
    if (p === "ins") ov += '<span class="tipc ev-ov" data-w="' + W + '" style="left:' + (126 / W * 100).toFixed(2) + "%;top:" + ((cy - 19) / H * 100).toFixed(2) + '%"' + noteAttr(evPerNote(p)) + ">training data · not a test</span>";
    s.push('<rect class="hit" data-i="' + i + '" data-p="' + p + '" x="0" y="' + ry + '" width="' + W + '" height="' + RH + '" tabindex="0" role="button" aria-pressed="' + on + '" aria-label="' +
      e[1] + ", " + evDates(p) + ", " + VAL[p].days + " days: extra " + evSgn(m) + " per day, 95% confidence range " + evSgn(ci[0]) + " to " + evSgn(ci[1]) + (z ? ", includes 0" : "") + '"/>');
  });
  return s.join("") + "</svg>" + ov;
}

/* the selected period in absolute terms; "if prices were known in advance" is hatched because no dispatcher can reach it */
var EV_BARS_TITLE = "Profit per day, A$ · energy + grid services + network bill saving − battery wear";
function evAbs(){
  var p = S.cper, b = VAL[p].q2b, f = evFrame("#ev-abs", 140), W = f.W, x0 = f.x0, x1 = f.x1, y0 = 40, P = 30, H = y0 + 3 * P + 16, s = [];
  var top = Math.ceil(Math.max.apply(null, EV_PER.map(function(e){ return VAL[e[0]].q2b[3]; })) / 1000) * 1000;
  var w = function(v){ return v / top * (x1 - x0); }, dn = glNote("DISPATCHER");
  s.push('<svg viewBox="0 0 ' + W + " " + H + '" class="lanes chart" role="img" aria-label="' + EV_BARS_TITLE + ". " + evName(p) + ", " + evDates(p) + ", " + VAL[p].days + " days: previous dispatcher (v1) A$" + nf(b[1]) + ", this dispatcher A$" + nf(b[0]) + ", if prices were known in advance A$" + nf(b[3]) + ' per day (illustrative)"><defs>' + HATCH("hEv") + "</defs>");
  s.push(tx(14, 16, evName(p), "lt") + tx(x0, 14, EV_BARS_TITLE, "lnm") + tx(x0, 28, evSpan(p) + " · average, same days and prices", "lu"));
  [["Previous dispatcher (v1)", b[1], 'style="fill:var(--bar)"', dn], ["This dispatcher", b[0], 'style="fill:var(--ok)"', dn],
   ["If prices were known in advance", b[3], 'fill="url(#hEv)" style="stroke:var(--axis)" stroke-width="1"', glNote("HINDSIGHT")]].forEach(function(r, i){
    var cy = y0 + i * P + P / 2;
    s.push(tx(14, cy + 4, r[0], "ann", hot(r[3])) + rc(x0, cy - 7, w(r[1]), 14, 'rx="2" ' + r[2]) +
      tx(x0 + w(r[1]) + 8, cy + 4, "A$" + nf(r[1]), "vl", ' style="fill:var(--text)"'));
    /* the ceiling is named under its bar: the line is too long to sit after the value in every period */
    if (i === 2) s.push(tx(x0, cy + 20, "ceiling · unattainable: prices are not known in advance", "lu"));
  });
  return s.join("") + "</svg>";
}

/* price forecast (Q2A): each metric is a pair of bars, simple baseline above the model, the change at the right;
   lower is better */
var EV_EVENING_NOTE = "17:00–21:00 is the evening peak, when prices are usually highest and the battery discharges, so errors in these hours matter most to its profit.";
function evQ2a(){
  var p = S.cper, a = VAL[p].q2a, W = fitW("#ev-q2a", 420, 600), x0 = 200, xr = W - 4, lab = 150, G = 54, H = 3 * G - 8, s = [];
  var M = [["Wholesale price, all day", "average error, A$/MWh", 0, "yesterday's prices", ""], ["Evening 17:00–21:00 price", "average error, A$/MWh", 0, "yesterday's prices", EV_EVENING_NOTE],
           ["Spike ≥ A$200", "spike forecast error (Brier score)", 3, "spike rate, last 28 days", ""]];
  s.push('<svg viewBox="0 0 ' + W + " " + H + '" class="lanes chart" role="img" aria-label="Price forecast error, model against a simple baseline, ' + evName(p) + ", " + evDates(p) + ' (illustrative)">');
  M.forEach(function(mt, j){
    /* one scale per metric across all periods, so a calm period shows shorter bars */
    var max = Math.max.apply(null, EV_PER.map(function(e){ return Math.max(VAL[e[0]].q2a[j][0], VAL[e[0]].q2a[j][1]); }));
    var w = function(v){ return v / max * (xr - 60 - lab - x0); }, gy = j * G, mod = a[j][0], base = a[j][1];
    s.push(tx(0, gy + 11, mt[0], "lt", mt[4] ? hot(mt[4]) : "") + tx(0, gy + 26, mt[1], "lu"));
    s.push(rc(x0, gy + 1, w(base), 12, 'rx="2" style="fill:var(--bar)"') + tx(x0 + w(base) + 6, gy + 11, nf(base, mt[2]) + " · " + mt[3], "ann"));
    s.push(rc(x0, gy + 17, w(mod), 12, 'rx="2" style="fill:var(--ok)"') + tx(x0 + w(mod) + 6, gy + 27, nf(mod, mt[2]) + " · model", "ann"));
    s.push(tx(xr, gy + 27, errChange(mod, base), "vl", ' text-anchor="end" style="fill:' + (mod <= base ? "var(--ok)" : "var(--bad)") + '"'));
  });
  return s.join("") + "</svg>";
}

/* limits matrix: the six hard limits and the 9 MW import contract (a cost, not a hard limit) in each period, icons only;
   a column shows the period's name and days, its dates open on hover (the dot plot above shows them too);
   hover a row name for the check, a cell for its count */
var EV_CMD_ROW_NOTE = glNote("CMD") + " Exceeding it is allowed; the extra demand charge is counted in the result. Why it is a check: the charge lowers the profit, so it must be in the result, not hidden.";
function evCell(p, k){
  var v = VAL[p], hh = nf(v.days * 48);
  if (k === "cmd") return v.cmd ? ["w", '<span class="ci w">!</span><span class="ev-pr">cost counted</span>', "Above 9 MW at times. The extra demand charge is counted in the result."]
                                 : ["ok", '<span class="ci ok">&#10003;</span>', "0 of " + hh + " half-hours above 9 MW"];
  if (k === HC_ROW && v.hc) return ["b", '<span class="ci b">&#10005;</span><b class="ev-n">' + nf(v.hc) + "</b>", nf(v.hc) + " of " + hh + " half-hours"];
  return ["ok", '<span class="ci ok">&#10003;</span>', "0 of " + hh + " half-hours"];
}
function evMatrix(){
  var rows = SITE_CHECKS.map(function(c, k){ return [k, SITE_SHORT[k], "", siteNote(k)]; })
    .concat([["cmd", "Import contract 9 MW", "a cost, not a hard limit", EV_CMD_ROW_NOTE]]);
  var t = '<thead><tr><td></td>' + EV_PER.map(function(e){
    return '<th scope="col" class="c' + (e[0] === S.cper ? " on" : "") + ' r"' + noteAttr(evPerNote(e[0])) + ">" + e[1] + '<span class="sub ev-d">' + VAL[e[0]].days + " d</span></th>"; }).join("") + "</tr></thead><tbody>";
  rows.forEach(function(r){
    t += '<tr data-k="' + r[0] + '"><th scope="row"' + noteAttr(r[3]) + ">" + r[1] + (r[2] ? ' <span class="sub ev-why">' + r[2] + "</span>" : "") + "</th>" + EV_PER.map(function(e){
      var c = evCell(e[0], r[0]);
      return '<td class="c ' + c[0] + (e[0] === S.cper ? " on" : "") + ' r"' + noteAttr(e[1] + " · " + evSpan(e[0]) + " · " + c[2]) + ">" + c[1] + "</td>"; }).join("") + "</tr>";
  });
  return t + "</tbody>";
}

function evPlaceChip(){
  var nm = document.querySelector('#ev-dots text[data-n="ins"]'), ov = document.querySelector("#ev-dots .ev-ov"), w = nm && nm.getComputedTextLength ? nm.getComputedTextLength() : 0;
  if (ov && w) ov.style.left = ((14 + w + 10) / +ov.getAttribute("data-w") * 100).toFixed(2) + "%";
}
function renderEvidence(){
  evDecision();
  $("#ev-dots").innerHTML = evDots(); evPlaceChip(); evHots("#ev-dots");
  $("#ev-abs").innerHTML = evAbs(); evHots("#ev-abs");
  $("#ev-q2a-per").innerHTML = '<span class="ev-per"' + noteAttr(evPerNote(S.cper)) + ">" + evName(S.cper) + " · " + VAL[S.cper].days + " d</span>";
  $("#ev-q2a").innerHTML = evQ2a(); evHots("#ev-q2a");
  $("#ev-q2a-chips").innerHTML = '<span class="tipc"' + noteAttr(glNote("MAE") + " Here it is measured on the half-hourly wholesale electricity price.") + ">average error (MAE), A$/MWh · lower is better</span>" +
    '<span class="tipc"' + noteAttr("Yesterday's price for the same half-hour: a forecast anyone could make at 09:00, so the model has to beat it to be worth using.") + ">simple baseline: repeat yesterday's prices</span>" +
    '<span class="tipc r"' + noteAttr(glNote("BRIER") + " Here it scores the chance that a half-hour clears at A$200/MWh or more; the baseline's chance is the share of half-hours that spiked in the previous 28 days.") + ">spike baseline: how often spikes happened in the last 28 days</span>";
  $("#ev-matrix").innerHTML = evMatrix();
  $("#ev-mx-chips").innerHTML = '<span class="tipc"' + noteAttr(siteNote(5)) + ">every input was public by 09:00</span>" +
    '<span class="tipc r"' + noteAttr("Test period 1 Jul – 22 Oct 2026, 114 days. Reviewed: " + glNote("TREV") + " Blind: " + glNote("TBLIND") + " " +
      glName("HOTSEASON") + ": " + glNote("HOTSEASON") + " For this battery it matters most: peak prices and the capacity charge happen then, so the test has not yet covered its most valuable days. The first Hot Season after the training period is 1 Dec 2026 – 31 Mar 2027.") +
    ">114 test days: 107 reviewed, 7 blind · no Hot Season (Dec–Mar) yet</span>";
}

/* hover table for a dot-plot row (tipShow in engine.js reads TIPS[data-tip]) */
TIPS.evd = function(k){
  var p = EV_PER[k][0], b = VAL[p].q2b;
  return {h:EV_PER[k][1] + " · " + evDates(p) + " · " + VAL[p].days + " days", s:evPerNote(p), r:[["", "", "Extra profit, average", evSgn(b[0] - b[1]) + "/day"], ["", "", "95% confidence range", evSgn(b[2][0]) + " to " + evSgn(b[2][1])],
    ["sq", "var(--bar)", "Previous dispatcher (v1)", "A$" + nf(b[1])], ["sq", "var(--ok)", "This dispatcher", "A$" + nf(b[0])], ["", "", "If prices were known in advance", "A$" + nf(b[3])]]};
};
/* a row is picked by click, Enter or Space; arrow keys move between rows. The chart is redrawn, so focus is put back */
function evPick(p, refocus){
  S.cper = p; render();
  if (refocus) { var h = document.querySelector('#ev-dots .hit[data-p="' + p + '"]'); if (h) h.focus(); }
}
$("#ev-dots").addEventListener("click", function(e){ var h = e.target.closest ? e.target.closest(".hit[data-p]") : null; if (h) evPick(h.getAttribute("data-p")); });
$("#ev-dots").addEventListener("keydown", function(e){
  var h = e.target.closest ? e.target.closest(".hit[data-p]") : null; if (!h) return;
  var i = +h.getAttribute("data-i"), d = e.key === "ArrowDown" ? 1 : (e.key === "ArrowUp" ? -1 : 0);
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); evPick(h.getAttribute("data-p"), true); }
  else if (d && EV_PER[i + d]) { e.preventDefault(); evPick(EV_PER[i + d][0], true); }
});

SCREENS.evidence = {t:"Evidence · site battery", chips:[["step","2 · Prove"]], ctx:function(){ return "Past days replayed · method built on the training period"; }};
PAGES.evidence = renderEvidence;
