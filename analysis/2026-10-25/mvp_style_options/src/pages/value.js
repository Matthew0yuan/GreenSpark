/* Customer fleet · Improve, scheme A (round 5, 2026-10-27). One question: how much more does the custom offer earn
   Zenith than the reference terms, are customers better off than normal self-use, and under which scenario?
   Every number comes from evalOffer and OFF; this file only arranges them. It runs after engine.js inside the same
   function scope and registers SCREENS.value and PAGES.value. Full wording sits in hover notes (data-note).
   Wording follows ROUND5.md section 7: plain labels, every acronym spelled out with a hover note, every period with
   its dates and days. Hover notes come from the shared glossary (GLOSS, gl, glNote, glName in engine.js); a page note
   only appends this page's numbers. Some engine texts (evalOffer row names and bases, OFF.src, verdict.p) still use old
   words (standby, policy C, customer reserve), so this page rewords them for display in V_ROWNAME and vBasis. */

/* a hover chip as on the plan page: a few words on the page, the full basis on hover or keyboard focus */
function vChip(t, note, cls){ return '<span class="tipc' + (cls ? " " + cls : "") + '"' + noteAttr(note) + ">" + t + "</span>"; }
/* a status item: icon and a short name; ✕ and ! carry the meaning as well as the colour */
function vStat(k, t, note){ var g = {ok:"&#10003;", w:"!", b:"&#10005;", u:"&#8211;"}[k]; return '<div class="ck ' + k + '" role="listitem"' + noteAttr(note) + '><span class="ci ' + k + '">' + g + "</span>" + t + "</div>"; }
function vOther(sc){ return sc === "S3" ? "Synergy" : "Other parties"; }
function vVerdict(x, sc){ return x.verdict.k === "ok" ? "Positive in " + sc : "No business case"; }
function vTerms(o){ return nf(o.rate * 100) + " c/kWh · " + o.cap + " calls"; }
/* how each held-out Hot Season was tested (ROUND5.md 7.1) */
var V_FWD = [glName("FORWARD"), glNote("FORWARD"), "FORWARD"];
var V_EARLY = [glName("EARLIERSEASON"), glNote("EARLIERSEASON"), "EARLIERSEASON"];
function vHow(k){ return CHRON[k] ? V_FWD : V_EARLY; }
var V_SD = ["1 Dec 2023 – 31 Mar 2024", "1 Dec 2024 – 31 Mar 2025", "1 Dec 2025 – 31 Mar 2026"], V_SN = [122, 121, 121];   /* the Hot Seasons in SEASONS */
function vSeason(x){ return "worst season (" + SEASONS[x.worstIdx] + ") · " + vHow(x.worstIdx)[0]; }
var V_YEAR = "contract year 2025-26 (1 Jul 2025 – 30 Jun 2026, 365 days)";
/* the blind test so far has no Hot Season day, so the capacity value is still untested on unseen data */
var V_BLIND = glNote("TBLIND") + " It has no Hot Season day, so the capacity value is not yet tested blind; first chance: Hot Season 2026-27, 1 Dec 2026 – 31 Mar 2027 (121 days). Hot Season: " + glNote("HOTSEASON");
var V_RULE = "A positive verdict needs customers no worse off than normal self-use, no customer limit broken and no Hot Season losing money for Zenith.";
var V_SHARE = Math.round(RUN.share * 100);
function vName(sc){ return GLOSS[sc] ? glName(sc) : SCN[sc].label; }
/* the scenario note: GLOSS text, plus the Synergy access arrangement (TPA) for S3 */
function vScen(sc){ return glNote(sc) + (sc === "S3" ? " " + glName("TPA") + ": " + glNote("TPA") : ""); }
/* the verdict's full reason. evalOffer's own text (verdict.p) still names the old scenario labels, so a positive verdict
   is worded here; a failing one uses evalOffer's reason or, with no calls, the S1 / S2a reason from the toolbar */
