/* Customer fleet · Decide (round 5): tonight's call in one line, the call budget as one bar, the call rule drawn on the
   system demand forecast, the season underneath. Full wording sits in hover notes (data-note). Hatching means "not known,
   or not yet happened, at the 09:00 decision" and nothing else. Runs after engine.js, inside the same function scope.
   Every number is the round-4 placeholder: nothing here is recomputed from inputs.
   Wording follows ROUND5.md section 7: plain labels, acronyms spelled out in a hover note, periods with their dates. */

/* hover notes shared by several labels on this page: the shared glossary (GLOSS in engine.js), plus this page's dates */
var CL_NOTE = {
  hot:glNote("HOTSEASON") + " Hot Season 2025-26: 1 Dec 2025 – 31 Mar 2026.",
  ircr:glName("IRCR") + ": " + glNote("IRCR"),
  hind:glNote("HINDSIGHT"),
  aemo:glName("AEMO") + ": " + glNote("AEMO") + " This system demand forecast is published before 09:00.",
  /* the window is the model's, not the market's */
  win:hhmm(19) + "–" + hhmm(23) + " is an assumption of this model, not a market rule; the official peak half-hours have fallen in this window in past seasons.",
  /* why the threshold is the 4th-highest peak, and where 85 MW comes from */
  thr:glNote("FLAGGED") + " " + glNote("PEAKDAY") + " The 4th-highest peak is used because a Hot Season has 4 official peak days.",
  cy:" Contract year 2025-26: 1 Jul 2025 – 30 Jun 2026.",
  year:glName("CONTRACTYEAR") + ": " + glNote("CONTRACTYEAR") + " Training period: 1 Apr 2025 – 30 Jun 2026."
};
function clHind(){ return '<span class="hind"' + noteAttr(CL_NOTE.hind) + ">Hindsight</span>"; }

/* the selected day as the page reads it: the decision, what it uses and what it pays */
function clState(d){
  var o = OFF[S.offer], calls = callsOf(o), called = calls.indexOf(d) >= 0, rsp = o.enrolled * RESP;
  return {o:o, d:d, calls:calls, called:called, flagged:o.flags.indexOf(d) >= 0, peak:PEAK.indexOf(d) >= 0,
    used:calls.filter(function(c){ return c < d; }).length, r4:run4(d), thr:run4(d) - o.tol,
    rsp:rsp, kwh:rsp * o.kwh, comp:called ? rsp * o.kwh * o.rate : 0, daysLeft:DAYS - d};
}
function clChip(t, note, cls){ return '<span class="tipc' + (cls ? " " + cls : "") + '"' + noteAttr(note) + ">" + t + "</span>"; }

/* ---------- the decision row ---------- */
var CL_EV = hhmm(19) + "–" + hhmm(23), CL_SB = hhmm(15) + "–" + hhmm(19);
/* season review only: did the call cover an official peak day? Known after the season, so it is labelled hindsight */
function clOutcome(c){
  var k = c.peak ? (c.called ? ["ok", "&#10003;", "Official peak day · covered"] : ["bad", "!", "Official peak day · missed"]) : ["mu", "–", "Not an official peak day"];
  var note = "Known only after the season. " + (c.peak ? "One of" : "Not one of") + " the 4 official peak days of Hot Season 2025-26 (1 Dec 2025 – 31 Mar 2026)." +
    (c.peak ? (c.called ? " This call covered it." : " No call covered it, so its capacity value was missed.") : (c.called ? " The call paid customers without capacity value." : "")) +
    " " + glNote("PEAKDAY") + " " + CL_NOTE.ircr;
  return '<span class="tipc cl-oc ' + k[0] + '"' + noteAttr(note) + '><b aria-hidden="true">' + k[1] + "</b>" + k[2] + "</span>" + clHind();
}
function clLeft(c){
  var h = [];
  if (S.view === "review") h.push(clOutcome(c));
  if (c.called) h.push(clChip("hold charge " + CL_SB + " · 20% customer floor kept", glNote("HOLDCHARGE") + " " + glNote("CUSTFLOOR")));
  else if (c.flagged) h.push(clChip("flagged by the call rule · no calls left", "The call rule flagged today, but all " + c.o.cap + " calls of this contract year (1 Jul 2025 – 30 Jun 2026) are used. " + glNote("FLAGGED"), "bad"));
  h.push(clChip("price spikes don't trigger calls", "Price spike risk today: " + (c.d % 4 === 0 ? "high" : "low") + ". Price spikes only drive the site battery's offers. What triggers a call is the peak-day rule: " + glNote("FLAGGED") +
    " Why peak days: " + CL_NOTE.ircr));
  return h.join("");
}
function clRight(c){
  var o = c.o, v = '<div class="cap">Expected payment to customers' + (c.called ? " for this call" : "") + '</div><div class="dv' + (c.called ? "" : " off") + '">' + aud(c.comp) + '</div><div class="chips">';
  if (c.called) v += clChip("payment to customers · transfer", glNote("TRANSFER") + " Paid on billed event net export: " + nf(o.rate * 100) + " c/kWh × " + nf(c.kwh) + " kWh (" + nf(o.kwh, 1) + " kWh × " + nf(c.rsp) + " homes).", "r") +
    clChip(nf(c.rsp) + " of " + nf(o.enrolled) + " enrolled homes expected to respond · average, not guaranteed", glNote("RESPONSE") + " " + glNote("ENROLLED"), "r");
  else v += clChip("no call used", "Normal self-use: each battery runs for its own household. Nothing is paid to customers and no call is used.", "r");
  return v + "</div>";
}

