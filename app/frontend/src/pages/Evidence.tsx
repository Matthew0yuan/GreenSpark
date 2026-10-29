import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Evidence as Ev } from "../api";
import { hbar, Placer, segPts, tw } from "../components/chart";
import { Chip, Failed, Gl, Loading, Note, PageHead, useWidth } from "../components/ui";
import { aud, isNum, nf, signed } from "../lib/format";
import { glNote } from "../lib/gloss";
import { CONN, DISP, useApp, whoItems } from "../state";

const KEYS = ["TRAIN", "TREV", "TBLIND"];
const NAME = ["Training period", "Test · reviewed", "Test · blind"];
const RANGE = ["1 Apr 2025 – 30 Jun 2026", "1 Jul – 15 Oct 2026", "16–22 Oct 2026"];

export default function Evidence() {
  const { settings } = useApp();
  const q = useQuery({ queryKey: ["evidence", settings.connection, settings.dispatcher], queryFn: () => api.evidence(settings), staleTime: Infinity });
  const [per, setPer] = useState(1);
  return (
    <section aria-label="Evidence">
      <PageHead title="Evidence · storage network" chips={<Chip cls="step">2 · Prove</Chip>}
        ctx={<>1 Apr 2025 – 22 Oct 2026 · 570 days replayed · <Gl k="DECISION">decided 09:00</Gl> · <Gl k={settings.connection === "factory" ? "FACTORY" : "OWNCONN"}>{CONN[settings.connection]}</Gl> · <Gl k="DISPATCHER">dispatcher {DISP[settings.dispatcher]}</Gl></>}
        who={whoItems(settings.connection)} />
      {q.isLoading && <Loading what="the evidence" />}
      {q.error && <Failed error={q.error} />}
      {q.data && <Body e={q.data} per={per} setPer={setPer} />}
    </section>
  );
}