function vNoScen(){ return glNote("S1") + " " + glNote("S2a"); }
function vVerdictNote(x, sc){
  if (x.calls === 0) return vNoScen();
  if (x.verdict.k !== "ok") return x.verdict.p;
  return (sc === "S3" ? "Depends on Zenith keeping an assumed " + V_SHARE + "% of the capacity value through Synergy. " : sc === "S2b" ? "Depends on an assumed future rule for the capacity charge (IRCR) from 2027; not current revenue. " : "") +
    "Customers are better off than normal self-use, no customer limit is broken (yearly call cap, max hours Zenith controls a battery per call, customer floor) and no Hot Season loses money. The capacity value has not yet been tested blind; enrolment follows an assumed sign-up curve.";
}

/* the three options on one axis. No calls is the baseline every other number is measured against, so it sits at 0 */
function vCust(x){ var v = x.c / x.enrolled; return "customers " + (v > 0.0005 ? "+" : "") + aud(v, 2) + " per enrolled home per year"; }
function vOptions(sc){
  var a = evalOffer("ref", sc), r = evalOffer("custom", sc), cap = callsOf(OFF.ref);
  return [
    {k:"none", name:"No calls", terms:"baseline", z:0, worst:0, calls:"0 calls", peaks:"", cust:"customers keep normal self-use", pill:["mu", "Baseline"]},
    {k:"ref", x:a, name:OFF.ref.name, terms:vTerms(OFF.ref), z:a.z, worst:a.worst,
      calls:a.calls + " of " + OFF.ref.cap + " calls used" + (a.calls >= OFF.ref.cap ? " · cap used up by " + dayName(cap[cap.length - 1]).replace(/^\w+ /, "") : ""),
      peaks:a.peaks[2] + " of 4 official peak days covered · known only afterwards", cust:vCust(a), pill:[a.verdict.k, vVerdict(a, sc)]},
    {k:"custom", x:r, name:OFF.custom.name, terms:vTerms(OFF.custom), z:r.z, worst:r.worst,
      calls:r.calls + " of " + OFF.custom.cap + " calls used" + (r.calls >= OFF.custom.cap ? " · cap used up" : ""),
      peaks:r.peaks[2] + " of 4 official peak days covered · known only afterwards", cust:vCust(r), pill:[r.verdict.k, vVerdict(r, sc)]}];
}
function niceStep(range, n){ var raw = range / n, mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10)); return [1, 2, 2.5, 5, 10].filter(function(f){ return f * mag >= raw; })[0] * mag; }
function kAud(v){ if (!v) return "0"; var a = Math.abs(v); return (v < 0 ? "−" : "") + "A$" + (a >= 1e6 ? nf(a / 1e6, 2).replace(/\.?0+$/, "") + "m" : nf(a / 1e3) + "k"); }

/* Primary chart: per option two bars on a shared axis with a zero line. The upper bar is Zenith's incremental profit in
   the contract year, the lower one its worst Hot Season in the holdouts; a loss runs left of zero in red. Labels sit at
   the bar ends (no legend); the verdict closes each row. The scale leaves room for every label inside the plot. */
