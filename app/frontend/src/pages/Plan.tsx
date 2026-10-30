import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Check, type Outcome, type Plan as PlanT } from "../api";
import { around, bandPath, DayAxis, geo, Hatch, linePath, linePts, N, Placer, segPts, stepPath, stepPts, tw, X0, YGrid } from "../components/chart";
import { ChartTip, Chip, DayPicker, Failed, Gl, Loading, Note, PageHead, Seg, useWidth, type TipData } from "../components/ui";
import { Point, WhyHead } from "../components/why";
import { addDays, aud, dayLabel, hhmm, isNum, nf, PERIOD_CHIP, periodOf } from "../lib/format";
import { glNote } from "../lib/gloss";
import { CONN, DISP, isDefault, useApp, useLive, whoItems } from "../state";

const FIRST = 2;
type Seg_ = { m: string; a: number; b: number; mw: number };

function segments(p: PlanT): Seg_[] {
  const act = p.plan.action, mw = p.plan.battery_mw;
  const out: Seg_[] = [];
  let k = FIRST;
  while (k < N) {
    let e = k;
    while (e + 1 < N && act[e + 1] === act[k]) e++;
    let m = 0;
    for (let j = k; j <= e; j++) m = Math.max(m, Math.abs(mw[j] ?? 0));
    out.push({ m: act[k], a: k, b: e + 1, mw: m });
    k = e + 1;
  }
  return out;
}

const VERB: Record<string, string> = { discharge: "Discharge", charge: "Charge", shave: "Shave" };
/** Fill and text colour of each action, in the chart's plan lane and the Why timeline. */
const ACT: Record<string, [string, string]> = { discharge: ["var(--dis)", "var(--on-dis)"], shave: ["var(--shave)", "var(--shave-text)"],
  charge: ["var(--chg)", "var(--text)"], hold: ["var(--hold)", "var(--text-3)"] };
const segName = (s: Seg_) => (VERB[s.m] ? `${VERB[s.m]} ${nf(s.mw, 1)} MW` : "Hold");

function mainAction(p: PlanT, segs: Seg_[]) {
  const dis = segs.filter((s) => s.m === "discharge" || s.m === "shave");
  if (!dis.length) return "Hold · grid services standby";
  const energy = (s: Seg_) => p.plan.battery_mw.slice(s.a, s.b).reduce<number>((t, v) => t + (v ?? 0), 0);
  const best = dis.reduce((a, b) => (energy(b) > energy(a) ? b : a));
  return `${VERB[best.m]} ${nf(best.mw, 1)} MW · ${hhmm(best.a)}–${hhmm(best.b)}`;
}

/** The extra-sale offer of each half-hour: v1 a floor and spare MW; e5 its layered offers above the plan, the
 *  lowest price and the MW offered below the market cap ("MAX" layers never sell). */
function extraOffer(p: PlanT): { floor: number | null; lowest: (number | null)[]; mw: number[] } {
  const off = p.offers_0900;
  const lowest: (number | null)[] = Array(N).fill(null), mw: number[] = Array(N).fill(0);
  if (!off) return { floor: null, lowest, mw };
  if (off.rule === "value") {
    for (let k = FIRST; k < N; k++) { lowest[k] = off.floor_aud_per_mwh; mw[k] = off.spare_mw[k] ?? 0; }
    return { floor: off.floor_aud_per_mwh, lowest, mw };
  }
  off.by_halfhour.forEach((rows, k) => {
    const priced = (rows ?? []).filter((x) => x.price_aud_per_mwh !== "MAX" && x.mw > 0.01);
    if (k >= FIRST && priced.length) {
      lowest[k] = Math.min(...priced.map((x) => x.price_aud_per_mwh as number));
      mw[k] = priced.reduce((t, x) => t + x.mw, 0);
    }
  });
  const got = lowest.filter(isNum);
  return { floor: got.length ? Math.min(...got) : null, lowest, mw };
}

const standby = (p: PlanT, k: number) => {
  const f = p.plan.fcess_mw;
  return (f.reg_raise[k] ?? 0) + (f.cont_raise[k] ?? 0) + (f.reg_lower[k] ?? 0) + (f.cont_lower[k] ?? 0);
};

const SHORT: Record<string, string> = {
  power: "Power ≤ 10 MW", soc_max: "Stored 0–40 MWh", soc_reserve: "Mandatory floor", fcess_cover: "Grid services covered",
  cycles: "≤ 1 cycle a day", connection_export: "Connection export", connection_import: "Connection import",
  site_reserve: "Factory reserve", cmd: "Import ≤ CMD", ircr_cap: "Import ≤ IRCR cap",
  info_timing: "Only 09:00 data", offer_format: "Offer format", contractual: "Contracts", market_eligibility: "Registration",
};

export default function Plan() {
  const { day, setDay, settings, revealed, setRevealed, go } = useApp();
  const live = useLive(day);
  // live data: a day's plan changes when the data are rebuilt, today's outcome with every new 5-minute price
  const plan = useQuery({ queryKey: ["plan", day, settings, live.after ? live.builtAt : null], queryFn: () => api.plan(day, settings) });
  const out = useQuery({ queryKey: ["outcome", day, settings.connection, settings.dispatcher, settings.fcess, settings.startMwh,
    live.after ? live.builtAt : null, live.isToday ? live.asOf : null],
    enabled: revealed && !!plan.data,
    queryFn: () => api.outcome(day, settings) });
  const [sel, setSel] = useState<number>(20);

  useEffect(() => { setSel(20); }, [day]);
  const p = plan.data;
  const o = revealed ? out.data : undefined;

  return (
    <section aria-label="Today's plan">
      <PageHead title="Today's plan"
        chips={<>
          <Chip cls="step">1 · Decide</Chip>
          <Chip cls={live.isToday ? "live" : ""} note={glNote(periodOf(day))}>{live.isToday ? `live · to ${live.asOf}` : PERIOD_CHIP[periodOf(day)]}</Chip>
          {!isDefault(settings) && <Chip cls="unv" note={glNote("UNVALIDATED")}>changed settings</Chip>}
          {p?.site?.ircr_flag_0900 && <Chip cls="hyp" note={glNote("IRCRFLAG")}>IRCR peak flag</Chip>}
        </>}
        ctx={<><Gl k="TRADINGDAY">trading day</Gl> 08:00–08:00 AWST · <Gl k="DECISION">plan 09:00</Gl> · <Gl k={settings.connection === "factory" ? "FACTORY" : "OWNCONN"}>{CONN[settings.connection]}</Gl>
          {" · "}<DispatcherName p={p} short={settings.dispatcher} /></>}
        who={whoItems(settings.connection)}
        right={<>
          <DayPicker day={day} setDay={setDay} />
          <Seg label="Show" value={revealed ? "out" : "plan"} onChange={(v) => setRevealed(v === "out")}
            options={[["plan", "Plan only"], ["out", "Plan + what happened"]]} />
        </>} />

      {plan.isLoading && <Loading what="the 09:00 plan" />}
      {plan.error && <Failed error={plan.error} />}
      {p && <PlanBody p={p} o={o} outLoading={revealed && out.isLoading} sel={sel} setSel={setSel} reveal={() => setRevealed(true)} go={go} />}
    </section>
  );
}