function Body({ e, per, setPer }: { e: Ev; per: number; setPer: (i: number) => void }) {
  const P = e.periods[per];
  const u = P.uplift_vs_plan_only;
  const broken = e.limits.reduce((t, l) => t + l.over_power + l.soc_outside, 0);
  const halfhours = e.limits.reduce((t, l) => t + l.halfhours, 0);
  const vd = e.verdict;
  const key = (d: string) => (d === "e5" ? "E5" : "V1");
  const factory = e.connection === "factory";
  const parts = P.adopted_parts_aud;
  const zero = u && isNum(u.lo95) && isNum(u.hi95) && u.lo95 <= 0 && u.hi95 >= 0;
  const sc = (i: number) => "r" + (i === per ? " selcol" : "");
  return (
    <>
      <div className="dec">
        <div className="dec-l">
          <div className="title">
            <span className={"big " + (zero ? "dim" : "pos")} data-note={glNote("PLANONLY") + ` Same days, same prices, same battery; ${vd.money}.`}>{u ? signed(u.mean) : "–"}</span>
            {" "}/day vs <Gl k="PLANONLY">09:00 plan alone</Gl>
          </div>
          <div className="chips dec-chips">
            <Chip note={glNote(KEYS[per])}>{NAME[per]} · {P.period.days} d</Chip>
            <Chip cls={vd.passed ? undefined : "hyp"} note={`${glNote("E5")} Training period on this connection, v2 − v1: ${signed(vd.e5_minus_v1_aud_per_day)}/day, 95% range ${signed(vd.lo95)} to ${signed(vd.hi95)}. ${vd.rule}.`}>
              {DISP.e5} {vd.passed ? "passed here" : "not passed here"}</Chip>
          </div>
        </div>
        <div className="dec-r">
          <div className="lbl"><Note note="Half-hours in the backtest where the battery's mean power went above 10 MW or its stored energy left 0–40 MWh.">Limits broken</Note></div>
          <div className="big pos">{broken} <span className="sub">of {nf(halfhours)} half-hours</span></div>
        </div>
      </div>

      <div className="blk">
        <div className="blk-h"><h2><Note note="Mean extra profit per day over the 09:00 plan alone, with the 95% range from resampling whole months. Click a period to see it below.">Extra profit per day</Note></h2></div>
        <DotPlot e={e} per={per} setPer={setPer} />
      </div>

      <div className="blk">
        <div className="blk-h"><h2>{NAME[per]}</h2></div>
        <Bars rows={[
          ["PLANONLY", "09:00 plan alone", P.per_day_aud["plan only"], "var(--base)"],
          ["DISPATCHER", `This dispatcher (${DISP[e.adopted]})`, P.per_day_aud[e.adopted], "var(--zen)"],
          [key(e.candidate), `${DISP[e.candidate]} · the other`, P.per_day_aud[e.candidate], "var(--bar)"],
          ["PERFECT", "Prices known ahead", P.per_day_aud["prices known"], "hatch"],
        ]} parts={parts} />
      </div>

      <div className="two">
        <div className="blk rule">
          <div className="blk-h"><h2>Price forecast</h2><span className="sub">{NAME[per]} · {e.forecast[per].period.days} d</span></div>
          <Forecast f={e.forecast[per]} />
        </div>
        <div className="blk rule">
          <div className="blk-h"><h2>Limits held</h2></div>
          <table className="t" aria-label="Limits held, by period">
            <thead><tr><th></th>{NAME.map((n, i) => <th key={n} className={"r" + (i === per ? " selcol" : "")}>{n}</th>)}</tr></thead>
            <tbody>
              <tr><td><Note note="Half-hour mean power within 10 MW, every half-hour of the backtest.">Within 10 MW</Note></td>
                {e.limits.map((l, i) => <td key={i} className={sc(i)}>{l.over_power === 0 ? <span style={{ color: "var(--ok)" }}>✓</span> : <b style={{ color: "var(--bad)" }}>{l.over_power}</b>}</td>)}</tr>
              <tr><td><Note note="Stored energy stayed between 0 and 40 MWh.">Stored 0–40 MWh</Note></td>
                {e.limits.map((l, i) => <td key={i} className={sc(i)}>{l.soc_outside === 0 ? <span style={{ color: "var(--ok)" }}>✓</span> : <b style={{ color: "var(--bad)" }}>{l.soc_outside}</b>}</td>)}</tr>
              {factory && <tr><td><Note note={`${glNote("CMD")} Half-hours whose metered import went above the month's cap (an economic limit, priced in the network bill, not blocked).`}>Meter ≤ CMD</Note></td>
                {e.limits.map((l, i) => <td key={i} className={sc(i)}>{!l.over_cmd ? <span style={{ color: "var(--ok)" }}>✓</span> : <b style={{ color: "var(--warn)" }}>△ {l.over_cmd}</b>}</td>)}</tr>}
              <tr><td><Note note="Each day's end energy equals start + charged × efficiency − discharged ÷ efficiency; largest error below 1e-12 MWh over all 570 days, checked for both connections.">Energy adds up</Note></td>
                {e.limits.map((_, i) => <td key={i} className={sc(i)} style={{ color: "var(--ok)" }}>✓</td>)}</tr>
              <tr><td><Note note="Replayed with every actual price after 09:00 multiplied by 3: the 09:00 plan did not change (3 days, both dispatchers). Every day on the Plan page repeats this check.">Only 09:00 data</Note></td>
                {e.limits.map((_, i) => <td key={i} className={sc(i)} style={{ color: "var(--ok)" }}>✓</td>)}</tr>
            </tbody>
          </table>
          <div className="chips" style={{ marginTop: 10 }}>
            <Chip note={`Training period: grid services ${aud(e.periods[0].adopted_parts_aud.fcess)} of ${aud(e.periods[0].per_day_aud[e.adopted])} a day${factory ? `, network bill saving ${aud(e.periods[0].adopted_parts_aud.network)}` : ""}. FCESS prices fell from April 2026.`}>
              {factory ? "grid services + bill" : "mostly grid services"}</Chip>
            {factory && e.site
              ? <Chip cls="hyp" note={`${glNote("IRCR")} ${e.site.ircr_season}: import in the 12 peak half-hours cut from ${nf(e.site.ircr_mw_no_battery ?? 0, 2)} MW to 0. ${e.site.load}.`}>IRCR {aud(e.site.ircr_saving_aud_per_year[e.adopted])} a year</Chip>
              : <Chip note="Network charges on the connection are not modelled.">no network charges</Chip>}
          </div>
        </div>
      </div>
    </>
  );
}