function vOptSVG(sel, sc){
  var opts = vOptions(sc), W = fitW(sel, 720, 1240), LW = 330, PW = 150, xL = LW, xR = W - PW - 12, pad = 10;
  var RH = 86, top = 28, yA = top + 3 * RH, H = yA + 40, BH = 18, FS = 11, s = [];
  var lw = function(t){ return t.length * FS * 0.54 + 12; };
  var bars = [];
  opts.forEach(function(o){ if (o.x) bars.push({v:o.z, t:"Zenith extra profit " + aud(o.z) + " · contract year 2025-26"}, {v:o.worst, t:aud(o.worst) + " · " + vSeason(o.x)}); });
  var lo = Math.min.apply(null, [0].concat(bars.map(function(b){ return b.v; }))), k = Infinity;
  bars.forEach(function(b){ var need = Math.max(b.v, 0) - lo; if (need > 0) k = Math.min(k, (xR - xL - pad - lw(b.t)) / need); });
  if (!isFinite(k) || k <= 0) k = 1e-3;
  var x0 = xL + pad - lo * k, hi = (xR - x0) / k, xv = function(v){ return x0 + v * k; };
  s.push('<svg viewBox="0 0 ' + W + " " + H + '" class="lanes vopt" data-tip="vopt" role="img" aria-label="Extra Zenith profit vs Zenith without the fleet, in ' + V_YEAR + ' and in its worst Hot Season (Dec–Mar), for no calls, reference terms and the custom offer, ' + vName(sc) + ' (illustrative)">');
  /* the custom offer is the row under review, so it carries the highlight */
  s.push(rc(0, top + 2 * RH + 2, W, RH - 4, 'rx="6" style="fill:var(--accent-soft)"'));
  [1, 2].forEach(function(i){ s.push(ln(0, top + i * RH, W, top + i * RH, 'style="stroke:var(--divider)"')); });
  /* axis: a light grid, a strong zero line, the unit and the baseline in the header */
  var step = niceStep(hi - lo, 7);
  for (var v = Math.ceil(lo / step) * step; v <= hi + 1e-6; v += step) {
    var gx = xv(v); if (gx < xL - 1 || gx > xR + 1) continue;
    if (Math.abs(v) > 1e-6) s.push(ln(gx, top - 4, gx, yA, 'class="gl"'));
    s.push(tx(gx, yA + 15, kAud(v), "", ' text-anchor="middle"'));
  }
  s.push(ln(x0, top - 8, x0, yA + 4, 'class="v-zero" style="stroke:var(--axis-strong)" stroke-width="1.2"'));
  s.push(tx(xL, 14, "Extra Zenith profit, A$ · vs Zenith without the fleet", "lnm") + tx(W - 2, 14, "Verdict", "lnm", ' text-anchor="end"'));
  opts.forEach(function(o, i){
    var y = top + i * RH, g = ['<g class="vrow" data-k="' + o.k + '">'];
    g.push('<text x="12" y="' + (y + 22) + '"><tspan class="vo-n">' + o.name + '</tspan><tspan class="vo-t" dx="8">' + o.terms + "</tspan></text>");
    g.push(tx(12, y + 39, o.calls, "vo-s") + (o.peaks ? tx(12, y + 54, o.peaks, "vo-s") : "") + tx(12, o.peaks ? y + 69 : y + 54, o.cust, "vo-c"));
    if (!o.x) g.push(tx(x0 + 6, y + RH / 2 + 4, '<tspan class="vo-vn">' + aud(0) + "</tspan> · the baseline both offers are measured against", "vo-v halo"));
    else [[o.z, "var(--zen)", "contract year 2025-26", "z", "Zenith extra profit "], [o.worst, o.worst < 0 ? "var(--bad)" : "color-mix(in srgb,var(--zen) 38%,var(--surface))", vSeason(o.x), "worst", ""]].forEach(function(b, j){
      var by = y + RH / 2 - BH - 3 + j * (BH + 6), bx = xv(b[0]), lx = (b[0] > 0 ? bx : x0) + 6;
      g.push(rc(Math.min(x0, bx), by, Math.abs(bx - x0), BH, 'rx="2" class="vbar" data-s="' + b[3] + '" data-v="' + b[0] + '" style="fill:' + b[1] + '"'));
      g.push(tx(lx, by + BH / 2 + 4, b[4] + '<tspan class="vo-vn' + (b[0] < 0 ? " vo-neg" : "") + '">' + aud(b[0]) + "</tspan> · " + b[2], "vo-v halo"));
    });
    var pk = o.pill[0], pt = o.pill[1], pw = pt.length * 11.5 * 0.58 + 22, px = W - 2 - pw, py = y + RH / 2 - 11;
    g.push('<g class="vo-pill ' + pk + '">' + rc(px, py, pw, 22, 'rx="11"') + tx(px + pw / 2, py + 15, pt, "", ' text-anchor="middle"') + "</g>");
    s.push(g.join("") + "</g>");
  });
  /* which holdouts the worst season comes from, and that only one of them is forward in time; hovering the line
     explains the two kinds of test (SVG text cannot carry a CSS hover note, so it uses the chart tooltip) */
  s.push(tx(12, yA + 33, "Worst season: each Hot Season (" + SEASONS.join(", ") + ") left out in turn and tested with a rule tuned on the others; only " + SEASONS[CHRON.indexOf(true)] + " is a forward test, tuned on earlier seasons only. Hover for details.", "vo-t"));
  opts.forEach(function(o, i){ s.push('<rect class="hit" data-i="' + i + '" x="0" y="' + (top + i * RH) + '" width="' + W + '" height="' + RH + '"/>'); });
  s.push('<rect class="hit vo-fn" data-i="3" x="8" y="' + (yA + 20) + '" width="' + (W - 8) + '" height="18"/>');
  return s.join("") + "</svg>";
}
/* hovering a row shows that option's full numbers, including what the customers and the other parties get */
TIPS.vopt = function(i){
  var sc = S.vscen;
  if (i === 3) return {h:"How the worst season is found", s:"Each Hot Season (1 Dec – 31 Mar) is left out in turn and tested with a call rule tuned on the others. Forward test: " + glNote("FORWARD") + " Tested on an earlier season: " + glNote("EARLIERSEASON"),
    r:SEASONS.map(function(sn, k){ return ["", "", sn + " · " + vHow(k)[0], V_SN[k] + " days"]; })};
  var o = vOptions(sc)[i], x = o.x;
  if (!x) return {h:"No calls · baseline", s:"Customer batteries stay in normal self-use. Both offers are measured against this.", r:[["sq", "var(--zen)", "Extra Zenith profit", aud(0)], ["sq", "var(--cust)", "Customer net vs normal self-use", aud(0)]]};
  var w = x.worstIdx;
  return {h:o.name + " · " + o.terms, s:glNote(o.k === "ref" ? "REFTERMS" : "CUSTOMOFFER") + " Contract year 2025-26: 1 Jul 2025 – 30 Jun 2026, 365 days. Worst season: Hot Season " + SEASONS[w] + ", " + V_SD[w] + ", " + V_SN[w] + " days, " +
      (CHRON[w] ? "a forward test: " : "tested on an earlier season: ") + glNote(vHow(w)[2]) + " Official peak days: " + glNote("PEAKDAY"), r:[
    ["sq", "var(--zen)", "Zenith · contract year 2025-26", aud(x.z)],
    ["sq", x.worst < 0 ? "var(--bad)" : "color-mix(in srgb,var(--zen) 38%,var(--surface))", "Zenith · worst season (" + SEASONS[w] + ")", aud(x.worst)],
    ["sq", "var(--cust)", "Customers · vs normal self-use", aud(x.c)],
    ["", "", "Customer net per enrolled home per year", aud(x.c / x.enrolled, 2)],
    ["sq", "var(--oth)", vOther(sc), aud(x.oth)],
    ["", "", "Enrolled homes", nf(x.enrolled)],
    ["", "", "Calls used", x.calls + " of " + x.o.cap],
    ["", "", "Official peak days covered · known only afterwards", x.peaks[2] + " of 4"],
    ["", "", "Verdict", vVerdict(x, sc)]]};
};