/** "dispatcher v2", and on a connection where e5 did not pass the selection rule, "· not passed here". */
export function DispatcherName({ p, short, passed }: { p?: PlanT; short: "e5" | "v1"; passed?: boolean }) {
  const ok = passed ?? p?.dispatcher.e5_passed;
  const v = p?.dispatcher.verdict;
  const note = `${glNote(short === "e5" ? "E5" : "V1")}${v ? ` Training period, this connection: v2 − v1 ${aud(v.e5_minus_v1_aud_per_day)}/day, 95% range ${aud(v.lo95)} to ${aud(v.hi95)}.` : ""}`;
  return <Note note={`${glNote("DISPATCHER")} ${note}`}>dispatcher {DISP[short]}{short === "e5" && ok === false ? " · not passed here" : ""}</Note>;
}

function PlanBody({ p, o, outLoading, sel, setSel, reveal, go }: { p: PlanT; o?: Outcome; outLoading: boolean; sel: number;
  setSel: (k: number) => void; reveal: () => void; go: (pg: "evidence" | "settings") => void }) {
  const segs = useMemo(() => segments(p), [p]);
  const [hover, setHover] = useState<number | null>(null);
  const [why, setWhy] = useState(false);
  const blocked = p.state === "BLOCKED";
  const failed = p.checks.filter((c) => c.status === "fail");
  const passed = p.checks.filter((c) => c.status === "pass").length;
  const notChecked = p.checks.filter((c) => c.status === "not_checked").length;
  const v = p.plan.value_at_forecast_aud;
  const next = addDays(p.day, 1);

  return (
    <>
      {blocked && (
        <div className="blocked" role="alert">
          <b>Blocked</b> {failed.map((c) => SHORT[c.id] ?? c.label).join(", ")} over the limit in {p.blocked_halfhours.length} half-hours
          <span className="sp" />
          <button className="btn" onClick={() => setSel(p.blocked_halfhours[0])}>Inspect</button>
          <button className="btn" onClick={() => go("settings")}>Settings</button>
        </div>
      )}
      <div className="dec">
        <div className="dec-l">
          <div className="statusline">
            {blocked ? <span className="pill bad">! Blocked</span> : <span className="pill ok">✓ Plan executable</span>}
            <span className="sub">{passed} of {p.checks.length} checks pass{notChecked ? ` · ${notChecked} not checked` : ""}</span>
            {p.explain && <button className="lnk" aria-expanded={why} aria-controls="why-plan" onClick={() => setWhy(!why)}>Why this plan</button>}
          </div>
          <div className="title">{blocked ? `Resolve ${SHORT[failed[0].id]?.toLowerCase() ?? "the limit"} first` : mainAction(p, segs)}</div>
        </div>
        <div className="dec-r">
          <div className="lbl">Expected profit today</div>
          <div className={"big" + (blocked ? " dim" : "")} data-note={"Forecast at 09:00, not settlement: energy + grid services − wear + the value of energy left at 08:00 tomorrow."}>{aud(v.net)}</div>
          <div className="chips dec-chips" style={{ justifyContent: "flex-end" }}>
            {blocked ? <Chip cls="unv">simulation only</Chip> : <Chip note={glNote("DECISION")}>forecast at 09:00</Chip>}
            {isNum(v.stored_end) && v.stored_end > 1 && <Chip note={glNote("STORED")}>incl. {aud(v.stored_end)} stored</Chip>}
          </div>
        </div>
      </div>
      {why && <WhyPlan p={p} sel={sel} setSel={setSel} close={() => setWhy(false)} />}

      <div className="fig">
        <DayChart p={p} o={o} segs={segs} sel={sel} setSel={setSel} hover={hover} setHover={setHover} day={dayLabel(p.day, false)} next={dayLabel(next, false)} />
        {p.site && <MeterChart p={p} o={o} sel={sel} setSel={setSel} hover={hover} setHover={setHover} day={dayLabel(p.day, false)} next={dayLabel(next, false)} />}
      </div>

      <Detail p={p} o={o} k={sel} segs={segs} outLoading={outLoading} reveal={reveal} />

      <div className="two">
        <div className="blk rule">
          <div className="blk-h"><h2>Value</h2><span className="sub">trading day, A$</span><span className="sp" />
            <button className="lnk" onClick={() => go("evidence")}>vs simpler ways →</button></div>
          <div className="wf-h">Plan at 09:00</div>
          <Waterfall parts={[["e", "Energy", (v.energy ?? 0) + (v.regulation_energy ?? 0)], ["f", "Grid services", v.fcess ?? 0], ["s", "Stored", v.stored_end ?? 0]]}
            wear={v.wear ?? 0} net={v.net ?? 0} dim={blocked} />
          {o ? (
            <>
              <div className="wf-h">{o.live ? `What happened · so far, to ${o.live.through}` : "What happened · replayed"}</div>
              <Waterfall parts={[["e", "Energy", o.money_aud.energy ?? 0], ["f", "Grid services", o.money_aud.fcess ?? 0],
                ...(isNum(o.money_aud.network) ? [["b", "Network bill", o.money_aud.network] as [string, string, number]] : [])]}
                wear={o.money_aud.wear ?? 0} net={o.money_aud.net ?? 0} />
              <div className="chips" style={{ marginTop: 4 }}>
                {(o.baselines ?? []).map((b) => (
                  <Chip key={b.name} note={glNote(b.name === "plan only" ? "PLANONLY" : b.name === "prices known" ? "PERFECT" : b.name === "e5" ? "E5" : "V1")}>
                    {b.name in DISP ? DISP[b.name as "e5" | "v1"] : b.name} {aud(b.net_aud)}
                  </Chip>
                ))}
              </div>
            </>
          ) : (
            <div className="sub" style={{ margin: "8px 0" }}>Outcome hidden · <button className="lnk" onClick={reveal}>Plan + what happened</button></div>
          )}
          <details className="more">
            <summary>Breakdown</summary>
            <table className="t">
              <thead><tr><th>A$</th><th className="r">Plan 09:00</th><th className="r">{o?.live ? "So far" : "Replayed"}</th></tr></thead>
              <tbody>
                <tr><td>Energy</td><td className="r">{aud(v.energy)}</td><td className="r">{o ? aud(o.money_aud.energy) : "hidden"}</td></tr>
                <tr><td>Regulation energy</td><td className="r">{aud(v.regulation_energy)}</td><td className="r">{o ? "in energy" : "hidden"}</td></tr>
                <tr><td>Grid services (FCESS)</td><td className="r">{aud(v.fcess)}</td><td className="r">{o ? aud(o.money_aud.fcess) : "hidden"}</td></tr>
                <tr><td>Wear</td><td className="r">{aud(-(v.wear ?? 0))}</td><td className="r">{o ? aud(-(o.money_aud.wear ?? 0)) : "hidden"}</td></tr>
                {p.site && <tr><td><Gl k="NETBILL">Network bill saving</Gl></td><td className="r">not priced</td>
                  <td className="r">{o ? (isNum(o.money_aud.network) ? aud(o.money_aud.network) : "month only") : "hidden"}</td></tr>}
                <tr><td>Stored at 08:00 tomorrow</td><td className="r">{aud(v.stored_end)}</td><td className="r">not cash</td></tr>
                <tr><td><b>Net</b></td><td className="r"><b>{aud(v.net)}</b></td><td className="r"><b>{o ? aud(o.money_aud.net) : "hidden"}</b></td></tr>
              </tbody>
            </table>
            <p className="sub">Plan: the dispatcher's own forecast at 09:00, from 09:00 on, with the value of energy left at 08:00 tomorrow.
              {o?.live ? `So far: the half-hours to ${o.live.through} at AEMO's 5-minute prices as published (final once AEMO's files hold the day, about 08:20 tomorrow)` : "Replayed: the whole day settled at actual prices"}{p.site
                ? "; plus the month's network bill saving per day (the bill is set by the month's highest import). The capacity charge (IRCR) is counted per year on Evidence. The factory load is synthetic [assumed]."
                : "; no network charges on this connection [assumed]."} Start: {nf(p.start_state.stored_mwh ?? 0, 1)} MWh at 08:00.</p>
          </details>
        </div>
        <div className="blk rule">
          <div className="blk-h"><h2>Checks</h2><span className="sub">hover for each</span></div>
          <div className="checks">
            {p.checks.map((c) => <CheckCell key={c.id} c={c} />)}
          </div>
        </div>
      </div>
    </>
  );
}