/* ---------- the call budget: one cell per call in the contract year's cap ---------- */
function clBudget(c){
  var o = c.o, dec = S.view === "decision", tonight = dec && c.called ? 1 : 0, solid = dec ? c.used : c.calls.length, cells = [], i;
  for (i = 0; i < o.cap; i++) cells.push('<i class="' + (i < solid ? "u" : (i < solid + tonight ? "t" : "")) + (!dec && c.called && i === c.used ? " cur" : "") + '"></i>');
  /* each label: [text, hover note or ""] */
  var lab = dec ? [["<b>" + (c.used + tonight) + "</b> of " + o.cap + " calls used this contract year", CL_NOTE.year], ["<b>" + (o.cap - c.used - tonight) + "</b> left", ""], [c.daysLeft + " Hot Season days remain", CL_NOTE.hot]]
                : [["<b>" + c.calls.length + "</b> of " + o.cap + " calls used", CL_NOTE.year], ["whole Hot Season · with hindsight", CL_NOTE.hind]];
  var aria = dec ? c.used + " calls used before today" + (tonight ? ", tonight's call not yet happened" : "") + ", " + (o.cap - c.used - tonight) + " left of the cap of " + o.cap : c.calls.length + " of the cap of " + o.cap + " calls used in Hot Season 2025-26";
  return '<span class="cl-bcap"' + noteAttr("The cap: at most " + o.cap + " calls per contract year 2025-26 (1 Jul 2025 – 30 Jun 2026); one cell per call. The days remaining are counted in Hot Season 2025-26 (1 Dec 2025 – 31 Mar 2026). Solid: calls before this day; hatched: tonight's call, not yet happened at 09:00.") + ">Cap: " + o.cap + " calls</span>" +
    '<span class="cl-cells" role="img" aria-label="' + aria + '">' + cells.join("") + "</span>" +
    '<span class="cl-bl">' + lab.map(function(t){ return "<span" + (t[1] ? ' class="nt"' + noteAttr(t[1]) : "") + ">" + t[0] + "</span>"; }).join("") + "</span>";
}