/* Who gets what under the custom offer: one bar per party from a shared zero, in the party's colour (a loss in red),
   with the fleet total and the per-enrolled-home figure at the bar end. The label column matches the option chart. */
function vPartySVG(sel, r, sc){
  var W = fitW(sel, 720, 1240), LW = 330, xL = LW, xR = W - 162, pad = 10, RH = 28, top = 2, H = top + 3 * RH + 2, BH = 16, s = [];
  var ps = [["z", "Zenith", "var(--zen)", r.z], ["c", "Customers", "var(--cust)", r.c], ["o", vOther(sc), "var(--oth)", r.oth]];
  var lab = function(v){ return aud(v / r.enrolled, 2) + " per enrolled home per year"; };
  var lo = Math.min(0, r.z, r.c, r.oth), k = Infinity;
  ps.forEach(function(p){ var need = Math.max(p[3], 0) - lo, t = aud(p[3]) + " · " + lab(p[3]); if (need > 0) k = Math.min(k, (xR - xL - pad - t.length * 12 * 0.56 - 14) / need); });
  if (!isFinite(k) || k <= 0) k = 1e-3;
  var x0 = xL + pad - lo * k, xv = function(v){ return x0 + v * k; };
  s.push('<svg viewBox="0 0 ' + W + " " + H + '" class="lanes vopt vpty" role="img" aria-label="Custom offer, ' + vName(sc) + ', ' + V_YEAR + ': Zenith ' + aud(r.z) + ", customers " + aud(r.c) + ", " + vOther(sc) + " " + aud(r.oth) + ' (illustrative)">');
  s.push(ln(x0, top - 2, x0, H - 2, 'class="v-zero" style="stroke:var(--axis-strong)" stroke-width="1.2"'));
  ps.forEach(function(p, i){
    var y = top + i * RH, by = y + (RH - BH) / 2, bx = xv(p[3]), neg = p[3] < -0.0005;
    s.push('<g class="vparty" data-p="' + p[0] + '"><circle cx="16" cy="' + (y + RH / 2) + '" r="4" style="fill:' + p[2] + '"/>' + tx(28, y + RH / 2 + 4.5, p[1], "vo-n"));
    s.push(rc(Math.min(x0, bx), by, Math.abs(bx - x0), BH, 'rx="2" class="vbar" data-v="' + p[3] + '" style="fill:' + (neg ? "var(--bad)" : p[2]) + '"'));
    s.push(tx((p[3] > 0 ? bx : x0) + 8, y + RH / 2 + 4.5, '<tspan class="vo-big' + (neg ? " vo-neg" : "") + '">' + aud(p[3]) + "</tspan> · " + lab(p[3]), "vo-v halo") + "</g>");
  });
  return s.join("") + "</svg>";
}

