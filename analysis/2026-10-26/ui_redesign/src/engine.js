/* GreenSpark decision instrument. Every number comes from window.GS_DATA, which build.py fills from data/*.json (saved
   model outputs, see DATA_CONTRACT.md). Where the data has no value the page says Not available; nothing is invented.
   Inputs in Settings only mark results stale, except the export limit, which re-checks today's plan against it. */
(function(){
"use strict";
var GS = window.GS_DATA || {}, SITE = GS.site_days || [], SUM = GS.site_summary || null, FL = GS.fleet || null;
var N = 48, X0 = 84, IW = 16, RM = 52;

/* ---------- small helpers ---------- */
function X(i){ return X0 + i * IW; }
function p2(n){ return (n < 10 ? "0" : "") + n; }
function hhmm(i){ var m = (480 + 30 * i) % 1440; return p2(Math.floor(m / 60)) + ":" + p2(m % 60); }
function nf(v, d){ d = d || 0; return Number(v).toLocaleString("en-AU", {minimumFractionDigits:d, maximumFractionDigits:d}); }
function aud(v, d){ return (v < -0.0005 ? "−" : "") + "A$" + nf(Math.abs(v), d); }
function sgn(v, d){ return (v < -0.0005 ? "−" : "+") + "A$" + nf(Math.abs(v), d); }
function num(v){ return typeof v === "number" && isFinite(v); }
function $(s){ return document.querySelector(s) || document.createElement("div"); }
function $$(s){ return Array.prototype.slice.call(document.querySelectorAll(s)); }
function sum(a){ return a.reduce(function(t, v){ return t + (num(v) ? v : 0); }, 0); }
function esc(t){ return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"); }
function noteAttr(t){ return ' tabindex="0" data-note="' + esc(t) + '"'; }
function maxOf(a){ var m = -Infinity; (a || []).forEach(function(v){ if (num(v) && v > m) m = v; }); return m; }
function minOf(a){ var m = Infinity; (a || []).forEach(function(v){ if (num(v) && v < m) m = v; }); return m; }
function na(what, why){ return '<div class="na-box"><b>' + what + " · Not available.</b> " + (why || "") + "</div>"; }
var MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function dshort(iso){ if (!iso) return ""; var p = iso.split("-"); return +p[2] + " " + MON[+p[1] - 1] + " " + p[0]; }
/* the three evaluation periods are never mixed: each carries its own label, dates and day count */
function perKey(p){ var l = ((p && p.label) || "").toLowerCase(); return l.indexOf("unseen") >= 0 ? "tnew" : (l.indexOf("test") >= 0 ? "tseen" : "ins"); }
var PER_NAME = {ins:"In-sample", tseen:"Test, seen", tnew:"Test, unseen"};
function perShort(p){ return PER_NAME[perKey(p)] + (p && p.days ? " · " + p.days + " d" : ""); }
function perLong(p){ if (!p) return ""; return PER_NAME[perKey(p)] + " · " + dshort(p.from) + " – " + dshort(p.to) + (p.days ? " · " + p.days + " days" : ""); }
function perChip(p){ return '<span class="chip ' + (perKey(p) === "ins" ? "per-ins" : "per-test") + '"' + noteAttr(perLong(p) + (perKey(p) === "ins" ? ". Used for training and selection, so it does not show future performance." : ". Reported only; never used to choose the method.")) + ">" + perShort(p) + "</span>"; }

/* ---------- state ---------- */
var S = {screen:"today", di:0, sel:null, lane:"plan", revealed:false, view:"decision", offer:"custom", cday:0, vscen:"S3", vper:null, vunit:"fleet"};
/* the customer fleet opens on the same day as the in-sample site day when the season has it (2 Feb 2026, an official peak day) */
(function(){ var days = (FL && FL.season_calls && FL.season_calls.days) || [], want = SITE.length ? SITE[0].day : "", at = -1;
  days.forEach(function(d, i){ if (d.day === want) at = i; }); S.cday = at >= 0 ? at : Math.max(0, days.length - 1); })();
var DEF = {exp:"10", fcess:"RR, RL, CR, CL", pay:85, cap:45, ev:"2", sb:"2", mx:6, res:20};
var IN = JSON.parse(JSON.stringify(DEF));

/* ---------- one site day, read from the export ---------- */
var ACT = {charge:"chg", chg:"chg", discharge:"dis", dis:"dis", shave:"shave", hold:"hold", pre:"pre", idle:"hold"};
function arr(a){ return Array.isArray(a) && a.length ? a : null; }
function model(i){
  var d = SITE[i == null ? S.di : i]; if (!d) return null;
  if (d._m) return d._m;
  var p = d.plan || {}, pr = d.price || {}, of = d.offers_0900 || {}, o = d.outcome || {}, f0 = num(p.first_interval) ? p.first_interval : 2, k;
  var bat = arr(p.battery_mw) || [], fc = p.fcess_mw || {};
  var m = {d:d, f0:f0, bat:bat, soc:arr(p.soc_mwh), floor:arr(p.reserve_floor_mwh), site:arr(p.site_reserve_floor_mwh), meter:arr(p.meter_mw), load:arr(p.load_fc_mw),
    fc:arr(pr.forecast_p50), p10:arr(pr.forecast_p10), p90:arr(pr.forecast_p90), actual:arr(pr.actual), amax:arr(pr.actual_max_5min), spike:arr(pr.spike_prob_ge_200), prot:arr(of.protected_mwh),
    outBat:arr(o.battery_mw), outSoc:arr(o.soc_mwh), outMeter:arr(o.meter_mw), cleared:arr(o.offer_cleared_mw),
    cap:(d.battery && d.battery.energy_mwh) || 40, pmax:(d.battery && d.battery.power_mw) || 10, lim:d.limits || {}, act:[], cond:[], raise:[], lower:[]};
  for (k = 0; k < N; k++) {
    var a = (p.action || [])[k], b = bat[k], capk = (p.action_cap || [])[k];
    a = a ? ACT[String(a).toLowerCase()] : null;
    if (!a) a = k < f0 ? "pre" : (!num(b) || Math.abs(b) < 0.05 ? "hold" : (b > 0 ? "dis" : "chg"));
    /* the model marks both kinds of shaving; the cap says which: the contract demand (CMD) or the capacity-charge peak (IRCR) */
    if (a === "shave" && capk === "ircr") a = "peak";
    m.act.push(a);
    var tr = (of.by_halfhour || [])[k], fin = (tr || []).filter(function(t){ return num(t.price_aud_per_mwh) && t.mw > 0.0005; }), cp = (tr || []).filter(function(t){ return !num(t.price_aud_per_mwh) && t.mw > 0.0005; });
    m.cond.push(tr == null ? null : {mw:sum(fin.map(function(t){ return t.mw; })), min:fin.length ? minOf(fin.map(function(t){ return t.price_aud_per_mwh; })) : null,
      max:fin.length ? maxOf(fin.map(function(t){ return t.price_aud_per_mwh; })) : null, capMw:sum(cp.map(function(t){ return t.mw; })), tr:tr});
    m.raise.push((fc.reg_raise || [])[k] + (fc.cont_raise || [])[k] || 0);
    m.lower.push((fc.reg_lower || [])[k] + (fc.cont_lower || [])[k] || 0);
  }
  m.val = p.value_at_forecast_aud || null;
  m.money = o.money_aud || null;
  m.base = d.baseline_same_day || null;
  d._m = m; return m;
}
/* runs of half-hours that share a key, from the first planned half-hour on */
function runs(from, key){ var g = [], k = from; while (k < N) { var v = key(k), e = k; while (e + 1 < N && key(e + 1) === v) e++; g.push({v:v, a:k, b:e + 1}); k = e + 1; } return g; }
function planSegs(m){ return runs(0, function(k){ return m.act[k]; }); }
function condSegs(m){ return runs(m.f0, function(k){ var c = m.cond[k]; return !!(c && c.mw > 0.05); }).filter(function(g){ return g.v; }); }
function fcessSegs(m){ return runs(m.f0, function(k){ return m.raise[k] + m.lower[k] > 0.05; }).filter(function(g){ return g.v; }); }
function segAt(list, k){ for (var i = 0; i < list.length; i++) if (k >= list[i].a && k < list[i].b) return list[i]; return null; }
function peakMw(m, g){ var v = 0; for (var k = g.a; k < g.b; k++) if (num(m.bat[k])) v = Math.max(v, Math.abs(m.bat[k])); return v; }
var ANAME = {dis:"Discharge", peak:"Discharge", chg:"Charge", shave:"Shave", hold:"Hold", pre:"Earlier plan"}, AICON = {dis:"↑", peak:"↑", chg:"↓", shave:"↧", hold:"‖", pre:"·"};
var ATAIL = {peak:" · IRCR peak", shave:" · CMD"};
function segName(m, g, tail){ var mw = peakMw(m, g); return g.v === "hold" || g.v === "pre" ? ANAME[g.v] : ANAME[g.v] + " " + nf(mw, 1) + " MW" + (tail ? ATAIL[g.v] || "" : ""); }
var AWHY = {peak:"At 09:00 the day was flagged as a possible capacity-charge (IRCR) peak day, so the battery covers the factory and keeps meter import at 0 MW in the peak window.",
  shave:"The battery holds factory import at the contract maximum demand (CMD) on the forecast load.", chg:"Energy is cheap enough now to store for later in the day.",
  dis:"The forecast price is worth more than keeping the energy.", hold:"No trade beats keeping the energy; the battery stays on standby for frequency services."};
function winOf(a, b){ return hhmm(a) + "–" + (b === N ? "08:00" : hhmm(b)); }

/* ---------- is today's plan executable under the current inputs? ---------- */
/* the export's own checks, with the meter-export check redone when the export limit in Settings changes */
function planCheck(){
  var m = model(); if (!m) return {list:[], fails:[], warns:[], none:[], bad:[], status:"nc"};
  var list = (m.d.checks_plan || []).map(function(c){ return {id:c.id, label:c.label, status:c.status, worst:c.worst, detail:c.detail}; });
  var exp = +IN.exp, bad = [];
  if (String(IN.exp) !== String(m.lim.export_mw != null ? m.lim.export_mw : DEF.exp) && m.meter) {
    /* the connection limit must hold even if all raise standby is called: export = raise headroom − meter flow on the forecast load */
    var worst = 0;
    for (var k = m.f0; k < N; k++) { var e = (m.raise[k] || 0) - (m.meter[k] || 0); worst = Math.max(worst, e); if (e > exp + 1e-6) bad.push(k); }
    var c = {id:"meter_export", label:"Meter export ≤ " + exp + " MW", status:bad.length ? "fail" : "pass", worst:nf(worst, 1) + " MW",
      detail:"Export limit from Settings (" + exp + " MW, unvalidated scenario), checked on the forecast load as if all FCESS raise standby were called." + (bad.length ? " Above it in " + bad.length + " half-hours, " + winOf(bad[0], bad[bad.length - 1] + 1) + "." : "")};
    var at = list.map(function(x){ return x.id; }).indexOf("meter_export");
    if (at >= 0) list[at] = c; else list.push(c);
  }
  var fails = list.filter(function(c){ return c.status === "fail"; }), warns = list.filter(function(c){ return c.status === "warn"; }), none = list.filter(function(c){ return c.status === "not_checked"; });
  return {list:list, fails:fails, warns:warns, none:none, bad:bad, status:!list.length ? "nc" : (fails.length ? "blocked" : "pass")};
}

/* ---------- svg helpers ---------- */
function stepPath(vals, yf, from, to){
  var d = "", pen = false;
  for (var k = from; k < to; k++) { var v = vals[k]; if (!num(v)) { pen = false; continue; } var y = yf(v).toFixed(2); d += (pen ? "V" + y : "M" + X(k).toFixed(2) + "," + y) + "H" + X(k + 1).toFixed(2); pen = true; }
  return d;
}
function bandPath(lo, hi, yf, from, to){
  var d = "M" + X(from).toFixed(2) + "," + yf(hi[from]).toFixed(2), k;
  for (k = from; k < to; k++) d += "V" + yf(hi[k]).toFixed(2) + "H" + X(k + 1).toFixed(2);
  for (k = to - 1; k >= from; k--) d += "V" + yf(lo[k]).toFixed(2) + "H" + X(k).toFixed(2);
  return d + "Z";
}
function linePath(vals, yf, from, to){ var d = ""; for (var b = from; b <= to; b++) if (num(vals[b])) d += (d ? "L" : "M") + X(b).toFixed(2) + "," + yf(vals[b]).toFixed(2); return d; }
function tx(x, y, t, cls, extra){ return '<text x="' + (+x).toFixed(1) + '" y="' + (+y).toFixed(1) + '"' + (cls ? ' class="' + cls + '"' : "") + (extra || "") + ">" + t + "</text>"; }
function ln(x1, y1, x2, y2, st){ return '<line x1="' + (+x1).toFixed(2) + '" y1="' + (+y1).toFixed(2) + '" x2="' + (+x2).toFixed(2) + '" y2="' + (+y2).toFixed(2) + '" ' + st + "/>"; }
function rc(x, y, w, h, st){ return '<rect x="' + (+x).toFixed(2) + '" y="' + (+y).toFixed(2) + '" width="' + Math.max(0, w).toFixed(2) + '" height="' + Math.max(0, h).toFixed(2) + '" ' + st + "/>"; }
function HATCH(id){ return '<pattern id="' + id + '" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" style="fill:var(--hatch-bg)"/><line x1="0" y1="0" x2="0" y2="6" style="stroke:var(--hatch-ln)" stroke-width="2"/></pattern>'; }
function CONDPAT(id){ return '<pattern id="' + id + '" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" style="fill:var(--cond-soft)"/><rect width="2.2" height="6" style="fill:var(--cond)" opacity=".55"/></pattern>'; }
function fitW(sel, min, dflt){ var el = document.querySelector(sel); if (!el || !el.clientWidth) return dflt; return Math.max(min, Math.floor(el.clientWidth)); }
function setW(W){ IW = (W - RM - X0) / N; return W; }
function fits(t, fs, w){ return String(t).length * fs * 0.56 <= w - 8; }
function mid(a, b){ return (X(a) + X(b)) / 2; }
function nice(v){ var s = Math.pow(10, Math.floor(Math.log10(Math.max(1, v)))), f = v / s; return (f <= 1 ? 1 : f <= 1.5 ? 1.5 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 3 ? 3 : f <= 4 ? 4 : f <= 5 ? 5 : f <= 6 ? 6 : f <= 8 ? 8 : 10) * s; }
function svgOpen(W, H, tip, label){ return '<svg viewBox="0 0 ' + W + " " + H + '" class="lanes chart"' + (tip ? ' data-tip="' + tip + '" data-xg="day" data-x0="' + X0 + '" data-iw="' + IW.toFixed(3) + '"' : "") + ' role="img" aria-label="' + esc(label) + '">'; }
function axisX(y1, d){
  var s = [];
  [0, 8, 16, 24, 32, 40, 48].forEach(function(q){ s.push(ln(X(q), y1, X(q), y1 + 4, 'style="stroke:var(--axis)"') + tx(X(q), y1 + 16, q < N ? hhmm(q) : "08:00", "", ' text-anchor="' + (q === 0 ? "start" : (q === N ? "end" : "middle")) + '"')); });
  return s.join("");
}
function planMark(m, y0, y1, label){ return ln(X(m.f0), y0 - (label ? 8 : 0), X(m.f0), y1, 'style="stroke:var(--mark)" stroke-width="1"') + (label ? tx(X(m.f0), y0 - 12, "09:00 PLAN", "mk9", ' text-anchor="middle"') : ""); }
function hitCols(y0, y1, lane){
  var s = ['<line class="xh" x1="0" x2="0" y1="' + y0 + '" y2="' + y1 + '"/>'];
  for (var k = 0; k < N; k++) s.push('<rect class="hit" data-i="' + k + '"' + (lane ? ' data-lane="' + lane + '"' : "") + ' x="' + X(k).toFixed(2) + '" y="' + y0 + '" width="' + IW.toFixed(2) + '" height="' + (y1 - y0) + '"/>');
  return s.join("");
}

/* ---------- layer 2: the day as lanes over the price, then the energy held ---------- */
var SEGC = {dis:["var(--dis)", "#FFFFFF"], peak:["var(--dis)", "#FFFFFF"], shave:["var(--shave)", "var(--dis)"], chg:["var(--chg)", "var(--chg-text)"], hold:["var(--hold)", "var(--hold-text)"]};
function dayLanesSVG(m){
  var W = setW(fitW("#t-chart", 560, 900)), XR = X(N), s = [], rev = S.revealed, pc = planCheck(), k;
  var yA = 26, hA = 30, yO = yA + hA + 6, hO = 24, yF = yO + hO + 6, hF = 12, y0 = yF + hF + 34, y1 = y0 + 160, H = y1 + 24;
  /* the price range covers the forecast and the offer triggers; it stays put when the outcome is revealed, so an actual
     price beyond it is clipped and marked instead of rescaling the chart */
  var condMins = m.cond.map(function(c){ return c && c.mw > 0.05 ? c.min : null; });
  var top = nice(Math.max(100, maxOf(m.p90 || m.fc || []), maxOf(condMins)) * 1.08);
  var bot = Math.min(0, Math.floor(Math.max(-300, minOf([].concat(m.fc || [], m.p10 || [], rev ? (m.actual || []) : [], [0]))) / 50) * 50);
  var yp = function(p){ return y1 - (Math.max(bot, Math.min(p, top)) - bot) * (y1 - y0) / (top - bot); };
  s.push(svgOpen(W, H, "day", "Plan for " + m.d.weekday_label + ": scheduled actions, conditional offers, FCESS standby and the price forecast") + "<defs>" + HATCH("hDy") + CONDPAT("cDy") + "</defs>");
  [[yA, hA, "Scheduled"], [yO, hO, "Conditional"], [yF, hF, "FCESS"]].forEach(function(l){ s.push(rc(X0, l[0], XR - X0, l[1], 'rx="3" style="fill:var(--surface-2)"') + tx(X0 - 8, l[0] + l[1] / 2 + 3.5, l[2], "lnm", ' text-anchor="end"')); });
  [8, 16, 24, 32, 40].forEach(function(q){ s.push(ln(X(q), yA, X(q), y1, 'class="gl"')); });
  s.push(rc(X(0), yA, X(m.f0) - X(0), y1 - yA, 'fill="url(#hDy)"'));
  /* price panel */
  var ticks = [bot, (bot + top) / 2, top].filter(function(v, i, a){ return a.indexOf(v) === i; });
  if (bot < 0) ticks.push(0);
  ticks.forEach(function(v){ s.push(ln(X0, yp(v), XR, yp(v), v === 0 ? 'style="stroke:var(--axis)"' : 'class="gl"') + tx(X0 - 8, yp(v) + 3.5, (v < 0 ? "−" : "") + nf(Math.abs(v)), "", ' text-anchor="end"')); });
  s.push(tx(X(m.f0) + 6, y0 - 10, "Price, A$/MWh", "lnm halo"));
  if (S.sel != null) s.push(rc(X(S.sel), y0, IW, y1 - y0, 'style="fill:var(--sel)"'));
  if (m.p10 && m.p90) {
    s.push('<path d="' + bandPath(m.p10, m.p90, yp, m.f0, N) + '" style="fill:var(--price)" opacity="0.07"/>');
    var bk = m.f0; for (k = m.f0; k < N; k++) if (num(m.p90[k]) && m.p90[k] > m.p90[bk]) bk = k;
    if (!rev) s.push(tx(X(bk + 1) + 4, yp(Math.min(top, m.p90[bk])) + 10, "P10–P90", "lim halo"));
  }
  if (m.fc) s.push('<path d="' + stepPath(m.fc, yp, m.f0, N) + '" fill="none" style="stroke:var(--price)" stroke-width="1.8" stroke-dasharray="5 3"/>');
  /* the lowest conditional threshold in each half-hour, where the plan offers more */
  s.push('<path d="' + stepPath(condMins, yp, m.f0, N) + '" fill="none" style="stroke:var(--cond)" stroke-width="1.6" stroke-dasharray="3 2"/>');
  if (rev && m.actual) s.push('<path d="' + stepPath(m.actual, yp, 0, N) + '" fill="none" style="stroke:var(--price)" stroke-width="2"/>');
  /* name the lines once, at the right edge, and the forecast peak where it is */
  if (m.fc) {
    var pk = m.f0; for (k = m.f0; k < N; k++) if (num(m.fc[k]) && m.fc[k] > m.fc[pk]) pk = k;
    s.push(tx(X(pk) + IW / 2, yp(m.fc[pk]) - 7, "A$" + nf(m.fc[pk]), "halo vl", ' text-anchor="middle" style="fill:var(--price)"'));
  }
  var lastC = -1; for (k = N - 1; k >= m.f0; k--) if (num(condMins[k])) { lastC = k; break; }
  if (lastC >= 0) s.push(tx(XR + 4, yp(condMins[lastC]) + 3.5, "trigger", "halo vl", ' style="fill:var(--cond-text)"'));
  if (m.fc) s.push(tx(XR + 4, yp(m.fc[N - 1]) + 3.5, "forecast", "halo lim"));
  if (rev && m.actual) { var ya = yp(m.actual[N - 1]), yf = m.fc ? yp(m.fc[N - 1]) : -99; if (Math.abs(ya - yf) < 11) ya = yf + (ya >= yf ? 11 : -11); s.push(tx(XR + 4, ya + 3.5, "actual", "halo lim", ' style="fill:var(--price);font-weight:600"')); }
  if (rev && m.actual) { var am = 0; for (k = 0; k < N; k++) if (m.actual[k] > m.actual[am]) am = k; if (m.actual[am] > top) s.push(tx(X(am) + IW / 2, yp(top) - 4, "A$" + nf(m.actual[am]) + " ↑", "halo vl", ' text-anchor="middle" style="fill:var(--price)"')); }
  if (!m.fc) s.push(tx((X(m.f0) + XR) / 2, (y0 + y1) / 2, "Price forecast not available", "lim", ' text-anchor="middle"'));
  /* scheduled actions, labelled where they happen */
  planSegs(m).forEach(function(g){
    if (g.v === "pre") return;
    var c = SEGC[g.v] || SEGC.hold, x = X(g.a) + 0.5, w = X(g.b) - X(g.a) - 1, full = AICON[g.v] + " " + segName(m, g, true), short = AICON[g.v] + " " + (g.v === "hold" ? "Hold" : nf(peakMw(m, g), 1));
    var on = S.lane === "plan" && S.sel != null && S.sel >= g.a && S.sel < g.b;
    s.push('<g class="seg-b" data-lane="plan" data-a="' + g.a + '">' + rc(x, yA, w, hA, 'rx="3" style="fill:' + c[0] + '"' + (on ? ' class="seg-on"' : "")) +
      (fits(full, 11, w) ? tx(x + w / 2, yA + hA / 2 + 4, full, "vl", ' text-anchor="middle" style="fill:' + c[1] + ';pointer-events:none"') : (fits(short, 11, w) ? tx(x + w / 2, yA + hA / 2 + 4, short, "vl", ' text-anchor="middle" style="fill:' + c[1] + ';pointer-events:none"') : "")) + "</g>");
  });
  /* conditional offers: how much more than the plan the 09:00 submission would sell in each half-hour, as a striped area
     (full lane = the battery's power rating); each runs only if the 5-minute price reaches its trigger, drawn amber below */
  var cy = function(v){ return yO + hO - hO * Math.min(1, Math.max(0, v) / m.pmax); }, cv = m.cond.map(function(c){ return c && c.mw > 0.05 ? c.mw : null; });
  s.push('<g class="seg-b" data-lane="cond">' + rc(X(m.f0), yO, XR - X(m.f0), hO, 'style="fill:transparent"'));
  for (k = m.f0; k < N; k++) if (num(cv[k])) s.push(rc(X(k), cy(cv[k]), IW, yO + hO - cy(cv[k]), 'fill="url(#cDy)"'));
  s.push('<path d="' + stepPath(cv, cy, m.f0, N) + '" fill="none" style="stroke:var(--cond)" stroke-width="1.2"/></g>');
  if (S.lane === "cond" && S.sel != null && num(cv[S.sel])) s.push(rc(X(S.sel), yO - 1, IW, hO + 2, 'style="fill:none;stroke:var(--text)" stroke-width="1.6" rx="2"'));
  var cs = condSegs(m);
  if (cs.length) {
    var big = cs.reduce(function(a, g){ return g.b - g.a > a.b - a.a ? g : a; }), lo = Infinity, hi = 0, mx = 0;
    cs.forEach(function(g){ for (k = g.a; k < g.b; k++) { lo = Math.min(lo, m.cond[k].min); hi = Math.max(hi, m.cond[k].max); mx = Math.max(mx, m.cond[k].mw); } });
    var lt = "◇ up to +" + nf(mx, 1) + " MW above the plan if price ≥ A$" + nf(lo) + (hi - lo >= 1 ? "–" + nf(hi) : "");
    if (fits(lt, 10.5, X(big.b) - X(big.a))) s.push(tx(X(big.a) + 8, yO + hO / 2 + 3.5, lt, "ol halo", ' style="pointer-events:none"'));
  }
  if (!m.d.offers_0900) s.push(tx((X(m.f0) + XR) / 2, yO + hO / 2 + 3.5, "Offers not available", "lim", ' text-anchor="middle"'));
  /* FCESS standby */
  var best = null;
  fcessSegs(m).forEach(function(g){ s.push('<g class="seg-b" data-lane="fcess" data-a="' + g.a + '">' + rc(X(g.a) + 0.5, yF, X(g.b) - X(g.a) - 1, hF, 'rx="3" style="fill:color-mix(in srgb,var(--fcess) 50%,var(--surface))"') + "</g>"); if (!best || g.b - g.a > best.b - best.a) best = g; });
  if (best && fits("Standby", 10, X(best.b) - X(best.a))) s.push(tx(X(best.a) + 6, yF + hF / 2 + 3.5, "Standby", "fl", ' style="pointer-events:none;font-size:9.5px"'));
  /* half-hours that break a limit under the current inputs */
  runs(0, function(q){ return pc.bad.indexOf(q) >= 0; }).filter(function(g){ return g.v; }).forEach(function(g){
    s.push(rc(X(g.a) - 1, yA - 3, X(g.b) - X(g.a) + 2, hA + 6, 'rx="4" style="fill:none;stroke:var(--bad)" stroke-width="2"'));
  });
  if (pc.bad.length) s.push(tx(X(pc.bad[0]), y0 + 12, "! Export above " + IN.exp + " MW", "halo vl", ' style="fill:var(--bad)"'));
  s.push(axisX(y1, 0));
  return s.join("") + planMark(m, yA, y1, true) + hitCols(y0, y1, "plan") + "</svg>";
}
function socSVG(m){
  var W = setW(fitW("#t-chart", 560, 900)), XR = X(N), s = [], rev = S.revealed, CAP = m.cap, k;
  var H = 196, y0 = 22, y1 = H - 26, ys = function(v){ return y1 - Math.max(0, Math.min(v, CAP)) * (y1 - y0) / CAP; };
  s.push(svgOpen(W, H, "soc", "Energy held in the battery on " + m.d.weekday_label + ": plan, reserve floor and, once revealed, what happened"));
  [0, CAP / 2, CAP].forEach(function(v){ s.push(ln(X0, ys(v), XR, ys(v), v ? 'class="gl"' : 'style="stroke:var(--axis)"') + tx(X0 - 8, ys(v) + 3.5, nf(v), "", ' text-anchor="end"')); });
  s.push(tx(X(m.f0) + 6, y0 - 8, "Energy held, MWh", "lnm halo"));
  if (S.sel != null) s.push(rc(X(S.sel), y0, IW, y1 - y0, 'style="fill:var(--sel)"'));
  s.push(ln(X0, ys(CAP), XR, ys(CAP), 'class="lmt"') + tx(XR + 4, ys(CAP) + 3.5, "Max", "lim"));
  /* energy the 09:00 offer protects: offered only at the price cap */
  if (m.prot) {
    s.push('<path d="' + bandPath(m.prot.map(function(){ return 0; }), m.prot, ys, m.f0, N) + '" fill="url(#hSo)" opacity=".55"/>');
    var pk2 = m.f0; for (k = m.f0; k < N; k++) if (num(m.prot[k]) && m.prot[k] > m.prot[pk2]) pk2 = k;
    if (m.prot[pk2] > 6) s.push(tx(X(pk2) + 6, ys(m.prot[pk2] / 2) + 4, "Protected: sold only at the price cap", "lim halo"));
  }
  if (m.floor) { s.push('<path d="' + stepPath(m.floor, ys, m.f0, N) + '" fill="none" style="stroke:var(--bad)" stroke-width="1.3" stroke-dasharray="2 3"/>'); s.push(tx(XR + 4, ys(m.floor[N - 1]) + 3.5, "Reserve", "lim", ' style="fill:var(--bad)"')); }
  if (rev && m.outSoc) {
    var a = linePath(m.outSoc, ys, 0, N);
    s.push('<path d="' + a + "L" + X(N).toFixed(2) + "," + y1 + "L" + X(0) + "," + y1 + 'Z" style="fill:var(--soc)" opacity="0.12"/><path d="' + a + '" fill="none" style="stroke:var(--soc)" stroke-width="2"/>');
    var yo = ys(m.outSoc[N]), yq = m.soc ? ys(m.soc[N]) : -99; if (Math.abs(yo - yq) < 12) yo = yq + (yo >= yq ? 12 : -12);
    s.push(tx(XR + 4, yo + 3.5, "Actual", "pt"));
  }
  if (m.soc) {
    s.push('<path d="' + linePath(m.soc, ys, m.f0, N) + '" fill="none" style="stroke:var(--soc)" stroke-width="2" stroke-dasharray="5 3" stroke-linejoin="round"/>');
    s.push(tx(XR + 4, ys(m.soc[N]) + 3.5, "Plan", "pt"));
    var lo = m.f0; for (k = m.f0; k <= N; k++) if (num(m.soc[k]) && m.soc[k] < m.soc[lo]) lo = k;
    s.push('<circle cx="' + X(lo).toFixed(1) + '" cy="' + ys(m.soc[lo]).toFixed(1) + '" r="3" style="fill:var(--soc)"/>' + tx(X(lo) + 6, ys(m.soc[lo]) - 6, "Lowest " + nf(m.soc[lo], 1), "halo pt"));
  } else s.push(tx((X(m.f0) + XR) / 2, (y0 + y1) / 2, "Planned energy not available", "lim", ' text-anchor="middle"'));
  if (m.site) { s.push('<path d="' + stepPath(m.site, ys, m.f0, N) + '" fill="none" style="stroke:var(--text-4)" stroke-width="1.2" stroke-dasharray="1.5 3"/>'); var sk = -1; for (k = N - 1; k >= m.f0; k--) if (m.site[k] > 0.5) { sk = k; break; } if (sk >= 0) s.push(tx(X(sk) - 4, ys(m.site[sk]) + 13, "Site reserve", "lim halo", ' text-anchor="end"')); }
  /* the selected conditional half-hour: where the energy would go if its tranches cleared in full (never drawn as the plan) */
  var cl = clearIf(m, S.sel);
  if (cl) s.push(ln(X(S.sel), ys(m.soc[S.sel]), X(S.sel + 1), ys(cl.e1), 'style="stroke:var(--cond)" stroke-width="2.2" stroke-dasharray="2 2"') + '<circle cx="' + X(S.sel + 1).toFixed(1) + '" cy="' + ys(cl.e1).toFixed(1) + '" r="3" style="fill:var(--cond)"/>' + tx(X(S.sel + 1) + 5, ys(cl.e1) + 4, "if it clears: " + nf(cl.e1, 1), "ol halo"));
  s.push("<defs>" + HATCH("hSo") + "</defs>");
  s.push(axisX(y1, 0));
  return s.join("") + planMark(m, y0, y1, false) + hitCols(y0, y1, "plan") + "</svg>";
}

/* if every sell tranche of one half-hour cleared in full: the extra energy leaves storage through the discharge efficiency,
   and never below the energy the offer protects (derived from the submission, not a model output) */
function clearIf(m, k){
  if (S.lane !== "cond" || k == null || !m.soc || !m.cond[k] || !(m.cond[k].mw > 0.05)) return null;
  var eta = Math.sqrt((m.d.battery && m.d.battery.round_trip) || 0.85), fl = Math.max(m.prot ? m.prot[k] || 0 : 0, m.floor ? m.floor[k] || 0 : 0);
  var e1 = Math.max(fl, m.soc[k + 1] - m.cond[k].mw * 0.5 / eta);
  return {e1:e1, fl:fl, mwh:m.soc[k + 1] - e1};
}
/* ---------- the selected block ---------- */
function meterFact(m, k){
  if (!m.meter || !num(m.meter[k])) return null;
  var v = m.meter[k], exp = +IN.exp, imp = m.lim.import_mw || 12;
  return v < 0 ? ["Meter export", '<b class="' + (-v > exp + 1e-6 ? "bad" : "") + '">' + nf(-v, 1) + " of " + exp + " MW</b>"] : ["Meter import", "<b>" + nf(v, 1) + " MW · CMD " + (m.lim.cmd_mw || 9) + " · limit " + imp + "</b>"];
}
function panelHTML(m){
  if (!m) return na("Trading day", "No site day was exported.");
  var k = S.sel, h, f = [], why = "";
  if (k == null) {
    return '<div class="cp-t">Selected block</div><div class="cp-h" style="font-size:16px;color:var(--text-3)">Click a block on the timeline</div><p class="cp-why">Scheduled actions are solid. Conditional offers are striped: they sell only if the price reaches their threshold. Hover anywhere for values.</p>';
  }
  if (S.lane === "cond") {
    var c = m.cond[k];
    if (!c || !(c.mw > 0.05)) { S.lane = "plan"; return panelHTML(m); }
    var cl = clearIf(m, k), dis = ((m.d.offers_0900 || {}).dis_per_halfhour || [])[k];
    h = '<div class="cp-t"><span class="cp-k cond">◇ CONDITIONAL</span>' + hhmm(k) + "–" + hhmm(k + 1) + "</div>" +
      '<div class="cp-h" style="color:var(--cond-text)">Up to +' + nf(c.mw, 1) + " MW above the plan</div>" + '<div class="cp-w">sells only if the 5-minute price reaches a trigger</div>';
    f.push(["Trigger", "<b>price ≥ A$" + nf(c.min, 2) + (c.max - c.min >= 0.01 ? " … " + nf(c.max, 2) : "") + "/MWh</b>"]);
    f.push(["Extra power, if all clear", "<b>" + nf(c.mw, 2) + " MW</b>"]);
    if (m.soc) f.push(["Energy held at " + hhmm(k + 1) + ", plan", "<b>" + nf(m.soc[k + 1], 1) + " MWh</b>"]);
    if (cl) f.push(["Protected or reserved", "<b>" + nf(cl.fl, 1) + " MWh</b>"]);
    if (cl) f.push(["If it all clears", '<b style="color:var(--cond-text)">−' + nf(cl.mwh, 1) + " MWh → " + nf(cl.e1, 1) + " MWh</b>"]);
    f.push(["Valid", "<b>" + hhmm(k) + "–" + hhmm(k + 1) + (dis ? ", " + dis + " five-minute intervals" : "") + "</b>"]);
    f.push(["Limits", "<b>export ≤ " + IN.exp + " MW · power ≤ " + m.pmax + " MW</b>"]);
    var t = '<table class="trn"><tr><th>Tranche</th><th>MW</th><th>A$/MWh</th><th' + noteAttr("Five-minute intervals of this half-hour in which the tranche is offered; the MW is the average over the half-hour.") + ">DIs</th></tr>" + (c.tr || []).filter(function(x){ return x.mw > 0.0005; }).map(function(x, j){ return "<tr><td>" + (j + 1) + "</td><td>" + nf(x.mw, 2) + "</td><td>" + (num(x.price_aud_per_mwh) ? nf(x.price_aud_per_mwh, 2) : String(x.price_aud_per_mwh)) + "</td><td>" + (x.dis_with_tranche != null ? x.dis_with_tranche : "") + "</td></tr>"; }).join("") + "</table>";
    why = "From the real-time market submission made at 09:00. Triggers are the value of the stored energy, so they rise as the battery empties. Energy below the protected level is offered only at the price cap. The expected value does not count these sales.";
    return h + kv(f) + t + '<p class="cp-why">' + why + "</p>" + outcomeBit(m, k);
  }
  if (S.lane === "fcess") {
    var gf = segAt(fcessSegs(m), k) || {a:k, b:k + 1};
    h = '<div class="cp-t"><span class="cp-k fc">FCESS STANDBY</span>' + winOf(gf.a, gf.b) + '</div><div class="cp-h">Raise ' + nf(m.raise[k], 1) + " · lower " + nf(m.lower[k], 1) + " MW</div>" +
      '<div class="cp-w"' + noteAttr("Frequency Co-optimised Essential System Services: capacity held ready for AEMO to call, regulation and contingency.") + ">frequency services, held ready</div>";
    f.push(["Half-hour", "<b>" + hhmm(k) + "–" + hhmm(k + 1) + "</b>"]);
    return h + kv(f) + outcomeBit(m, k);
  }
  var gp = segAt(planSegs(m), k);
  if (gp.v === "pre") {
    return '<div class="cp-t"><span class="cp-k pre">BEFORE 09:00</span>' + winOf(gp.a, gp.b) + '</div><div class="cp-h">Earlier plan</div><p class="cp-why">The 09:00 plan takes over from ' + hhmm(m.f0) + ". Before that the battery follows the previous day's plan, and no 09:00 forecast exists for these half-hours.</p>" + outcomeBit(m, k);
  }
  h = '<div class="cp-t"><span class="cp-k sch">SCHEDULED</span>' + winOf(gp.a, gp.b) + '</div><div class="cp-h" style="color:' + (gp.v === "hold" ? "var(--text-2)" : (gp.v === "chg" ? "var(--chg-text)" : "var(--dis)")) + '">' + AICON[gp.v] + " " + segName(m, gp, true) + "</div>" +
    '<div class="cp-w">' + hhmm(k) + "–" + hhmm(k + 1) + (num(m.bat[k]) ? " · " + (m.bat[k] >= 0 ? "+" : "−") + nf(Math.abs(m.bat[k]), 1) + " MW" : "") + "</div>";
  if (m.soc) f.push(["Energy held", "<b>" + nf(m.soc[gp.a], 1) + " → " + nf(m.soc[gp.b], 1) + " MWh</b>"]);
  if (m.fc && num(m.fc[k])) f.push(["Price forecast", "<b>A$" + nf(m.fc[k]) + "/MWh" + (m.p10 && m.p90 ? " · P10–P90 " + nf(m.p10[k]) + "–" + nf(m.p90[k]) : "") + "</b>"]);
  var mf = meterFact(m, k); if (mf) f.push(mf);
  var pcx = planCheck(); if (pcx.bad.indexOf(k) >= 0) f.push(["Export if raise is called", '<b class="bad">' + nf((m.raise[k] || 0) - (m.meter[k] || 0), 1) + " of " + IN.exp + " MW allowed</b>"]);
  if (m.floor) f.push(["Reserve floor", "<b>" + nf(m.floor[k], 1) + " MWh</b>"]);
  if (m.cond[k] && m.cond[k].mw > 0.05) f.push(["Conditional on top", '<b style="color:var(--cond-text)">+' + nf(m.cond[k].mw, 1) + " MW if ≥ A$" + nf(m.cond[k].min) + "</b>"]);
  /* the model records no reason per half-hour; the reason here follows from the action and the cap it holds (see sources) */
  var reason = AWHY[gp.v];
  return h + kv(f) + (reason ? '<p class="cp-why"' + noteAttr("Derived from the plan's power and the cap it holds; the model's decision record gives no reason per half-hour.") + ">" + esc(reason) + "</p>" : "") + outcomeBit(m, k);
}
function kv(f){ return '<div class="kvl">' + f.map(function(x){ return "<div><span>" + x[0] + "</span>" + x[1] + "</div>"; }).join("") + "</div>"; }
function outcomeBit(m, k){
  if (!S.revealed) return '<div class="locked">Outcome hidden. <button class="lnk" data-reveal>Reveal the replayed outcome →</button></div>';
  var f = [];
  if (m.actual && num(m.actual[k])) f.push(["Actual price", "<b>A$" + nf(m.actual[k]) + "/MWh</b>"]);
  if (m.outBat && num(m.outBat[k])) f.push(["Executed", "<b>" + (m.outBat[k] >= 0 ? "+" : "−") + nf(Math.abs(m.outBat[k]), 1) + " MW</b>"]);
  if (m.cleared && num(m.cleared[k])) f.push(["Conditional cleared", "<b>" + nf(m.cleared[k], 2) + " MW</b>"]);
  return '<div class="kvl" style="margin-top:14px"><div style="border-top:0;padding-top:0"><span class="cap">Outcome · replayed</span><span></span></div>' + f.map(function(x){ return "<div><span>" + x[0] + "</span>" + x[1] + "</div>"; }).join("") + "</div>";
}

/* ---------- Today ---------- */
function mainSeg(m){
  var best = null, bv = 0;
  planSegs(m).forEach(function(g){ if (g.v !== "dis" && g.v !== "peak" && g.v !== "chg") return; var e = 0; for (var k = g.a; k < g.b; k++) e += Math.abs(m.bat[k] || 0); if (g.v !== "chg") e *= 2; if (e > bv) { bv = e; best = g; } });
  return best;
}
function renderToday(){
  var m = model(), pc = planCheck(), blocked = pc.status === "blocked";
  document.body.classList.toggle("plan-blocked", blocked);
  $("#t-days").innerHTML = SITE.map(function(d, i){ return '<button data-di="' + i + '" class="' + (i === S.di ? "on" : "") + '">' + d.weekday_label + " <small>" + perShort(d.period).replace(/ · \d+ d$/, "") + "</small></button>"; }).join("");
  if (!m) {
    $("#t-status").innerHTML = '<span class="xs nc">NOT CHECKED</span>'; $("#t-head").textContent = "No trading day available"; $("#t-acts").innerHTML = ""; $("#t-val").innerHTML = "";
    $("#t-chart").innerHTML = na("Trading day", "data/site_days.json was not exported."); $("#t-panel").innerHTML = ""; renderPerf(); renderSafe(pc, m); return;
  }
  /* layer 1: status, decision, value */
  var ic = {pass:'<svg viewBox="0 0 14 14"><path d="M3 7.5l2.5 2.5L11 4.5"/></svg>', blocked:'<svg viewBox="0 0 14 14"><path d="M7 3v5M7 10.5v.5"/></svg>', nc:""};
  var st = '<span class="xs ' + pc.status + '">' + ic[pc.status] + {pass:"PASS", blocked:"BLOCKED", nc:"NOT CHECKED"}[pc.status] + "</span>";
  if (blocked) st += '<span class="why">' + pc.fails.map(function(c){ return c.label; }).join(" · ") + " · do not execute</span><button class=\"btn sm\" data-inspect>Inspect</button>";
  else if (pc.status === "pass") st += "<span>" + (pc.list.length - pc.none.length) + " checks pass</span>" + (pc.warns.length ? '<span class="cnt" style="color:var(--warn)">△ ' + pc.warns.length + " economic</span>" : "") + (pc.none.length ? '<span class="cnt"' + noteAttr("No check exists for: " + pc.none.map(function(c){ return c.label; }).join(", ") + ". They are not counted as passed.") + '><i class="ci u"></i>' + pc.none.length + " not checked</span>" : "");
  else st += "<span>No checks were exported for this plan.</span>";
  $("#t-status").innerHTML = st;
  var g = mainSeg(m);
  /* open on the main action, so the panel explains it before anything is clicked */
  if (S.sel == null) { S.sel = g ? g.a : m.f0; S.lane = "plan"; }
  $("#t-head").textContent = blocked ? "Resolve " + pc.fails[0].label.replace(/ ≤.*/, "").toLowerCase() + " before execution" : (g ? segName(m, g, true) + " · " + winOf(g.a, g.b) : "Hold: no energy trade scheduled");
  var chips = [], segs = planSegs(m).filter(function(x){ return x.v === "dis" || x.v === "peak" || x.v === "chg" || x.v === "shave"; });
  segs.sort(function(a, b){ return b.b - b.a - (a.b - a.a); }).slice(0, 3).sort(function(a, b){ return a.a - b.a; }).forEach(function(x){
    chips.push('<button class="act ' + (x.v === "peak" ? "dis" : x.v) + '" data-pick="plan:' + x.a + '"' + noteAttr(AWHY[x.v] || "") + "><i>" + AICON[x.v] + "</i><b>" + ANAME[x.v] + "</b>" + nf(peakMw(m, x), 1) + " MW" + (ATAIL[x.v] || "") + " · " + winOf(x.a, x.b) + "</button>"); });
  var cs = condSegs(m);
  if (cs.length) { var lo = Infinity, hi = 0, mx = 0, best = cs[0].a, gap = -Infinity;
    cs.forEach(function(x){ for (var q = x.a; q < x.b; q++) { lo = Math.min(lo, m.cond[q].min); hi = Math.max(hi, m.cond[q].max); mx = Math.max(mx, m.cond[q].mw);
      /* open on the half-hour where the forecast comes closest to the trigger: the offer most likely to sell */
      if (m.fc && num(m.fc[q]) && m.fc[q] - m.cond[q].min > gap) { gap = m.fc[q] - m.cond[q].min; best = q; } } });
    chips.push('<button class="act cond" data-pick="cond:' + best + '"' + noteAttr("The 09:00 market submission offers the battery's spare power above the plan in tranches. Each sells only if the 5-minute price reaches its trigger. Not counted in the expected value.") + "><i>◇</i><b>Conditional</b>up to +" + nf(mx, 1) + " MW if price ≥ A$" + nf(lo) + (hi - lo >= 1 ? "–" + nf(hi) : "") + "</button>"); }
  $("#t-acts").innerHTML = chips.join("");
  var v = m.val, vh = '<div class="cap">Expected net value · trading day</div>';
  if (v && num(v.net)) {
    var parts = [["energy", "energy"], ["regulation_energy", "regulation energy"], ["fcess", "FCESS"]].filter(function(x){ return num(v[x[0]]); }).map(function(x){ return x[1] + " " + aud(v[x[0]]); });
    var how = "At 09:00 forecast prices, from the engine's own plan record: " + parts.join(" + ") + (num(v.wear) ? " − wear " + aud(v.wear) : "") + (num(v.stored_end) ? " + value of the energy still stored at 08:00 tomorrow " + aud(v.stored_end) + " (storage-value table, an estimate)" : "") + ". Not settlement.";
    vh += '<div class="bigv' + (blocked ? " off" : "") + '">' + aud(v.net) + "</div><div class=\"chips\">" + (blocked
      ? '<span class="tipc bad r"' + noteAttr("The plan breaks a hard limit under the current inputs. This value is a simulation and must not be executed.") + ">Simulation only · not executable</span>"
      : '<span class="tipc r"' + noteAttr(how) + '>forecast 09:00</span>' + (num(v.stored_end) ? '<span class="tipc r"' + noteAttr("Of the expected value, " + aud(v.stored_end) + " is energy left in the battery at 08:00 tomorrow, valued with the storage-value table. It is not cash today.") + ">incl. " + aud(v.stored_end) + " stored</span>" : "") +
        '<span class="tipc r"' + noteAttr("Counts the scheduled plan only; conditional offers are left out. The network bill and the capacity charge (IRCR) are steered by caps, not priced in this value. The model assumes the battery does not move the price.") + ">scheduled only</span>") + "</div>";
  } else vh += '<div class="bigv na">Not available</div><div class="chips"><span class="tipc r"' + noteAttr((m.d.gaps || []).filter(function(x){ return /value/.test(x.field); }).map(function(x){ return x.reason; }).join(" ") || "The model gives no expected value at 09:00.") + ">why</span></div>";
  if (S.revealed && m.money && num(m.money.net)) {
    var up = m.base && num(m.base.net_aud) ? m.money.net - m.base.net_aud : null;
    vh += '<div class="vs"' + noteAttr("Replayed at actual prices: energy, FCESS and the network bill saving, less wear; stored energy is not valued and IRCR is settled per season. Baseline: the frozen v1 dispatch on the same day and prices" + (m.base && m.base.note ? " (" + m.base.note + ")" : "") + ".") + ">Outcome <b>" + aud(m.money.net) + "</b>" + (up != null ? " · baseline <b>" + aud(m.base.net_aud) + '</b> · <b class="' + (up < 0 ? "neg" : "pos") + '">' + sgn(up) + "</b>" : "") + ' <span class="sub">same day, replayed</span></div>';
  } else if (m.base) vh += '<div class="vs sub">Same-day baseline: shown with the outcome</div>';
  $("#t-val").innerHTML = vh;
  /* layer 2 */
  $("#t-chart").innerHTML = dayLanesSVG(m) + socSVG(m);
  $("#t-panel").innerHTML = panelHTML(m);
  bindReveal($("#t-panel"));
  renderPerf(); renderSafe(pc, m);
}
/* layer 3, left: the backtest record against the frozen baseline, one row per period */
function cbRows(periods, label){
  var max = 0; periods.forEach(function(p){ max = Math.max(max, p.baseline_aud_per_day || 0, p.optimizer_aud_per_day || 0); });
  return periods.map(function(p){
    var k = perKey(p.period), ci = p.ci95_aud_per_day, unclear = ci && ci[0] <= 0;
    return '<div class="cbr ' + k + '"><div class="pl">' + PER_NAME[k] + "<small>" + dshort(p.period.from) + " – " + dshort(p.period.to) + " · " + p.period.days + " d" + (k === "ins" ? " · used to select" : "") + "</small></div>" +
      '<div class="bars"><div class="b"><i style="width:' + (100 * (p.baseline_aud_per_day || 0) / max * 0.62).toFixed(1) + '%"></i>' + aud(p.baseline_aud_per_day) + " baseline</div>" +
      '<div class="o"><i style="width:' + (100 * (p.optimizer_aud_per_day || 0) / max * 0.62).toFixed(1) + '%"></i>' + aud(p.optimizer_aud_per_day) + " " + label + "</div></div>" +
      '<div class="up' + (unclear ? " muted" : "") + '"' + noteAttr(ci ? "95% interval " + sgn(ci[0]) + " to " + sgn(ci[1]) + " per day" + (unclear ? "; it includes 0, so this period alone shows no clear uplift." : ". ") + (p.ci95_note ? " Method: " + p.ci95_note.split(";")[0] + "." : "") : "No interval: " + (p.ci95_note || "not reported for this period.")) + ">" + sgn(p.uplift_aud_per_day) + "<small>a day · " + (p.uplift_pct >= 0 ? "+" : "−") + nf(Math.abs(p.uplift_pct), 1) + "%</small>" + (ci ? "<small>95% " + sgn(ci[0]) + " … " + sgn(ci[1]) + "</small>" : "<small>no interval</small>") + "</div></div>";
  }).join("");
}
function renderPerf(){
  var h = '<div class="pnl-h"><h2>Optimizer vs baseline</h2><span class="sub">net value per day · same days, same prices · frozen v1 baseline</span><span class="sp"></span><button class="lnk" data-go="svalid">Validation →</button></div>';
  if (!SUM || !SUM.q2b || !SUM.q2b.periods) { $("#t-perf").innerHTML = h + na("Backtest comparison", "data/site_summary.json was not exported."); return; }
  $("#t-perf").innerHTML = h + cbRows(SUM.q2b.periods, "optimizer") + '<p class="foot">In-sample results were used to choose the method, so they do not show future performance. Test rows are reported only.</p>';
}
/* short names for the check tiles; the full wording and the worst value open on hover */
var CKS = {power:"Power ≤ 10 MW", soc_max:"Energy ≤ 40 MWh", soc_reserve:"Energy ≥ floor", site_reserve:"Site reserve kept", meter_import:"Import ≤ 12 MW",
  cmd:"Import ≤ CMD", ircr_cap:"Import ≤ IRCR cap", fcess_cover:"FCESS deliverable", cycles:"≤ 1 cycle a day", info_timing:"Info public by 09:00",
  offer_format:"Offer format valid", contractual:"Contracts", market_eligibility:"Market eligibility"};
/* layer 3, right: can it run, and what is the downside */
function renderSafe(pc, m){
  var h = '<div class="pnl-h"><h2>Safety and risk</h2><span class="sub">today\'s plan, checked against the limits</span></div>';
  if (!pc.list.length) h += na("Plan checks", "No checks were exported for this day, so nothing is shown as passed.");
  else {
    h += '<div class="ckgrid">' + pc.list.map(function(c){
      var k = {pass:"ok", fail:"b", warn:"w", not_checked:"u"}[c.status] || "u", ic = {ok:"✓", b:"!", w:"△", u:""}[k];
      var note = c.label + ": " + {pass:"pass", fail:"FAIL", warn:"exceeded (economic limit, priced in the bill, not a block)", not_checked:"NOT CHECKED, no check exists"}[c.status] + (c.worst ? " · worst " + c.worst : "") + (c.detail ? ". " + c.detail : "");
      return '<div class="ck ' + k + '"' + noteAttr(note) + (c.status === "fail" ? " data-inspect" : "") + '><span class="ci ' + k + '">' + ic + "</span><span>" + (c.id === "meter_export" ? "Export ≤ " + IN.exp + " MW" : (CKS[c.id] || c.label)) + "</span></div>"; }).join("") + "</div>";
  }
  var rows = SUM && SUM.q2b && SUM.q2b.periods ? SUM.q2b.periods.filter(function(p){ return num(p.p10_daily_uplift_aud) || num(p.share_days_below_baseline); }) : [];
  h += '<div class="rk-h">Downside against the baseline <span class="sub">daily uplift, backtest</span></div>';
  if (!rows.length) h += na("Downside", "No daily distribution was exported.");
  else h += '<table class="tbl" style="margin-top:6px"><tr><th>Period</th><th class="n">P10 day</th><th class="n">Days below baseline</th><th class="n">Worst day</th></tr>' + rows.map(function(p){
    return "<tr><td>" + perShort(p.period) + '</td><td class="n' + (p.p10_daily_uplift_aud < 0 ? " neg" : "") + '">' + sgn(p.p10_daily_uplift_aud) + '</td><td class="n">' + nf(100 * p.share_days_below_baseline, 0) + '%</td><td class="n' + (p.worst_day && p.worst_day.uplift_aud < 0 ? " neg" : "") + '">' + (p.worst_day ? sgn(p.worst_day.uplift_aud) + ' <span class="sub">' + dshort(p.worst_day.day) + "</span>" : "—") + "</td></tr>"; }).join("") + "</table>";
  if (m) h += '<p class="foot">' + (m.p10 && m.p90 ? "Price uncertainty for today is drawn as the P10–P90 band on the timeline." : "Today's price forecast has no P10–P90 range in the export, so no band is drawn.") + "</p>";
  $("#t-safe").innerHTML = h;
}

/* ---------- site validation ---------- */
function renderSValid(){
  if (!SUM) { ["#sv-q2a", "#sv-q2b", "#sv-lim", "#sv-risk"].forEach(function(s){ $(s).innerHTML = na("Validation", "data/site_summary.json was not exported."); }); return; }
  var q = SUM.q2a, h = '<div class="pnl-h"><h2>Q2A · price forecast</h2><span class="sub"' + (q ? noteAttr(q.metric + ". Baseline: " + q.baseline + ". Forecast: " + (q.forecast || "")) : "") + ">" + (q ? "mean absolute error (MAE), A$/MWh · baseline: yesterday's price · lower is better" : "") + "</span></div>";
  if (q && q.periods) {
    var mx = 0; q.periods.forEach(function(p){ mx = Math.max(mx, p.model || 0, p.baseline || 0); });
    h += q.periods.map(function(p){ var k = perKey(p.period);
      return '<div class="mrow"><div class="pl">' + PER_NAME[k] + "<small>" + dshort(p.period.from) + " – " + dshort(p.period.to) + " · " + p.period.days + ' d</small></div><div class="bars"><div><i style="width:' + (78 * p.baseline / mx).toFixed(1) + '%;background:var(--base)"></i>' + nf(p.baseline, 1) + " baseline</div><div><i style=\"width:" + (78 * p.model / mx).toFixed(1) + '%;background:var(--brand)"></i>' + nf(p.model, 1) + ' model</div></div><div class="chg ' + (p.change_pct < 0 ? "pos" : "neg") + '">' + (p.change_pct < 0 ? "−" : "+") + nf(Math.abs(p.change_pct), 1) + "%</div></div>"; }).join("") +
      '<p class="foot">A lower error than the baseline is not an accuracy percentage.</p>';
  } else h += na("Forecast error", "Not in the export.");
  $("#sv-q2a").innerHTML = h;
  var rc2 = SUM.q2b && SUM.q2b.reference_test_combined;
  $("#sv-q2b").innerHTML = '<div class="pnl-h"><h2>Q2B · dispatch</h2><span class="sub">net value per day against the frozen v1 baseline</span></div>' + (SUM.q2b && SUM.q2b.periods ? cbRows(SUM.q2b.periods, "optimizer") : na("Dispatch comparison", "Not in the export.")) +
    '<p class="foot">In-sample results were used to choose the method; they do not show future performance.' + (rc2 && rc2.ci95_aud_per_day ? " The only interval reported for the test covers all " + rc2.period.days + " test days together: " + sgn(rc2.uplift_aud_per_day, 1) + " a day, 95% " + sgn(rc2.ci95_aud_per_day[0]) + " to " + sgn(rc2.ci95_aud_per_day[1]) + "." : "") + "</p>";
  var L = SUM.hard_limits, cols = L && L.periods ? L.periods : [];
  var defs = [["power", "Battery power ≤ rating", "b"], ["soc", "Energy within 0 – max", "b"], ["meter_import", "Meter import ≤ limit", "b"], ["meter_export", "Meter export ≤ limit", "b"], ["cmd_exceeded_halfhours", "Import above CMD 9 MW (economic)", "w"]];
  var t = '<div class="pnl-h"><h2>Constraint compliance</h2><span class="sub">replayed dispatch, counts of half-hours (or days, where stated)</span></div>';
  if (!cols.length) t += na("Constraint record", "Not in the export.");
  else {
    t += '<table class="limgrid"><tr><th>Check</th>' + cols.map(function(p){ return "<th>" + perShort(p.period) + "</th>"; }).join("") + "</tr>";
    defs.forEach(function(dd){ t += "<tr><td>" + dd[1] + "</td>" + cols.map(function(p){ var v = p[dd[0]];
      if (!num(v)) return '<td><span class="cell u"' + noteAttr("Not checked or not in the export.") + ">not checked</span></td>";
      var cls = v === 0 ? "ok" : dd[2]; return '<td><span class="cell ' + cls + '"' + noteAttr((p.note || "") + (dd[0] === "power" && L.power_cap_note ? " " + L.power_cap_note : "")) + ">" + (v === 0 ? "✓ 0" : (cls === "b" ? "! " : "△ ") + nf(v)) + "</span></td>"; }).join("") + "</tr>"; });
    t += "</table>" + (L.power_cap_note ? '<p class="foot"' + noteAttr(L.power_cap_note) + "><b>Power:</b> results made before the 26 Oct 2026 fix had 3 in-sample days over 10 MW; with the fixed code it is 0, and the reports were regenerated with it. Hover for detail.</p>" : "") +
      '<p class="foot"><b>CMD</b> is an economic limit: going over it raises the month\'s network bill, which the results include. It is not a breach of the connection.</p>';
  }
  $("#sv-lim").innerHTML = t;
  var r = '<div class="pnl-h"><h2>Own-battery value</h2><span class="sub">market revenue plus bill saving, per day</span></div>', ov = SUM.own_value;
  if (ov && ov.periods) r += '<div class="risk">' + ov.periods.map(function(p){ return "<div>" + perShort(p.period) + "<b>" + aud(p.market_plus_bill_aud_per_day) + '</b><span class="sub">market ' + aud(p.market_aud_per_day) + " + bill " + aud(p.bill_saving_aud_per_day) + "</span></div>"; }).join("") + "</div>" +
    (num(ov.ircr_saving_aud_per_year) ? '<p class="foot">Separately, the capacity charge (IRCR) saving: ' + aud(ov.ircr_saving_aud_per_year) + " a year, from Hot Season " + ov.ircr_season + " (in-sample). The test period has no Hot Season, so it has no IRCR test.</p>" : "");
  else r += na("Own-battery value", "Not in the export.");
  $("#sv-risk").innerHTML = r;
  $("#sv-src").innerHTML = srcList(SUM);
}
function srcList(obj){
  var s = obj.sources || {}, g = obj.gaps || [];
  return '<ul class="src-list">' + Object.keys(s).map(function(k){ return "<li><b>" + esc(k) + "</b>: <code>" + esc(typeof s[k] === "string" ? s[k] : JSON.stringify(s[k])) + "</code></li>"; }).join("") + "</ul>" +
    (g.length ? '<p class="foot"><b>Gaps</b></p><ul class="src-list">' + g.map(function(x){ return "<li><code>" + esc(x.field) + "</code>: " + esc(x.reason) + "</li>"; }).join("") + "</ul>" : "");
}

/* ---------- customer fleet (filled from data/fleet.json) ---------- */
function renderCalls(){ if (!FL) { $("#c-dec").innerHTML = na("Tonight's call", "data/fleet.json was not exported."); $("#c-season").innerHTML = ""; $("#c-budget").innerHTML = ""; return; } fleetCalls(); }
function renderValue(){ if (!FL) { $("#v-flow").innerHTML = na("Value by party", "data/fleet.json was not exported."); return; } fleetValue(); }
function renderFValid(){ if (!FL) { $("#fv-hold").innerHTML = na("Fleet validation", "data/fleet.json was not exported."); return; } fleetValid(); }
/* which offer, which scenario: the call rule and the accounts differ by both */
function offerOf(id){ return (FL.offers || []).filter(function(o){ return o.id === id; })[0] || {}; }
function offerName(id){ return id === "custom" ? "Custom offer" : "Reference terms"; }
function offerNote(id){ var o = offerOf(id); return (o.name || offerName(id)) + ". " + (o.what_it_is || ""); }
var SCN = {S2b:{label:"S2b · retailer, 2027 Peak IRCR", tag:"assumed future rule", cls:"fut"}, S3:{label:"S3 · Synergy TPA, 50% share", tag:"hypothetical share", cls:"hyp"}};
function scnChip(sc){ return '<span class="chip ' + SCN[sc].cls + '"' + noteAttr((FL.value_by_party && FL.value_by_party.scenarios && FL.value_by_party.scenarios[sc]) || "") + ">" + SCN[sc].label + " · " + SCN[sc].tag + "</span>"; }
function segHTML(attr, cur, items){ return items.map(function(it){ return '<button ' + attr + '="' + it[0] + '" class="' + (it[0] === cur ? "on" : "") + '"' + (it[2] ? noteAttr(it[2]) : "") + ">" + it[1] + "</button>"; }).join(""); }
function calledOn(d, off, sc){ return !!d[off === "custom" ? (sc === "S3" ? "called_custom_s3" : "called_custom") : (sc === "S3" ? "called_ref_s3" : "called_ref")]; }
function flaggedOn(d, off, sc){ return !!(off === "ref" && sc === "S2b" ? d.flagged_ref_s2b : d.flagged_custom); }
function thrOn(d, off, sc){ return off === "ref" && sc === "S2b" ? d.threshold_ref_s2b_mw : d.threshold_mw; }
function seasonDays(){ return (FL.season_calls && FL.season_calls.days) || []; }
function dLabel(iso){ var dt = new Date(iso + "T00:00:00Z"); return dt.toLocaleDateString("en-AU", {weekday:"short", day:"numeric", month:"short", year:"numeric", timeZone:"UTC"}).replace(",", ""); }

/* ---------- Tonight's call ---------- */
function fleetCalls(){
  var days = seasonDays(), off = S.offer, sc = S.vscen, dec = S.view === "decision";
  if (!days.length) { $("#c-dec").innerHTML = na("Tonight's call", "No season of call decisions was exported."); return; }
  S.cday = Math.max(0, Math.min(days.length - 1, S.cday));
  var i = S.cday, d = days[i], o = offerOf(off), cap = o.max_calls_per_year || 0, am = o.as_modelled || {};
  var used = 0; for (var j = 0; j < i; j++) if (calledOn(days[j], off, sc)) used++;
  var call = calledOn(d, off, sc), flag = flaggedOn(d, off, sc), thr = thrOn(d, off, sc);
  $("#c-offer-seg").innerHTML = segHTML("data-offer", off, [["custom", "Custom offer", offerNote("custom")], ["ref", "Reference terms", offerNote("ref")]]);
  $("#c-scen-seg").innerHTML = scenSeg(sc);
  $("#day-label").textContent = dLabel(d.day);
  var why = d.auto_flag_first_days && thr == null ? "Season start: every day is flagged until four days of the season are known."
    : (num(thr) ? "Forecast peak <b>" + nf(d.forecast_peak_mw) + " MW</b> " + (d.forecast_peak_mw >= thr ? "≥" : "&lt;") + " threshold <b>" + nf(thr) + " MW</b>" : "Threshold not available");
  if (num(d.forecast_dayahead_mw) && num(d.threshold_dayahead_mw) && off === "custom") why += ' <span class="sub">· day-ahead ' + nf(d.forecast_dayahead_mw) + " vs " + nf(d.threshold_dayahead_mw) + "</span>";
  var head = call ? "Call customer batteries · " + (am.event_window || "17:30–19:30").replace("-", "–") : (flag ? "Flagged, but no call" : "No call · normal self-use");
  var h = '<div class="l1-main"><div class="xstat"><span class="dec-pill' + (call ? "" : " no") + '">' + (call ? "CALL" : "NO CALL") + "</span><span>" + why + "</span></div>" +
    '<h2 class="dec-h">' + head + "</h2>" +
    '<div class="acts">' + (call ? '<span class="act"><i style="background:var(--cust-soft);color:var(--cust)">·</i>Standby ' + (am.standby_window || "").replace("-", "–") + '</span><span class="act"><i style="background:var(--cust);color:#fff">↑</i><b>Event</b>' + (am.event_window || "").replace("-", "–") + '</span><span class="act"><i style="background:var(--hold);color:var(--hold-text)">‖</i>' + (am.reserve_soc_pct || 20) + "% reserve kept</span>" : "") +
    (flag && !call ? '<span class="act"><i style="background:var(--warn-soft);color:var(--warn)">!</i>all ' + cap + " calls of the year used</span>" : "") + "</div>";
  if (!dec) h += '<div class="acts"><span class="hind">Hindsight</span><span class="act">' + (d.ircr_peak_day ? (call ? '<i style="background:var(--ok-soft);color:var(--ok)">★</i><b>Official peak day</b> covered by this call' : '<i style="background:var(--bad-soft);color:var(--bad)">★</i><b>Official peak day</b> missed') : "Not an official peak day" + (call ? " · the call cost compensation without capacity value" : "")) + "</span><span class=\"act\">actual peak " + nf(d.actual_peak_mw) + " MW · rank " + d.actual_rank_in_season + " of " + days.length + "</span></div>";
  h += '</div><div class="l1-val"><div class="cap">Calls used before today · contract year 2025-26</div><div class="bigv">' + used + '<span style="font-size:20px;color:var(--text-3);font-weight:500"> / ' + cap + "</span></div>" +
    '<div class="budget-bar" style="width:240px;margin-left:auto"><i style="width:' + (100 * used / cap).toFixed(1) + '%;background:var(--cust)"></i>' + (call ? '<i style="width:' + (100 / cap).toFixed(1) + '%;background:repeating-linear-gradient(45deg,var(--cust) 0 1.5px,var(--cust-soft) 1.5px 4px)"></i>' : "") + "</div>" +
    '<div class="chips" style="margin-top:8px">' + scnChip(sc) + '<span class="tipc r"' + noteAttr(offerNote(off) + " " + (o.status_note || "")) + ">" + offerName(off) + " · " + (o.pay_c_per_kwh || "") + " c/kWh · " + cap + " calls</span></div></div>";
  $("#c-dec").innerHTML = h;
  $("#c-dec").classList.toggle("no", !call);
  $("#c-season").innerHTML = '<div class="pnl-h"><h2>Hot Season 2025-26</h2><span class="sub">' + (dec ? "only what was known at 09:00 on " + dLabel(d.day) : "the whole season, with hindsight") + '</span><span class="sp"></span>' + (dec ? "" : '<span class="hind">Hindsight</span>') + "</div>" + seasonSVG(days, i, off, sc, dec) +
    '<div class="legend"><span><i class="sw2 sq" style="--c:var(--hold)"></i>Daily peak demand</span><span><i class="sw2 sq" style="--c:var(--cust)"></i>Called</span><span><i class="sw2 dsh" style="--c:var(--peak)"></i>Call threshold at 09:00</span>' + (dec ? '<span><i class="sw2 sq" style="--c:var(--surface);outline:1.5px dashed var(--peak)"></i>Today: 09:00 forecast</span>' : '<span style="color:var(--peak)">★ <span style="color:var(--text-2)">Official peak day</span></span>') + "</div>";
  var all = days.filter(function(x){ return calledOn(x, off, sc); }).length, sm = FL.season_calls.summary || {}, key = off + "_" + sc;
  $("#c-budget").innerHTML = '<div class="pnl-h"><h2>Season result</h2><span class="sub">' + offerName(off) + " · " + SCN[sc].label + " · Hot Season 2025-26 · in-sample</span></div>" +
    '<div class="risk"><div>Calls made<b>' + all + " of " + cap + "</b></div><div>Official peak days covered" + '<b class="' + ((sm.peak_days_covered || {})[key] < 4 ? "neg" : "") + '">' + ((sm.peak_days_covered || {})[key] != null ? sm.peak_days_covered[key] + " of 4" : "—") + "</b></div><div>Missed<b>" + (((sm.peak_days_missed || {})[key] || []).map(dshort).join(", ") || "none") + "</b></div></div>" +
    '<details class="fold" style="margin-top:16px"><summary>How the call rule works</summary><p class="foot" style="margin-top:0">' + esc(FL.season_calls.rule || "") + "</p></details>";
}
function seasonSVG(days, i, off, sc, dec){
  var n = days.length, L = 64, W = fitW("#c-season", 560, 900), XE = W - 16, DW = (XE - L) / n, BW = Math.max(2, DW - 1.5), xa = function(q){ return L + DW * q; };
  var vals = days.map(function(x){ return x.actual_peak_mw; }).concat(days.map(function(x){ return x.forecast_peak_mw; })), lo = Math.floor(minOf(vals) / 250) * 250 - 250, hi = Math.ceil(maxOf(vals) / 250) * 250;
  var y0 = 16, y1 = 170, ya = function(v){ return y1 - (Math.max(lo, v) - lo) * (y1 - y0) / (hi - lo); }, s = [], q;
  s.push('<svg viewBox="0 0 ' + W + ' 214" class="lanes chart" data-tip="season" data-xg="season" data-x0="' + L + '" data-iw="' + DW.toFixed(3) + '" role="img" aria-label="Hot Season 2025-26: daily peak demand, call threshold and calls">' + "<defs>" + HATCH("hSe") + "</defs>");
  for (var v = lo + 250; v <= hi; v += 500) s.push(ln(L, ya(v), XE, ya(v), 'class="gl"') + tx(L - 8, ya(v) + 3.5, nf(v), "", ' text-anchor="end"'));
  s.push(tx(L, y0 - 4, "Daily peak, MW", "lnm"));
  for (q = 0; q < n; q++) {
    var x = days[q], c = calledOn(x, off, sc);
    if (dec && q > i) break;
    if (dec && q === i) { s.push(rc(xa(q) + 0.5, ya(x.forecast_peak_mw), BW, y1 - ya(x.forecast_peak_mw), 'style="fill:var(--surface);stroke:var(--peak)" stroke-width="1.3" stroke-dasharray="2 1.5"')); continue; }
    s.push(rc(xa(q) + 0.5, ya(x.actual_peak_mw), BW, y1 - ya(x.actual_peak_mw), 'style="fill:' + (c ? "var(--cust)" : "var(--hold)") + '"' + (c ? ' opacity=".85"' : "")));
    if (!dec && x.ircr_peak_day) s.push(tx(xa(q) + DW / 2, ya(x.actual_peak_mw) - 4, "★", "", ' text-anchor="middle" style="font-size:12px;fill:var(--peak)"'));
  }
  /* the threshold each morning, as known at 09:00 */
  var thr = days.map(function(x){ return thrOn(x, off, sc); }), d = "", pen = false;
  for (q = 0; q < n; q++) { if (dec && q > i) break; if (!num(thr[q])) { pen = false; continue; } var yy = ya(thr[q]).toFixed(1); d += (pen ? "V" + yy : "M" + xa(q).toFixed(1) + "," + yy) + "H" + xa(q + 1).toFixed(1); pen = true; }
  s.push('<path d="' + d + '" fill="none" style="stroke:var(--peak)" stroke-width="1.4" stroke-dasharray="3 2"/>');
  if (dec && i < n - 1) { s.push(rc(xa(i + 1), y0, xa(n) - xa(i + 1), y1 - y0, 'fill="url(#hSe)"')); if (xa(n) - xa(i + 1) > 200) s.push(tx((xa(i + 1) + xa(n)) / 2, (y0 + y1) / 2, "Not yet known at 09:00", "lim", ' text-anchor="middle" style="font-size:12px;font-weight:600"')); }
  s.push(rc(xa(i), y0, DW, y1 - y0 + 26, 'style="fill:var(--sel)"'));
  s.push(ln(L, y1, XE, y1, 'style="stroke:var(--axis)"') + tx(L - 8, y1 + 17, "Calls", "", ' text-anchor="end" style="font-size:11px"'));
  for (q = 0; q < n; q++) { if (dec && q > i) break; if (calledOn(days[q], off, sc)) s.push('<circle cx="' + (xa(q) + DW / 2).toFixed(1) + '" cy="' + (y1 + 13) + '" r="2.8" style="fill:var(--cust)"/>'); }
  days.forEach(function(x, k){ if (x.day.slice(8) === "01") s.push(ln(xa(k), y1, xa(k), y1 + 24, 'style="stroke:var(--axis)"') + tx(xa(k) + 3, y1 + 38, MON[+x.day.slice(5, 7) - 1] + (k === 0 || x.day.slice(5, 7) === "01" ? " " + x.day.slice(0, 4) : ""), "")); });
  s.push('<line class="xh" x1="0" x2="0" y1="' + y0 + '" y2="' + (y1 + 20) + '"/>');
  for (q = 0; q < n; q++) s.push('<rect class="hit" data-d="' + q + '" x="' + xa(q).toFixed(2) + '" y="' + y0 + '" width="' + DW.toFixed(2) + '" height="' + (y1 + 20 - y0) + '"/>');
  return s.join("") + "</svg>";
}

/* ---------- Value by party ---------- */
function vRows(){ return (FL.value_by_party && FL.value_by_party.rows) || []; }
function perKeyF(p){ return (p.segment || p.label); }
function perShortF(p){ var s = p.segment || p.label; if (/^test/.test(s)) return PER_NAME[perKey(p)]; return s.replace(/[,(].*$/, "").trim().replace(/^contract/, "Contract").replace(/^hot/i, "Hot"); }
function rowOf(off, sc, pk){ return vRows().filter(function(r){ return r.offer === off && r.scenario === sc && perKeyF(r.period) === pk; })[0] || null; }
function fleetValue(){
  var sc = S.vscen, rows = vRows().filter(function(r){ return r.scenario === sc; }), pers = [];
  rows.forEach(function(r){ var k = perKeyF(r.period); if (pers.map(function(p){ return perKeyF(p); }).indexOf(k) < 0) pers.push(r.period); });
  if (!pers.length) { $("#v-flow").innerHTML = na("Value by party", "No rows for this scenario."); return; }
  if (!S.vper || !pers.some(function(p){ return perKeyF(p) === S.vper; })) S.vper = perKeyF(pers[0]);
  var P = pers.filter(function(p){ return perKeyF(p) === S.vper; })[0], unit = S.vunit || "fleet";
  $("#v-scen-seg").innerHTML = scenSeg(sc);
  $("#v-per-seg").innerHTML = segHTML("data-vper", S.vper, pers.map(function(p){ return [perKeyF(p), perShortF(p), p.segment ? p.segment + " · " + dshort(p.from) + " – " + dshort(p.to) : perLong(p)]; }));
  $("#v-unit-seg").innerHTML = segHTML("data-vunit", unit, [["fleet", "Fleet"], ["home", "Per enrolled home", "Divides by the assumed number of enrolled homes, which removes the effect of the participation curve."]]);
  $("#v-scope").innerHTML = scnChip(sc) + " " + perChip(P);
  var R = {ref:rowOf("ref", sc, S.vper), custom:rowOf("custom", sc, S.vper)};
  var div = function(r){ return unit === "home" ? r.homes : 1; }, fm = function(v, r){ return unit === "home" ? aud(v / r.homes) : (Math.abs(v) >= 1e6 ? (v < 0 ? "−" : "") + "A$" + nf(Math.abs(v) / 1e6, 2) + "M" : aud(v)); };
  /* where the value goes: one bar per offer on a shared scale; transfers drawn apart, they create nothing */
  var parts = [["zenith_aud", "Zenith", "var(--brand)", "#fff"], ["customers_aud", "Customers", "var(--cust)", "#fff"], ["retailer_aud", "Retailer", "color-mix(in srgb,var(--cust) 35%,var(--surface))", "var(--text)"], ["other_aud", sc === "S3" ? "Synergy" : "Other", "var(--base)", "var(--text)"]];
  var span = 0, neg = 0;
  ["ref", "custom"].forEach(function(k){ var r = R[k]; if (!r) return; var p = 0, ng = 0; parts.forEach(function(x){ var v = r[x[0]] / div(r); if (v > 0) p += v; else ng -= v; }); span = Math.max(span, p); neg = Math.max(neg, ng); });
  var tot = span + neg || 1, z0 = 100 * neg / tot;
  var h = '<div class="pnl-h"><h2>Where the value goes</h2><span class="sub">value created against normal self-use, split by who ends up with it · ' + (unit === "home" ? "A$ per enrolled home" : "A$, fleet") + "</span></div>";
  if (!R.ref && !R.custom) h += na("This period", "No rows.");
  ["ref", "custom"].forEach(function(k){
    var r = R[k]; if (!r) return;
    var x = z0, xn = z0, bars = parts.map(function(pp){ var v = r[pp[0]] / div(r); if (Math.abs(v) < 1e-6) return ""; var w = 100 * Math.abs(v) / tot, left = v >= 0 ? x : xn - w; if (v >= 0) x += w; else xn -= w;
      return '<i style="left:' + left.toFixed(2) + "%;width:" + w.toFixed(2) + "%;background:" + (v < 0 ? "var(--bad-soft);color:var(--bad);box-shadow:inset 0 0 0 1px var(--bad-line)" : pp[2] + ";color:" + pp[3]) + '"' + noteAttr(pp[1] + ": " + fm(r[pp[0]], r) + (pp[0] === "retailer_aud" && r.retailer_note ? ". " + r.retailer_note : "") + (pp[0] === "other_aud" && r.other_note ? ". " + r.other_note : "")) + ">" + (w > 9 ? pp[1] + " " + fm(r[pp[0]], r) : "") + "</i>"; }).join("");
    var td = r.transfers_detail_aud || {};
    h += '<div class="fl-row"><div class="pl">' + offerName(k) + "<small>" + (r.calls != null ? r.calls + " calls · " : "no calls · ") + nf(r.homes) + ' homes (assumed)</small></div><div class="fl-track"><span class="zero" style="left:' + z0.toFixed(2) + '%"></span>' + bars + '</div><div class="fl-v">' + fm(r.value_created_aud, r) + '<div class="sub">created</div></div></div>' +
      (r.transfers_aud > 0 ? '<div class="tr-arrow"><span' + noteAttr("Transfers move money between parties and create no value: Zenith pays customers for events, customers pay their retailer for the extra imports" + (sc === "S3" ? ", Synergy passes Zenith its share of the capacity value" : "") + ".") + ">⇄ transfers inside it: Zenith → customers <b>" + fm(td.zenith_to_customers || 0, r) + "</b> · customers → retailer <b>" + fm(td.customers_to_retailer_bill || 0, r) + "</b>" + (td.synergy_to_zenith_capacity_share ? " · Synergy → Zenith <b>" + fm(td.synergy_to_zenith_capacity_share, r) + "</b>" : "") + "</span></div>" : "");
  });
  h += '<p class="foot">Each bar adds up to the value created against normal self-use; a negative share sits left of the zero line. Transfers move money between parties and create nothing, so they are listed under each bar, not added to it. Enrolment follows an assumed participation curve; switch to per enrolled home to remove its effect.</p>';
  $("#v-flow").innerHTML = h;
  /* the two offers side by side */
  var E = (FL.value_by_party.expected_year || []).filter(function(r){ return r.scenario === sc; });
  var eOf = function(k){ return E.filter(function(r){ return r.offer === k; })[0]; };
  var cmp = [["Zenith incremental profit", function(r){ return fm(r.zenith_aud, r); }, function(r){ return r.zenith_aud; }],
    ["Customers vs normal self-use, per enrolled home", function(r){ return aud(r.customers_aud / r.homes); }, function(r){ return r.customers_aud; }],
    ["Calls", function(r){ return r.calls == null ? "0" : String(r.calls); }],
    ["Official peak days covered", function(r){ return r.peak_days_covered == null ? "—" : r.peak_days_covered + " of 4"; }],
    ["Enrolled homes (assumed curve)", function(r){ return nf(r.homes); }]];
  var t = '<div class="pnl-h"><h2>Custom offer against reference terms</h2><span class="sub">' + perShortF(P) + " · " + SCN[sc].label + '</span></div><table class="tbl"><tr><th></th><th class="n"' + noteAttr(offerNote("ref")) + '>Reference terms</th><th class="n hl"' + noteAttr(offerNote("custom")) + ">Custom offer</th></tr>";
  cmp.forEach(function(c){ t += "<tr><td>" + c[0] + "</td>" + ["ref", "custom"].map(function(k){ var r = R[k]; if (!r) return '<td class="n">—</td>'; var v = c[2] ? c[2](r) : 0; return '<td class="n' + (k === "custom" ? " hl" : "") + (v < 0 ? " neg" : "") + '">' + c[1](r) + "</td>"; }).join("") + "</tr>"; });
  if (E.length) t += '<tr><td>Expected year, Zenith<div class="sub"' + noteAttr(E[0].period.segment + " · " + dshort(E[0].period.from) + " – " + dshort(E[0].period.to)) + '>three Hot Seasons, the study\'s objective · hover</div></td>' + ["ref", "custom"].map(function(k){ var r = eOf(k); return '<td class="n' + (k === "custom" ? " hl" : "") + '">' + (r ? aud(r.zenith_aud) : "—") + "</td>"; }).join("") + "</tr>" +
    '<tr><td>Expected year, risk-adjusted<div class="sub">the study\'s downside penalty</div></td>' + ["ref", "custom"].map(function(k){ var r = eOf(k); return '<td class="n' + (k === "custom" ? " hl" : "") + (r && r.zenith_risk_adjusted_aud < 0 ? " neg" : "") + '">' + (r && num(r.zenith_risk_adjusted_aud) ? aud(r.zenith_risk_adjusted_aud) : "—") + "</td>"; }).join("") + "</tr>";
  $("#v-cmp").innerHTML = t + "</table>" + (perKey(P) !== "ins" ? '<p class="foot">The test period has no Hot Season, so no calls are made and only operating cost remains.</p>' : '<p class="foot">Reference terms are Synergy Battery Rewards, used for comparison only. The custom offer is a model suggestion. Neither is a Zenith contract.</p>');
  /* one call, one home */
  var pc = FL.per_call || {}, bo = (pc.by_offer || []).filter(function(r){ return r.scenario === sc && perKeyF(r.period) === S.vper; }), ir = FL.ircr || {};
  var u = '<div class="pnl-h"><h2>One call, one home</h2><span class="sub">per responding home · ' + perShortF(P) + "</span></div>";
  if (bo.length) u += '<table class="tbl"><tr><th></th><th class="n">Reference terms</th><th class="n hl">Custom offer</th></tr>' +
    [["Billed event net export", function(r){ return nf(r.billed_export_kwh_per_responding_home_per_call, 2) + " kWh"; }], ["Paid per call", function(r){ return aud(r.payment_aud_per_responding_home_per_call, 2); }], ["Effective rate per billed kWh", function(r){ return nf(r.effective_c_per_billed_kwh, 1) + " c"; }]].map(function(c){
      return "<tr><td>" + c[0] + "</td>" + ["ref", "custom"].map(function(k){ var r = bo.filter(function(x){ return x.offer === k; })[0]; return '<td class="n' + (k === "custom" ? " hl" : "") + '">' + (r && num(r.billed_export_kwh_per_responding_home_per_call) ? c[1](r) : "—") + "</td>"; }).join("") + "</tr>"; }).join("") + "</table>";
  else u += na("Per call", "No calls in this period.");
  if (sc === "S2b" && pc.why_not_85c) u += '<p class="foot"' + noteAttr(pc.why_not_85c) + ">Why the custom offer pays more than 85c per billed kWh under S2b · hover</p>";
  if (num(ir.kw_per_responding_home)) u += '<div class="risk"><div>IRCR reduction<b>' + nf(ir.kw_per_responding_home, 2) + ' kW</b><span class="sub">per responding home, ' + ir.season + '</span></div><div>Capacity value<b>' + aud(ir.value_aud_per_responding_home) + '</b><span class="sub">per responding home, ' + ir.price_capacity_year + ' price</span></div><div>Capacity price<b>' + aud(ir.price_aud_per_mw_yr) + '</b><span class="sub">per MW-yr, ' + ir.price_capacity_year + "; " + aud(ir.forward_price_aud_per_mw_yr) + " in " + ir.forward_price_capacity_year + "</span></div></div>" + (ir.ratio_note ? '<p class="foot">' + esc(ir.ratio_note) + "</p>" : "");
  $("#v-unit").innerHTML = u;
  /* the ledger, both offers side by side */
  var lines = (FL.value_by_party.ledger_lines || []).filter(function(l){ return l.scenario === sc && perKeyF(l.period) === S.vper; }), items = [];
  lines.forEach(function(l){ var key = l.item + "|" + l.payer + "|" + l.receiver; if (items.map(function(x){ return x.key; }).indexOf(key) < 0) items.push({key:key, l:l}); });
  var kindTag = {value_created:"VALUE", transfer:"TRANSFER", cost:"COST"};
  $("#v-ledger-sub").textContent = perShortF(P) + " · " + SCN[sc].label + " · A$, fleet";
  $("#v-ledger").innerHTML = '<table class="tbl"><tr><th>Item</th><th>From → to</th><th class="n">Reference terms</th><th class="n hl">Custom offer</th></tr>' + items.map(function(it){
    var l = it.l, v = function(k){ var x = lines.filter(function(y){ return y.offer === k && y.item + "|" + y.payer + "|" + y.receiver === it.key; })[0]; return x ? x.aud : null; };
    return "<tr><td>" + esc(l.label) + '<span class="trt">' + (kindTag[l.kind] || l.kind) + "</span></td><td>" + esc(l.payer || "") + (l.receiver ? " → " + esc(l.receiver) : "") + "</td>" + ["ref", "custom"].map(function(k){ var x = v(k); return '<td class="n' + (k === "custom" ? " hl" : "") + (x < 0 ? " neg" : "") + '">' + (x == null ? "—" : aud(x)) + "</td>"; }).join("") + "</tr>"; }).join("") + "</table>" +
    '<p class="foot">Transfer rows move money between parties and add up to zero across them. Sources: ' + esc(FL.value_by_party.source || "") + "</p>";
}

/* ---------- fleet validation ---------- */
function scenSeg(sc){ var t = (FL.value_by_party && FL.value_by_party.scenarios) || {}; return segHTML("data-vscen", sc, [["S2b", "S2b · retailer", t.S2b], ["S3", "S3 · Synergy TPA", t.S3]]); }
function fleetValid(){
  var V = FL.validation || {}, sc = S.vscen;
  $("#fv-scen-seg").innerHTML = scenSeg(sc);
  var H = (V.holdouts || []).filter(function(h){ return h.scenario === sc; }), seasons = [];
  H.forEach(function(h){ if (seasons.indexOf(h.held_out_season) < 0) seasons.push(h.held_out_season); });
  var mx = 0; H.forEach(function(h){ mx = Math.max(mx, Math.abs(h.zenith_realised_held_out_aud)); });
  var h = '<div class="pnl-h"><h2>Hot Season holdouts</h2><span class="sub">choose the offer without one season, score it on that season · Zenith, A$ · ' + SCN[sc].label + '</span></div><div class="legend" style="margin:-4px 0 6px"><span><i class="sw2 sq" style="--c:var(--base)"></i>Reference terms</span><span><i class="sw2 sq" style="--c:var(--brand)"></i>Custom offer</span><span><i class="sw2 sq" style="--c:var(--bad)"></i>Zenith loses money</span></div>';
  h += seasons.map(function(sn){ var c = H.filter(function(x){ return x.held_out_season === sn && /custom/.test(x.case); })[0], r = H.filter(function(x){ return x.held_out_season === sn && x.case === "ref"; })[0], fw = (c || r).chronological;
    var bar = function(x, col){ if (!x) return "<div>—</div>"; var v = x.zenith_realised_held_out_aud, w = 70 * Math.abs(v) / mx; return '<div><i style="width:' + w.toFixed(1) + "%;background:" + (v < 0 ? "var(--bad)" : col) + '"></i><span class="' + (v < 0 ? "neg" : "") + '">' + aud(v) + "</span>&nbsp;· " + x.peak_days_covered_of_4 + "/4 peaks</div>"; };
    return '<div class="mrow"><div class="pl">' + sn + "<small>" + (fw ? '<b style="color:var(--ok)">chronological</b> · forward' : "not chronological") + '</small></div><div class="bars" style="white-space:nowrap">' + bar(r, "var(--base)") + bar(c, "var(--brand)") + '</div><div class="chg sub"></div></div>'; }).join("");
  h += '<p class="foot">' + esc(V.holdout_note || "") + "</p>";
  $("#fv-hold").innerHTML = h;
  /* customer constraints: one row per constraint, one column per offer, contract year 2025-26 */
  var C = (V.constraints || []).filter(function(c){ return c.scenario === sc && (c.period.segment || "") === "contract year 2025-26"; }), ids = [];
  C.forEach(function(c){ if (ids.indexOf(c.id) < 0) ids.push(c.id); });
  var t = '<div class="pnl-h"><h2>Customer constraints</h2><span class="sub">contract year 2025-26 · in-sample · violations in the replay</span></div><table class="limgrid"><tr><th>Constraint</th><th>Reference terms</th><th>Custom offer</th></tr>';
  ids.forEach(function(id){ t += "<tr><td>" + esc((C.filter(function(c){ return c.id === id; })[0] || {}).label || id) + "</td>" + ["ref", "custom"].map(function(k){ var c = C.filter(function(x){ return x.id === id && x.offer === k; })[0];
    if (!c || !num(c.violations)) return '<td><span class="cell u">not checked</span></td>';
    return '<td><span class="cell ' + (c.violations ? "b" : "ok") + '"' + noteAttr((c.worst ? "Worst: " + c.worst + ". " : "") + (c.limit_source || "")) + ">" + (c.violations ? "! " + c.violations : "✓ 0") + "</span></td>"; }).join("") + "</tr>"; });
  $("#fv-chk").innerHTML = t + '</table><p class="foot">' + esc(V.constraints_note || "") + "</p>";
  /* stress tests, the S3 break-even, the call signal that failed, and numbers not to use */
  var St = (V.stress || []).filter(function(x){ return x.scenario === sc; }), names = [];
  St.forEach(function(x){ if (names.indexOf(x.stress) < 0) names.push(x.stress); });
  var n = '<div class="pnl-h"><h2>Stress tests</h2><span class="sub">contract year 2025-26 · ' + SCN[sc].label + ' · fails = Zenith loses money, customers worse off, or fewer than 3 of 4 peak days</span></div><div class="ckgrid" style="grid-template-columns:repeat(auto-fill,minmax(260px,1fr))">';
  names.forEach(function(nm){ ["ref", "custom"].forEach(function(k){ var x = St.filter(function(y){ return y.stress === nm && y.offer === k; })[0]; if (!x) return;
    n += '<div class="ck ' + (x.fails ? "b" : "ok") + '"' + noteAttr(offerName(k) + " · " + nm + ": Zenith " + aud(x.zenith_aud) + ", customers " + aud(x.customers_aud_per_enrolled_home) + " per enrolled home, " + x.peak_days_covered_of_4 + " of 4 peak days") + '><span class="ci ' + (x.fails ? "b" : "ok") + '">' + (x.fails ? "!" : "✓") + "</span><span>" + (k === "ref" ? "Ref · " : "Custom · ") + esc(nm) + "</span></div>"; }); });
  n += "</div>";
  (V.call_signal_tests || []).forEach(function(c){ n += '<p class="foot"><b>' + esc(c.name) + ":</b> " + esc(c.result) + "</p>"; });
  if (sc === "S3" && V.s3_break_even_share) n += '<p class="foot"><b>S3 capacity share:</b> ' + esc(V.s3_break_even_share.note || "") + "</p>";
  n += '<details class="fold" style="margin-top:14px"><summary>Numbers not to use, and where the study disagrees with itself</summary><ul class="src-list">' + (FL.do_not_use || []).map(function(x){ return "<li><b>" + esc(x.number) + "</b>: " + esc(x.why) + "</li>"; }).join("") + (FL.inconsistencies || []).map(function(x){ return "<li><b>" + esc(x.topic) + "</b>: " + esc(x.detail) + "</li>"; }).join("") + "</ul></details>";
  $("#fv-notes").innerHTML = n;
}

/* ---------- settings and run state ---------- */
function fillConfig(){
  $$("[data-k]").forEach(function(el){ var k = el.getAttribute("data-k"); if (IN[k] != null) el.value = IN[k]; });
  var d = SITE[0];
  $("#c-fixed").textContent = d ? "From the backtest: " + d.battery.power_mw + " MW / " + d.battery.energy_mwh + " MWh, round trip " + nf(100 * d.battery.round_trip) + "%, import " + d.limits.import_mw + " MW, CMD " + d.limits.cmd_mw + " MW, configuration " + d.config + ". Changing these needs a new storage-value table, so they are fixed here." : "";
}
function checks(){
  var L = [], blocking = false, changed = false;
  Object.keys(DEF).forEach(function(k){ if (String(IN[k]) !== String(DEF[k])) changed = true; });
  if (+IN.ev + +IN.sb > +IN.mx) { blocking = true; L.push(["b", "Standby + event = " + (+IN.ev + +IN.sb) + " h is longer than the maximum control " + IN.mx + " h. Blocking: the offer is infeasible.", "c-mx"]); }
  else L.push(["ok", "Standby + event = " + (+IN.ev + +IN.sb) + " h, within the maximum control " + IN.mx + " h."]);
  if (+IN.res < 0 || +IN.res >= 100) { blocking = true; L.push(["b", "Customer reserve must be at least 0% and below 100%.", "c-res"]); }
  if (+IN.cap < 1) { blocking = true; L.push(["b", "Annual cap must be at least 1.", "c-cap"]); }
  if (String(IN.exp) !== String(DEF.exp)) { var pc = planCheck(); L.push([pc.fails.length ? "w" : "i", "Export limit " + IN.exp + " MW: today's plan is re-checked against it" + (pc.fails.length ? " and is blocked in " + pc.bad.length + " half-hours." : " and passes.")]); }
  if (String(IN.fcess) !== String(DEF.fcess)) L.push(["i", "Changing FCESS services needs a new backtest run."]);
  return {list:L, blocking:blocking, changed:changed};
}
function renderConfig(){
  var c = checks();
  $("#c-checks").innerHTML = c.list.map(function(x){ var g = {ok:"✓", w:"!", b:"!", i:"i"}[x[0]]; return '<li><span class="ci ' + x[0] + '">' + g + "</span><span>" + x[1] + "</span></li>"; }).join("");
  $$(".f").forEach(function(f){ f.classList.remove("err"); });
  c.list.forEach(function(x){ if (x[2]) { var el = document.getElementById(x[2]), f = el && el.closest(".f"); if (f) f.classList.add("err"); } });
}
var RUNID = (SITE[0] && SITE[0].config) ? "backtest " + SITE[0].config.split(" ")[0] : "saved backtest";
function refreshRun(){
  var c = checks(), run = $("#run"), pri = $("#pri"), t, sub, cls;
  document.body.classList.toggle("stale", c.changed);
  if (c.blocking) { cls = "bad"; t = "Inputs blocked"; sub = "fix them in Settings"; pri.textContent = "Recompute"; pri.disabled = true; }
  else if (c.changed) { cls = "stale"; t = "Results stale"; sub = "inputs changed"; pri.textContent = "Recompute"; pri.disabled = false; }
  else { cls = "ok"; t = "Results up to date"; sub = RUNID; pri.textContent = "Save run"; pri.disabled = false; }
  run.className = "run " + cls; $("#run-t").textContent = t; $("#run-s").textContent = sub;
  $("#strip-st").classList.toggle("show", c.changed);
  $("#st-text").textContent = (c.blocking ? "Inputs changed and blocked by the input check. " : "Inputs changed. ") + "The results shown are from the saved " + RUNID + " with the previous inputs.";
  $("#strip-nc").classList.toggle("show", !!S.nc && c.changed);
}

/* ---------- shell ---------- */
var HOME = "today", ALIAS = {plan:"today", schedule:"today", compare:"svalid", forecast:"svalid", activation:"calls", validation:"svalid"};
var SCREENS = {
  today:{t:"Today", chips:function(){ var m = model(); return m ? perChip(m.d.period) + (m.d.ircr_flag_0900 ? '<span class="chip"' + noteAttr("At 09:00 the day was flagged as a possible capacity-charge (IRCR) peak day, so the plan keeps import low in the peak window.") + ">IRCR peak flag</span>" : "") : ""; },
    ctx:function(){ var m = model(); return m ? "Site battery · " + m.d.weekday_label + " · trading day 08:00–08:00 AWST · plan issued 09:00" : "Site battery"; }},
  svalid:{t:"Validation · site battery", chips:function(){ return ""; }, ctx:function(){ return "Selection used in-sample data only (to 30 Jun 2026). Test results are reported, never used to choose."; }},
  calls:{t:"Tonight's call", chips:function(){ return ""; }, ctx:function(){ return "Customer fleet · decided at 09:00 with what was known then"; }},
  value:{t:"Value by party", chips:function(){ return ""; }, ctx:function(){ return "Customer fleet · baseline is the same battery in normal self-use · compensation is a transfer"; }},
  fvalid:{t:"Validation · customer fleet", chips:function(){ return ""; }, ctx:function(){ return "Hot Season holdouts and customer constraints"; }},
  config:{t:"Settings", chips:function(){ return ""; }, ctx:function(){ return "Any change marks results stale until the engine runs again"; }}
};
function header(){
  var sc = SCREENS[S.screen];
  $("#ph-title").textContent = sc.t; $("#ph-chips").innerHTML = sc.chips(); $("#ph-ctx").textContent = sc.ctx();
  document.body.setAttribute("data-screen", S.screen);
}
function render(){
  if (tipEl) tipHide();
  header();
  if (S.screen === "today") renderToday();
  if (S.screen === "svalid") renderSValid();
  if (S.screen === "calls") renderCalls();
  if (S.screen === "value") renderValue();
  if (S.screen === "fvalid") renderFValid();
  if (S.screen === "config") renderConfig();
  refreshRun();
}
function go(name){
  name = ALIAS[name] || name;
  if (!SCREENS[name] || !document.getElementById("s-" + name)) name = HOME;
  S.screen = name;
  $$(".screen").forEach(function(s){ s.classList.toggle("on", s.id === "s-" + name); });
  $$(".nav").forEach(function(b){ var on = b.getAttribute("data-go") === name; b.classList.toggle("on", on); if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  $$("[data-grp]").forEach(function(e){ e.classList.toggle("on", e.getAttribute("data-grp").split(" ").indexOf(name) >= 0); });
  try { history.replaceState(null, "", "#" + name); } catch (e) {}
  render(); window.scrollTo(0, 0);
}
function pick(lane, k){ S.lane = lane; S.sel = k; renderToday(); }
document.addEventListener("click", function(e){
  var t = e.target && e.target.closest ? e.target : null; if (!t) return;
  var b = t.closest("[data-go]"); if (b) { go(b.getAttribute("data-go")); return; }
  var dd = t.closest("[data-di]"); if (dd) { S.di = +dd.getAttribute("data-di"); S.sel = null; S.lane = "plan"; render(); return; }
  var pk = t.closest("[data-pick]"); if (pk) { var p = pk.getAttribute("data-pick").split(":"); pick(p[0], +p[1]); return; }
  var sg = t.closest(".seg-b"); if (sg) { var svg = sg.closest("svg"), r = svg.getBoundingClientRect(), x = (e.clientX - r.left) * (+svg.viewBox.baseVal.width / r.width); pick(sg.getAttribute("data-lane"), Math.max(0, Math.min(N - 1, Math.floor((x - X0) / IW)))); return; }
  var h = t.closest(".hit[data-lane]"); if (h) { pick(h.getAttribute("data-lane"), +h.getAttribute("data-i")); return; }
  var hd = t.closest(".hit[data-d]"); if (hd) { S.cday = +hd.getAttribute("data-d"); render(); return; }
  if (t.closest("[data-reveal]")) { setReveal(true); render(); return; }
  var c = t.closest("[data-csvdl]"); if (c) { csvDownload(c.getAttribute("data-csvdl")); return; }
  if (t.closest("[data-inspect]")) { var pc = planCheck(); if (pc.bad.length) { S.lane = "plan"; S.sel = pc.bad[0]; } renderToday(); var el = document.getElementById("t-day"); if (el && el.scrollIntoView) el.scrollIntoView({block:"center"}); }
});
function setReveal(v){ S.revealed = v; $$("[data-po]").forEach(function(o){ o.classList.toggle("on", o.getAttribute("data-po") === (v ? "outcome" : "plan")); }); $$("[data-po] .lk").forEach(function(e){ e.style.display = v ? "none" : ""; }); }
$$("[data-po]").forEach(function(b){ b.addEventListener("click", function(){ setReveal(b.getAttribute("data-po") === "outcome"); render(); }); });
function bindReveal(){}
function segBind(attr, key){ document.addEventListener("click", function(e){ var b = e.target && e.target.closest ? e.target.closest("[" + attr + "]") : null; if (!b) return; S[key] = b.getAttribute(attr); render(); }); }
segBind("data-view", "view"); segBind("data-offer", "offer"); segBind("data-vscen", "vscen"); segBind("data-vper", "vper"); segBind("data-vunit", "vunit");
$("#day-prev").addEventListener("click", function(){ S.cday = Math.max(0, S.cday - 1); render(); });
$("#day-next").addEventListener("click", function(){ S.cday = S.cday + 1; render(); });
$$("[data-k]").forEach(function(el){ el.addEventListener("change", function(){ var k = el.getAttribute("data-k"); IN[k] = el.type === "number" ? (el.value === "" ? "" : +el.value) : el.value; S.nc = false; render(); }); });
$("#revert").addEventListener("click", function(){ IN = JSON.parse(JSON.stringify(DEF)); S.nc = false; fillConfig(); render(); toast("Inputs reverted. Results are up to date again."); });
$("#nc-close").addEventListener("click", function(){ S.nc = false; refreshRun(); });
$("#pri").addEventListener("click", function(){ if (checks().changed) { S.nc = true; refreshRun(); return; } toast("Prototype: saving runs is not implemented."); });
$("#exp-all").addEventListener("click", function(){ toast(checks().changed ? "Export is off while results are stale." : "Prototype: report export is not implemented. Use the CSV button on a chart."); });
var toastT;
function toast(msg){ var t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(function(){ t.classList.remove("show"); }, 4500); }
var rzT; window.addEventListener("resize", function(){ clearTimeout(rzT); rzT = setTimeout(render, 150); });
if (window.ResizeObserver) {
  var boxW = {}, ro = new ResizeObserver(function(en){ var ch = false; en.forEach(function(e){ var w = Math.round(e.contentRect.width); if (boxW[e.target.id] !== undefined && boxW[e.target.id] !== w) ch = true; boxW[e.target.id] = w; }); if (ch) requestAnimationFrame(render); });
  ["#t-chart", "#c-season"].forEach(function(q){ var el = document.querySelector(q); if (el) ro.observe(el); });
}
document.addEventListener("keydown", function(e){
  if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
  var dl = e.key === "ArrowRight" ? 1 : (e.key === "ArrowLeft" ? -1 : 0); if (!dl) return;
  if (S.screen === "today") { S.sel = Math.max(0, Math.min(N - 1, (S.sel == null ? (model() ? model().f0 : 2) - dl : S.sel) + dl)); if (S.lane === "cond" && !segAt(condSegs(model()), S.sel)) S.lane = "plan"; renderToday(); e.preventDefault(); }
  if (S.screen === "calls") { S.cday = Math.max(0, S.cday + dl); render(); e.preventDefault(); }
});

/* ---------- hover tables ---------- */
var TIPS = {
  day:function(k){
    var m = model(), r = [], b = m.bat[k], c = m.cond[k];
    if (k < m.f0) return {h:hhmm(k) + "–" + hhmm(k + 1), s:"Before 09:00: earlier plan, no 09:00 forecast", r:S.revealed && m.actual ? [["ln", "var(--price)", "Actual price", "A$" + nf(m.actual[k]) + "/MWh"]] : []};
    if (num(b)) r.push(["sq", "var(--" + (b < 0 ? "chg" : "dis") + ")", "Scheduled", (b > 0 ? "+" : (b < 0 ? "−" : "")) + nf(Math.abs(b), 1) + " MW"]);
    if (c && c.mw > 0.05) r.push(["cnd", "", "Conditional", "+" + nf(c.mw, 1) + " MW if ≥ A$" + nf(c.min)]);
    if (m.raise[k] + m.lower[k] > 0.05) r.push(["sq", "color-mix(in srgb,var(--fcess) 50%,var(--surface))", "FCESS standby", "raise " + nf(m.raise[k], 1) + " · lower " + nf(m.lower[k], 1) + " MW"]);
    if (m.fc) r.push(["dsh", "var(--price)", "Price forecast", num(m.fc[k]) ? "A$" + nf(m.fc[k]) + "/MWh" : "not issued"]);
    if (m.p10 && m.p90 && num(m.p10[k])) r.push(["", "", "P10–P90", "A$" + nf(m.p10[k]) + "–" + nf(m.p90[k])]);
    if (m.spike && num(m.spike[k])) r.push(["", "", "Chance of A$200 or more", nf(100 * m.spike[k]) + "%"]);
    if (S.revealed && m.actual) r.push(["ln", "var(--price)", "Actual price", "A$" + nf(m.actual[k]) + "/MWh" + (m.amax && m.amax[k] > m.actual[k] + 1 ? " · 5-min max " + nf(m.amax[k]) : "")]);
    if (S.revealed && m.cleared && m.cleared[k] > 0.005) r.push(["cnd", "", "Conditional cleared", nf(m.cleared[k], 2) + " MW"]);
    return {h:hhmm(k) + "–" + hhmm(k + 1), s:ANAME[m.act[k]], r:r};
  },
  season:function(q){
    var days = seasonDays(), x = days[q], dec = S.view === "decision", off = S.offer, sc = S.vscen, r = [];
    if (dec && q > S.cday) return {h:dLabel(x.day), s:"Not yet known at 09:00 on " + dLabel(days[S.cday].day), r:[]};
    if (dec && q === S.cday) r.push(["dsh", "var(--peak)", "09:00 forecast peak", nf(x.forecast_peak_mw) + " MW"]);
    else r.push(["sq", "var(--hold)", "Daily peak demand", nf(x.actual_peak_mw) + " MW"]);
    r.push(["dsh", "var(--peak)", "Call threshold at 09:00", num(thrOn(x, off, sc)) ? nf(thrOn(x, off, sc)) + " MW" : "season start: all flagged"]);
    r.push(["", "", "Flagged", flaggedOn(x, off, sc) ? "yes" : "no"], ["sq", "var(--cust)", "Called", calledOn(x, off, sc) ? "yes" : "no"]);
    if (!dec) r.push(["", "", "Official peak day · hindsight", x.ircr_peak_day ? "yes" : "no"]);
    return {h:dLabel(x.day), s:offerName(off) + " · " + SCN[sc].label, r:r};
  },
  soc:function(k){
    var m = model(), r = [];
    if (m.soc && k >= m.f0) r.push(["dsh", "var(--soc)", "Planned", nf(m.soc[k], 1) + " → " + nf(m.soc[k + 1], 1) + " MWh"]);
    if (S.revealed && m.outSoc) r.push(["ln", "var(--soc)", "Actual", nf(m.outSoc[k], 1) + " → " + nf(m.outSoc[k + 1], 1) + " MWh"]);
    if (m.floor && num(m.floor[k])) r.push(["", "", "Reserve floor", nf(m.floor[k], 1) + " MWh"]);
    if (m.prot && num(m.prot[k])) r.push(["", "", "Protected (sold only at the cap)", nf(m.prot[k], 1) + " MWh"]);
    return {h:hhmm(k) + "–" + hhmm(k + 1), s:ANAME[m.act[k]], r:r};
  }
};
var tipEl = null, tipKey = "";
function swatch(kind, col){ return '<i class="sw2 ' + kind + '"' + (col ? ' style="--c:' + col + '"' : "") + "></i>"; }
function tipShow(svg, k, ev){
  var f = TIPS[svg.getAttribute("data-tip")]; if (!f) return;
  if (!tipEl) { tipEl = document.createElement("div"); tipEl.className = "ctip"; tipEl.setAttribute("role", "tooltip"); document.body.appendChild(tipEl); }
  var key = svg.getAttribute("data-tip") + k;
  if (tipKey !== key) { var t = f(k); tipKey = key; tipEl.innerHTML = '<div class="ct-h"><b>' + t.h + "</b>" + (t.s ? "<span>" + t.s + "</span>" : "") + "</div>" + (t.r.length ? "<table>" + t.r.map(function(r){ return "<tr><td>" + (r[0] ? swatch(r[0], r[1]) : "") + "</td><td>" + r[2] + '</td><td class="n">' + r[3] + "</td></tr>"; }).join("") + "</table>" : ""); }
  tipEl.style.display = "block";
  var w = tipEl.offsetWidth, hh = tipEl.offsetHeight, x = ev.clientX + 16, y = ev.clientY + 14;
  if (x + w > window.innerWidth - 8) x = ev.clientX - w - 16;
  if (y + hh > window.innerHeight - 8) y = Math.max(8, ev.clientY - hh - 14);
  tipEl.style.left = x + "px"; tipEl.style.top = y + "px";
  $$('svg[data-xg="' + svg.getAttribute("data-xg") + '"]').forEach(function(g){ var xh = g.querySelector(".xh"); if (!xh) return; var cx = (+g.getAttribute("data-x0") + (k + 0.5) * +g.getAttribute("data-iw")).toFixed(1); xh.setAttribute("x1", cx); xh.setAttribute("x2", cx); xh.style.visibility = "visible"; });
}
function tipHide(){ if (tipEl) tipEl.style.display = "none"; tipKey = ""; $$(".xh").forEach(function(l){ l.style.visibility = "hidden"; }); }
document.addEventListener("mousemove", function(e){
  var t = e.target && e.target.closest ? e.target : null, hit = t ? t.closest(".hit") : null, svg = hit && hit.closest("svg[data-tip]");
  if (!svg && t) { var sg = t.closest(".seg-b"); if (sg) { svg = sg.closest("svg[data-tip]"); var r = svg.getBoundingClientRect(), x = (e.clientX - r.left) * (+svg.viewBox.baseVal.width / r.width); tipShow(svg, Math.max(0, Math.min(N - 1, Math.floor((x - X0) / IW))), e); return; } }
  if (!svg) { if (tipKey || (tipEl && tipEl.style.display === "block")) tipHide(); return; }
  tipShow(svg, +(hit.getAttribute("data-i") || hit.getAttribute("data-d")), e);
});
window.addEventListener("scroll", tipHide, true);

/* ---------- CSV of exactly what is shown: no actuals before they are revealed ---------- */
function csvDownload(kind){
  if (document.body.classList.contains("stale")) { toast("Export is off while results are stale."); return; }
  var rev = S.revealed, head, rows = [], name, meta, k, m = model();
  if (kind === "day" && m) {
    head = ["interval_start", "interval_end", "action", "planned_battery_mw", "conditional_mw", "conditional_min_price_aud_per_mwh", "fcess_raise_mw", "fcess_lower_mw", "price_forecast_p50_aud_per_mwh", "soc_start_mwh", "reserve_floor_mwh"].concat(rev ? ["price_actual_aud_per_mwh", "executed_battery_mw", "soc_actual_end_mwh"] : []);
    for (k = 0; k < N; k++) rows.push([hhmm(k), hhmm(k + 1), m.act[k], m.bat[k], m.cond[k] ? m.cond[k].mw : null, m.cond[k] ? m.cond[k].min : null, m.raise[k], m.lower[k], m.fc ? m.fc[k] : null, m.soc ? m.soc[k] : null, m.floor ? m.floor[k] : null].concat(rev ? [m.actual ? m.actual[k] : null, m.outBat ? m.outBat[k] : null, m.outSoc ? m.outSoc[k + 1] : null] : []));
    name = "greenspark_plan_" + m.d.day; meta = "trading day " + m.d.day + " 08:00-08:00 AWST, plan issued 09:00, " + perLong(m.d.period) + (rev ? " | includes revealed actuals" : "");
  } else { toast("Nothing to download here."); return; }
  var txt = "# GreenSpark | HISTORICAL REPLAY from saved backtest outputs | " + meta + "\n" + head.join(",") + "\n" + rows.map(function(r){ return r.map(function(v){ return v == null ? "" : (typeof v === "number" ? String(+v.toFixed(3)) : v); }).join(","); }).join("\n") + "\n";
  var a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt], {type:"text/csv"})); a.download = name + ".csv";
  document.body.appendChild(a); a.click(); setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  toast("Downloaded " + name + ".csv");
}
$$(".ctools[data-csv]").forEach(function(t){ t.innerHTML = '<button class="ib" data-csvdl="' + t.getAttribute("data-csv") + '" title="Download CSV" aria-label="Download CSV"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5v8M4.5 7L8 10.5 11.5 7M2.5 12.5v1.5h11v-1.5"/></svg></button>'; });

fillConfig();
setReveal(false);
go((location.hash || "#" + HOME).slice(1));
})();