/* ---------- the season: daily peaks, the rule's flags and the calls; the decision view stops at 09:00 today ---------- */
function actSVG(){
  var o = OFF[S.offer], dec = S.view === "decision", d0 = S.day, calls = callsOf(o), s = [], d;
  var L = 62, W = fitW("#act-chart", 560, 792), XE = W - 20, DW = (XE - L) / DAYS, BW = Math.max(2, DW - 2), xa = function(d){ return L + DW * d; };
  var ya = function(v){ return 156 - (v - 2600) * 0.08; };
  s.push('<svg viewBox="0 0 ' + W + ' 206" class="lanes chart" data-tip="act" data-xg="season" data-x0="' + L + '" data-iw="' + DW.toFixed(3) + '" role="img" aria-label="Hot Season 2025-26 daily peak demand, flags and calls (illustrative)">' + "<defs>" + HATCH("aF") + "</defs>");
  [[3000,"3,000"],[3500,"3,500"],[4000,"4,000"]].forEach(function(g){ s.push(ln(L, ya(g[0]), XE, ya(g[0]), 'class="gl"') + tx(L - 8, ya(g[0]) + 3.5, g[1], "", ' text-anchor="end"')); });
  s.push(yTitle(14, 92, "Daily peak, MW"));
  for (d = 0; d < DAYS; d++) {
    if (dec && d > d0) break;
    var fl = o.flags.indexOf(d) >= 0;
    if (dec && d === d0) { var f = fcPeak(d); s.push(rc(xa(d) + 1, ya(f), BW, 156 - ya(f), 'style="fill:var(--surface);stroke:var(--peak)" stroke-width="1.2" stroke-dasharray="2 1.5"')); continue; }
    s.push(rc(xa(d) + 1, ya(ACT[d]), BW, 156 - ya(ACT[d]), fl ? 'style="fill:var(--peak)" opacity="0.8"' : 'style="fill:var(--bar)"'));
    if (!dec && PEAK.indexOf(d) >= 0) s.push(tx(xa(d) + DW / 2, ya(ACT[d]) - 4, "★", "", ' text-anchor="middle" style="font-size:12px;fill:var(--peak)"'));
  }
  /* the rest of the season is not known at the decision time: say so where the bars would be */
  if (dec && d0 < DAYS - 1) {
    s.push(rc(xa(d0 + 1), 20, xa(DAYS) - xa(d0 + 1), 136, 'fill="url(#aF)"'));
    var cx = (xa(d0 + 1) + xa(DAYS)) / 2;
    if (xa(DAYS) - xa(d0 + 1) > 240) s.push(tx(cx, 84, "Not yet known at the 09:00 decision", "", ' text-anchor="middle" style="font-size:12.5px;font-weight:600;fill:var(--text-3)"') + tx(cx, 101, "Switch to “Whole season · with hindsight” to see the rest", "", ' text-anchor="middle" style="font-size:11px;fill:var(--text-3)"'));
  }
  if (dec) {
    var thr = run4(d0) - o.tol, xe = xa(d0) + 10;
    s.push(tx(xa(d0) + DW / 2, ya(fcPeak(d0)) - 6, "Today", "halo", ' text-anchor="middle" style="font-size:10.5px;font-weight:600;fill:var(--peak)"'));
    /* late in the season there is no room right of today: the label then starts the line, above December's low bars */
    var tl = "call threshold today " + nf(thr) + " MW", tst = ' style="font-size:10.5px;font-weight:600;fill:var(--peak)"';
    s.push(ln(L, ya(thr), xe, ya(thr), 'style="stroke:var(--peak)" stroke-width="1.2" stroke-dasharray="2 2"') +
      (fits("← " + tl, 10.5, XE - xe - 4) ? tx(xe + 4, ya(thr) + 3.5, "← " + tl, "halo", tst) : tx(L + 4, ya(thr) - 5, tl, "halo", tst)));
  } else {
    s.push(rc(xa(d0), 20, DW, 136, 'style="fill:var(--sel)"'));
  }
  s.push(ln(L, 156, XE, 156, 'style="stroke:var(--axis)"') + tx(L - 8, 173.5, "Calls", "", ' text-anchor="end" style="font-size:11px;fill:var(--text-3)"'));
  calls.forEach(function(c){ if (dec && c > d0) return; var today = dec && c === d0;
    s.push('<circle cx="' + (xa(c) + DW / 2).toFixed(1) + '" cy="170" r="' + (today ? 3.5 : 3) + '" style="fill:' + (today ? "var(--surface);stroke:var(--cust)" : "var(--cust)") + '"' + (today ? ' stroke-width="1.6"' : "") + "/>"); });
  [["Dec 2025",0],["Jan 2026",31],["Feb",62],["Mar",90]].forEach(function(m){ s.push(ln(xa(m[1]), 156, xa(m[1]), 161, 'style="stroke:var(--axis)"') + tx(xa(m[1]), 194, m[0])); });
  s.push('<line class="xh" x1="0" x2="0" y1="20" y2="176"/>');
  for (d = 0; d < DAYS; d++) s.push('<rect class="hit" data-d="' + d + '" x="' + xa(d).toFixed(2) + '" y="20" width="' + DW.toFixed(2) + '" height="156"/>');
  s.push("</svg>");
  return s.join("");
}
/* the only legend on the page: the season chart encodes four things in a small space */
function clLegend(dec){
  /* flagged and called differ: a flagged day is called only while calls remain in the contract year's cap */
  return '<span><i class="sw" style="background:var(--bar)"></i>Daily peak</span>' +
    '<span' + noteAttr("Flagged: " + glNote("FLAGGED") + " A flagged day is not always called: once the cap is used, flagged days get no call.") + '><i class="sw" style="background:var(--peak);opacity:.8"></i><u>Flagged by the call rule</u></span>' +
    '<span' + noteAttr("Called: flagged and a call was still available, so customers' home batteries discharged together " + CL_EV + ". " + glNote("CALL") + CL_NOTE.cy) + '><i class="sw" style="background:var(--cust);border-radius:50%;width:8px"></i><u>Called</u></span>' +
    (dec ? "" : '<span class="r"' + noteAttr(glNote("PEAKDAY") + " " + CL_NOTE.ircr) + '><b aria-hidden="true" style="color:var(--peak)">&#9733;</b><u>Official peak day · sets the capacity charge</u></span>');
}