/** The plan's reasons as a timeline: the yardsticks most choices are weighed against, then one row per stretch with one
 *  reason (the action pill on the first row of each run, coloured as in the chart's plan lane); a row selects its first
 *  half-hour, and its time carries the whole sentence on hover. */
function WhyPlan({ p, sel, setSel, close }: { p: PlanT; sel: number; setSel: (k: number) => void; close: () => void }) {
  const e = p.explain;
  return (
    <section className="why-panel" id="why-plan" aria-label="Why this plan">
      <WhyHead title="Why this plan" sub={`${e.start} at 09:00 → ${e.end} at 08:00 tomorrow`} close={close}
        extra={e.flagged ? <Chip cls="hyp" note={glNote("IRCRFLAG")}>capacity peak day</Chip> : undefined} />
      <div className="ys" aria-label="What each choice is weighed against">
        <span className="eyebrow">Weighed against</span>
        {e.yardsticks.map((y) => <div key={y.l} className="y" tabIndex={0} data-note={y.note}><b>{y.v}</b><span>{y.l}</span></div>)}
      </div>
      <ol className="wl">
        <li className="wl-h" aria-hidden="true"><span>Time</span><span>Plan</span><span>Forecast</span><span>Reason</span></li>
        {e.segments.flatMap((s) => s.stretches.map((st, i) => {
          const [bg, fg] = ACT[s.action] ?? ACT.hold;
          return (
            <li key={st.from} className={"wr" + (sel >= st.from && sel < st.to ? " on" : "") + (i ? " cont" : "")}
              style={{ "--c": bg } as CSSProperties} onClick={() => setSel(st.from)}>
              <button className="wr-t" data-note={st.text}>{hhmm(st.from)}–{hhmm(st.to)}</button>
              <span className="wr-a"><span className="act-pill" style={{ background: bg, color: fg }}>{segName({ m: s.action, a: st.from, b: st.to, mw: st.mw ?? 0 })}</span></span>
              <span className="wr-p">{st.price}</span>
              <div className="pts">{st.points.length ? st.points.map((x, j) => <Point key={j} p={x} />) : <div className="pt"><span className="pt-w">{st.text}</span></div>}</div>
            </li>
          );
        }))}
      </ol>
      <p className="sub foot">{e.basis}</p>
    </section>
  );
}

function CheckCell({ c }: { c: Check }) {
  const icon = c.status === "pass" ? "✓" : c.status === "fail" ? "!" : c.status === "warn" ? "△" : "○";
  const note = `${c.label}. ${c.status === "not_checked" ? "Not checked: " : c.status === "warn" ? "Economic limit, priced not blocked: " : ""}${c.detail}${c.worst ? ` (worst: ${c.worst})` : ""}`;
  return <div className={"ck " + c.status} tabIndex={0} data-note={note}><i>{icon}</i><span>{SHORT[c.id] ?? c.label}</span></div>;
}

/* ------------------------------------------------------------------------------------------------ money */
const WF: Record<string, [string, string]> = { e: ["var(--dis)", "var(--on-dis)"], f: ["var(--fcess)", "var(--on-fcess)"],
  s: ["var(--surface-3)", "var(--text-2)"], b: ["var(--meter)", "var(--on-meter)"] };

/** Gross parts side by side (2 px gaps), wear hanging under the end of the gross, net below. A part whose label does not
 *  fit inside its bar is named in a line under the bars. */