function DotPlot({ e, per, setPer }: { e: Ev; per: number; setPer: (i: number) => void }) {
  const [ref, W] = useWidth<HTMLDivElement>(560);
  const L = 214, R = 24, rowH = 64, top = 26;
  const vals = e.periods.flatMap((p) => [p.uplift_vs_plan_only?.lo95, p.uplift_vs_plan_only?.hi95, p.uplift_vs_plan_only?.mean]).filter(isNum);
  const lo = Math.min(-100, ...vals), hi = Math.max(...vals) * 1.08;
  const step = hi - lo > 800 ? 200 : 100;
  const pad = 70;  // room for the range labels outside the whiskers
  const x = (v: number) => L + pad + ((v - lo) / (hi - lo)) * (W - L - R - 2 * pad);
  const H = top + rowH * 3 + 30;
  const ticks: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) ticks.push(t);
  const pl = new Placer({ x0: L + 4, x1: W, y0: top, y1: top + rowH * 3 });
  pl.line(segPts(x(0), top, x(0), top + rowH * 3));
  const rows = e.periods.map((p, i) => {
    const u = p.uplift_vs_plan_only;
    const y = top + rowH * i + rowH / 2;
    if (!u || !isNum(u.mean)) return { u, y, labels: {} as Record<string, { x: number; y: number; a: "start" | "middle" | "end" } | null> };
    const hasR = isNum(u.lo95) && isNum(u.hi95);
    if (hasR) pl.line(segPts(x(u.lo95!), y, x(u.hi95!), y)).line(segPts(x(u.lo95!), y - 6, x(u.lo95!), y + 6)).line(segPts(x(u.hi95!), y - 6, x(u.hi95!), y + 6));
    pl.block({ x0: x(u.mean) - 7, x1: x(u.mean) + 7, y0: y - 7, y1: y + 7 });
    const labels = {
      mean: pl.place(signed(u.mean), "ann", [[x(u.mean), y - 13, "middle"], [x(u.mean), y + 23, "middle"]]),
      lo: hasR ? pl.place(signed(u.lo95), "tick", [[x(u.lo95!) - 6, y + 4, "end"], [x(u.lo95!), y + 20, "middle"]]) : null,
      hi: hasR ? pl.place(signed(u.hi95), "tick", [[x(u.hi95!) + 6, y + 4, "start"], [x(u.hi95!), y + 20, "middle"]]) : null,
      few: !isNum(u.lo95) ? pl.place("too few days", "ann", [[x(u.mean) + 12, y + 4, "start"], [x(u.mean) - 12, y + 4, "end"]]) : null,
    };
    return { u, y, labels };
  });
  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} role="group" aria-label="Extra profit per day over the 09:00 plan alone, mean and 95% range, by period">
        <text className="ttl" x={L} y={12}>Extra profit, A$/day</text>
        {ticks.map((t) => <g key={t}><line className="gl" x1={x(t)} x2={x(t)} y1={top} y2={top + rowH * 3} />
          <text x={x(t)} y={top + rowH * 3 + 16} textAnchor="middle">{t > 0 ? "+" : t < 0 ? "−" : ""}{nf(Math.abs(t))}</text></g>)}
        {e.periods.map((p, i) => {
          const { u, y, labels } = rows[i];
          const zero = u && isNum(u.lo95) && u.lo95 <= 0;
          const col = i === 2 || zero ? "var(--text-4)" : "var(--zen)";
          return (
            <g key={i} style={{ cursor: "pointer" }} onClick={() => setPer(i)} role="button" tabIndex={0} aria-pressed={i === per}
              onKeyDown={(ev) => (ev.key === "Enter" || ev.key === " ") && setPer(i)}>
              <rect x={0} y={y - rowH / 2} width={W} height={rowH} style={{ fill: i === per ? "var(--surface-2)" : "transparent" }} />
              {i === per && <rect x={0} y={y - rowH / 2} width={3} height={rowH} style={{ fill: "var(--text)" }} />}
              <text className="lt" x={14} y={y - 2} style={i === per ? { fontWeight: 600 } : undefined}>{NAME[i]}</text>
              <text x={14} y={y + 13}>{RANGE[i]} · {p.period.days} d</text>
              {u && isNum(u.mean) && (
                <>
                  {isNum(u.lo95) && isNum(u.hi95) && <>
                    <line className="series" x1={x(u.lo95)} x2={x(u.hi95)} y1={y} y2={y} style={{ stroke: col, strokeWidth: 2 }} />
                    <line className="series" x1={x(u.lo95)} x2={x(u.lo95)} y1={y - 6} y2={y + 6} style={{ stroke: col, strokeWidth: 1.5 }} />
                    <line className="series" x1={x(u.hi95)} x2={x(u.hi95)} y1={y - 6} y2={y + 6} style={{ stroke: col, strokeWidth: 1.5 }} />
                  </>}
                  <circle cx={x(u.mean)} cy={y} r={6} style={{ fill: i === 0 ? "var(--surface)" : col, stroke: col, strokeWidth: 2 }} />
                  {labels.lo && <text x={labels.lo.x} y={labels.lo.y} textAnchor={labels.lo.a}>{signed(u.lo95)}</text>}
                  {labels.hi && <text x={labels.hi.x} y={labels.hi.y} textAnchor={labels.hi.a}>{signed(u.hi95)}</text>}
                  {labels.mean && <text className="ann" x={labels.mean.x} y={labels.mean.y} textAnchor="middle" style={{ fill: col, fontWeight: 600 }}>{signed(u.mean)}</text>}
                  {labels.few && <text className="ann" x={labels.few.x} y={labels.few.y} textAnchor={labels.few.a} style={{ fill: "var(--text-3)" }}>too few days</text>}
                </>
              )}
              <title>{`${NAME[i]}: ${u ? signed(u.mean) : "–"} a day; 95% range ${u && isNum(u.lo95) ? `${signed(u.lo95)} to ${signed(u.hi95)}` : "not computable (one month)"}; better on ${u?.days_better ?? "–"} of ${p.period.days} days`}</title>
            </g>
          );
        })}
        <line className="ref" x1={x(0)} x2={x(0)} y1={top} y2={top + rowH * 3} style={{ stroke: "var(--axis)", pointerEvents: "none" }} />
      </svg>
    </div>
  );
}