function renderValue(){
  var sc = S.vscen, r = evalOffer("custom", sc), a = evalOffer("ref", sc), gain = r.z - a.z;
  /* toolbar: scenario names and notes from the glossary; Synergy named once, visibly, with its note */
  $$("[data-vscen]").forEach(function(b){ var k = b.getAttribute("data-vscen"); b.textContent = glName(k); b.setAttribute("data-note", vScen(k)); });
  $("#v-noscen").setAttribute("data-note", vNoScen());
  $("#v-syn").innerHTML = gl("SYNERGY");
  $("#v-f-ircr > summary").setAttribute("data-note", glNote("IRCR"));
  /* decision: the verdict, the gain over the reference terms, and the basis as chips */
  $("#v-pill").className = "pst " + r.verdict.k;
  $("#v-pill").innerHTML = "<b" + noteAttr(V_RULE + " " + vVerdictNote(r, sc)) + ">" + vVerdict(r, sc) + "</b>";
  $("#v-head").innerHTML = '<span class="v-n ' + (gain < 0 ? "neg" : "pos") + '">' + (gain < 0 ? "−" : "+") + aud(Math.abs(gain)).replace("A$ ", "A$") + "</span> " + (gain < 0 ? "Zenith profit vs reference terms" : "extra Zenith profit vs reference terms");
  $("#v-chips").innerHTML =
    vChip("replay of contract year 2025-26 (1 Jul 2025 – 30 Jun 2026)", "A replay (historical simulation) of 365 days. " + glNote("CONTRACTYEAR"), "r") +
    (GLOSS[sc] ? vChip(glName(sc), vScen(sc), "r " + (sc === "S3" ? "hyp" : sc === "S2b" ? "fut" : "")) : vChip(SCN[sc].tag, SCN[sc].label, "r")) +
    /* the first "Hot Season" on the page: its months on the label, what it is in the note */
    vChip("blind test: Hot Season 2026-27 (Dec–Mar)", V_BLIND, "r") +
    gl("RUN", "", "r");
  $("#v-opts").innerHTML = vOptSVG("#v-opts", sc);
  /* who gets what: three separate bars on one axis, never stacked; the baselines are chips beside the heading */
  $("#v-pchips").innerHTML = gl("VPP", "the fleet = customers' home batteries") +
    vChip("Zenith: vs Zenith without the fleet", "Zenith's extra profit: Profit(Zenith + the fleet) − Profit(Zenith without the fleet). Custom offer, whole fleet, " + V_YEAR + ".") +
    vChip("Customers: vs their normal self-use", "The customer baseline is the same battery in normal self-use, not \u201cno battery\u201d. Zenith's payments to customers are a transfer payment: " + glNote("TRANSFER"));
  $("#v-parties").innerHTML = vPartySVG("#v-parties", r, sc);
  /* why Synergy's bar is the longest under S3 */
  $("#v-pnote").innerHTML = sc === "S3" ? vChip("Synergy keeps the energy value and " + (100 - V_SHARE) + "% of the capacity value under S3 · assumption", glNote("S3")) : "";
  /* the verdict's conditions as four status items; a failing verdict leads with its reason */
  var st = [];
  if (r.verdict.k === "bad") st.push(vStat("b", r.calls === 0 ? "No call pays" : "Loses in Hot Season " + SEASONS[r.worstIdx], r.verdict.p));
  else st.push(r.c > 0.0005 ? vStat("ok", "Customers better off", "Customers are better off than the same battery in normal self-use: " + "+" + aud(r.c / r.enrolled, 2) + " per enrolled home per year, after battery wear and disutility.")
                            : vStat("b", "Customers not better off", "Customer net " + aud(r.c / r.enrolled, 2) + " per enrolled home per year against normal self-use."));
  st.push(vStat("ok", "Customer limits held", "Customer limits held: yearly call cap, max hours Zenith controls a battery per call (hold charge + call ≤ 6 h) and customer floor kept: 0 breaks in the replay of " + V_YEAR + "."));
  st.push(sc === "S3" ? vStat("w", "Zenith keeps " + V_SHARE + "% of the capacity value · assumption", vScen("S3")) : (sc === "S2b" ? vStat("w", "Assumed future rule", glNote("S2b")) : vStat("w", SCN[sc].tag, SCN[sc].label)));
  st.push(vStat("u", "Not yet tested blind", V_BLIND));
  $("#v-status").innerHTML = st.join("");
  renderValueFolds(r, sc);
}

