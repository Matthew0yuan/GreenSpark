/* Several days on the Plan page (7 days to a year): what happened and the 09:00 price forecast, as saved. Each half-hour up
   to a month; beyond it one column a day, the day's average with its range. A click opens the day. */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Num, type Span, type SpanRow } from "../api";
import { around, bandPath, columnsPath, linePath, N, Placer, segPts, socTicks, spanGeo, stepPath, stepPts, tw, X0, YGrid } from "../components/chart";
import { Waterfall } from "../components/money";
import { ChartTip, Chip, Failed, Loading, Measured, useWidth, type TipData } from "../components/ui";
import { addDays, aud, audShort, dayLabel, hhmm, isNum, nf, niceTicks } from "../lib/format";
import { FIRST_DAY, isDefault, useApp, useLive } from "../state";

const CUTOFF = "2026-07-01", LIVE_FROM = "2026-10-23";  // as lib/format's periodOf
const OFF_SCALE = 500;  // A$/MWh: the price axis stops here, and higher prices are marked at its top

/** The evaluation periods the days to `end` fall in: [the glossary's key, the chip's word]. */
export function spanPeriods(end: string, days: number): [string, string][] {
  const a = addDays(end, 1 - days), start = a < FIRST_DAY ? FIRST_DAY : a;
  const out: [string, string][] = [];
  if (start < CUTOFF) out.push(["TRAIN", "training"]);
  if (end >= CUTOFF && start < LIVE_FROM) out.push(["TREV", "test"]);
  if (end >= LIVE_FROM) out.push(["LIVE", "live"]);
  return out;
}

const when = (iso: string, o: Intl.DateTimeFormatOptions) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-AU", { ...o, timeZone: "UTC" }).replace(",", "");

/** Days labelled under the chart: each day to a fortnight, each week to six weeks, then the first of each month. */
function axisDays(rows: SpanRow[], IW: number) {
  const n = rows.length, out: { i: number; label: string }[] = [];
  if (n <= 14) {
    const every = IW < 50 ? 2 : 1;
    for (let i = (n - 1) % every; i < n; i += every) out.push({ i, label: when(rows[i].day, { weekday: "short", day: "numeric" }) });
  } else if (n <= 45) {
    for (let i = (n - 1) % 7; i < n; i += 7) out.push({ i, label: when(rows[i].day, { day: "numeric", month: "short" }) });
  } else {
    rows.forEach((r, i) => { if (r.day.endsWith("-01")) out.push({ i, label: when(r.day, r.day.slice(5, 7) === "01" ? { month: "short", year: "2-digit" } : { month: "short" }) }); });
  }
  return out;
}

/** A key above a panel: short samples of its lines or colours and their names, right-aligned. */
function PanelKey({ right, y, items }: { right: number; y: number; items: [string, React.CSSProperties, boolean?][] }) {
  let x = right;
  return (
    <g>
      {[...items].reverse().map(([label, style, block]) => {
        x -= tw(label, "ann");
        const at = x;
        x -= block ? 12 : 20;
        const sample = x;
        x -= 12;
        return (
          <g key={label}>
            {block ? <rect x={sample} y={y - 8} width={8} height={8} rx={1.5} style={style} /> : <line x1={sample} x2={sample + 15} y1={y - 4} y2={y - 4} style={style} />}
            <text className="ann" x={at} y={y}>{label}</text>
          </g>
        );
      })}
    </g>
  );
}

const flat = (rows: SpanRow[], f: (r: SpanRow) => Num[] | null | undefined): Num[] => rows.flatMap((r) => (f(r) ?? Array(N).fill(null)).slice(0, N));
const ACTUAL: React.CSSProperties = { fill: "none", stroke: "var(--actual)", strokeWidth: 1.6 };
const FORECAST: React.CSSProperties = { fill: "none", stroke: "var(--price)", strokeWidth: 1.4, strokeDasharray: "5 3" };
const FACTORY: React.CSSProperties = { fill: "none", stroke: "var(--base)", strokeWidth: 1, strokeDasharray: "2 2" };
const CAP: React.CSSProperties = { fill: "none", stroke: "var(--warn)", strokeWidth: 1.4, strokeDasharray: "6 3" };
const RANGE: React.CSSProperties = { fill: "var(--actual)", opacity: 0.16 };