function Bars({ rows, parts }: { rows: [string, string, number | null | undefined, string][]; parts: { energy: number | null; fcess: number | null; wear: number | null; network?: number | null } }) {
  const [ref, W] = useWidth<HTMLDivElement>(480);
  const L = 214, rh = 28;
  const max = Math.max(1, ...rows.map((r) => r[2] ?? 0));
  const endW = Math.max(...rows.map((r) => tw(aud(r[2]), "annB"))) + 16;
  const w = (v: number) => (Math.max(0, v) / max) * (W - L - endW);
  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${W} ${rows.length * rh + 22}`} role="img" aria-label={rows.map((r) => `${r[1]} ${aud(r[2])}`).join(", ")}>
        <text className="ttl" x={L} y={12}>Profit per day, A$</text>
        {rows.map(([k, name, v, col], i) => {
          const y = 24 + i * rh;
          return (
            <g key={name}>
              <text className="lt" x={14} y={y + 12}><title>{glNote(k)}</title>{name}</text>
              <path d={hbar(L, L + w(v ?? 0), y, 16)} style={{ fill: col === "hatch" ? "url(#bh)" : col, stroke: col === "hatch" ? "var(--hatch-ln)" : "none" }}>
                {k === "DISPATCHER" && <title>{`Energy ${aud(parts.energy)} + grid services ${aud(parts.fcess)} − wear ${aud(parts.wear)}${isNum(parts.network) ? ` + network bill ${aud(parts.network)}` : ""} a day`}</title>}
              </path>
              <text className="ann" x={L + w(v ?? 0) + 8} y={y + 12} style={{ fontWeight: 600 }}>{aud(v)}</text>
            </g>
          );
        })}
        <line className="ax" x1={L} x2={L} y1={20} y2={24 + rows.length * rh - 8} />
        <defs><pattern id="bh" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="5" height="5" style={{ fill: "var(--hatch-bg)" }} /><line x1="0" y1="0" x2="0" y2="5" style={{ stroke: "var(--hatch-ln)" }} strokeWidth="2" /></pattern></defs>
      </svg>
    </div>
  );
}

/** Mean forecast error of the model against "same as yesterday", by measured widths: names left, bars, the gain right. */
function Forecast({ f }: { f: Ev["forecast"][number] }) {
  const [ref, W] = useWidth<HTMLDivElement>(420);
  const rows: [string, number | null, number | null][] = [["Price, all day", f.mae_all_day, f.mae_all_day_yesterday], ["Price, 17:00–21:00", f.mae_17_21, f.mae_17_21_yesterday]];
  const max = Math.max(1, ...rows.flatMap((r) => [r[1] ?? 0, r[2] ?? 0]));
  const gain = (m: number | null, y: number | null) => (isNum(m) && isNum(y) ? `${nf((100 * (y - m)) / y, 1)}% lower error` : "");
  const L = Math.max(...rows.map((r) => tw(r[0], "lt")), tw("error, A$/MWh")) + 20;
  const right = Math.max(...rows.map((r) => tw(gain(r[1], r[2]), "annB"))) + 16;
  const endW = Math.max(...rows.flatMap((r) => [tw(`${isNum(r[2]) ? nf(r[2]) : "–"} · yesterday`), tw(`${isNum(r[1]) ? nf(r[1]) : "–"} · model`)])) + 10;
  const w = (v: number | null) => ((v ?? 0) / max) * Math.max(40, W - L - right - endW);
  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${W} 112`} role="img" aria-label="Average forecast error against yesterday's prices">
        {rows.map(([name, m, y], i) => {
          const y0 = 4 + i * 56;
          return (
            <g key={name}>
              <text className="lt" x={0} y={y0 + 12}>{name}</text>
              <text x={0} y={y0 + 28}><title>{glNote("MAE")}</title>error, A$/MWh</text>
              <path d={hbar(L, L + w(y), y0 + 2, 12)} style={{ fill: "var(--bar)" }} />
              <text x={L + w(y) + 6} y={y0 + 12}>{isNum(y) ? nf(y) : "–"} · yesterday</text>
              <path d={hbar(L, L + w(m), y0 + 18, 12)} style={{ fill: "var(--zen)" }} />
              <text x={L + w(m) + 6} y={y0 + 28}>{isNum(m) ? nf(m) : "–"} · model</text>
              {gain(m, y) && <text className="ann" x={W} y={y0 + 20} textAnchor="end" style={{ fill: "var(--zen)", fontWeight: 600 }}>{gain(m, y)}</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