function Waterfall({ parts, wear, net, dim }: { parts: [string, string, number][]; wear: number; net: number; dim?: boolean }) {
  const [ref, W] = useWidth<HTMLDivElement>(240);
  const shown = parts.filter((x) => x[2] > 0.5);
  const gross = shown.reduce((t, x) => t + x[2], 0);
  const scale = Math.max(gross, Math.abs(net), 1);
  const px = (v: number) => (Math.max(0, v) / scale) * W;
  let x = 0;
  const bars = shown.map(([c, l, v]) => {
    const b = { c, l, v, x, w: Math.max(1, px(v) - 2), text: `${l} ${nf(v)}` };
    x += px(v);
    return b;
  });
  const hidden = bars.filter((b) => tw(b.text, "annB") + 12 > b.w);
  const wearX = px(gross - wear), wearW = Math.max(2, px(wear));
  const netW = Math.max(2, px(Math.abs(net))), netText = `Net ${aud(net)}`, netIn = tw(netText, "annB") + 16 < netW;
  const showWear = wear > 0.5;
  const yW = 28, yN = showWear ? 52 : 28, keyY = yN + 40;
  const H = hidden.length ? keyY + 4 : yN + 22;
  let kx = 0;
  return (
    <div className="wf chart" ref={ref} style={dim ? { opacity: 0.45 } : undefined}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${shown.map((b) => `${b[1]} ${aud(b[2])}`).join(", ")}${showWear ? `, wear −${aud(wear)}` : ""}, net ${aud(net)}`}>
        {bars.map((b) => (
          <g key={b.l} tabIndex={0} data-note={`${b.l}: ${aud(b.v)}`}>
            <rect x={b.x} y={0} width={b.w} height={22} rx={3} style={{ fill: WF[b.c][0] }} />
            {!hidden.includes(b) && <text className="ann" x={b.x + 7} y={15} style={{ fill: WF[b.c][1], fontWeight: 600 }}>{b.text}</text>}
          </g>
        ))}
        {showWear && <g tabIndex={0} data-note={`Wear: −${aud(wear)}`}>
          <rect x={wearX} y={yW} width={wearW} height={16} rx={2} style={{ fill: "var(--bad)", opacity: 0.85 }} />
          <text className="ann" x={wearX - 6} y={yW + 12} textAnchor="end" style={{ fill: "var(--bad)", fontWeight: 600 }}>Wear −{nf(wear)}</text>
        </g>}
        <rect x={0} y={yN} width={netW} height={22} rx={3} style={{ fill: net < 0 ? "var(--bad)" : "var(--zen)" }} />
        <text className="ann" x={netIn ? 8 : netW + 8} y={yN + 15} style={{ fill: netIn ? "var(--on-zen)" : "var(--text)", fontWeight: 600 }}>{netText}</text>
        {hidden.map((b) => {
          const at = kx;
          kx += 14 + tw(b.text, "ann") + 16;
          return <g key={"k" + b.l}><rect x={at} y={keyY - 9} width={10} height={10} rx={2} style={{ fill: WF[b.c][0] }} /><text className="ann" x={at + 14} y={keyY}>{b.text}</text></g>;
        })}
      </svg>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ the day chart */
type Sync = { sel: number; setSel: (k: number) => void; hover: number | null; setHover: (k: number | null) => void; day: string; next: string };

function DayChart({ p, o, segs, sel, setSel, hover, setHover, day, next }: { p: PlanT; o?: Outcome; segs: Seg_[] } & Sync) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const g = geo(W);
  const [tip, setTip] = useState<TipData>(null);
  const P = p.asset.power_mw, E = p.asset.energy_mwh;
  const xo = useMemo(() => extraOffer(p), [p]);
  const ladder = p.offers_0900?.rule === "ladder";
  const floor = xo.floor;
  const pr = p.price;

  // bands, top to bottom: 09:00 marker, three lanes, price title, price plot, time axis
  const LN = [{ y: 20, h: 22 }, { y: 48, h: 20 }, { y: 74, h: 20 }];
  const laneTop = 20, laneBot = 94;
  const top = 130, bot = 264, H = bot + 22;
  const hi = Math.max(250, floor ?? 0, ...pr.forecast_p50.filter(isNum), ...(pr.forecast_p90 ?? []).filter(isNum),
    ...(o ? o.price_actual.filter(isNum).map((x) => Math.min(x, 500)) : []));
  const lo = Math.min(0, ...pr.forecast_p50.filter(isNum), ...(o ? o.price_actual.filter(isNum) : []).map((x) => Math.max(x, -100)));
  const yP = (v: number) => bot - ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * (bot - top);
  const step = hi > 600 ? 250 : hi > 300 ? 100 : 50;
  const ticks: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) ticks.push(t);

  const sTop = 26, sBot = 152, SH = sBot + 34;
  const yS = (v: number) => sBot - (v / E) * (sBot - sTop);
  const soc = p.plan.soc_mwh, floorS = p.plan.reserve_floor_mwh;

  const p50 = pr.forecast_p50;
  let kPeak = FIRST, kLow = FIRST;
  for (let k = FIRST; k < N; k++) { if ((p50[k] ?? -1e9) > (p50[kPeak] ?? -1e9)) kPeak = k; if ((p50[k] ?? 1e9) < (p50[kLow] ?? 1e9)) kLow = k; }
  let kMin = FIRST + 1;
  for (let b = FIRST + 1; b <= N; b++) if ((soc[b] ?? 1e9) < (soc[kMin] ?? 1e9)) kMin = b;

  const sold = o ? o.offer_cleared_mw.map((x) => (x ?? 0) > 0.01) : [];
  const soldN = sold.filter(Boolean).length;
  const soldAud = o ? o.offer_sold_aud.reduce<number>((t, x) => t + (x ?? 0), 0) : 0;
  const fcessAud = p.plan.value_at_forecast_aud.fcess ?? 0;
  const spare = Math.max(0, ...xo.mw);
  const meter = p.site?.meter_mw;

  /* price labels: reference line first, then peak, low and the largest off-scale actual */
  const floorPts = floor == null ? [] : ladder ? stepPts(g, xo.lowest, yP, FIRST) : segPts(g.X(FIRST), yP(floor), g.X(N), yP(floor));
  const pl = new Placer({ x0: g.X(FIRST) + 3, x1: W, y0: top - 6, y1: bot - 2 });
  pl.line(stepPts(g, p50, yP, FIRST)).line(stepPts(g, pr.actual_before_0900, yP, 0, FIRST)).line(floorPts);
  if (o) pl.line(stepPts(g, o.price_actual, yP));
  const kFloor = floor == null ? -1 : ladder ? xo.lowest.findIndex((v) => v === floor) : N - 1;
  const floorTxt = floor != null ? `floor A$${nf(floor)}` : "";
  const floorAt = floor == null ? null : pl.place(floorTxt, "ann", ladder
    ? around(g.X(kFloor) + g.IW / 2, yP(floor), "ann", true)
    : [[g.X(N) - 4, yP(floor) - 5, "end"], [g.X(N) - 4, yP(floor) + 15, "end"], [g.X(FIRST) + 6, yP(floor) - 5, "start"]]);
  const peakTxt = isNum(p50[kPeak]) ? `peak A$${nf(p50[kPeak]!)}` : "";
  const peakAt = peakTxt ? pl.place(peakTxt, "ann", around(g.X(kPeak) + g.IW / 2, yP(p50[kPeak]!))) : null;
  const lowTxt = isNum(p50[kLow]) && kLow !== kPeak ? `low A$${nf(p50[kLow]!)}` : "";
  const lowAt = lowTxt ? pl.place(lowTxt, "ann", around(g.X(kLow) + g.IW / 2, yP(p50[kLow]!), "ann", true)) : null;
  const over = o ? o.price_actual.map((v, k) => (isNum(v) && v > hi ? k : -1)).filter((k) => k >= 0) : [];
  const kSpike = over.length ? over.reduce((a, b) => ((o!.price_actual[b] ?? 0) > (o!.price_actual[a] ?? 0) ? b : a)) : -1;
  const spikeTxt = kSpike >= 0 ? `A$${nf(o!.price_actual[kSpike]!)}` : "";
  const spikeAt = spikeTxt ? pl.place(spikeTxt, "ann", [[g.X(kSpike) + g.IW / 2 + 5, top + 6, "start"], [g.X(kSpike) + g.IW / 2 - 5, top + 6, "end"]]) : null;

  /* stored-energy labels: limits first, then the end, then the lowest point unless the line just stays there */
  const lastFloor = [...floorS].reverse().find(isNum) ?? 0;
  const ps = new Placer({ x0: g.X(FIRST) + 3, x1: W, y0: 0, y1: sBot - 2 });
  ps.line(linePts(g, soc, yS, 0, FIRST)).line(linePts(g, soc, yS, FIRST)).line(stepPts(g, floorS, yS, FIRST)).line(segPts(X0, yS(E), g.X(N), yS(E)));
  if (o) ps.line(linePts(g, o.soc_mwh, yS));
  const fullAt = ps.place(`Full · ${nf(E)} MWh`, "tick", [[g.X(N) - 2, yS(E) - 5, "end"]]);
  const minAt = ps.place("Minimum", "tick", [[g.X(N) - 2, yS(lastFloor) - 5, "end"], [g.X(N) - 2, yS(lastFloor) + 13, "end"]]);
  const endTxt = isNum(soc[N]) ? `${nf(soc[N]!, 1)} MWh` : "";
  const endAt = endTxt ? ps.place(endTxt, "ann", [[g.X(N) - 2, yS(soc[N]!) - 6, "end"], [g.X(N) - 2, yS(soc[N]!) + 15, "end"]]) : null;
  const flatAfter = isNum(soc[kMin]) && soc.slice(kMin).every((v) => !isNum(v) || Math.abs(v - soc[kMin]!) < 0.05);
  const minTxt = isNum(soc[kMin]) && !flatAfter ? `${nf(soc[kMin]!, 1)} MWh` : "";
  const minLblAt = minTxt ? ps.place(minTxt, "ann", around(g.X(kMin), yS(soc[kMin]!), "ann", true)) : null;

  const tipFor = (k: number, e: React.MouseEvent) => {
    const mw = p.plan.battery_mw[k] ?? 0;
    const rows: [string, string][] = [
      ["Power", Math.abs(mw) < 0.01 ? "hold" : `${mw > 0 ? "out" : "in"} ${nf(Math.abs(mw), 1)} MW`],
      meter ? ["Import", isNum(meter[k]) ? `${nf(meter[k]!, 1)} MW` : "–"] : ["Offer", k >= FIRST && isNum(xo.lowest[k]) ? `≥ A$${nf(xo.lowest[k]!)}` : "–"],
      ["Forecast", isNum(p50[k]) ? `A$${nf(p50[k]!)}` : "–"],
    ];
    if (o) rows.push(["Actual", isNum(o.price_actual[k]) ? `A$${nf(o.price_actual[k]!)}` : "–"]);
    rows.push(["Stored", isNum(soc[k + 1]) ? `${nf(soc[k + 1]!, 1)} MWh` : "–"]);
    setTip({ x: e.clientX, y: e.clientY, title: `${hhmm(k)}–${hhmm(k + 1)}`, rows });
  };

  const hits = (y0: number, y1: number) => Array.from({ length: N }, (_, k) => (
    <rect key={k} className="hit" x={g.X(k)} y={y0} width={g.IW} height={y1 - y0}
      onMouseMove={(e) => { setHover(k); tipFor(k, e); }} onMouseLeave={() => { setHover(null); setTip(null); }}
      onClick={() => setSel(k)} />
  ));
  const cross = (y0: number, y1: number) => hover != null && (
    <line x1={g.X(hover) + g.IW / 2} x2={g.X(hover) + g.IW / 2} y1={y0} y2={y1} style={{ stroke: "var(--text-4)", pointerEvents: "none" }} />);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowRight") { setSel(Math.min(N - 1, sel + 1)); e.preventDefault(); }
    if (e.key === "ArrowLeft") { setSel(Math.max(0, sel - 1)); e.preventDefault(); }
  };

  const colours = ACT;
  const sellTxt = `${floor ? `price ≥ A$${nf(floor)}: +up to ${nf(spare, 0)} MW` : "no extra offer"}${o ? ` · sold ${soldN} · ${aud(soldAud)}` : ""}`;
  const earnTxt = fcessAud > 0.5 ? `earns ${aud(fcessAud)}` : "";
  const tag = (txt: string, lane: { y: number; h: number }, fill: string) => (
    <g>
      <rect className="lane-tag" x={g.X(FIRST) + 4} y={lane.y + 3} width={tw(txt, "ann") + 12} height={lane.h - 6} rx={3} />
      <text className="ann" x={g.X(FIRST) + 10} y={lane.y + lane.h / 2 + 4} style={{ fill }}>{txt}</text>
    </g>
  );
  const laneName = (txt: string, lane: { y: number; h: number }, note: string) => (
    <text className="lt" x={X0 - 10} y={lane.y + lane.h / 2 + 4} textAnchor="end"><title>{note}</title>{txt}</text>);

  return (
    <div className="blk chart" ref={ref} tabIndex={0} onKeyDown={onKey} aria-label="Trading day chart; arrow keys move the selected half-hour">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Plan for the trading day: ${segs.map(segName).join(", ")}. Price forecast and actions by half-hour.`}>
        <defs><Hatch id="pre" /></defs>
        <rect x={g.X(sel)} y={laneTop} width={g.IW} height={laneBot - laneTop} className="selc" />
        <rect x={g.X(sel)} y={top} width={g.IW} height={bot - top} className="selc" />
        <rect x={X0} y={laneTop} width={g.X(FIRST) - X0} height={laneBot - laneTop} fill="url(#pre)" />
        <rect x={X0} y={top} width={g.X(FIRST) - X0} height={bot - top} fill="url(#pre)" />
        <line className="ref" x1={g.X(FIRST)} x2={g.X(FIRST)} y1={14} y2={bot} style={{ stroke: "var(--mark)" }} />
        <text className="mk9" x={g.X(FIRST)} y={10} textAnchor="middle">PLAN MADE 09:00</text>

        {/* plan lane */}
        {laneName("Plan", LN[0], glNote("DISPATCHER"))}
        {segs.map((s) => {
          const [bg, fg] = colours[s.m] ?? colours.hold;
          const w = g.X(s.b) - g.X(s.a);
          const label = segName(s), short = s.m === "hold" ? "" : nf(s.mw, 1);
          const txt = tw(label, "act") + 12 < w ? label : short && tw(short, "act") + 8 < w ? short : "";
          return (
            <g key={s.a}>
              <rect x={g.X(s.a) + 1} y={LN[0].y} width={w - 2} height={LN[0].h} rx={3} style={{ fill: bg }} />
              {txt && <text className="act" x={g.X(s.a) + w / 2} y={LN[0].y + 15} textAnchor="middle" style={{ fill: fg }}>{txt}</text>}
            </g>
          );
        })}
        {p.blocked_halfhours.map((k) => <rect key={"b" + k} className="bad-box" x={g.X(k)} y={laneTop - 1} width={g.IW} height={bot - laneTop + 1} />)}

        {/* sell extra lane */}
        {laneName("Sell extra if", LN[1], glNote(ladder ? "EXTRAE5" : "EXTRA"))}
        <rect x={g.X(FIRST) + 1} y={LN[1].y} width={g.X(N) - g.X(FIRST) - 2} height={LN[1].h} rx={3} style={{ fill: "var(--cond-soft)", fillOpacity: 0.5, stroke: "var(--cond)", strokeDasharray: "3 2" }} />
        {sold.map((s, k) => s && <rect key={"s" + k} x={g.X(k) + 0.5} y={LN[1].y + 2} width={g.IW - 1} height={LN[1].h - 4} rx={1} style={{ fill: "var(--cond)" }} />)}
        {tag(sellTxt, LN[1], "var(--cond-text)")}

        {/* grid services lane */}
        {laneName("Grid services", LN[2], glNote("FCESS"))}
        {Array.from({ length: N - FIRST }, (_, j) => {
          const k = j + FIRST, s = standby(p, k);
          return s > 0.01 ? <rect key={"f" + k} x={g.X(k)} y={LN[2].y} width={g.IW} height={LN[2].h} shapeRendering="crispEdges"
            style={{ fill: "color-mix(in srgb, var(--fcess) " + Math.round(35 + 65 * Math.min(1, s / P)) + "%, var(--surface))" }} /> : null;
        })}
        {earnTxt && tag(earnTxt, LN[2], "var(--text-2)")}

        {/* price panel */}
        <text className="ttl" x={0} y={top - 12}>Price, A$/MWh</text>
        <YGrid g={g} ticks={ticks} y={yP} />
        {pr.forecast_p10 && pr.forecast_p90 && <path d={bandPath(g, pr.forecast_p10, pr.forecast_p90, yP, FIRST)} style={{ fill: "var(--range)", opacity: "var(--range-op)" }}><title>{glNote("RANGE")}</title></path>}
        <path className="series" d={stepPath(g, p50, yP, FIRST)} style={{ fill: "none", stroke: "var(--price)", strokeWidth: 1.6, strokeDasharray: "5 3" }} />
        <path className="series" d={stepPath(g, pr.actual_before_0900, yP, 0, FIRST)} style={{ fill: "none", stroke: "var(--actual)", strokeWidth: 1.8 }} />
        {floor != null && (ladder
          ? <path className="ref" d={stepPath(g, xo.lowest, yP, FIRST)} style={{ fill: "none", stroke: "var(--cond)", strokeWidth: 1.4, strokeDasharray: "6 3" }}><title>{glNote("EXTRAE5")}</title></path>
          : <line className="ref" x1={g.X(FIRST)} x2={g.X(N)} y1={yP(floor)} y2={yP(floor)} style={{ stroke: "var(--cond)", strokeWidth: 1.4, strokeDasharray: "6 3" }} />)}
        {o && <path className="series" d={stepPath(g, o.price_actual, yP)} style={{ fill: "none", stroke: "var(--actual)", strokeWidth: 1.8 }} />}
        {over.map((k) => <line key={"o" + k} x1={g.X(k) + g.IW / 2} x2={g.X(k) + g.IW / 2} y1={top - 2} y2={top + 8} style={{ stroke: "var(--actual)", strokeWidth: 2 }} />)}
        {o && o.offer_price_live.map((v, k) => isNum(v) && v <= hi ? <circle key={"lp" + k} cx={g.X(k) + g.IW / 2} cy={yP(v)} r={2.6} style={{ fill: "var(--cond)" }} /> : null)}
        {floorAt && <text className="ann halo" x={floorAt.x} y={floorAt.y} textAnchor={floorAt.a} style={{ fill: "var(--cond-text)" }}>{floorTxt}</text>}
        {peakAt && <text className="ann halo" x={peakAt.x} y={peakAt.y} textAnchor={peakAt.a}>{peakTxt}</text>}
        {lowAt && <text className="ann halo" x={lowAt.x} y={lowAt.y} textAnchor={lowAt.a}>{lowTxt}</text>}
        {spikeAt && <text className="ann halo" x={spikeAt.x} y={spikeAt.y} textAnchor={spikeAt.a} style={{ fill: "var(--actual)" }}>{spikeTxt}</text>}
        <DayAxis g={g} y={bot} />
        {cross(laneTop, bot)}
        {hits(laneTop, bot)}
      </svg>

      <svg viewBox={`0 0 ${W} ${SH}`} role="img" aria-label="Energy stored through the day, planned at 09:00">
        <rect x={g.X(sel)} y={sTop} width={g.IW} height={sBot - sTop} className="selc" />
        <text className="ttl" x={0} y={14}><title>{glNote("SOC")}</title>Energy stored, MWh</text>
        <YGrid g={g} ticks={[0, 10, 20, 30, 40]} y={yS} />
        <line className="ref" x1={X0} x2={g.X(N)} y1={yS(E)} y2={yS(E)} style={{ stroke: "var(--axis)", strokeDasharray: "2 3" }} />
        <path className="ref" d={stepPath(g, floorS, yS, FIRST)} style={{ fill: "none", stroke: "var(--text-4)", strokeDasharray: "1 3" }}><title>{glNote("RESERVE")}</title></path>
        <line className="ref" x1={g.X(FIRST)} x2={g.X(FIRST)} y1={sTop} y2={sBot} style={{ stroke: "var(--mark)" }} />
        <path className="series" d={linePath(g, soc, yS, 0, FIRST)} style={{ fill: "none", stroke: "var(--soc)", strokeWidth: 2 }} />
        <path className="series" d={linePath(g, soc, yS, FIRST)} style={{ fill: "none", stroke: "var(--soc)", strokeWidth: 2, strokeDasharray: "5 3" }} />
        {o && <path className="series" d={linePath(g, o.soc_mwh, yS)} style={{ fill: "none", stroke: "var(--actual)", strokeWidth: 1.8 }} />}
        {fullAt && <text x={fullAt.x} y={fullAt.y} textAnchor={fullAt.a}>Full · {nf(E)} MWh</text>}
        {minAt && <text x={minAt.x} y={minAt.y} textAnchor={minAt.a}><title>{glNote("RESERVE")}</title>Minimum</text>}
        {endAt && <text className="ann halo" x={endAt.x} y={endAt.y} textAnchor={endAt.a} style={{ fill: "var(--soc)" }}>{endTxt}</text>}
        {minLblAt && <text className="ann halo" x={minLblAt.x} y={minLblAt.y} textAnchor={minLblAt.a} style={{ fill: "var(--soc)" }}>{minTxt}</text>}
        <DayAxis g={g} y={sBot} day={day} next={next} />
        {cross(sTop, sBot)}
        {hits(sTop, sBot)}
      </svg>
      <ChartTip tip={tip} />
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ the factory meter */
/** Behind the factory meter: what the connection draws (the factory's load less the battery), planned at 09:00 and,
 *  after the reveal, metered; the CMD cap (or the month's highest import so far) and, on a flagged day, the IRCR cap. */
function MeterChart({ p, o, sel, setSel, hover, setHover, day, next }: { p: PlanT; o?: Outcome } & Sync) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const g = geo(W);
  const [tip, setTip] = useState<TipData>(null);
  const s = p.site!;
  const cap = s.cmd_cap_mw ?? [];
  const vals = [...s.meter_mw, ...s.load_fc_mw, ...cap, ...(o?.site ? [...o.site.meter_mw, ...o.site.load_mw] : [])].filter(isNum);
  const lo = Math.min(0, ...vals), hi = Math.max(s.import_limit_mw, ...vals);
  const top = 26, bot = 130, H = bot + 34;
  const y = (v: number) => bot - ((v - lo) / (hi - lo)) * (bot - top);
  const ticks: number[] = [];
  for (let t = Math.ceil(lo / 3) * 3; t <= hi; t += 3) ticks.push(t);
  const capEnd = [...cap].reverse().find(isNum) ?? s.cmd_mw;
  const pl = new Placer({ x0: g.X(FIRST) + 3, x1: W, y0: 0, y1: bot - 2 });
  pl.line(stepPts(g, s.meter_mw, y)).line(stepPts(g, s.load_fc_mw, y)).line(stepPts(g, cap, y));
  if (s.ircr_cap_mw) pl.line(stepPts(g, s.ircr_cap_mw, y));
  if (o?.site) pl.line(stepPts(g, o.site.meter_mw, y));
  const cmdTxt = `CMD · ${nf(s.cmd_mw)} MW`;
  const cmdAt = pl.place(cmdTxt, "ann", [[g.X(N) - 4, y(capEnd) - 5, "end"], [g.X(N) - 4, y(capEnd) + 15, "end"], [g.X(FIRST) + 8, y(capEnd) - 5, "start"]]);
  const tipFor = (k: number, e: React.MouseEvent) => {
    const rows: [string, string][] = [["Import", isNum(s.meter_mw[k]) ? `${nf(s.meter_mw[k]!, 1)} MW` : "–"],
      ["Factory", isNum(s.load_fc_mw[k]) ? `${nf(s.load_fc_mw[k]!, 1)} MW` : "–"], ["Cap", isNum(cap[k]) ? `${nf(cap[k]!, 1)} MW` : "–"]];
    if (o?.site) rows.push(["Metered", isNum(o.site.meter_mw[k]) ? `${nf(o.site.meter_mw[k]!, 1)} MW` : "–"]);
    setTip({ x: e.clientX, y: e.clientY, title: `${hhmm(k)}–${hhmm(k + 1)}`, rows });
  };
  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Factory meter import through the day, planned at 09:00, with the CMD cap">
        <rect x={g.X(sel)} y={top} width={g.IW} height={bot - top} className="selc" />
        <text className="ttl" x={0} y={14}><title>{glNote("METER")}</title>Site import, MW</text>
        <YGrid g={g} ticks={ticks} y={y} />
        <path className="series" d={stepPath(g, s.load_fc_mw, y)} style={{ fill: "none", stroke: "var(--base)", strokeWidth: 1, strokeDasharray: "2 2" }}><title>{glNote("FACTORY")}</title></path>
        {s.ircr_cap_mw && <path className="ref" d={stepPath(g, s.ircr_cap_mw, y)} style={{ fill: "none", stroke: "var(--warn)", strokeWidth: 2 }}><title>{glNote("IRCR")}</title></path>}
        <path className="ref" d={stepPath(g, cap, y)} style={{ fill: "none", stroke: "var(--warn)", strokeWidth: 1.4, strokeDasharray: "6 3" }}><title>{glNote("CMD")}</title></path>
        <line className="ref" x1={g.X(FIRST)} x2={g.X(FIRST)} y1={top} y2={bot} style={{ stroke: "var(--mark)" }} />
        <path className="series" d={stepPath(g, s.meter_mw, y, 0, FIRST)} style={{ fill: "none", stroke: "var(--meter)", strokeWidth: 2 }} />
        <path className="series" d={stepPath(g, s.meter_mw, y, FIRST)} style={{ fill: "none", stroke: "var(--meter)", strokeWidth: 2, strokeDasharray: "5 3" }} />
        {o?.site && <path className="series" d={stepPath(g, o.site.meter_mw, y)} style={{ fill: "none", stroke: "var(--actual)", strokeWidth: 1.8 }} />}
        {cmdAt && <text className="ann halo" x={cmdAt.x} y={cmdAt.y} textAnchor={cmdAt.a} style={{ fill: "var(--warn)" }}><title>{glNote("CMD")}</title>{cmdTxt}</text>}
        <DayAxis g={g} y={bot} day={day} next={next} />
        {hover != null && <line x1={g.X(hover) + g.IW / 2} x2={g.X(hover) + g.IW / 2} y1={top} y2={bot} style={{ stroke: "var(--text-4)", pointerEvents: "none" }} />}
        {Array.from({ length: N }, (_, k) => (
          <rect key={k} className="hit" x={g.X(k)} y={top} width={g.IW} height={bot - top}
            onMouseMove={(e) => { setHover(k); tipFor(k, e); }} onMouseLeave={() => { setHover(null); setTip(null); }}
            onClick={() => setSel(k)} />
        ))}
      </svg>
      <ChartTip tip={tip} />
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ selected half-hour */
function Detail({ p, o, k, segs, outLoading, reveal }: { p: PlanT; o?: Outcome; k: number; segs: Seg_[]; outLoading: boolean; reveal: () => void }) {
  const mw = p.plan.battery_mw[k] ?? 0;
  const s = segs.find((x) => k >= x.a && k < x.b);
  const shave = p.plan.action[k] === "shave";
  const act = k < FIRST ? "Before the plan" : Math.abs(mw) < 0.01 ? "Holding" : mw > 0 ? `${shave ? "Shaving" : "Discharging"} ${nf(mw, 1)} MW` : `Charging ${nf(-mw, 1)} MW`;
  const xo = extraOffer(p);
  const soc = p.plan.soc_mwh;
  const s_ = p.site;
  const hh = p.explain?.halfhours?.[k] ?? null;
  const dec = o?.explain?.decisions?.[k] ?? null;  // the decision in effect at this half-hour's start, and why
  const rows: [ReactNode, ReactNode][] = [
    ["Stored", isNum(soc[k]) && isNum(soc[k + 1]) ? `${nf(soc[k]!, 1)} → ${nf(soc[k + 1]!, 1)} MWh` : "–"],
    [<Gl k="FCESS">Grid services</Gl>, `${nf(standby(p, k), 1)} MW standby`],
    [<Gl k={p.offers_0900?.rule === "ladder" ? "EXTRAE5" : "EXTRA"}>Sell extra if</Gl>, k >= FIRST && isNum(xo.lowest[k]) ? `≥ A$${nf(xo.lowest[k]!)} · up to ${nf(xo.mw[k], 1)} MW` : "–"],
    ["Forecast price", isNum(p.price.forecast_p50[k]) ? `A$${nf(p.price.forecast_p50[k]!)}/MWh${hh ? ` · ${hh.rank}` : ""}` : "not yet issued"],
    ...(s_ ? [[<Gl k="METER">Site import</Gl>, isNum(s_.meter_mw[k]) ? `${nf(s_.meter_mw[k]!, 1)} MW${isNum(s_.cmd_cap_mw?.[k]) ? ` · cap ${nf(s_.cmd_cap_mw![k]!, 1)}` : ""}` : "–"] as [ReactNode, ReactNode]] : []),
  ];
  return (
    <div className="detail" aria-live="polite">
      <div>
        <div className="t">{hhmm(k)}–{hhmm(k + 1)} <span className={"tag " + (k < FIRST ? "out" : "plan")}>{k < FIRST ? "EXECUTED" : "SCHEDULED"}</span></div>
        <div className="a" style={{ color: mw > 0.01 ? "var(--dis)" : undefined }}>{act}</div>
        {s && k >= FIRST && <div className="sub">{segName(s)} · {hhmm(s.a)}–{hhmm(s.b)}</div>}
      </div>
      <dl className="kvs">{rows.map(([a, b], i) => <FragmentKV key={i} a={a} b={b} />)}</dl>
      <div className="det-out">
        {o ? (
          <dl className="kvs">
            <dt>Outcome</dt><dd><span className="tag out">{o.live ? (isNum(o.price_actual[k]) ? "SO FAR" : "NOT YET") : "REPLAYED"}</span></dd>
            <dt>Actual price</dt><dd>{isNum(o.price_actual[k]) ? `A$${nf(o.price_actual[k]!)}/MWh` : "–"}</dd>
            <dt>Battery</dt><dd>{isNum(o.battery_mw[k]) ? `${nf(o.battery_mw[k]!, 1)} MW` : "–"}</dd>
            {o.site && <><dt>Site import</dt><dd>{isNum(o.site.meter_mw[k]) ? `${nf(o.site.meter_mw[k]!, 1)} MW` : "–"}</dd></>}
            <dt>Sold extra</dt><dd>{(o.offer_cleared_mw[k] ?? 0) > 0.01 ? `${nf(o.offer_cleared_mw[k]!, o.offer_cleared_mw[k]! < 0.05 ? 2 : 1)} MW · ${aud(o.offer_sold_aud[k])}` : "none"}</dd>
            <dt>Offer price</dt><dd>{isNum(o.offer_price_live[k]) ? `A$${nf(o.offer_price_live[k]!)}` : "not set"}</dd>
          </dl>
        ) : outLoading ? <Loading what="what happened" /> : (
          <div className="det-hidden"><span className="na">Outcome hidden</span><button className="lnk" onClick={reveal}>Plan + what happened</button></div>
        )}
      </div>
      {p.explain && (
        <div className="why-hh">
          <div className="wh">
            <span className="eyebrow tipc" tabIndex={0} data-note={p.explain.by_halfhour[k]}>Why</span>
            <div className="pts">
              {hh ? <>{hh.points.map((x, j) => <Point key={j} p={x} />)}{hh.grid && <Point p={hh.grid} />}</>
                : <div className="pt"><span className="pt-w">{p.explain.by_halfhour[k]}</span></div>}
            </div>
          </div>
          {o?.explain && (
            <div className="wh">
              <span className="eyebrow tipc" tabIndex={0} data-note={[dec?.text, o.explain.by_halfhour[k]].filter(Boolean).join(" ")}>What happened</span>
              <div className="pts">
                {dec && <Point p={dec.head} />}
                {dec?.changed && <>{dec.points.map((x, j) => <Point key={j} p={x} />)}{dec.grid && <Point p={dec.grid} />}</>}
                <Point p={o.explain.points[k]} />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function FragmentKV({ a, b }: { a: ReactNode; b: ReactNode }) {
  return <><dt>{a}</dt><dd>{b}</dd></>;
}