/* ---------- the day: system demand against the call threshold, the rule written where it applies ---------- */
/* intraday system demand: the 14 Jan profile scaled to each day's peak (illustrative) */
function demDay(d, actual){ var pk = actual ? ACT[d] : fcPeak(d); return DEMFC.map(function(v){ return 2350 + (v - 2350) * (pk - 2350) / 1520; }); }
function callWin(d){ return callsOf(OFF[S.offer]).indexOf(d) >= 0; }
function demSVG(sel){
  var c = clState(S.day), d = c.d, dec = S.view === "decision", fc = demDay(d, false), ac = demDay(d, true);
  var W = setW(fitW(sel, 560, 852)), XR = X(N), s = [], H = 248, y0 = 28, y1 = H - 40, k;
  var fpk = Math.max.apply(null, fc), apk = Math.max.apply(null, ac), kp = fc.indexOf(fpk), px = X(kp) + IW / 2;
  var top = Math.max(4100, Math.ceil((Math.max(fpk, dec ? 0 : apk, c.thr) + 60) / 100) * 100);
  var yd = function(v){ return y1 - (v - 2000) * (y1 - y0) / (top - 2000); };
  var smooth = function(vals){ var p = ""; for (k = 0; k < N; k++) p += (k ? "L" : "M") + (X(k) + IW / 2).toFixed(2) + "," + yd(vals[k]).toFixed(2); return p; };
  var dot = function(x, y, filled){ return '<circle cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="3.5" style="fill:' + (filled ? "var(--peak)" : "var(--surface);stroke:var(--peak)") + '"' + (filled ? "" : ' stroke-width="1.6"') + "/>"; };
  s.push(svgOpen(W, H, "dem", "System demand for " + dayName(d, true) + " against the customer call threshold " + nf(c.thr) + " MW, the 4th-highest daily peak so far (" + nf(c.r4) + ") minus a " + c.o.tol + " MW margin. " + CL_NOTE.thr + (c.called ? " Customer call " + CL_EV + ": " + CL_NOTE.win : "") + " (illustrative)"));
  [2000, 2500, 3000, 3500, 4000].forEach(function(v){ s.push(ln(X0, yd(v), XR, yd(v), v > 2000 ? 'class="gl"' : 'style="stroke:var(--axis)"') + tx(X0 - 8, yd(v) + 3.5, nf(v), "", ' text-anchor="end"')); });
  s.push(yTitle(14, (y0 + y1) / 2, "Whole-system demand, MW"));
  /* the customer call and the hold-charge period before it, when there is one; native SVG titles carry the notes */
  if (c.called) s.push("<g><title>" + glNote("HOLDCHARGE") + " " + glNote("CUSTFLOOR") + "</title>" + rc(X(15), y0, X(19) - X(15), y1 - y0, 'style="fill:var(--cust)" opacity="0.05"') + tx(mid(15, 19), y1 - 8, "Hold charge", "vl", ' text-anchor="middle" style="fill:var(--cust-text)" opacity="0.8"') + "</g>" +
    "<g><title>" + CL_NOTE.win + "</title>" + rc(X(19), y0, X(23) - X(19), y1 - y0, 'style="fill:var(--cust)" opacity="0.14"') + tx(mid(19, 23), y1 - 8, "Call " + CL_EV, "vl", ' text-anchor="middle" style="fill:var(--cust-text)"') + "</g>");
  /* the rule: the threshold line, and how it is set, at the line's end */
  s.push("<g><title>" + CL_NOTE.thr + "</title>" + ln(X0, yd(c.thr), XR, yd(c.thr), 'style="stroke:var(--peak)" stroke-width="1.3" stroke-dasharray="1.5 3"') +
    tx(XR - 2, yd(c.thr) - 6, "Call threshold " + nf(c.thr) + " MW", "lim halo", ' text-anchor="end" style="fill:var(--peak);font-weight:600"') +
    tx(XR - 2, yd(c.thr) + 14, "= 4th-highest daily peak so far (" + nf(c.r4) + ") − " + c.o.tol + " MW margin", "lim halo", ' text-anchor="end"') + "</g>");
  /* the lines are named at their peaks, where the rule compares them: the two lines meet at both ends of the day, and
     near the peak they run a few pixels apart, so the names stack above both peaks in the order of the lines, each with
     a short sample of its line (dashed forecast, solid actual) */
  s.push('<path d="' + smooth(fc) + '" fill="none" style="stroke:var(--peak)" stroke-width="1.8" stroke-dasharray="5 3"/>');
  if (!dec) s.push('<path d="' + smooth(ac) + '" fill="none" style="stroke:var(--peak)" stroke-width="2"/>' + dot(px, yd(apk), true));
  s.push(dot(px, yd(fpk), false));
  var names = [[fpk, false, "Forecast peak " + nf(fpk) + " MW" + (dec ? (fpk >= c.thr ? " · above threshold" : " · below threshold") : "")]];
  if (!dec) names.push([apk, true, "Actual peak " + nf(apk) + " MW · hindsight"]);
  names.sort(function(a, b){ return a[0] - b[0]; });
  var ny = Math.min(yd(fpk), dec ? Infinity : yd(apk)) - 9, nx = px + 12;
  names.forEach(function(nm, j){
    var y = ny - 15 * j, t = dec ? 0 : 22;
    if (t) s.push(ln(nx, y - 4, nx + 16, y - 4, 'style="stroke:var(--peak)" stroke-width="2"' + (nm[1] ? "" : ' stroke-dasharray="4 2"')));
    s.push(tx(nx + t, y, nm[2], "halo vl", ' style="fill:var(--peak)"'));
  });
  /* the decision time: the forecast was issued before it */
  s.push(ln(X(2), y0 - 6, X(2), y1, 'style="stroke:var(--mark)" stroke-width="1"') + tx(X(2), y0 - 10, "09:00 DECISION", "mk9", ' text-anchor="middle"'));
  return s.join("") + dayAxis(y1, d) + hitCols(y0, y1) + "</svg>";
}