function SpanChart({ s, openDay }: { s: Span; openDay: (d: string) => void }) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<TipData>(null);
  const [hover, setHover] = useState<number | null>(null);
  const rows = s.rows, fine = s.resolution === "halfhour", per = fine ? N : 1;
  const g = spanGeo(W, rows.length * per), gd = spanGeo(W, rows.length), n = g.n;
  const site = s.asset.site, E = s.asset.energy_mwh, P = s.asset.power_mw;

  // the series, half-hour by half-hour or day by day: the drawing below is the same for both
  const d = useMemo(() => {
    if (fine) {
      const stored = flat(rows, (r) => r.halfhours!.stored_mwh);
      stored.push(rows[rows.length - 1].halfhours!.stored_mwh[N]);
      return { actual: flat(rows, (r) => r.halfhours!.price_actual), forecast: flat(rows, (r) => r.halfhours!.price_forecast),
        lo: flat(rows, (r) => r.halfhours!.price_p10), hi: flat(rows, (r) => r.halfhours!.price_p90), top: flat(rows, (r) => r.halfhours!.price_actual),
        battery: flat(rows, (r) => r.halfhours!.battery_mw), stored, storedLo: [] as Num[], storedHi: [] as Num[],
        meter: flat(rows, (r) => r.halfhours!.meter_mw), load: flat(rows, (r) => r.halfhours!.load_mw), cap: flat(rows, (r) => r.halfhours!.cap_mw) };
    }
    const of = (f: (r: SpanRow) => Num) => rows.map(f);
    return { actual: of((r) => r.price.actual_mean), forecast: of((r) => r.price.forecast_mean), lo: of((r) => r.price.actual_lo), hi: of((r) => r.price.actual_hi),
      top: of((r) => r.price.actual_hi), battery: [] as Num[], stored: of((r) => r.stored_mwh.end), storedLo: of((r) => r.stored_mwh.lo), storedHi: of((r) => r.stored_mwh.hi),
      meter: of((r) => r.site?.peak_mw ?? null), load: of((r) => r.site?.load_peak_mw ?? null), cap: of((r) => r.site?.cap_mw ?? null) };
  }, [rows, fine]);

  let yy = 28;
  const panel = (h: number) => { const p = { top: yy + 12, bot: yy + 12 + h }; yy = p.bot + 26; return p; };
  const pB = panel(64), pP = panel(130), pS = panel(84), pM = site ? panel(84) : null, pV = panel(72);
  const end = pV.bot, H = end + 24;

  /* battery: out above 0, in below; MW each half-hour, MWh a day */
  const outs = rows.map((r) => r.discharged_mwh), ins = rows.map((r) => r.charged_mwh);
  const maxB = fine ? P : Math.ceil(Math.max(1, ...outs, ...ins) / 10) * 10;
  const yB = (v: number) => (pB.top + pB.bot) / 2 - (v / maxB) * ((pB.bot - pB.top) / 2);
  const gap = gd.IW >= 3 ? 0.5 : 0;
  const bOut = fine ? columnsPath(g, d.battery.map((v) => (isNum(v) && v > 0 ? v : null)), yB, () => 0, 0.05) : columnsPath(gd, outs, yB, () => 0, 0.05, gap);
  const bIn = fine ? columnsPath(g, d.battery.map((v) => (isNum(v) && v < 0 ? v : null)), yB, () => 0, 0.05) : columnsPath(gd, ins.map((v) => -v), yB, () => 0, 0.05, gap);

  /* price: axis as on the day chart, higher prices marked at its top */
  const seen = [...d.actual, ...d.forecast, ...d.top].filter(isNum);
  const hiP = Math.max(250, ...seen.map((v) => Math.min(v, OFF_SCALE)));
  const loP = Math.min(0, ...seen.map((v) => Math.max(v, -100)));
  const yP = (v: number) => pP.bot - ((Math.max(loP, Math.min(hiP, v)) - loP) / (hiP - loP)) * (pP.bot - pP.top);
  const stepP = hiP > 300 ? 100 : 50;
  const ticksP: number[] = [];
  for (let t = Math.ceil(loP / stepP) * stepP; t <= hiP; t += stepP) ticksP.push(t);
  const over = d.top.map((v, k) => (isNum(v) && v > hiP ? k : -1)).filter((k) => k >= 0);
  const kTop = d.top.reduce<number>((b, v, k) => (isNum(v) && v > (d.top[b] ?? -Infinity) ? k : b), 0);
  const topV = d.top[kTop];

  /* stored energy, import, profit */
  const yS = (v: number) => pS.bot - (v / E) * (pS.bot - pS.top);
  const mVals = [...d.meter, ...d.load, ...d.cap].filter(isNum);
  const hiM = site ? Math.max(site.import_limit_mw, ...mVals) : 1, loM = site ? Math.min(0, ...mVals) : 0;
  const yM = pM ? (v: number) => pM.bot - ((v - loM) / (hiM - loM)) * (pM.bot - pM.top) : (v: number) => v;
  const ticksM = niceTicks(loM, hiM, 3);
  const capEnd = [...rows].reverse().find((r) => isNum(r.site?.cap_mw))?.site?.cap_mw;
  // as on the day chart: the CMD until a month's import goes higher, then that
  const capTxt = site && isNum(capEnd) && capEnd > site.cmd_mw + 0.05 ? `Cap ${capEnd.toFixed(1)} MW` : `CMD ${nf(site?.cmd_mw ?? 0, site && site.cmd_mw % 1 ? 1 : 0)} MW`;
  const market = rows.map((r) => r.money_aud.market_net), network = rows.map((r) => r.money_aud.network);
  const tops = rows.map((r, i) => (isNum(network[i]) ? Math.max(0, r.money_aud.market_net ?? 0) + network[i]! : null));
  const loV = Math.min(0, ...market.filter(isNum)), hiV = Math.max(1, ...market.filter(isNum), ...tops.filter(isNum));
  const ticksV = niceTicks(loV, hiV, 3);
  const yV = (v: number) => pV.bot - ((v - loV) / (hiV - loV)) * (pV.bot - pV.top);

  /* where the evaluation period changes */
  const marks = ([[CUTOFF, "test →"], [LIVE_FROM, "live →"]] as const).filter(([m]) => rows[0].day < m && m <= rows[rows.length - 1].day)
    .map(([m, label]) => ({ x: gd.X(rows.findIndex((r) => r.day >= m)), label }));
  const plots = [pB, pP, pS, ...(pM ? [pM] : []), pV];
  const ticks = axisDays(rows, gd.IW);

  /* the highest price, clear of the lines */
  const pl = new Placer({ x0: X0 + 3, x1: W, y0: pP.top - 4, y1: pP.bot - 2 });
  pl.line(stepPts(g, d.actual, yP, 0, n)).line(stepPts(g, d.forecast, yP, 0, n));
  marks.forEach((m) => pl.line(segPts(m.x, pP.top, m.x, pP.bot)));
  const topTxt = isNum(topV) ? `A$${nf(topV)}` : "";
  const topAt = topTxt ? pl.place(topTxt, "ann", over.length
    ? [[g.X(kTop) + 5, pP.top + 8, "start"], [g.X(kTop) - 5, pP.top + 8, "end"]]
    : around(g.X(kTop) + g.IW / 2, yP(topV!))) : null;

  const tipFor = (k: number, e: React.MouseEvent) => {
    const r = rows[Math.floor(k / per)];
    const a = (v: Num) => (isNum(v) ? `A$${nf(v)}` : "–");
    if (fine) {
      const h = r.halfhours!, j = k % N, mw = h.battery_mw[j], st = h.stored_mwh[j + 1];
      const out: [string, string][] = [["Battery", !isNum(mw) ? "–" : Math.abs(mw) < 0.05 ? "hold" : `${mw > 0 ? "out" : "in"} ${nf(Math.abs(mw), 1)} MW`],
        ["Forecast", a(h.price_forecast[j])], ["Actual", a(h.price_actual[j])], ["Stored", isNum(st) ? `${nf(st, 1)} MWh` : "–"]];
      if (h.meter_mw) out.push(["Import", isNum(h.meter_mw[j]) ? `${nf(h.meter_mw[j]!, 1)} MW` : "–"]);
      setTip({ x: e.clientX, y: e.clientY, title: `${dayLabel(r.day, false)} ${hhmm(j)}`, rows: out });
    } else {
      const out: [string, string][] = [["Price", `${a(r.price.actual_mean)}, high ${a(r.price.actual_hi)}`],
        ["Battery", `out ${nf(r.discharged_mwh)}, in ${nf(r.charged_mwh)} MWh`], ["Stored", isNum(r.stored_mwh.end) ? `end ${nf(r.stored_mwh.end, 1)} MWh` : "–"]];
      if (r.site) out.push(["Import", isNum(r.site.peak_mw) ? `peak ${nf(r.site.peak_mw, 1)} MW` : "–"]);
      out.push(["Profit", aud(r.money_aud.net)]);
      setTip({ x: e.clientX, y: e.clientY, title: dayLabel(r.day), rows: out });
    }
  };
  const at = (e: React.MouseEvent<SVGRectElement>) => {
    const b = e.currentTarget.getBoundingClientRect();
    return Math.max(0, Math.min(n - 1, Math.floor(((e.clientX - b.left) / b.width) * n)));
  };
  const title = (txt: string, top: number) => <text className="ttl" x={0} y={top - 12}>{txt}</text>;

  return (
    <div className="blk chart" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${s.days} days to ${dayLabel(s.end)}: battery, price, energy stored${site ? ", site import" : ""} and profit`}>
        {/* day ticks through every panel */}
        {plots.map((p, j) => <g key={j}>{ticks.map((t) => <line key={t.i} className="gl" x1={gd.X(t.i)} x2={gd.X(t.i)} y1={p.top} y2={p.bot} />)}</g>)}

        {title(fine ? "Battery, MW" : "Battery, MWh a day", pB.top)}
        <YGrid g={g} ticks={[maxB, 0, -maxB]} y={yB} fmt={(v) => (v > 0 ? `${nf(v)} out` : v < 0 ? `${nf(-v)} in` : "0")} />
        <path d={bOut} style={{ fill: "var(--dis)" }} />
        <path d={bIn} style={{ fill: "var(--chg)" }} />

        {title("Price, A$/MWh", pP.top)}
        <PanelKey right={g.X(n)} y={pP.top - 12} items={[[fine ? "Actual" : "Actual average", ACTUAL], [fine ? "Forecast 09:00" : "Forecast average", FORECAST],
          ...(!fine ? [["Lowest to highest", { ...RANGE, opacity: 0.3 }, true] as [string, React.CSSProperties, boolean]] : [])]} />
        <YGrid g={g} ticks={ticksP} y={yP} />
        <path d={bandPath(g, d.lo, d.hi, yP, 0, n)} style={fine ? { fill: "var(--range)", opacity: "var(--range-op)" } : RANGE} />
        <path className="series" d={stepPath(g, d.forecast, yP, 0, n)} style={FORECAST} />
        <path className="series" d={stepPath(g, d.actual, yP, 0, n)} style={ACTUAL} />
        {over.map((k) => <line key={k} x1={g.X(k) + g.IW / 2} x2={g.X(k) + g.IW / 2} y1={pP.top - 2} y2={pP.top + 6} style={{ stroke: "var(--actual)", strokeWidth: 1.5 }} />)}
        {topAt && <text className="ann halo" x={topAt.x} y={topAt.y} textAnchor={topAt.a} style={{ fill: "var(--actual)" }}>{topTxt}</text>}

        {title("Energy stored, MWh", pS.top)}
        <YGrid g={g} ticks={socTicks(E)} y={yS} />
        <line className="ref" x1={X0} x2={g.X(n)} y1={yS(E)} y2={yS(E)} style={{ stroke: "var(--axis)", strokeDasharray: "2 3" }} />
        {!fine && <path d={bandPath(g, d.storedLo, d.storedHi, yS, 0, n)} style={RANGE} />}
        <path className="series" d={fine ? linePath(g, d.stored, yS, 0, n) : stepPath(g, d.stored, yS, 0, n)} style={ACTUAL} />
        <text className="ann" x={g.X(n)} y={pS.top - 12} textAnchor="end">Full {nf(E)} MWh</text>

        {pM && <>
          {title(fine ? "Site import, MW" : "Highest site import, MW", pM.top)}
          <PanelKey right={g.X(n)} y={pM.top - 12} items={[["Metered", ACTUAL], ["Factory", FACTORY], [capTxt, CAP]]} />
          <YGrid g={g} ticks={ticksM} y={yM} />
          <path className="series" d={stepPath(g, d.load, yM, 0, n)} style={FACTORY} />
          <path className="ref" d={stepPath(g, d.cap, yM, 0, n)} style={CAP} />
          <path className="series" d={stepPath(g, d.meter, yM, 0, n)} style={ACTUAL} />
        </>}

        {title("Profit a day, A$", pV.top)}
        {site && <PanelKey right={g.X(n)} y={pV.top - 12} items={[["Market", { fill: "var(--zen)" }, true], ["Network bill", { fill: "var(--chg)" }, true]]} />}
        <YGrid g={g} ticks={ticksV} y={yV} fmt={audShort} />
        <path d={columnsPath(gd, market.map((v) => (isNum(v) && v > 0 ? v : null)), yV, () => 0, 0, gap)} style={{ fill: "var(--zen)" }} />
        <path d={columnsPath(gd, market.map((v) => (isNum(v) && v < 0 ? v : null)), yV, () => 0, 0, gap)} style={{ fill: "var(--bad)" }} />
        <path d={columnsPath(gd, tops, yV, (i) => Math.max(0, market[i] ?? 0), 0, gap)} style={{ fill: "var(--chg)" }} />

        {/* the evaluation periods, and the axis */}
        {marks.map((m) => (
          <g key={m.label}>
            <path className="ref" d={plots.map((p) => `M${m.x.toFixed(2)},${p.top}V${p.bot}`).join("")} style={{ fill: "none", stroke: "var(--mark)" }} />
            <text className="mk9" x={m.x} y={pB.top - 4} textAnchor="middle">{m.label}</text>
          </g>
        ))}
        <line x1={X0} x2={g.X(n)} y1={end} y2={end} style={{ stroke: "var(--axis)" }} />
        {ticks.map((t) => <text key={t.i} x={gd.X(t.i)} y={end + 15} textAnchor={rows.length <= 14 ? "start" : "middle"}>{t.label}</text>)}

        {hover != null && plots.map((p, j) => <line key={j} x1={g.X(hover) + g.IW / 2} x2={g.X(hover) + g.IW / 2} y1={p.top} y2={p.bot} style={{ stroke: "var(--text-4)", pointerEvents: "none" }} />)}
        <rect x={X0} y={pB.top} width={g.X(n) - X0} height={end - pB.top} style={{ fill: "transparent", cursor: "pointer" }}
          onMouseMove={(e) => { const k = at(e); setHover(k); tipFor(k, e); }} onMouseLeave={() => { setHover(null); setTip(null); }}
          onClick={(e) => openDay(rows[Math.floor(at(e) / per)].day)} />
      </svg>
      <ChartTip tip={tip} />
    </div>
  );
}

/** The days worth opening: the best and worst for money in the market, the highest price and import. */
function Standouts({ s, openDay }: { s: Span; openDay: (d: string) => void }) {
  const whole = s.rows.filter((r) => r.complete);
  const best = (f: (r: SpanRow) => Num, sign = 1) => whole.reduce<SpanRow | null>((b, r) => (isNum(f(r)) && (!b || sign * f(r)! > sign * f(b)!) ? r : b), null);
  const list: [string, SpanRow | null, string][] = [];
  const add = (name: string, r: SpanRow | null, txt: (r: SpanRow) => string) => list.push([name, r, r ? txt(r) : "–"]);
  add("Best day", best((r) => r.money_aud.market_net), (r) => aud(r.money_aud.market_net));
  add("Worst day", best((r) => r.money_aud.market_net, -1), (r) => aud(r.money_aud.market_net));
  add("Highest price", best((r) => r.price.actual_hi), (r) => `A$${nf(r.price.actual_hi ?? 0)}/MWh`);
  if (s.asset.site) add("Highest import", best((r) => r.site?.peak_mw ?? null), (r) => `${nf(r.site?.peak_mw ?? 0, 1)} MW`);
  return (
    <div className="blk rule">
      <div className="blk-h"><h2>Standouts</h2>{s.asset.site && <span className="sub">money before the network bill</span>}</div>
      <table className="t">
        <tbody>
          {list.map(([name, r, txt]) => (
            <tr key={name}>
              <td>{name}</td>
              <td className="r">{txt}</td>
              <td className="r">{r && <button className="lnk" onClick={() => openDay(r.day)}>{dayLabel(r.day)}</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SpanBody({ end, days, openDay }: { end: string; days: number; openDay: (d: string) => void }) {
  const { settings } = useApp();
  const live = useLive(end);
  const q = useQuery({ queryKey: ["span", end, days, settings.connection, settings.dispatcher, settings.energyMwh, settings.cmdMw,
    live.after ? live.builtAt : null, live.isToday ? live.asOf : null], queryFn: () => api.span(end, days, settings) });
  if (q.isLoading) return <Loading what={`${days} days`} />;
  if (q.error) return <Failed error={q.error} />;
  const s = q.data;
  if (!s) return null;
  const t = s.totals, site = s.asset.site;
  const unsettled = site && s.network_to !== s.end;
  const note = `Energy + grid services − wear${site ? " + the month's network bill saving per day" : ""}, settled at actual prices.${site ? " The capacity charge (IRCR) is counted per year on Evidence." : ""}${unsettled ? ` The network bill is settled per calendar month: known to ${s.network_to ? dayLabel(s.network_to) : "no day here"}.` : ""}`;
  return (
    <>
      <div className="dec">
        <div className="dec-l">
          <div className="statusline"><span className="sub">{s.days} days, {dayLabel(s.start)} to {dayLabel(s.end)}</span></div>
          <div className="title"><Measured text={`${nf(t.discharged_mwh)} MWh out, ${nf(t.charged_mwh)} MWh in`} /></div>
        </div>
        <div className="dec-r">
          <div className="lbl">Profit</div>
          <div className="big" data-note={note}>{aud(t.net)}</div>
          <div className="chips dec-chips" style={{ justifyContent: "flex-end" }}>
            <Chip>{aud(t.per_day)}/day</Chip>
            {s.live && <Chip cls="live">today to {s.live.through}</Chip>}
            {unsettled && <Chip note={note}>{s.network_to ? `network bill to ${when(s.network_to, { day: "numeric", month: "short" })}` : "no network bill"}</Chip>}
            {!isDefault(settings) && <Chip cls="unv">simulation only</Chip>}
          </div>
        </div>
      </div>
      <div className="fig"><SpanChart s={s} openDay={openDay} /></div>
      <div className="two">
        <div className="blk rule">
          <div className="blk-h"><h2>Value</h2><span className="sub">{s.days} days, A$</span></div>
          <Waterfall parts={[["e", "Energy", t.energy ?? 0], ["f", "Grid services", t.fcess ?? 0], ...(isNum(t.network) ? [["b", "Network bill", t.network] as [string, string, number]] : [])]}
            wear={t.wear ?? 0} net={t.net ?? 0} />
        </div>
        <Standouts s={s} openDay={openDay} />
      </div>
    </>
  );
}