var FLEET_CHECKS = [
  ["Yearly call cap", "Calls per contract year stay within the cap: " + OFF.custom.cap + " calls for the custom offer, " + OFF.ref.cap + " for reference terms. " + glNote("CALL")],
  ["Max hours Zenith controls a battery per call", "Hold charge + call ≤ 6 h: up to 2 h of hold charge and a 2 h call. " + glNote("HOLDCHARGE")],
  ["Customer floor kept", glNote("CUSTFLOOR")],
  ["Accounts add up", "The three parties' accounts add up to the system value. Transfer payment: " + glNote("TRANSFER")]];
/* engine row names reworded for display (evalOffer still says standby and events), and their hover notes */
var V_ROWNAME = {"Standby offset credits":"Hold-charge offset credits", "Retail bill change after events":"Retail bill change after calls"};
var V_ROWNOTE = {"Capacity charge (IRCR) saving, per season":glNote("IRCR"), "Feed-in credit (DEBS) change":glNote("DEBS"), "Operating cost":glNote("ENROLLED")};
/* the basis under a ledger row: evalOffer's text, with its old words replaced */
function vBasis(row, sc){
  var n = row[0], t = row[5];
  if (/^Capacity charge/.test(n) && sc === "S3") return "Zenith keeps " + V_SHARE + "% of the capacity value (assumption); the rest stays with Synergy. See \u201cHow the capacity charge (IRCR) saving is calculated\u201d below.";
  if (/^Capacity charge/.test(n) && sc === "S2b") return glNote("S2b") + " Zenith as retailer keeps the whole change.";
  if (n === "Standby offset credits") return "Extra import while the battery holds charge before a call, credited to the customer.";
  if (n === "Retail bill change after events") return "Customers import more after a call; the retailer earns the margin on that extra energy.";
  if (n === "Operating cost" && /380 homes/.test(t)) return "380 homes enrol even at zero expected benefit (assumed sign-up curve); A$25 a year each.";
  return t.replace(/per event/g, "per call");
}
/* the evidence behind the page, folded: ledger, capacity value, own battery, holdouts, customer constraints */
function renderValueFolds(r, sc){
  var oname = vOther(sc), a = evalOffer("ref", sc);
  /* the ledger: who gains what, fleet, contract year; click a row for its basis */
  $("#v-ledger-sub").textContent = "Custom offer · replay of " + V_YEAR + ", inside the training period · " + vName(sc) + " · A$, whole fleet";
  var u = '<tr><th>Item</th><th class="n" style="color:var(--zen)">Zenith</th><th class="n" style="color:var(--cust)">Customers</th><th class="n">' + oname + '</th><th class="n">System</th></tr>';
  var c = function(v){ return '<td class="n' + (v < -0.0005 ? " neg" : "") + '">' + (Math.abs(v) < 0.0005 ? "0" : aud(v)) + "</td>"; };
  r.rows.forEach(function(row, k){
    var nm = V_ROWNAME[row[0]] || row[0];
    u += '<tr class="x" data-row="' + k + '"><td>' + (V_ROWNOTE[row[0]] ? '<span class="v-rn"' + noteAttr(V_ROWNOTE[row[0]]) + ">" + nm + "</span>" : nm) + (row[4] ? '<span class="trt"' + noteAttr(glNote("TRANSFER")) + ">TRANSFER PAYMENT</span>" : "") + "</td>" + c(row[1]) + c(row[2]) + c(row[3]) + c(row[1] + row[2] + row[3]) + "</tr>";
    u += '<tr class="dt" data-dt="' + k + '"><td colspan="5">' + vBasis(row, sc) + "</td></tr>";
  });
  $("#v-ledger").innerHTML = u + '<tr class="tot"><td>Net</td>' + c(r.z) + c(r.c) + c(r.oth) + c(r.sys) + "</tr>";
  bindRows("#v-ledger");
  $("#v-ledger-foot").textContent = "Each row sums across the parties to the System column; transfer payments sum to 0. Illustrative numbers.";
  /* capacity value: the result, with the 12 peak intervals folded away */
  var ir;
  if (r.calls === 0) ir = '<p class="foot">No calls, so no capacity value.</p>';
  else {
    ir = '<table class="kvt"><tr><td>Median change in net import per responding home, per half-hour</td><td class="n">' + nf(r.dmed, 2) + " kWh</td></tr>" +
      "<tr><td>× 2 (kWh per half-hour to kW) × " + nf(r.resp) + ' responding homes (average response, not guaranteed)</td><td class="n">' + nf(-2 * Math.abs(r.dmed) * r.resp / 1000, 2) + " MW</td></tr>" +
      '<tr><td>× market operator (AEMO) ratio 1.0 (assumption) × A$216,091.78/MW-yr</td><td class="n">' + aud(r.capv) + "</td></tr>" +
      "<tr><td>Zenith's part" + (sc === "S3" ? " (Zenith keeps " + V_SHARE + "%, assumption)" : "") + '</td><td class="n">' + aud(r.zc) + "</td></tr></table>";
    ir += '<details class="ri-more"><summary>The 12 peak half-hours that set the charge, Hot Season 2025-26</summary><table class="tbl"><tr><th>Peak half-hour</th><th class="n">Change in net import, kWh per half-hour per home</th></tr>';
    r.o.ircr.forEach(function(v, k){ var day = Math.floor(k / 3) + 1, times = day === 4 ? ["17:00","17:30","18:00"] : ["17:30","18:00","18:30"];
      ir += "<tr><td>Day " + day + " · " + times[k % 3] + (v === 0 ? " · before the event window" : "") + '</td><td class="n">' + nf(v, 1) + "</td></tr>"; });
    ir += "</table></details>";
    ir += '<p class="foot">Settled once per season, not a daily reward. Season 2025-26 sets capacity year 2026-27 (as-if price). The ' + gl("PEAKDAY", "official peak days") + ' are known only after the season.</p>';
  }
  $("#v-ircr").innerHTML = ir;
  /* own battery: separate meters, so the two ledgers add */
  var own = VAL.ins.q2b[0] * 365;   /* the adopted dispatch, A$ a day over the training period, as on Evidence */
  $("#v-own").innerHTML = '<p class="v-fsub">The site battery\'s profit is separate and adds to the fleet\'s; both are Zenith\'s.</p><table class="kvt v-own"><tr><td>Site battery alone · A$' + nf(VAL.ins.q2b[0]) + ' a day, training-period average (1 Apr 2025 – 30 Jun 2026, 456 days) × 365</td><td class="n">' + aud(own) + "</td></tr>" +
    "<tr><td>+ Customer fleet · custom offer · " + vName(sc) + '</td><td class="n">' + aud(r.z) + "</td></tr>" +
    '<tr class="v-sum"><td>Zenith with both · separate meters, so the two accounts add</td><td class="n">' + aud(own + r.z) + "</td></tr></table>" +
    '<p class="foot">Saved run R‑0142 · training period · illustrative numbers. <button class="lnk" data-go="evidence">Site battery evidence →</button></p>';
  /* season-by-season tests: one Hot Season left out at a time; only leaving out 2025-26 is a forward test */
  $("#v-hold-sub").innerHTML = "Each Hot Season (Dec–Mar) left out in turn and tested with a rule tuned on the others · " + vName(sc) + " " + gl("EARLIERSEASON", "", "w") + gl("FORWARD");
  var h = '<table class="tbl v-hold"><thead><tr><th rowspan="2">Season left out</th><th colspan="3" class="grp">' + OFF.custom.name + '</th><th colspan="3" class="grp g2">' + OFF.ref.name + "</th></tr>" +
    '<tr><th class="n">Calls</th><th class="n">' + gl("PEAKDAY", "Official peak days") + '</th><th class="n">Zenith</th><th class="n g2">Calls</th><th class="n">' + gl("PEAKDAY", "Official peak days") + '</th><th class="n">Zenith</th></tr></thead><tbody>';
  SEASONS.forEach(function(sn, k){
    h += '<tr data-season="' + sn + '"><td>' + sn + " " + gl(vHow(k)[2], "", CHRON[k] ? "" : "w") + '<div class="v-sd">' + V_SD[k] + " · " + V_SN[k] + " days</div></td>";
    [r, a].forEach(function(x, j){ h += '<td class="n' + (j ? " g2" : "") + '">' + x.seasonCalls[k] + '</td><td class="n">' + x.peaks[k] + ' of 4</td><td class="n' + (x.seasonZ[k] < 0 ? " neg" : "") + '">' + aud(x.seasonZ[k]) + "</td>"; });
    h += "</tr>";
  });
  $("#v-hold").innerHTML = h + '</tbody></table><p class="foot">Extra Zenith profit per Hot Season, vs Zenith without the fleet · official peak days covered, known only afterwards · saved run R‑0142 · illustrative numbers.</p>';
  /* customer constraints as check tiles; the full condition opens on hover */
  $("#v-cons").innerHTML = FLEET_CHECKS.map(function(ch){ return '<div class="ck ok"' + noteAttr(ch[1]) + '><span class="ci ok">&#10003;</span>' + ch[0] + "</div>"; }).join("");
  $("#v-cons-foot").textContent = "0 breaks · both offers · replay of " + V_YEAR + " · saved run R‑0142.";
}

segBind("data-vscen", "vscen");
SCREENS.value = {t:"Value by party", chips:[["step","2 · Improve"]], ctx:function(){ return "Customers' home batteries · custom offer vs reference terms"; }};
PAGES.value = renderValue;