/* ---------- the page ---------- */
function renderCalls(){
  var c = clState(S.day), dec = S.view === "decision";
  $("#day-label").textContent = dayName(S.day, true);
  /* the headline says once, in plain words, what a call is; the pill's note is the glossary entry */
  $("#call-head").innerHTML = '<span class="dec-pill' + (c.called ? "" : " no") + '"' + noteAttr((c.called ? "Call tonight: " : "No call tonight: ") + glNote("CALL") + CL_NOTE.cy) + ">" + (c.called ? "CALL" : "NO CALL") + "</span>" +
    (c.called ? '<span class="nt"' + noteAttr(CL_NOTE.win) + ">Home batteries discharge " + CL_EV + "</span>" : "<span>Normal self-use</span>");
  $("#call-chips").innerHTML = clLeft(c);
  $("#call-val").innerHTML = clRight(c);
  $("#call-budget").innerHTML = clBudget(c);
  $("#act-dem-h").textContent = dec ? "System demand forecast" : "System demand";
  /* the subtitle names the source; its note spells out AEMO */
  var sub = $("#act-dem-sub");
  sub.textContent = dec ? "AEMO forecast, before 09:00" : "AEMO forecast and actual · hindsight";
  sub.tabIndex = 0;
  sub.setAttribute("data-note", CL_NOTE.aemo + (dec ? "" : " The actual line is known only after the fact."));
  $("#act-dem").innerHTML = demSVG("#act-dem");
  $("#act-tag").innerHTML = dec ? "" : clHind();
  $("#act-legend").innerHTML = clLegend(dec);
  $("#act-chart").innerHTML = actSVG();
  $$("#act-chart .hit").forEach(function(r){ r.addEventListener("click", function(){ S.day = +r.getAttribute("data-d"); render(); }); });
}

