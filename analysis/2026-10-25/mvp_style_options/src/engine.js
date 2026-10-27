/* All numbers are ILLUSTRATIVE placeholders that are internally consistent (VISUAL_DIRECTION.md section 2.8).
   Nothing is computed from user inputs: changing an input only marks results stale. */
(function(){
"use strict";
var N = 48, X0 = 104, IW = 16, EFF = 0.922;
function X(i){ return X0 + i * IW; }
function p2(n){ return (n < 10 ? "0" : "") + n; }
function hhmm(i){ var m = (480 + 30 * i) % 1440; return p2(Math.floor(m / 60)) + ":" + p2(m % 60); }
function nf(v, d){ d = d || 0; return Number(v).toLocaleString("en-AU", {minimumFractionDigits:d, maximumFractionDigits:d}); }
function aud(v, d){ return (v < -0.0005 ? "−A$ " : "A$ ") + nf(Math.abs(v), d); }
/* a layout may leave a slot out; writing to a detached element then does nothing */
function $(s){ return document.querySelector(s) || document.createElement("div"); }
function $$(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); }
function blk(spec){ var a = [], i; for (i = 0; i < N; i++) a.push(null); spec.forEach(function(s){ for (var j = s[0]; j < s[1]; j++) a[j] = s[2]; }); return a; }
function sum(a){ return a.reduce(function(t, v){ return t + (v || 0); }, 0); }
function median(a){ var b = a.slice().sort(function(x, y){ return x - y; }); var n = b.length; return n % 2 ? b[(n - 1) / 2] : (b[n / 2 - 1] + b[n / 2]) / 2; }

/* ---------- own battery, 14 Jan 2026 (illustrative) ---------- */
var P = {
  price: blk([[2,4,90],[4,14,25],[14,19,70],[19,24,230],[24,30,120],[30,42,75],[42,48,95]]),
  yday:  blk([[0,2,100],[2,4,110],[4,14,30],[14,19,95],[19,24,180],[24,30,140],[30,42,80],[42,48,100]]),
  actual:blk([[0,4,95],[4,14,18],[14,19,85],[19,20,260],[20,21,310],[21,22,640],[22,23,280],[23,24,210],[24,30,135],[30,42,70],[42,48,105]]),
  pspike:blk([[2,14,.03],[14,19,.12],[19,24,.45],[24,30,.15],[30,48,.04]]),
  dis: blk([[0,4,0],[4,18,.4],[18,19,0],[19,24,7.5],[24,48,0]]),
  chg: blk([[0,34,0],[34,46,4.2],[46,48,0]]),
  rr:  blk([[0,4,1],[4,24,0],[24,48,1]]),
  rl:  blk([[0,24,0],[24,48,1]]),
  cr:  blk([[0,48,2]]),
  cl:  blk([[0,48,1.5]]),
  load:blk([[0,4,8],[4,18,9.2],[18,20,3.45],[20,48,3]]),
  fRR: blk([[0,4,14],[4,24,19],[24,48,14]]),
  fRL: blk([[0,24,8],[24,48,6]]),
  fCR: blk([[0,48,4]]),
  fCL: blk([[0,48,1.2]])
};
P.lo = P.price.map(function(v){ return v == null ? null : v * 0.6; });
P.hi = P.price.map(function(v, i){ return v == null ? null : v * (i >= 19 && i < 24 ? 1.9 : 1.6); });
var SOC = [30], MAND = [], ECON = [], METER = [], i;
for (i = 0; i < N; i++) {
  SOC.push(SOC[i] + 0.5 * (P.chg[i] * EFF - P.dis[i] / EFF));
  MAND.push(1 + 0.25 * (P.cr[i] + P.rr[i]) / EFF);
  ECON.push(i >= 19 ? 3.5 : (i < 4 ? 14 * 0.2 / EFF : (i <= 17 ? (18 - i) * 0.2 / EFF : 0)));
  METER.push(P.load[i] - P.dis[i] + P.chg[i]);
}
var SOCOUT = SOC.map(function(v, b){ return v - (b >= 24 ? 0.3 : 0.3 * b / 24); });
var DEMFC = [], DEMACT = [];
for (i = 0; i < N; i++) { var hm = 8 + 0.5 * i + 0.25; DEMFC.push(2350 + 1520 * Math.exp(-Math.pow((hm - 18.25) / 3.3, 2))); }
DEMACT = DEMFC.map(function(v){ return v * 3840 / 3870; });
var MONEY = (function(){
  var e = 0, eo = 0, fc = 0, dm = 0;
  for (var k = 0; k < N; k++) {
    e += (P.price[k] || 0) * (P.dis[k] - P.chg[k]) * 0.5;
    eo += P.actual[k] * (P.dis[k] - P.chg[k]) * 0.5;
    fc += 0.5 * (P.rr[k] * P.fRR[k] + P.rl[k] * P.fRL[k] + P.cr[k] * P.fCR[k] + P.cl[k] * P.fCL[k]);
    dm += P.dis[k] * 0.5;
  }
  var plan = {energy:e, fcess:fc, network:900, wear:-30 * dm};
  plan.net = plan.energy + plan.fcess + plan.network + plan.wear;
  var out = {energy:eo, fcess:fc * 0.9, network:900, wear:-30 * dm};
  out.net = out.energy + out.fcess + out.network + out.wear;
  return {plan:plan, out:out, dischargeMWh:dm};
})();
/* the conditional layer: spare energy offered 17:30–20:00 in layers from A$150/MWh. It is sized so that, if it all
   sells, the battery still stays above its reserve floor (plan margin +1.3 MWh, less the replay's own 0.3 MWh drift).
   In the replay it sells wherever the actual price reached the offer; the plan's expected value leaves it out. */
/* the threshold falls through the window: at 17:30 two more hours of forecast A$230 are still ahead, so holding the
   energy is worth that plus wear and the price must beat it; by 19:30 only the A$75 night is ahead and the threshold
   drops to wear plus the round-trip loss. Half-hourly and one quantity: a simplification of the price-quantity curve
   a real dispatcher re-submits every 5 minutes. */
var OFFERS = [{a:19, b:24, mw:0.3, price:[300, 280, 260, 240, 150], why:["two more hours of forecast A$230 still ahead", "90 minutes of forecast A$230 still ahead", "one more hour of forecast A$230 still ahead", "one more half-hour of forecast A$230 ahead", "only the A$75 night ahead: wear plus the round-trip loss sets it"]}];
function offerAt(k){ var o = OFFERS[0]; return k >= o.a && k < o.b ? {a:o.a, b:o.b, mw:o.mw, price:o.price[k - o.a], why:o.why[k - o.a]} : null; }
var OFFER_OUT = P.actual.map(function(v, k){ return offerAt(k) && v >= offerAt(k).price ? offerAt(k).mw : 0; });
(function(){ var cum = 0; for (var b = 0; b <= N; b++) { if (b) cum += OFFER_OUT[b - 1] * 0.5 / EFF; SOCOUT[b] -= cum; } })();
MONEY.out.offer = sum(OFFER_OUT.map(function(v, k){ return v * 0.5 * P.actual[k]; })); MONEY.out.offerHH = OFFER_OUT.filter(function(v){ return v > 0; }).length; MONEY.out.net += MONEY.out.offer; MONEY.plan.offer = 0;
var MARGIN = (function(){
  var m = 1e9, from = -1, to = -1;
  for (var k = 0; k < N; k++) { var v = SOC[k + 1] - MAND[k] - ECON[k]; if (v < m - 0.005) { m = v; from = k; to = k; } else if (Math.abs(v - m) < 0.005) to = k; }
  return {v:m, from:from, to:to, req:MAND[from] + ECON[from]};
})();

/* ---------- customer fleet, Hot Season 2025-26 (illustrative) ---------- */
var DAYS = 121, TODAY = 44;
var ACT = [3050,3120,2980,3200,3010,3150,3650,3720,3300,3200,3350,3400,3100,3780,3450,3500,3300,3550,3400,3760,3905,3600,3500,3650,3700,3450,3300,3930,3550,3650,3600,3400,3700,3800,3960,3750,3600,3500,3780,3650,3915,3700,3600,3550,3840,4080];
for (i = 46; i < DAYS; i++) ACT.push(Math.round(3350 + 220 * Math.sin(i / 5.3) + 130 * Math.cos(i / 2.9)));
var OVR = {49:3790,52:3880,55:3800,58:3900,59:3930,63:4050,64:4120,67:3780,70:3890,74:3820,78:3870,81:3810,85:3920,92:3860,99:3940,107:3950,108:4010,114:3900};
Object.keys(OVR).forEach(function(k){ ACT[+k] = OVR[k]; });
var PEAK = [45,63,64,108];
var FLAG_C = [6,7,13,19,20,27,33,34,40,44,45,52,58,59,63,64,70,78,85,92,99,107,108,114];
var FLAG_R = FLAG_C.concat([2,3,10,16,24,30,37,49,55,67,74,81]).sort(function(a, b){ return a - b; });
function run4(d){ var a = ACT.slice(0, d).sort(function(x, y){ return y - x; }); return a.length >= 4 ? a[3] : 3300; }
function fcPeak(d){ var r = run4(d); return FLAG_C.indexOf(d) >= 0 ? r - 35 : (FLAG_R.indexOf(d) >= 0 ? r - 110 : Math.min(ACT[d], r - 170)); }
var OFF = {
  custom: {name:"Custom offer", short:"Custom 85 c/kWh, 45 calls", rate:0.85, cap:45, q:"0.75", tol:85, enrolled:3000, flags:FLAG_C, kwh:9.0,
    ircr:[-1.7,-1.6,-1.5,-1.6,-1.5,-1.4,-1.7,-1.6,-1.5,0,-1.6,-1.5], sb:3200, retail:32000, debs:16000, energy:24000,
    seasons:{S2b:[670000,1366000,1287528]}, cust:[196.67,150.00,140.45], calls:[41,26,24], peaks:[4,4,4],
    src:"The terms this tool suggests. Not a contract; the payment level must come from a pilot."},
  ref: {name:"Reference terms", short:"Reference 70 c/kWh, 30 calls", rate:0.70, cap:30, q:"0.9", tol:140, enrolled:2000, flags:FLAG_R, kwh:9.0,
    ircr:[-1.5,-1.4,-1.6,-1.4,-1.3,-1.5,-1.4,-1.5,-1.3,0,0,0], sb:2400, retail:24000, debs:12000, energy:18000,
    seasons:{S2b:[-372000,1032000,735802]}, cust:[145.00,140.00,139.65], calls:[30,30,30], peaks:[0,4,3],
    src:"Public Synergy Battery Rewards terms used for comparison. Not a Zenith tariff or contract."}
};
function callsOf(o){ return o.flags.slice(0, o.cap); }
var SEASONS = ["2023-24","2024-25","2025-26"], CHRON = [false,false,true];
var RUN = {scen:"S2b"};   /* inputs of the saved run R‑0142 */
var CAPPRICE = 216091.78, RESP = 0.9;

function evalOffer(k, sc){
  var o = OFF[k], r = {o:o, sc:sc};
  if (sc === "S1" || sc === "S2a") {
    r.enrolled = 380; r.calls = 0; r.resp = 342;
    r.rows = [
      ["Capacity charge (IRCR) saving, per season", 0, 0, 0, false, sc === "S1" ? "S1 has no capacity value." : "Current rule: net export counts 0, and normal self-use already zeroes peak import."],
      ["Wholesale value of changed net export", 0, 0, 0, false, "No call has a positive expected margin at 09:00, so the rule makes no calls."],
      ["Compensation for event net export", 0, 0, 0, true, "No calls, so no compensation."],
      ["Operating cost", -9500, 0, 0, false, "380 customers enrol even at zero expected benefit (assumed sign-up curve); A$25 a year each."]];
    r.seasonZ = [-9500,-9500,-9500]; r.custHome = [0,0,0]; r.seasonCalls = [0,0,0]; r.peaks = [0,0,0];
  } else {
    var calls = Math.min(o.flags.length, o.cap), en = o.enrolled, rsp = en * RESP;
    var dmed = median(o.ircr), capv = Math.floor(2 * Math.abs(dmed) * rsp / 1000 * CAPPRICE);
    var comp = o.rate * o.kwh * rsp * calls, wear = 0.05 * o.kwh * rsp * calls, hassle = 0.5 * rsp * calls, ops = 25 * en + 0.05 * rsp * calls;
    var zc = capv;
    r.enrolled = en; r.calls = calls; r.resp = rsp; r.dmed = dmed; r.capv = capv; r.zc = zc;
    r.rows = [
      ["Capacity charge (IRCR) saving, per season", zc, 0, 0, false, "Assumed future rule: net export counts negative; Zenith as retailer keeps the whole change."],
      ["Wholesale value of changed net export", o.energy, 0, 0, false, "Zenith settles the change in the meters' net position at actual prices."],
      ["Compensation for event net export", -comp, comp, 0, true, nf(o.rate * 100) + " c/kWh × " + nf(o.kwh, 1) + " kWh × " + nf(rsp) + " responding customers × " + calls + " calls. Paid on billed event net export."],
      ["Hold-charge offset credits", -o.sb, o.sb, 0, true, "Extra import while the battery holds charge before a call, credited to the customer."],
      ["Retail bill change after calls", o.retail, -o.retail, 0, true, "Customers import more after a call; the retailer earns the margin on that extra energy."],
      ["Feed-in credit (DEBS) change", 0, o.debs, -o.debs, true, "Feed-in credits on extra exports, paid by the scheme's payer."],
      ["Customer battery wear", 0, -wear, 0, false, "A$0.05 per kWh of extra throughput (assumption)."],
      ["Customer disutility", 0, -hassle, 0, false, "A$0.50 per responding customer per event (assumption)."],
      ["Operating cost", -ops, 0, 0, false, "A$25 per enrolled customer a year + A$0.05 per responding customer per event (assumptions)."],
      ["Programme fixed cost", 0, 0, 0, false, "Default 0 (assumption)."]];
    r.seasonZ = o.seasons[sc]; r.custHome = o.cust; r.seasonCalls = o.calls; r.peaks = o.peaks;
  }
  r.z = sum(r.rows.map(function(x){ return x[1]; })); r.c = sum(r.rows.map(function(x){ return x[2]; })); r.oth = sum(r.rows.map(function(x){ return x[3]; }));
  r.sys = r.z + r.c + r.oth;
  r.worst = Math.min.apply(null, r.seasonZ); r.worstIdx = r.seasonZ.indexOf(r.worst);
  r.expZ = sum(r.seasonZ) / 3; r.expC = sum(r.custHome) / 3;
  if (r.calls === 0) r.verdict = {k:"bad", t:"No business case", p: sc === "S1" ? "S1, energy only: no call pays for itself at 09:00, so only the operating cost remains." : "S2a, today's capacity rule: the fleet's export counts 0 against the peak, and normal self-use already removes peak import, so calls add almost no capacity value."};
  else if (r.worst < 0) r.verdict = {k:"bad", t:"No business case", p:"Fails the downside test: Zenith loses " + aud(-r.worst) + " in Hot Season " + SEASONS[r.worstIdx] + " (" + r.peaks[r.worstIdx] + " of 4 official peak days covered, known only afterwards). The expected value alone is not enough."};
  else r.verdict = {k:"ok", t:"Positive in this scenario", p:"Depends on an assumed future rule for the capacity charge (IRCR) from 2027; not current revenue. Customers are better off than normal self-use, no customer limit is broken (yearly call cap, max control time, customer floor) and no Hot Season (Dec–Mar) loses money. The capacity value has not yet been tested blind; the first chance is the 2026-27 Hot Season. Enrolment follows an assumed curve."};
  return r;
}

/* ---------- state ---------- */
var S = {screen:"plan", sel:20, revealed:false, view:"decision", offer:"custom", day:TODAY, vscen:"S2b", per:"ins", cper:"tseen", nc:false};
/* page registry: each page file sets PAGES.<name> = its render function and SCREENS.<name> = {t, chips, ctx} */
var PAGES = {};
var DEF = {start:"model", pay:85, evp:0, fee:0, cap:45, ev:"2", sb:"2", mx:6, res:20, win:"17:30–19:30", elig:10000, curve:"Central", resp:90, ops:25, fix:0, exp:"10", fcess:"RR, RL, CR, CL", scen:"S2b"};
var IN = JSON.parse(JSON.stringify(DEF));

/* ---------- svg helpers ---------- */
function stepPath(vals, yf, from, to){
  var d = "", pen = false;
  for (var k = from; k < to; k++) {
    var v = vals[k]; if (v == null) { pen = false; continue; }
    var y = yf(v).toFixed(2);
    d += (pen ? "V" + y : "M" + X(k).toFixed(2) + "," + y) + "H" + X(k + 1).toFixed(2); pen = true;
  }
  return d;
}
function bandPath(lo, hi, yf, from, to){
  var d = "M" + X(from).toFixed(2) + "," + yf(hi[from]).toFixed(2), k;
  for (k = from; k < to; k++) d += "V" + yf(hi[k]).toFixed(2) + "H" + X(k + 1).toFixed(2);
  for (k = to - 1; k >= from; k--) d += "V" + yf(lo[k]).toFixed(2) + "H" + X(k).toFixed(2);
  return d + "Z";
}
/* a line through interval boundaries b = from..to (state of charge is a level at each boundary) */
function linePath(vals, yf, from, to){ var d = ""; for (var b = from; b <= to; b++) d += (b === from ? "M" : "L") + X(b).toFixed(2) + "," + yf(vals[b]).toFixed(2); return d; }
function tx(x, y, t, cls, extra){ return '<text x="' + (+x).toFixed(1) + '" y="' + (+y).toFixed(1) + '"' + (cls ? ' class="' + cls + '"' : "") + (extra || "") + ">" + t + "</text>"; }
function ln(x1, y1, x2, y2, st){ return '<line x1="' + (+x1).toFixed(2) + '" y1="' + (+y1).toFixed(2) + '" x2="' + (+x2).toFixed(2) + '" y2="' + (+y2).toFixed(2) + '" ' + st + "/>"; }
function rc(x, y, w, h, st){ return '<rect x="' + (+x).toFixed(2) + '" y="' + (+y).toFixed(2) + '" width="' + (+w).toFixed(2) + '" height="' + Math.max(0, h).toFixed(2) + '" ' + st + "/>"; }
/* each chart gets its own pattern id: a pattern inside a hidden screen may not render if another chart refers to it */
function HATCH(id){ return '<pattern id="' + id + '" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" style="fill:var(--hatch-bg)"/><line x1="0" y1="0" x2="0" y2="6" style="stroke:var(--hatch-ln)" stroke-width="2"/></pattern>'; }
/* chart width follows the container, so text keeps its size in every layout */
function fitW(sel, min, dflt){
  var el = document.querySelector(sel); if (!el || !el.clientWidth) return dflt;
  var cs = getComputedStyle(el), w = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  return Math.max(min, Math.floor(w));
}
/* every time-of-day chart uses the same margins, so charts stacked in one box line up hour for hour */
var RM = 56;
function setW(W){ IW = (W - RM - X0) / N; return W; }
/* a layout lets the charts fill a fixed-height box (the console) by marking the box data-fill;
   the box must grow with the layout, not with its content, or the charts would chase their own height */
function fitH(sel){ var el = document.querySelector(sel); return el && el.hasAttribute("data-fill") && getComputedStyle(el).flexGrow !== "0" ? Math.floor(el.clientHeight) : 0; }
function fits(t, fs, w){ return t.length * fs * 0.56 <= w - 6; }
function mid(a, b){ return (X(a) + X(b)) / 2; }

/* ---------- chart grammar, after the market tools used as reference ----------
   Colour names the quantity. A dashed line is a forecast or plan, a solid line is what happened, a shaded
   band is the forecast range. Axes carry units. Limits such as Max or the reserve floor are labelled where
   the line ends. Legend items switch series on and off; hovering shows the values as a small table. */
var HID = {};   /* series switched off in a legend, keyed "chart:series" */
function shown(c, k){ return !HID[c + ":" + k]; }
function swatch(kind, col){ return '<i class="sw2 ' + kind + '"' + (col ? ' style="--c:' + col + '"' : "") + "></i>"; }
function lgdHTML(c, items){
  return '<div class="lgd" role="group" aria-label="Show or hide series">' + items.map(function(it){
    var on = shown(c, it[0]);
    return '<button class="lg' + (on ? "" : " off") + '" data-lg="' + c + ":" + it[0] + '" aria-pressed="' + on + '" title="' + (on ? "Hide" : "Show") + '">' + swatch(it[1], it[2]) + it[3] + "</button>"; }).join("") + "</div>";
}
function yTitle(x, y, t){ return '<text class="ytl" transform="translate(' + x + "," + y.toFixed(1) + ') rotate(-90)" text-anchor="middle">' + t + "</text>"; }
function svgOpen(W, H, tip, label){ return '<svg viewBox="0 0 ' + W + " " + H + '" class="lanes chart"' + (tip ? ' data-tip="' + tip + '" data-xg="day" data-x0="' + X0 + '" data-iw="' + IW.toFixed(3) + '"' : "") + ' role="img" aria-label="' + label + '">'; }
/* the trading day runs 08:00 to 08:00; the calendar date sits under its first hour and under midnight */
function dayAxis(y1, d){
  var s = [];
  [0, 8, 16, 24, 32, 40, 48].forEach(function(q){
    s.push(ln(X(q), y1, X(q), y1 + 4, 'style="stroke:var(--axis)"') + tx(X(q), y1 + 16, q < N ? hhmm(q) : "08:00", "", ' text-anchor="' + (q === 0 ? "start" : (q === N ? "end" : "middle")) + '"'));
  });
  return s.join("") + tx(X(0), y1 + 30, dayName(d), "lu") + tx(X(32), y1 + 30, dayName(d + 1), "lu", ' text-anchor="middle"');
}
/* the 09:00 decision, drawn like the "latest" marker of a market tool */
function planMark(y0, y1, label){ return ln(X(2), y0 - (label ? 8 : 0), X(2), y1, 'style="stroke:var(--mark)" stroke-width="1"') + (label ? tx(X(2), y0 - 12, "PLAN MADE 09:00", "mk9", ' text-anchor="middle"') : ""); }
/* invisible columns that drive hover and click, and the crosshair they move */
function hitCols(y0, y1){
  var s = ['<line class="xh" x1="0" x2="0" y1="' + y0 + '" y2="' + y1 + '"/>'];
  for (var k = 0; k < N; k++) s.push('<rect class="hit" data-i="' + k + '" x="' + X(k).toFixed(2) + '" y="' + y0 + '" width="' + IW.toFixed(2) + '" height="' + (y1 - y0) + '"/>');
  return s.join("");
}
function limLine(y, label, x1){ return ln(X0, y, X(N), y, 'class="lmt"') + (label ? tx(x1 || X(N) - 2, y - 5, label, "lim halo", ' text-anchor="end"') : ""); }

/* ---------- Today's plan: battery power under the price, then the energy it holds ---------- */
var FLOOR = MAND.map(function(m, k){ return m + ECON[k]; });
function modeOf(k){ return k < 2 ? "pre" : (P.chg[k] > 0 ? "chg" : (P.dis[k] > 1 ? "dis" : (P.dis[k] > 0 ? "shave" : "hold"))); }
function doing(k){
  var m = modeOf(k);
  if (m === "dis") return "Discharge " + nf(P.dis[k], 1) + " MW";
  if (m === "chg") return "Recharge " + nf(P.chg[k], 1) + " MW";
  if (m === "shave") return "Cut import " + nf(P.dis[k], 1) + " MW";
  if (m === "pre") return "Before the 09:00 plan";
  return "Hold";
}
/* what is firm and what depends on the market. As in the model: the plan (shave, discharge, hold, recharge) is scheduled
   and re-solved every 30 minutes with the latest price; only spare energy above the plan is offered, in layers from a
   A$150/MWh floor, and sells only if the price reaches each layer. The offer itself is defined with the data above. */

/* direct: the lines are named on the chart, with no legend (scheme A's plan page) */
function socSVG(sel, h, direct){
  var W = setW(fitW(sel, 560, 852)), XR = X(N), s = [], rev = S.revealed, CAP = 40;
  var H = h || 210, y0 = 18, y1 = H - 40;
  var ys = function(v){ return y1 - v * (y1 - y0) / CAP; };
  s.push(svgOpen(W, H, "soc", "Plan for 14 Jan 2026: energy held in the battery (illustrative)"));
  [0, 10, 20, 30, 40].forEach(function(v){ s.push(ln(X0, ys(v), XR, ys(v), v ? 'class="gl"' : 'style="stroke:var(--axis)"') + tx(X0 - 8, ys(v) + 3.5, String(v), "", ' text-anchor="end"')); });
  s.push((direct ? tx(X0, y0 - 6, "Energy stored, MWh", "lnm") : yTitle(14, (y0 + y1) / 2, "Energy stored, MWh")) + rc(X(S.sel), y0, IW, y1 - y0, 'style="fill:var(--sel)"'));
  s.push(limLine(ys(CAP), "Full · 40 MWh") + '<path d="' + stepPath(FLOOR, ys, 0, N) + '" fill="none" class="lmt"/>' + tx(XR - 2, ys(FLOOR[N - 1]) - 5, "Minimum", "lim halo", ' text-anchor="end"'));
  /* what happened: 08:00–09:00 is already known; the whole day once the outcome is revealed */
  var to = rev ? N : 2;
  if (shown("soc", "act")) {
    var a = linePath(SOCOUT, ys, 0, to);
    s.push('<path d="' + a + "L" + X(to).toFixed(2) + "," + y1 + "L" + X(0) + "," + y1 + 'Z" style="fill:var(--actual)" opacity="0.08"/><path d="' + a + '" class="actual" fill="none" style="stroke:var(--actual)" stroke-width="2" stroke-linejoin="round"/>');
  }
  if (shown("soc", "plan")) {
    s.push('<path d="' + linePath(SOC, ys, 2, N) + '" fill="none" style="stroke:var(--soc)" stroke-width="2" stroke-dasharray="5 3" stroke-linejoin="round"/>');
    s.push('<circle cx="' + X(24).toFixed(1) + '" cy="' + ys(SOC[24]).toFixed(1) + '" r="3" style="fill:var(--soc)"/>' + tx(X(24) + 7, ys(SOC[24]) - 7, nf(SOC[24], 1) + " MWh", "halo pt"));
    s.push(tx(XR - 2, ys(SOC[N]) - 8, nf(SOC[N], 1) + " MWh", "halo pt", ' text-anchor="end"'));
    var sx = X(S.sel) + IW / 2, sy = (ys(SOC[S.sel]) + ys(SOC[S.sel + 1])) / 2;
    s.push('<circle cx="' + sx.toFixed(1) + '" cy="' + sy.toFixed(1) + '" r="4" style="fill:var(--surface);stroke:var(--soc)" stroke-width="2"/>');
  }
  return s.join("") + planMark(y0, y1, false) + dayAxis(y1, TODAY) + hitCols(y0, y1) + "</svg>";
}
/* is the plan on screen executable under the current inputs? This is not the run status: a run can be up to date and
   its plan still break a limit, for example after the export limit is lowered */
function planCheck(){
  var exp = +IN.exp, k, bad = [], maxImp = 0, maxExp = 0;
  for (k = 0; k < N; k++) { maxImp = Math.max(maxImp, METER[k]); maxExp = Math.max(maxExp, -METER[k]); if (-METER[k] > exp + 1e-9) bad.push(k); }
  /* [passes, full text, "warn" for an economic limit, short label] */
  var list = [
    [true, "Energy plus the power held ready for grid services never exceeds the battery's 10 MW rating (highest 9.5 MW)", "", "Power ≤ 10 MW"],
    [MARGIN.v >= 0, "Energy stored stays above the grid-service reserve plus the factory backup (lowest margin +" + nf(MARGIN.v, 1) + " MWh). " + glNote("GRIDRESERVE") + " " + glNote("BACKUP"), "", "Grid reserve + backup kept"],
    [maxImp <= 12, "Grid import ≤ the 12 MW connection limit (highest " + nf(maxImp, 1) + " MW)", "", "Connection import ≤ 12 MW"],
    [maxImp <= 9, "Factory grid import stays under the 9 MW import contract (highest " + nf(maxImp, 1) + " MW). " + glNote("CMD"), "warn", "Import contract 9 MW · a cost"],
    [!bad.length, "Grid export ≤ the " + exp + " MW connection limit (highest " + nf(maxExp, 1) + " MW" + (bad.length ? ", above the limit in " + bad.length + " half-hours, " + hhmm(bad[0]) + "–" + hhmm(bad[bad.length - 1] + 1) : "") + ")", "", "Connection export ≤ " + exp + " MW"],
    [true, "The same MW or MWh is never promised to both the energy market and grid services. " + glNote("FCESS"), "", "Nothing sold twice"],
    [true, glNote("DECISION"), "", "Only data known at 09:00"]];
  var fails = list.filter(function(c){ return !c[0] && !c[2]; }), warns = list.filter(function(c){ return !c[0] && c[2]; });
  return {list:list, fails:fails, warns:warns, bad:bad, exp:exp, badShort:bad.length ? "Export above " + exp + " MW" : "",
    why:bad.length ? bad.length + " half-hours above the " + exp + " MW export limit (" + hhmm(bad[0]) + "–" + hhmm(bad[bad.length - 1] + 1) + ")" : ""};
}

/* the selected half hour: one sentence and three numbers */
function railHTML(k){
  var m = modeOf(k), pr = P.price[k], exp = +IN.exp, over = -METER[k] > exp + 1e-9;
  var lvl = nf(SOC[k], 1) + " → " + nf(SOC[k + 1], 1) + " MWh", price = pr == null ? "not issued yet" : "A$" + nf(pr) + "/MWh";
  var act, say, facts, kind = "Scheduled";
  if (m === "dis") {
    act = "Discharging " + nf(P.dis[k], 1) + " MW";
    say = "The evening price is forecast at A$" + nf(pr) + "/MWh, worth more than keeping the energy. The plan is re-solved every 30 minutes with the latest price.";
    facts = [["Battery", lvl], ["Sent to the grid", '<span class="' + (over ? "neg" : "") + '">' + nf(-METER[k], 1) + " of " + exp + " MW export limit</span>"], ["Extra offer this half-hour", "+" + nf(OFFERS[0].mw, 1) + " MW if price ≥ A$" + offerAt(k).price + "/MWh"], ["Why this threshold", offerAt(k).why]];
  } else if (m === "shave") {
    act = "Cutting import " + nf(P.dis[k], 1) + " MW";
    say = "Without the battery the factory would draw " + nf(P.load[k], 1) + " MW, above its 9 MW contract limit (CMD).";
    facts = [["Battery", lvl], ["Factory import", nf(METER[k], 1) + " of 9 MW allowed"], ["Price forecast", price]];
  } else if (m === "chg") {
    act = "Charging " + nf(P.chg[k], 1) + " MW";
    say = "Night-time energy is cheap (A$" + nf(pr) + "/MWh forecast), so the battery refills for tomorrow.";
    facts = [["Battery", lvl], ["Price forecast", price], ["Factory import", nf(METER[k], 1) + " of 9 MW allowed"]];
  } else if (m === "pre") {
    act = "Following the earlier plan";
    say = "Today's forecast is issued at 09:00. Until then the battery runs the transition plan.";
    facts = [["Battery", nf(SOC[k], 1) + " MWh"], ["Price forecast", price]];
  } else {
    act = k >= 24 && k < 34 ? "Holding " + nf(SOC[k], 1) + " MWh" : "Standing by";
    say = "No trade beats keeping the energy at A$" + nf(pr) + "/MWh. Part of the battery is held ready for grid services (FCESS), which pays for availability.";
    facts = [["Battery", nf(SOC[k], 1) + " MWh"], ["Price forecast", price], ["Held ready for grid services (FCESS)", nf(P.rr[k] + P.cr[k], 1) + " MW"]];
  }
  var col = {dis:"var(--phs-dis)", chg:"var(--phs-chg)", shave:"var(--phs-shave)", hold:"var(--text-2)", pre:"var(--text-2)"}[m];
  var h = '<div class="ri-head"><div class="t">' + hhmm(k) + "–" + hhmm(k + 1) + '<span class="tag plan">' + kind.toUpperCase() + '</span></div><div class="act" style="color:' + col + '">' + act + "</div></div>";
  h += '<p class="ri-say">' + say + "</p>";
  h += '<div class="ri-facts">' + facts.map(function(f){ return "<div><span>" + f[0] + "</span><b>" + f[1] + "</b></div>"; }).join("") + "</div>";
  if (S.revealed) h += '<div class="ri-out"><div class="cap">Outcome<span class="tag out">REPLAYED</span></div><table class="kvt"><tr><td>Actual price</td><td class="n">A$' + nf(P.actual[k]) + '/MWh</td></tr><tr><td>Energy settled</td><td class="n">' + aud(P.actual[k] * (P.dis[k] - P.chg[k]) * 0.5) + "</td></tr>" + (offerAt(k) ? "<tr><td>Extra offer</td><td class=\"n\">" + (OFFER_OUT[k] > 0 ? "sold at A$" + nf(P.actual[k]) + " → " + aud(OFFER_OUT[k] * 0.5 * P.actual[k]) : "not sold, price below A$" + offerAt(k).price) + "</td></tr>" : "") + "</table></div>";
  else h += '<div class="locked ri-out">What happened is hidden in "Plan only". <button class="lnk" data-reveal>Show what happened →</button></div>';
  return h;
}

/* ---------- Today's plan, scheme A (round 5): decide in one line, the day as lanes, reasons on the chart ----------
   A layout with a #plan-dec slot gets this page; the other schemes keep the round-4 page in renderPlan below.
   Scheduled actions are solid blocks; the conditional offer is a dashed outline, because it runs only if the price
   reaches it; hatching marks 08:00–09:00, still on the earlier plan with no 09:00 forecast, and nothing else.
   Full wording sits in hover notes (data-note). */
/* ---------- glossary: one plain sentence (what it is) and one on why it matters; every page's hover notes come from here ----------
   gl(key, label) renders a hover chip; glNote(key) returns the note text for other elements; glName(key) the plain name. */
var GLOSS = {
  ZENITH:   ["Zenith", "Zenith is the electricity retailer this tool is built for. It owns a 10 MW / 40 MWh battery at a factory site and can also call the batteries of its business customers.", "Every A$ on these pages is Zenith's gain or loss unless a party is named."],
  FACTORY:  ["the factory site", "The factory whose grid connection the site battery sits behind. Its load is synthetic (simulated), not metered.", "The battery earns part of its value by keeping the factory's grid import under its contract."],
  SYNERGY:  ["Synergy", "Synergy is Western Australia's incumbent retailer. Its public Battery Rewards terms (70 c/kWh, 30 calls a year) are the reference terms the custom offer is compared against.", "Synergy is not a party to Zenith's offer, and its customers are not in the fleet."],
  AEMO:     ["market operator (AEMO)", "The Australian Energy Market Operator runs the wholesale market and publishes the demand forecasts and prices used here.", "Only AEMO data published before 09:00 may be used for the 09:00 decision."],
  WEM:      ["WA wholesale market (WEM)", "The Wholesale Electricity Market: Western Australia's electricity market, separate from the east-coast market.", "Its rules set the prices, the Hot Season and the capacity charge used here."],
  TRADINGDAY: ["trading day", "A market day runs from 08:00 to 08:00 the next morning, following the WA market's clock.", "Every plan, price and result on a page covers one such day."],
  DECISION: ["09:00 decision", "The plan is made at 09:00 each trading day with the forecasts available by then; 08:00–09:00 runs on the plan carried over from yesterday.", "Nothing published after 09:00 is used, so the replay never peeks at the future."],
  DISPATCHER: ["the dispatcher", "The dispatcher is this tool's scheduling software: it decides each half-hour whether the battery charges, holds, discharges or offers energy.", "The previous version (v1) is kept unchanged as the baseline to beat."],
  RUN:      ["saved run R‑0142", "A saved replay of past days with one fixed set of inputs. Its numbers on these pages are placeholders from the design prototype.", "Change an input and the saved results no longer apply until the engine runs again."],
  SETTLE:   ["settlement", "The actual money that changes hands after the day, at the prices that really occurred.", "A 09:00 expected value is a forecast, not settlement."],
  CMD:      ["import contract (CMD)", "Contract maximum demand: the grid import the factory has contracted for, 9 MW here.", "Going above it costs a demand charge in the network bill; it is a cost, not a physical limit."],
  NETBILL:  ["network bill", "The charge the network operator bills the factory for its peak grid import.", "Keeping import under the contract is part of the battery's value."],
  FCESS:    ["grid services (FCESS)", "Frequency control services: the battery is paid to stand ready to inject or absorb power at short notice to keep the grid frequency steady.", "It earns money without moving much energy, but the capacity it holds ready cannot be sold as energy at the same time."],
  GRIDRESERVE: ["grid-service reserve", "Energy the battery must keep so it can deliver the grid services it has promised.", "It is never offered to the energy market at any price."],
  BACKUP:   ["factory backup", "Energy kept so the factory can ride through a grid outage.", "The plan never discharges below it."],
  CUSTFLOOR:["customer floor", "The 20% of each customer battery that is never discharged during a call.", "It protects the customer's own evening use."],
  SOC:      ["energy stored (state of charge)", "How much energy the battery holds at a moment, in MWh, out of 40 MWh when full.", "The plan must stay between the grid-service reserve plus factory backup, and full."],
  EXTRAOFFER: ["extra offer", "Spare energy offered to the market at a threshold price; it sells only in the half-hours where the market price reaches it, and is then paid that market price, not the threshold. The threshold falls through the evening: while high-price half-hours are still ahead, holding the energy is worth more, so the price must beat that; once only the cheap night is ahead it drops to wear plus the round-trip loss. Still simplified: shown half-hourly with one quantity, while a real dispatcher re-submits a price–quantity curve of several steps every 5 minutes.", "It is not counted in the expected value because it may never sell."],
  SHAVE:    ["cut import", "Discharging just enough to keep the factory's grid import under its contract.", "It saves the demand charge rather than earning market revenue."],
  RANGE:    ["likely price range", "The band the forecast expects the price to fall inside 8 times in 10 (10th to 90th percentile).", "A wide band means an uncertain forecast."],
  MAE:      ["average error (MAE)", "Mean absolute error: the average gap between the forecast and the actual price, in A$/MWh.", "Lower is better; it is not an accuracy percentage."],
  BRIER:    ["spike forecast error (Brier score)", "How wrong the model's probability of a price spike (A$200/MWh or more) was, from 0 (perfect) to 1.", "Lower is better; the baseline is how often spikes happened in the last 28 days."],
  TRAIN:    ["training period", "1 Apr 2025 – 30 Jun 2026, 456 days: the data used to build and choose the method.", "Results here cannot show how the method does on new data."],
  TREV:     ["test · reviewed", "1 Jul – 15 Oct 2026, 107 days: not used to build the method, but looked at repeatedly while refining it.", "Weaker evidence than a blind test."],
  TBLIND:   ["test · blind", "16–22 Oct 2026, 7 days: never looked at before the method was locked.", "The only fully blind test so far; too short to prove anything on its own."],
  CONTRACTYEAR: ["contract year 2025-26", "1 Jul 2025 – 30 Jun 2026: the year the customer offer's call cap and payments are counted over.", "It lies inside the training period, so its results are not a blind test."],
  HOTSEASON:["Hot Season (Dec–Mar)", "1 Dec – 31 Mar in the WA market: when system demand peaks.", "The capacity charge for the following year is set by the 12 peak half-hours of this season."],
  IRCR:     ["capacity charge (IRCR)", "Each retailer's share of the cost of keeping enough generation available, set by its customers' demand in the 12 peak half-hours of the Hot Season.", "Lowering demand in those 12 half-hours lowers the charge for a whole year; that is the main value of calling customer batteries."],
  PEAKDAY:  ["official peak day", "One of the 4 Hot Season days that contain the 12 peak half-hours. They are known only after the season.", "A call on a peak day lowers the capacity charge; a call on any other day only costs the payment."],
  CALL:     ["call", "Asking customers' batteries to discharge together for a set evening window, for a payment.", "Each call uses one of a limited number per contract year."],
  FLAGGED:  ["flagged by the call rule", "A day the rule marks as a likely peak day at 09:00: the forecast peak is within 85 MW of the 4th-highest peak seen so far this season.", "Flagged days are called while calls remain; the 85 MW margin is set from past seasons."],
  HOLDCHARGE:["hold charge", "Before a call, customer batteries stop discharging and keep their energy for the event window.", "Without it the batteries would be empty by the evening peak."],
  TRANSFER: ["transfer payment", "Money moved from Zenith to customers for responding to a call.", "It changes who gains, not the total value created, so it sums to zero across parties."],
  RESPONSE: ["customers expected to respond", "On average 90% of enrolled customers respond to a call. 3,000 enrolled customers is an assumption.", "No customer is obliged to respond, so this is an average, not guaranteed capacity."],
  DEBS:     ["feed-in credit (DEBS)", "The Distributed Energy Buyback Scheme: the credit customers receive for electricity they export to the grid, paid by the scheme.", "A call changes how much customers export, so it changes this credit."],
  VPP:      ["customer batteries (VPP)", "The batteries at Zenith's business customers' sites, called together as one virtual power plant.", "Zenith pays for each call and has a yearly cap on calls."],
  HINDSIGHT:["hindsight", "Known only after the fact.", "It was not available at 09:00 when the decision was made, so it can judge a decision but never inform it."],
  EARLIERSEASON: ["tested on an earlier season", "The rule was tuned on later seasons and then tested on this earlier one.", "That is not how it would be used in practice, so it is weaker evidence than a forward test."],
  FORWARD:  ["forward test", "The rule was tuned on earlier seasons only and then tested on the next one, as it would be used.", "The strongest kind of evidence available before a blind season."],
  S1:  ["S1 · energy only", "Zenith only sells the fleet's extra export at the wholesale price; no capacity value.", "No call pays for itself at 09:00, so there is no business case."],
  S2a: ["S2a · today's capacity rule", "Zenith as retailer; under today's rule a customer's export counts as zero demand, not negative.", "Normal self-use already removes the peak import, so calls add almost nothing."],
  S2b: ["S2b · assumed 2027 capacity rule", "Zenith as retailer; an assumed future rule in which export counts against the peak.", "Not current revenue: the rule text is not yet confirmed."],
  REFTERMS: ["reference terms", "The public Synergy Battery Rewards terms (70 c/kWh, 30 calls a year), used only for comparison.", "Not a Zenith tariff and not a contract."],
  CUSTOMOFFER: ["custom offer", "The terms this tool suggests (85 c/kWh, 45 calls a year), found by searching the training period.", "A suggestion to test in a pilot, not a contract."],
  ENROLLED: ["enrolled customers", "Customers that signed up to the offer (3,000 assumed). Offered: customers the offer was shown to. Responding: enrolled customers that actually respond to a call (90% on average).", "Payments are per responding customer; fixed costs are per enrolled customer."]
};
function glName(k){ return GLOSS[k][0]; }
function glNote(k){ return GLOSS[k][1] + " " + GLOSS[k][2]; }
function gl(k, label, cls){ return '<span class="tipc ' + (cls || "") + '"' + noteAttr(glNote(k)) + ">" + (label || GLOSS[k][0]) + "</span>"; }
/* one line under every page header: who the parties are */
function whoHTML(){ return [["ZENITH", "Zenith · the retailer, owns the site battery"], ["FACTORY", "factory site"], ["VPP", "customers' batteries"], ["AEMO", "AEMO · market operator"], ["WEM", "WEM · WA wholesale market"]].map(function(x){ return gl(x[0], x[1]); }).join(""); }
function noteAttr(t){ return ' tabindex="0" data-note="' + String(t).replace(/&/g, "&amp;").replace(/"/g, "&quot;") + '"'; }
function segsOf(from, f){ var g = [], k = from; while (k < N) { var m = f(k), e = k; while (e + 1 < N && f(e + 1) === m) e++; g.push({m:m, a:k, b:e + 1}); k = e + 1; } return g; }
var SEGC = {shave:["color-mix(in srgb,var(--dis) 20%,var(--surface))", "var(--dis)"], dis:["var(--dis)", "var(--surface)"], chg:["var(--chg)", "var(--text)"], hold:["var(--surface-3)", "var(--text-3)"]};
function segLabel(g, w){
  var full = {shave:"Cut import " + nf(P.dis[g.a], 1) + " MW", dis:"Discharge " + nf(P.dis[g.a], 1) + " MW", chg:"Recharge " + nf(P.chg[g.a], 1) + " MW", hold:"Hold"}[g.m];
  var short = {shave:"Cut import " + nf(P.dis[g.a], 1) + " MW", dis:nf(P.dis[g.a], 1) + " MW", chg:nf(P.chg[g.a], 1) + " MW", hold:"Hold"}[g.m];
  return full && fits(full, 11, w) ? full : (fits(short, 11, w) ? short : "");
}
function standby(k){ return P.rr[k] + P.rl[k] + P.cr[k] + P.cl[k]; }
function laneSVG(sel){
  var W = setW(fitW(sel, 560, 852)), XR = X(N), s = [], rev = S.revealed, pc = planCheck(), o = OFFERS[0];
  var yA = 22, hA = 30, yO = yA + hA + 5, hO = 16, yF = yO + hO + 5, hF = 14, y0 = yF + hF + 28, y1 = y0 + 150, H = y1 + 22;
  var yp = function(p){ return y1 - Math.max(0, Math.min(p, 500)) * (y1 - y0) / 500; };
  s.push(svgOpen(W, H, "day", "Plan for 14 Jan 2026: scheduled actions, the extra offer with its falling threshold (half-hourly and one quantity, a simplification of the 5-minute price-quantity curves a real dispatcher submits), grid-service reserve and the forecast price (illustrative)") + "<defs>" + HATCH("hDy") + "</defs>");
  /* lane tracks named in the left margin, a light grid every four hours, and no forecast before 09:00 */
  [[yA, hA, "Plan", "Scheduled battery actions, decided at 09:00 from the forecast."], [yO, hO, "Sell more if", glNote("EXTRAOFFER")], [yF, hF, "Grid services", glNote("FCESS")]].forEach(function(l){ s.push(rc(X0, l[0], XR - X0, l[1], 'rx="3" style="fill:var(--surface-2)"') + '<text x="' + (X0 - 8) + '" y="' + (l[0] + l[1] / 2 + 3.5).toFixed(1) + '" class="lnm" text-anchor="end"><title>' + l[3] + "</title>" + l[2] + "</text>"); });
  [8, 16, 24, 32, 40].forEach(function(q){ s.push(ln(X(q), yA, X(q), y1, 'class="gl"')); });
  s.push(rc(X(0), yA, X(2) - X(0), y1 - yA, 'fill="url(#hDy)"'));
  /* the price panel: forecast with its P10–P90 band, the offer price, and the actual price once revealed */
  [0, 250, 500].forEach(function(v){ s.push(ln(X0, yp(v), XR, yp(v), v ? 'class="gl"' : 'style="stroke:var(--axis)"') + tx(X0 - 8, yp(v) + 3.5, nf(v), "", ' text-anchor="end"')); });
  s.push(tx(X(2) + 6, y0 - 10, "Price, A$/MWh", "lnm halo") + rc(X(S.sel), y0, IW, y1 - y0, 'style="fill:var(--sel)"'));
  {
    s.push('<path d="' + bandPath(P.lo, P.hi, yp, 2, N) + '" class="band" style="fill:var(--range)" opacity="0.32"/><path d="' + stepPath(P.price, yp, 2, N) + '" fill="none" style="stroke:var(--price)" stroke-width="1.8" stroke-dasharray="5 3"/>');
    var pk = 2, lo = -1, k;
    for (k = 2; k < N; k++) { if (P.price[k] > P.price[pk]) pk = k; if (P.chg[k] > 0 && (lo < 0 || P.price[k] < P.price[lo])) lo = k; }
    [pk, lo].forEach(function(q){ if (q < 0) return; var a = q, b = q + 1; while (a > 2 && P.price[a - 1] === P.price[q]) a--; while (b < N && P.price[b] === P.price[q]) b++;
      s.push(q === pk ? tx(X(a) - 4, yp(P.price[q]) - 4, "peak A$" + nf(P.price[q]), "halo vl", ' text-anchor="end" style="fill:var(--price)"')
        : tx(mid(a, b), yp(P.price[q]) + 13, "low A$" + nf(P.price[q]), "halo vl", ' text-anchor="middle" style="fill:var(--price)"'));
    });
  }
  s.push(tx(X(o.b) + 5, yp(o.price[o.price.length - 1]) - 5, "threshold A$" + o.price[0] + "→" + o.price[o.price.length - 1], "halo vl", ' style="fill:var(--dis)"'));
  for (k = o.a; k < o.b; k++) s.push(ln(X(k), yp(offerAt(k).price), X(k + 1), yp(offerAt(k).price), 'class="othr" style="stroke:var(--dis)" stroke-width="1.6" stroke-dasharray="3 2"') + (k > o.a ? ln(X(k), yp(offerAt(k - 1).price), X(k), yp(offerAt(k).price), 'style="stroke:var(--dis)" stroke-width="1" stroke-dasharray="3 2"') : ""));
  if (rev) {
    s.push('<path d="' + stepPath(P.actual, yp, 0, N) + '" class="actual" fill="none" style="stroke:var(--actual)" stroke-width="2"/>');
    /* a price above the axis top is clipped; a short tick marks where it went off the scale (value in the hover table) */
    P.actual.forEach(function(v, q){ if (v > 500) s.push(ln(X(q) + IW / 2, yp(500) - 2, X(q) + IW / 2, yp(500) - 8, 'style="stroke:var(--actual)" stroke-width="2"') + tx(X(q) + IW / 2 + 5, yp(500) - 3, "A$" + nf(v), "halo vl", ' style="fill:var(--actual)"')); });
  }
  /* what the battery is scheduled to do, labelled where it happens */
  segsOf(2, modeOf).forEach(function(g){
    var c = SEGC[g.m], x = X(g.a) + 0.5, w = X(g.b) - X(g.a) - 1, t = segLabel(g, w);
    s.push(rc(x, yA, w, hA, 'rx="3" style="fill:' + c[0] + '"') + (t ? tx(x + w / 2, yA + hA / 2 + 4, t, "vl", ' text-anchor="middle" style="fill:' + c[1] + '"') : ""));
  });
  /* the conditional offer: an outline, it sells only if the price reaches it */
  var ox = X(o.a) + 0.5, ow = X(o.b) - X(o.a) - 1, ot = "+" + nf(o.mw, 1) + " MW if price ≥ A$" + o.price[0] + "→" + o.price[o.price.length - 1], oy = yO + hO / 2 + 3.5;
  var fr = segsOf(2, function(q){ return standby(q) > 0; }).filter(function(g){ return g.m; }), best = null;
  fr.forEach(function(g){ s.push(rc(X(g.a) + 0.5, yF, X(g.b) - X(g.a) - 1, hF, 'rx="3" style="fill:color-mix(in srgb,var(--cr) 45%,var(--surface))"')); if (!best || g.b - g.a > best.b - best.a) best = g; });
  /* the window is a square dashed outline (not a pill, which reads as a switch); the row's label sits at the start of the
     track like the grid-services row, so the outline is only the time window */
  s.push(rc(ox, yO, ow, hO, 'style="fill:color-mix(in srgb,var(--dis) 8%,var(--surface));stroke:var(--dis)" stroke-width="1.2" stroke-dasharray="3 2"'));
  if (rev) segsOf(2, function(q){ return OFFER_OUT[q] > 0; }).filter(function(g){ return g.m; }).forEach(function(g){ s.push(rc(X(g.a), yO, X(g.b) - X(g.a), hO, 'class="osold" style="fill:var(--dis)"')); });
  if (rev) s.push(tx(ox + ow + 6, oy, "sold " + MONEY.out.offerHH + " of " + (o.b - o.a) + " · " + aud(MONEY.out.offer), "ol"));
  if (best) s.push(tx(X(2) + 8, yF + hF / 2 + 3.5, "earns " + aud(MONEY.plan.fcess), "fl"));

  /* grid-service standby (FCESS), labelled in its longest run */
  /* half-hours where the plan breaks a limit under the current inputs */
  segsOf(0, function(q){ return pc.bad.indexOf(q) >= 0; }).filter(function(g){ return g.m; }).forEach(function(g){
    s.push(rc(X(g.a) - 1, yA - 3, X(g.b) - X(g.a) + 2, hA + 6, 'rx="4" style="fill:none;stroke:var(--bad)" stroke-width="2"'));
  });
  if (pc.bad.length) s.push(tx(X(pc.bad[0]), y0 + 12, pc.badShort, "halo vl", ' style="fill:var(--bad)"'));
  /* the selected half-hour: a mark under the lanes (an outline would cover their labels) and a shaded column below */
  s.push(rc(X(S.sel), yF + hF + 4, IW, 3, 'class="selm"'));
  [0, 8, 16, 24, 32, 40, 48].forEach(function(q){ s.push(tx(X(q), y1 + 15, q < N ? hhmm(q) : "08:00", "", ' text-anchor="' + (q === 0 ? "start" : (q === N ? "end" : "middle")) + '"')); });
  return s.join("") + planMark(yA, y1, true) + hitCols(yA - 3, y1) + "</svg>";
}
/* the day's value as a small waterfall: what earns stacks to the right, wear comes off the end, the net is the third bar */
function vbarHTML(blocked){
  var parts = [["energy", "Energy", "var(--dis)", "var(--surface)"], ["offer", "Offer cleared", "color-mix(in srgb,var(--dis) 14%,var(--surface))", "var(--dis)"],
    ["fcess", "Grid services", "color-mix(in srgb,var(--cr) 45%,var(--surface))", "var(--text)"], ["network", "Network bill saving", "var(--econ)", "var(--text)"],
    ["wear", "Wear", "color-mix(in srgb,var(--bad) 14%,var(--surface))", "var(--bad)"]];
  var rows = [["Plan at 09:00", MONEY.plan]].concat(S.revealed ? [["Outcome, replayed", MONEY.out]] : []);
  var gain = function(m){ return sum(parts.map(function(p){ return Math.max(0, m[p[0]] || 0); })); };
  var max = Math.max.apply(null, rows.map(function(r){ return gain(r[1]); }));
  var pct = function(v){ return (100 * v / max).toFixed(2) + "%"; };
  var seg = function(p, v, lead){ var t = p[1] + " " + (v < 0 ? "−" : "") + nf(Math.abs(v)); return '<i style="' + (lead || "") + "width:" + pct(Math.abs(v)) + ";background:" + p[2] + ";color:" + p[3] + '" title="' + t + '"><span>' + t + "</span></i>"; };
  return rows.map(function(r){
    var m = r[1], negs = parts.filter(function(p){ return (m[p[0]] || 0) < 0; }), net = gain(m) - sum(negs.map(function(p){ return -m[p[0]]; }));
    return '<div class="vb' + (blocked ? " off" : "") + '"><div class="vb-h">' + r[0] + (blocked ? " · simulation only" : "") + "</div>" +
      '<div class="vb-row">' + parts.filter(function(p){ return (m[p[0]] || 0) > 0; }).map(function(p){ return seg(p, m[p[0]]); }).join("") + "</div>" +
      '<div class="vb-row">' + negs.map(function(p, j){ return seg(p, m[p[0]], j ? "" : "margin-left:" + pct(net) + ";"); }).join("") + "</div>" +
      '<div class="vb-row"><i class="vb-net" style="width:' + pct(Math.max(0, net)) + '"><span>Net ' + aud(m.net) + "</span></i></div></div>";
  }).join("");
}
function moneyTable(){
  var m = MONEY, rows = [["Energy", "energy"], ["Conditional offer, cleared", "offer"], ["Grid services (FCESS), paid for availability", "fcess"], ["Network bill saving (import contract)", "network"], ["Battery wear", "wear"]];
  var t = '<tr><th>Item</th><th class="n">Plan at 09:00</th><th class="n">Outcome, replayed</th></tr>';
  var dim = function(x){ return '<span style="color:var(--text-3);font-weight:400">' + x + "</span>"; };
  rows.forEach(function(r){ t += "<tr><td>" + r[0] + '</td><td class="n">' + (r[1] === "offer" ? dim("not counted") : aud(m.plan[r[1]])) + '</td><td class="n">' + (S.revealed ? aud(m.out[r[1]]) : dim("hidden")) + "</td></tr>"; });
  return t + '<tr class="tot"><td>Net for the trading day</td><td class="n">' + aud(m.plan.net) + '</td><td class="n">' + (S.revealed ? aud(m.out.net) : "—") + "</td></tr>";
}
function renderPlan(){
  var pc = planCheck(), blocked = pc.fails.length > 0;
  document.body.classList.toggle("plan-blocked", blocked);
  $("#plan-status").className = "pst " + (blocked ? "bad" : "ok");
  $("#plan-status").innerHTML = blocked
    ? "<b>Plan blocked</b><span>" + pc.why + " · do not execute</span><button class=\"btn sm\" data-inspect>Inspect</button>"
    : "<b>Plan executable</b><span>" + pc.list.length + " checks pass</span>";
  $("#plan-head").textContent = blocked ? "Resolve the export limit before execution" : "Discharge " + nf(P.dis[19], 1) + " MW · " + hhmm(19) + "–" + hhmm(24);
  $("#plan-val").innerHTML = '<div class="cap">Expected profit today</div><div class="dv' + (blocked ? " off" : "") + '">' + aud(MONEY.plan.net) + '</div><div class="chips">' + (blocked
    ? '<span class="tipc bad r"' + noteAttr("The plan breaks a hard limit under the current inputs, so this value is a simulation and must not be executed.") + ">Simulation only · not executable</span>"
    : '<span class="tipc r"' + noteAttr("Energy sales + grid services + network bill saving − battery wear, expected at 09:00 from forecasts. " + glNote("SETTLE")) + '>forecast made at 09:00</span><span class="tipc r"' + noteAttr("Counts the scheduled actions only. " + glNote("EXTRAOFFER") + " The model also assumes the battery does not move the market price.") + ">scheduled actions only</span>") + "</div>";
  $("#sch-chart").innerHTML = laneSVG("#sch-chart") + socSVG("#sch-chart", 190, true);
  $$("#sch-chart .hit").forEach(function(r){ r.addEventListener("click", function(){ S.sel = +r.getAttribute("data-i"); renderPlan(); }); });
  $("#sch-rail").innerHTML = railHTML(S.sel);
  bindReveal($("#sch-rail"));
  $("#plan-vbar").innerHTML = vbarHTML(blocked);
  $("#sch-money").innerHTML = moneyTable();
  $("#sch-checks").innerHTML = pc.list.map(function(c){ var k = c[0] ? "ok" : (c[2] ? "w" : "b");
    return '<div class="ck ' + k + '"' + noteAttr(c[1]) + '><span class="ci ' + k + '">' + (c[0] ? "&#10003;" : "!") + "</span>" + c[3] + "</div>"; }).join("");
}

/* ---------- Customer calls ---------- */
function dayName(d, yr){ var dt = new Date(Date.UTC(2025, 11, 1 + d)); return dt.toLocaleDateString("en-AU", {weekday:"short", day:"numeric", month:"short", timeZone:"UTC"}).replace(",", "") + (yr ? " " + dt.getUTCFullYear() : ""); }
/* ---------- Value by party ---------- */
var SCN = {
  S1:{label:"S1 · energy only", tag:"Hypothetical arrangement", cls:"hyp"},
  S2a:{label:"S2a · today's capacity rule", tag:"Current rule", cls:""},
  S2b:{label:"S2b · assumed 2027 capacity rule", tag:"Assumed future rule · not current revenue", cls:"fut"}
};
/* ---------- Validation ---------- */
var VAL = {
  ins:{note:["", "Training period · 1 Apr 2025 – 30 Jun 2026 · 456 days. The data used to build and choose the method; results here cannot show how it does on new data (in-sample)."], q2a:[[41,58],[96,131],[0.081,0.104]], q2b:[3050,2640,[190,620],4900], days:456, hc:0, cmd:1, lab:"Training period · 1 Apr 2025 – 30 Jun 2026 · 456 days"},
  tseen:{note:["w", "Test, reviewed · 1 Jul – 15 Oct 2026 · 107 days. Not used to build the method, but looked at repeatedly while refining it, so weaker than a blind test (out-of-sample, seen). No Hot Season."], q2a:[[33,45],[71,99],[0.064,0.077]], q2b:[2050,1700,[140,560],3300], days:107, hc:0, cmd:1, lab:"Test, reviewed · 1 Jul – 15 Oct 2026 · 107 days"},
  tnew:{note:["i", "Test, blind · 16–22 Oct 2026 · 7 days. Never looked at before the method was frozen: the only fully blind test so far (out-of-sample, unseen). Too short to prove anything on its own. No Hot Season."], q2a:[[22,27],[40,52],[0.030,0.033]], q2b:[1400,1330,[-20,160],2100], days:7, hc:0, cmd:0, lab:"Test, blind · 16–22 Oct 2026 · 7 days"}
};
function bindRows(sel){ $$(sel + " tr.x").forEach(function(tr){ tr.addEventListener("click", function(){ tr.classList.toggle("open"); $(sel + ' tr[data-dt="' + tr.getAttribute("data-row") + '"]').classList.toggle("show"); }); }); }

/* ---------- Configuration ---------- */
var PRESET = {model:{pay:85, evp:0, fee:0, cap:45, ev:"2", sb:"2", mx:6, res:20, tag:"MODEL SUGGESTION", cls:"src md", note:"The terms this tool suggests, found by searching the training period. The payment level follows an assumed sign-up curve and must come from a pilot. Not a contract."},
              ref:{pay:70, evp:0, fee:0, cap:30, ev:"2", sb:"2", mx:6, res:20, tag:"REFERENCE TERMS", cls:"src rf", note:"Public Synergy Battery Rewards terms used for comparison. Not a Zenith tariff and not a contract."}};
function fillConfig(){
  $$("[data-k]").forEach(function(el){ var k = el.getAttribute("data-k"); if (IN[k] != null) el.value = IN[k]; });
  var p = PRESET[IN.start]; $("#c-start-tag").className = p.cls; $("#c-start-tag").textContent = p.tag; $("#c-start-note").textContent = p.note;
  $$(".sc").forEach(function(b){ b.classList.toggle("on", b.getAttribute("data-sc") === IN.scen); b.setAttribute("aria-checked", b.getAttribute("data-sc") === IN.scen ? "true" : "false"); });
}
function checks(){
  var L = [], blocking = false, changed = false;
  Object.keys(DEF).forEach(function(k){ if (String(IN[k]) !== String(DEF[k])) changed = true; });
  var ev = +IN.ev, sb = +IN.sb, mx = +IN.mx;
  if (ev + sb > mx) { blocking = true; L.push(["b", "Standby + event = " + (ev + sb) + " h is longer than the maximum control " + mx + " h. Blocking: the offer is infeasible.", "c-mx"]); }
  else L.push(["ok", "Standby + event = " + (ev + sb) + " h, within the maximum control " + mx + " h."]);
  if (+IN.res < 0 || +IN.res >= 100) { blocking = true; L.push(["b", "Customer reserve must be at least 0% and below 100%. Blocking.", "c-res"]); }
  if (+IN.cap < 1) { blocking = true; L.push(["b", "Annual cap must be at least 1.", "c-cap"]); }
  else if (+IN.cap < 45) L.push(["w", "Annual cap " + IN.cap + " may run out before late peak days in a heavy season (research: caps of 30 or less failed in 2023-24)."]);
  if (String(IN.exp) !== String(DEF.exp)) L.push(["i", "Export limit " + IN.exp + " MW recomputes the own-battery day, labelled unvalidated scenario."]);
  var pc = planCheck(); if (pc.fails.length) L.push(["w", "Today's plan exports more than " + IN.exp + " MW in " + pc.bad.length + " half-hours, so the plan is blocked until it is recomputed."]);
  if (IN.scen === "S2b") L.push(["i", "S2b uses an assumed future rule; results are not current revenue."]);
  L.push(["i", "Site load is synthetic. The network bill saving depends on it."]);
  return {list:L, blocking:blocking, changed:changed};
}
function renderConfig(){
  var c = checks();
  $("#c-checks").innerHTML = c.list.map(function(x){ var g = {ok:"&#10003;", w:"!", b:"!", u:"?", i:"i"}[x[0]]; return '<li><span class="ci ' + x[0] + '">' + g + "</span><span>" + x[1] + "</span></li>"; }).join("");
  $$(".f").forEach(function(f){ f.classList.remove("err"); });
  c.list.forEach(function(x){ if (x[2]) { var el = document.getElementById(x[2]), f = el && el.closest(".f"); if (f) f.classList.add("err"); } });
}
PAGES.plan = renderPlan; PAGES.config = renderConfig;
function refreshRun(){
  var c = checks(), run = $("#run"), pri = $("#pri"), t, sub, cls;
  document.body.classList.toggle("stale", c.changed);
  if (c.blocking) { cls = "bad"; t = "Inputs blocked"; sub = "fix them in Settings"; pri.textContent = "Recompute"; pri.disabled = true; }
  else if (c.changed) { cls = "stale"; t = "Results stale"; sub = "inputs changed since R‑0142"; pri.textContent = "Recompute"; pri.disabled = false; }
  else { cls = "ok"; t = "Results up to date"; sub = "run R‑0142"; pri.textContent = "Save run"; pri.disabled = false; }
  run.className = "run " + cls; $("#run-t").textContent = t; $("#run-s").textContent = sub;
  $("#strip-st").classList.toggle("show", c.changed);
  $("#st-text").textContent = (c.blocking ? "Inputs changed and blocked by the input check. " : "Inputs changed. ") + "The results shown are from run R‑0142 with the previous inputs.";
  $("#strip-nc").classList.toggle("show", S.nc && c.changed);
}

/* ---------- shell ---------- */
/* two assets, each read as Decide → Improve → Prove; settings sit apart */
var HOME = "plan", ALIAS = {schedule:"plan", forecast:"plan", activation:"calls", validation:"evidence", compare:"evidence", svalid:"evidence", fvalid:"value"};
var SCREENS = {
  plan:{t:"Today's plan", chips:[["step","1 · Decide"],["","training-period day","14 Jan 2026 lies in the training period. " + glNote("TRAIN")]], ctx:function(){ return document.getElementById("plan-dec") ? "Wed 14 Jan 2026 · trading day 08:00–08:00 AWST · plan 09:00" : "Site battery · Wed 14 Jan 2026 · trading day 08:00–08:00 AWST · plan issued 09:00 with information available at 09:00"; }},
  config:{t:"Settings", chips:[], ctx:function(){ return "Inputs for the next run. Any change marks the results stale until they are recomputed."; }}
};
function header(){
  var sc = SCREENS[S.screen];
  $("#ph-title").textContent = sc.t;
  $("#ph-chips").innerHTML = sc.chips.map(function(c){ return '<span class="chip ' + c[0] + '"' + (c[2] ? noteAttr(c[2]) : "") + ">" + c[1] + "</span>"; }).join("");
  $("#ph-ctx").textContent = sc.ctx();
  $("#who-line").innerHTML = whoHTML();
  var dd = S.screen === "calls" ? S.day : TODAY;
  $("#fb-day").textContent = dayName(dd, true);
  $("#mast-day").textContent = new Date(Date.UTC(2025, 11, 1 + dd)).toLocaleDateString("en-AU", {weekday:"long", day:"numeric", month:"long", year:"numeric", timeZone:"UTC"}).replace(",", "");
  document.body.setAttribute("data-screen", S.screen);
}
function render(){
  if (tipEl) tipHide();
  header();
  if (PAGES[S.screen]) PAGES[S.screen]();
  refreshRun();
}
function go(name){
  name = ALIAS[name] || name;
  if (!SCREENS[name] || !document.getElementById("s-" + name)) name = HOME;
  S.screen = name;
  $$(".screen").forEach(function(s){ s.classList.toggle("on", s.id === "s-" + name); });
  $$(".nav").forEach(function(b){ var on = b.getAttribute("data-go") === name; b.classList.toggle("on", on); if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  /* the asset switch and its row of pages are on when they list the open page */
  $$("[data-grp]").forEach(function(e){ e.classList.toggle("on", e.getAttribute("data-grp").split(" ").indexOf(name) >= 0); });
  try { history.replaceState(null, "", "#" + name); } catch (e) {}
  render(); window.scrollTo(0, 0);
}
/* delegated, so links drawn later (for example inside a summary tile) work too */
document.addEventListener("click", function(e){ var b = e.target && e.target.closest ? e.target.closest("[data-go]") : null; if (b) go(b.getAttribute("data-go")); });
function segBind(attr, key, after){
  $$("[" + attr + "]").forEach(function(b){ b.addEventListener("click", function(){
    S[key] = b.getAttribute(attr);
    $$("[" + attr + "]").forEach(function(o){ o.classList.toggle("on", o.getAttribute(attr) === S[key]); });
    if (after) after(); render(); }); });
}
function setReveal(v){ S.revealed = v; $$("[data-po]").forEach(function(o){ o.classList.toggle("on", o.getAttribute("data-po") === (v ? "outcome" : "plan")); }); $$(".po-out").forEach(function(e){ e.hidden = !v; }); $$("[data-po] .lk").forEach(function(e){ e.style.display = v ? "none" : ""; }); }
$$("[data-po]").forEach(function(b){ b.addEventListener("click", function(){ setReveal(b.getAttribute("data-po") === "outcome"); render(); }); });
function bindReveal(root){ $$("[data-reveal]").forEach(function(b){ if (root.contains(b)) b.addEventListener("click", function(){ setReveal(true); render(); }); }); }
$$("[data-k]").forEach(function(el){ el.addEventListener("change", function(){
  var k = el.getAttribute("data-k"); IN[k] = el.type === "number" ? (el.value === "" ? "" : +el.value) : el.value;
  if (k === "start") { var p = PRESET[el.value]; ["pay","evp","fee","cap","ev","sb","mx","res"].forEach(function(q){ IN[q] = p[q]; }); fillConfig(); }
  S.nc = false; render(); }); });
$$(".sc").forEach(function(b){ b.addEventListener("click", function(){ IN.scen = b.getAttribute("data-sc"); fillConfig(); S.nc = false; render(); }); });
$("#revert").addEventListener("click", function(){ IN = JSON.parse(JSON.stringify(DEF)); S.nc = false; fillConfig(); render(); toast("Inputs reverted to run R‑0142. Results are up to date again."); });
var toastT;
function toast(m){ var t = $("#toast"); t.textContent = m; t.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(function(){ t.classList.remove("show"); }, 4500); }
var rzT;
window.addEventListener("resize", function(){ clearTimeout(rzT); rzT = setTimeout(render, 150); });
/* redraw a chart as soon as its box changes size (layout settling, sidebar, fonts); a filling box also tracks height */
if (window.ResizeObserver) {
  var boxSize = {}, ro = new ResizeObserver(function(entries){
    var changed = false;
    entries.forEach(function(e){
      var key = Math.round(e.contentRect.width) + "x" + (e.target.hasAttribute("data-fill") ? Math.round(e.contentRect.height) : 0);
      if (boxSize[e.target.id] !== undefined && boxSize[e.target.id] !== key) changed = true;
      boxSize[e.target.id] = key;
    });
    if (changed) requestAnimationFrame(render);
  });
  $$(".chartbox").forEach(function(el){ if (el.id) ro.observe(el); });
}
document.addEventListener("keydown", function(e){
  if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
  var dl = e.key === "ArrowRight" ? 1 : (e.key === "ArrowLeft" ? -1 : 0); if (!dl) return;
  if (S.screen === "plan") { S.sel = Math.max(0, Math.min(N - 1, S.sel + dl)); renderPlan(); e.preventDefault(); }
  if (S.screen === "calls") { S.day = Math.max(0, Math.min(DAYS - 1, S.day + dl)); render(); e.preventDefault(); }
});

/* ---------- chart interactions: hover table, legend switches, CSV ---------- */
function rng(lo, hi){ return lo == null ? "–" : "A$" + nf(lo) + "–" + nf(hi); }
var TIPS = {
  day:function(k){
    var v = P.dis[k] - P.chg[k], r = [["sq", "var(--" + (v < 0 ? "chg" : "dis") + ")", "Power", (v > 0 ? "+" : (v < 0 ? "−" : "")) + nf(Math.abs(v), 1) + " MW"]];
    if (offerAt(k)) {
      r.push(["off", "", "Sell more", "≥ A$" + offerAt(k).price]);
      if (S.revealed) r.push(["", "", "Sold", OFFER_OUT[k] > 0 ? aud(OFFER_OUT[k] * 0.5 * P.actual[k]) : "no"]);
    }
    r.push(["dsh", "var(--price)", "Forecast", P.price[k] == null ? "–" : "A$" + nf(P.price[k])]);
    if (S.revealed) r.push(["ln", "var(--actual)", "Actual", "A$" + nf(P.actual[k])]);
    return {h:hhmm(k) + "–" + hhmm(k + 1), s:"", r:r};
  },
  soc:function(k){
    var r = [];
    if (k >= 2) r.push(["dsh", "var(--soc)", "Planned", nf(SOC[k], 1) + " → " + nf(SOC[k + 1], 1) + " MWh"]);
    if (k < 2 || S.revealed) r.push(["area", "var(--actual)", "Actual", nf(SOCOUT[k], 1) + " → " + nf(SOCOUT[k + 1], 1) + " MWh"]);
    r.push(["", "", "Minimum", nf(FLOOR[k], 1) + " MWh"]);
    return {h:hhmm(k) + "–" + hhmm(k + 1), s:"", r:r};
  }
};
var tipEl = null, tipKey = "";
function tipShow(svg, k, ev){
  var name = svg.getAttribute("data-tip"), f = TIPS[name]; if (!f) return;
  if (!tipEl) { tipEl = document.createElement("div"); tipEl.className = "ctip"; tipEl.setAttribute("role", "tooltip"); document.body.appendChild(tipEl); }
  if (tipKey !== name + k) {
    var t = f(k); tipKey = name + k;
    tipEl.innerHTML = '<div class="ct-h"><b>' + t.h + "</b>" + (t.s ? "<span>" + t.s + "</span>" : "") + "</div>" + (t.r.length ? "<table>" + t.r.map(function(r){ return "<tr><td>" + (r[0] ? swatch(r[0], r[1]) : "") + "</td><td>" + r[2] + '</td><td class="n">' + r[3] + "</td></tr>"; }).join("") + "</table>" : "");
  }
  tipEl.style.display = "block";
  var w = tipEl.offsetWidth, h = tipEl.offsetHeight, x = ev.clientX + 16, y = ev.clientY + 14;
  if (x + w > window.innerWidth - 8) x = ev.clientX - w - 16;
  if (y + h > window.innerHeight - 8) y = Math.max(8, ev.clientY - h - 14);
  tipEl.style.left = x + "px"; tipEl.style.top = y + "px";
  /* the crosshair follows on every chart that shares this time axis */
  $$('svg[data-xg="' + svg.getAttribute("data-xg") + '"]').forEach(function(g){
    var xh = g.querySelector(".xh"); if (!xh) return;
    var cx = (+g.getAttribute("data-x0") + (k + 0.5) * +g.getAttribute("data-iw")).toFixed(1);
    xh.setAttribute("x1", cx); xh.setAttribute("x2", cx); xh.style.visibility = "visible";
  });
}
function tipHide(){ if (tipEl) tipEl.style.display = "none"; tipKey = ""; $$(".xh").forEach(function(l){ l.style.visibility = "hidden"; }); }
document.addEventListener("mousemove", function(e){
  var hit = e.target && e.target.closest ? e.target.closest(".hit") : null, svg = hit && hit.closest("svg[data-tip]");
  if (!svg) { if (tipKey || (tipEl && tipEl.style.display === "block")) tipHide(); return; }
  tipShow(svg, +(hit.getAttribute("data-i") || hit.getAttribute("data-d")), e);
});
window.addEventListener("scroll", tipHide, true);

/* CSV of exactly what the chart shows: no actuals before they are revealed, no future days in the decision view */
function isoDay(d){ return new Date(Date.UTC(2025, 11, 1 + d)).toISOString().slice(0, 10); }
function csvDownload(kind){
  if (document.body.classList.contains("stale")) { toast("Export is off while results are stale."); return; }
  var rev = S.revealed, head, rows = [], name, meta, k;
  if (kind === "day") {
    head = ["interval_start", "interval_end", "price_forecast_p50_aud_per_mwh", "price_p10_aud_per_mwh", "price_p90_aud_per_mwh", "discharge_mw", "conditional_offer_mw", "offer_floor_aud_per_mwh", "charge_mw", "soc_start_mwh", "soc_end_mwh", "reserve_floor_mwh"].concat(rev ? ["price_actual_aud_per_mwh", "soc_end_actual_mwh"] : []);
    for (k = 0; k < N; k++) rows.push([hhmm(k), hhmm(k + 1), P.price[k], P.lo[k], P.hi[k], P.dis[k], offerAt(k) ? offerAt(k).mw : 0, offerAt(k) ? offerAt(k).price : null, P.chg[k], SOC[k], SOC[k + 1], FLOOR[k]].concat(rev ? [P.actual[k], SOCOUT[k + 1]] : []));
    name = "R-0142_plan_2026-01-14"; meta = "trading day 14 Jan 2026 08:00-08:00 AWST, plan issued 09:00" + (rev ? " | includes revealed actuals" : "");
  } else {
    var o = OFF[S.offer], dec = S.view === "decision", calls = callsOf(o);
    head = ["date", "daily_peak_mw", "peak_is_forecast", "flagged", "called"].concat(dec ? [] : ["official_peak_day_hindsight"]);
    for (var d = 0; d < DAYS; d++) { if (dec && d > S.day) break;
      rows.push([isoDay(d), dec && d === S.day ? fcPeak(d) : ACT[d], dec && d === S.day ? "yes" : "no", o.flags.indexOf(d) >= 0 ? "yes" : "no", calls.indexOf(d) >= 0 ? "yes" : "no"].concat(dec ? [] : [PEAK.indexOf(d) >= 0 ? "yes" : "no"])); }
    name = "R-0142_calls_" + (dec ? "as_of_" + isoDay(S.day) : "season_review_hindsight"); meta = "Hot Season 2025-26, " + o.short + ", scenario " + SCN[RUN.scen].label;
  }
  var txt = "# Run R-0142 | HISTORICAL REPLAY | ILLUSTRATIVE placeholder values from the design prototype | " + meta + "\n" + head.join(",") + "\n" +
    rows.map(function(r){ return r.map(function(v){ return v == null ? "" : (typeof v === "number" ? String(+v.toFixed(3)) : v); }).join(","); }).join("\n") + "\n";
  var a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt], {type:"text/csv"})); a.download = name + ".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  toast("Downloaded " + name + ".csv. Illustrative values.");
}
$$(".ctools[data-csv]").forEach(function(t){
  t.innerHTML = '<button class="ib" data-csvdl="' + t.getAttribute("data-csv") + '" title="Download CSV" aria-label="Download CSV"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5v8M4.5 7L8 10.5 11.5 7M2.5 12.5v1.5h11v-1.5"/></svg></button>';
});
document.addEventListener("click", function(e){
  var t = e.target && e.target.closest ? e.target : null; if (!t) return;
  var b = t.closest("[data-lg]"); if (b) { var key = b.getAttribute("data-lg"); HID[key] = !HID[key]; tipHide(); render(); return; }
  var c = t.closest("[data-csvdl]"); if (c) { csvDownload(c.getAttribute("data-csvdl")); return; }
  var sc = t.closest("[data-scroll]"); if (sc) { e.preventDefault(); var to = document.getElementById(sc.getAttribute("href").slice(1)); if (to && to.scrollIntoView) to.scrollIntoView({behavior:"smooth", block:"start"}); return; }
  if (t.closest("[data-inspect]")) { var pc = planCheck(); if (pc.bad.length) S.sel = pc.bad[0]; renderPlan(); var el = document.getElementById("sch-chart"); if (el && el.scrollIntoView) el.scrollIntoView({block:"center"}); }
});

/* style options: design review only, not part of the product; keeps the open screen */
$$(".sty a[data-to]").forEach(function(a){ a.addEventListener("click", function(e){ e.preventDefault(); location.href = a.getAttribute("data-to") + location.hash; }); });