TIPS.dem = function(k){
    var d = S.day, dec = S.view === "decision", r = [["dsh", "var(--peak)", "Forecast", nf(demDay(d, false)[k]) + " MW"]];
    if (!dec) r.push(["ln", "var(--peak)", "Actual · hindsight", nf(demDay(d, true)[k]) + " MW"]);
    r.push(["", "", "Call threshold", nf(run4(d) - OFF[S.offer].tol) + " MW"]);
    if (callWin(d) && k >= 15 && k < 19) r.push(["sq", "color-mix(in srgb,var(--cust) 30%,var(--surface))", "Customer batteries", "hold charge"]);
    if (callWin(d) && k >= 19 && k < 23) r.push(["sq", "var(--cust)", "Customer batteries", "called"]);
    return {h:hhmm(k) + "–" + hhmm(k + 1), s:dayName(d, true), r:r};
};
TIPS.act = function(d){
    var o = OFF[S.offer], dec = S.view === "decision", calls = callsOf(o), r = [];
    if (dec && d > S.day) return {h:dayName(d, true), s:"Not yet known at 09:00 on " + dayName(S.day).replace(/^\w+ /, ""), r:[]};
    if (dec && d === S.day) r.push(["dsh", "var(--peak)", "Forecast daily peak", nf(fcPeak(d)) + " MW"]);
    else r.push(["sq", o.flags.indexOf(d) >= 0 ? "var(--peak)" : "var(--bar)", "Daily peak demand", nf(ACT[d]) + " MW"]);
    r.push(["", "", "Flagged by the call rule", o.flags.indexOf(d) >= 0 ? "Yes" : "No"]);
    r.push(["", "", "Customer batteries called", calls.indexOf(d) >= 0 ? "Yes" : "No"]);
    if (!dec) r.push(["", "", "Official peak day · hindsight", PEAK.indexOf(d) >= 0 ? "Yes" : "No"]);
    return {h:dayName(d, true), s:dec ? (d === S.day ? "Today · decided at 09:00" : "Known by 09:00") : "Whole season · hindsight", r:r};
};
$("#day-prev").addEventListener("click", function(){ S.day = Math.max(0, S.day - 1); render(); });
$("#day-next").addEventListener("click", function(){ S.day = Math.min(DAYS - 1, S.day + 1); render(); });
segBind("data-view", "view");
/* the season heading is the Hot Season's first appearance in the page body: it carries the dates and the note */
$("#act-h").tabIndex = 0;
$("#act-h").setAttribute("data-note", CL_NOTE.hot);
$('[data-view="review"]').setAttribute("data-note", CL_NOTE.hind);
/* the header is rebuilt on every render, so the training-period note names the selected day */
SCREENS.calls = {t:"Tonight's call",
  get chips(){ return [["step","1 · Decide"],
    ["","training-period day", dayName(S.day, true).replace(/^\w+ /, "") + " lies in the training period: the method saw this day while it was being built. " + glNote("TRAIN")],
    ["","Hot Season 2025-26 (Dec–Mar)", CL_NOTE.hot]]; },
  ctx:function(){ var o = OFF[S.offer]; return "Pays " + nf(o.rate * 100) + " c/kWh (" + (o === OFF.custom ? "model suggestion" : "reference terms") + ") · decided 09:00"; }};
PAGES.calls = renderCalls;
