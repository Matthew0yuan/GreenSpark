import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Check, type Outcome, type Plan as PlanT } from "../api";
import { bandPath, DayAxis, geo, Hatch, linePath, N, stepPath, X0, YGrid } from "../components/chart";
import { ChartTip, Chip, DayPicker, Failed, Gl, Loading, Note, Seg, useWidth, Who, type TipData } from "../components/ui";
import { addDays, aud, dayLabel, hhmm, isNum, nf, periodOf } from "../lib/format";
import { glNote } from "../lib/gloss";
import { CONN, DISP, isDefault, useApp, whoItems } from "../state";

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
  const plan = useQuery({ queryKey: ["plan", day, settings], queryFn: () => api.plan(day, settings) });
  const out = useQuery({ queryKey: ["outcome", day, settings.connection, settings.dispatcher, settings.fcess, settings.startMwh],
    enabled: revealed && !!plan.data,
    queryFn: () => api.outcome(day, settings) });
  const [sel, setSel] = useState<number>(20);

  useEffect(() => { setSel(20); }, [day]);
  const p = plan.data;
  const o = revealed ? out.data : undefined;

  return (
    <section aria-label="Today's plan">
      <header className="ph">
        <div className="ph-line">
          <h1>Today's plan</h1>
          <span className="chips">
            <Chip cls="step">1 · Decide</Chip>
            <Chip note={glNote(periodOf(day))}>{periodOf(day) === "TRAIN" ? "training-period day" : periodOf(day) === "TREV" ? "test · reviewed" : "test · blind"}</Chip>
            {!isDefault(settings) && <Chip cls="unv" note={glNote("UNVALIDATED")}>changed settings</Chip>}
            {p?.site?.ircr_flag_0900 && <Chip cls="hyp" note={glNote("IRCRFLAG")}>IRCR peak flag</Chip>}
          </span>
        </div>
        <div className="ctx">
          <Gl k="TRADINGDAY">trading day</Gl> 08:00–08:00 AWST · <Gl k="DECISION">plan 09:00</Gl> · <Gl k={settings.connection === "factory" ? "FACTORY" : "OWNCONN"}>{CONN[settings.connection]}</Gl>
          {" · "}<DispatcherName p={p} short={settings.dispatcher} />
        </div>
        <Who items={whoItems(settings.connection)} />
      </header>

      <div className="blk-h" style={{ justifyContent: "space-between" }}>
        <DayPicker day={day} setDay={setDay} />
        <span className="chips">
          <Seg label="Show" value={revealed ? "out" : "plan"} onChange={(v) => setRevealed(v === "out")}
            options={[["plan", "Plan only"], ["out", "Plan + what happened"]]} />
        </span>
      </div>

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

      <DayChart p={p} o={o} segs={segs} sel={sel} setSel={setSel} day={dayLabel(p.day, false)} next={dayLabel(next, false)} />
      {p.site && <MeterChart p={p} o={o} sel={sel} setSel={setSel} day={dayLabel(p.day, false)} next={dayLabel(next, false)} />}
      {outLoading && <Loading what="what happened" />}

      <Detail p={p} o={o} k={sel} segs={segs} />

      <div className="two">
        <div className="blk rule">
          <div className="blk-h"><h2>Value</h2><span className="sub">trading day, A$</span><span className="sp" />
            <button className="lnk" onClick={() => go("evidence")}>vs simpler ways →</button></div>
          <div className="wf-h">Plan at 09:00</div>
          <Waterfall parts={[["e", "Energy", (v.energy ?? 0) + (v.regulation_energy ?? 0)], ["f", "Grid services", v.fcess ?? 0], ["s", "Stored", v.stored_end ?? 0]]}
            wear={v.wear ?? 0} net={v.net ?? 0} dim={blocked} />
          {o ? (
            <>
              <div className="wf-h">What happened · replayed</div>
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
              <thead><tr><th>A$</th><th className="r">Plan 09:00</th><th className="r">Replayed</th></tr></thead>
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
              Replayed: the whole day settled at actual prices{p.site
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

function CheckCell({ c }: { c: Check }) {
  const icon = c.status === "pass" ? "✓" : c.status === "fail" ? "!" : c.status === "warn" ? "△" : "○";
  const note = `${c.label}. ${c.status === "not_checked" ? "Not checked: " : c.status === "warn" ? "Economic limit, priced not blocked: " : ""}${c.detail}${c.worst ? ` (worst: ${c.worst})` : ""}`;
  return <div className={"ck " + c.status} tabIndex={0} data-note={note}><i>{icon}</i><span>{SHORT[c.id] ?? c.label}</span></div>;
}

function Waterfall({ parts, wear, net, dim }: { parts: [string, string, number][]; wear: number; net: number; dim?: boolean }) {
  const gross = parts.reduce((t, x) => t + Math.max(0, x[2]), 0);
  const scale = Math.max(gross, Math.abs(net), 1);
  const pct = (x: number) => `${(100 * Math.max(0, x)) / scale}%`;
  return (
    <div className="wf" style={dim ? { opacity: 0.45 } : undefined}>
      <div className="wf-row">
        {parts.filter((x) => x[2] > 0.5).map(([c, l, x]) => (
          <span key={l} className={c} style={{ width: pct(x) }} data-note={`${l}: ${aud(x)}`} tabIndex={0}>{l} {nf(x)}</span>
        ))}
      </div>
      {wear > 0.5 && <div className="wf-row" style={{ paddingLeft: `min(${pct(gross - wear)}, calc(100% - 90px))` }}>
        {/* a small wear still gets room for its label, as Net does */}
        <span className="w" style={{ width: pct(wear), minWidth: 90 }} data-note={`Wear: −${aud(wear)}`} tabIndex={0}>Wear −{nf(wear)}</span></div>}
      <div className="wf-row"><span className="n" style={{ width: pct(Math.abs(net)), minWidth: 90 }}>Net {aud(net)}</span></div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------ the day chart */
function DayChart({ p, o, segs, sel, setSel, day, next }: { p: PlanT; o?: Outcome; segs: Seg_[]; sel: number; setSel: (k: number) => void; day: string; next: string }) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const g = geo(W);
  const [hover, setHover] = useState<number | null>(null);
  const [tip, setTip] = useState<TipData>(null);
  const P = p.asset.power_mw, E = p.asset.energy_mwh;
  const xo = useMemo(() => extraOffer(p), [p]);
  const ladder = p.offers_0900?.rule === "ladder";
  const floor = xo.floor;
  const pr = p.price;

  const hi = Math.max(250, floor ?? 0, ...pr.forecast_p50.filter(isNum), ...(pr.forecast_p90 ?? []).filter(isNum),
    ...(o ? o.price_actual.filter(isNum).map((x) => Math.min(x, 500)) : []));
  const lo = Math.min(0, ...pr.forecast_p50.filter(isNum), ...(o ? o.price_actual.filter(isNum) : []).map((x) => Math.max(x, -100)));
  const top = 108, bot = 248;
  const yP = (v: number) => bot - ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * (bot - top);
  const step = hi > 600 ? 250 : hi > 300 ? 100 : 50;
  const ticks: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) ticks.push(t);

  const sTop = 22, sBot = 150;
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

  const colours: Record<string, [string, string]> = { discharge: ["var(--dis)", "#fff"], shave: ["var(--shave)", "var(--shave-text)"],
    charge: ["var(--chg)", "var(--text)"], hold: ["var(--hold)", "var(--text-3)"] };
  const H = 280;

  return (
    <div className="blk chart" ref={ref} tabIndex={0} onKeyDown={onKey} aria-label="Trading day chart; arrow keys move the selected half-hour">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Plan for the trading day: ${segs.map(segName).join(", ")}. Price forecast and actions by half-hour.`}>
        <defs><Hatch id="pre" /></defs>
        <rect x={g.X(sel)} y={20} width={g.IW} height={bot - 20} className="selc" />
        <rect x={X0} y={20} width={g.X(FIRST) - X0} height={bot - 20} fill="url(#pre)" />
        <line x1={g.X(FIRST)} x2={g.X(FIRST)} y1={14} y2={bot} style={{ stroke: "var(--mark)" }} />
        <text className="mk9" x={g.X(FIRST)} y={10} textAnchor="middle">PLAN MADE 09:00</text>

        {/* plan lane */}
        <text className="lt" x={X0 - 8} y={37} textAnchor="end"><title>{glNote("DISPATCHER")}</title>Plan</text>
        {segs.map((s) => {
          const [bg, fg] = colours[s.m] ?? colours.hold;
          const w = g.X(s.b) - g.X(s.a);
          const label = segName(s);
          const fits = label.length * 6.2 < w - 6;
          const short = s.m === "hold" ? "" : nf(s.mw, 1);
          return (
            <g key={s.a}>
              <rect x={g.X(s.a) + 0.5} y={22} width={w - 1} height={24} rx={2} style={{ fill: bg }} />
              {(fits || short.length * 6.2 < w - 4) && <text className="act" x={g.X(s.a) + w / 2} y={38} textAnchor="middle" style={{ fill: fg }}>{fits ? label : short}</text>}
            </g>
          );
        })}
        {p.blocked_halfhours.map((k) => <rect key={"b" + k} className="bad-box" x={g.X(k)} y={21} width={g.IW} height={bot - 21} />)}

        {/* sell extra lane */}
        <text className="lt" x={X0 - 8} y={64} textAnchor="end"><title>{glNote(ladder ? "EXTRAE5" : "EXTRA")}</title>Sell extra if</text>
        <rect x={g.X(FIRST) + 0.5} y={52} width={g.X(N) - g.X(FIRST) - 1} height={16} style={{ fill: "none", stroke: "var(--cond)", strokeDasharray: "3 2" }} />
        {sold.map((s, k) => s && <rect key={"s" + k} x={g.X(k)} y={52} width={g.IW} height={16} style={{ fill: "var(--cond)" }} />)}
        <text className="ann halo" x={g.X(FIRST) + 6} y={64} style={{ fill: "var(--cond-text)" }}>
          {floor ? `price ≥ A$${nf(floor)}: +up to ${nf(spare, 0)} MW` : "no extra offer"}{o ? ` · sold ${soldN} · ${aud(soldAud)}` : ""}
        </text>

        {/* grid services lane */}
        <text className="lt" x={X0 - 8} y={86} textAnchor="end"><title>{glNote("FCESS")}</title>Grid services</text>
        {Array.from({ length: N - FIRST }, (_, j) => {
          const k = j + FIRST, s = standby(p, k);
          return s > 0.01 ? <rect key={"f" + k} x={g.X(k)} y={76} width={g.IW} height={14} shapeRendering="crispEdges"
            style={{ fill: "color-mix(in srgb, var(--fcess) " + Math.round(35 + 65 * Math.min(1, s / P)) + "%, var(--surface))" }} /> : null;
        })}
        {fcessAud > 0.5 && <text className="ann halo" x={g.X(FIRST) + 6} y={87}>earns {aud(fcessAud)}</text>}

        {/* price panel */}
        <YGrid g={g} ticks={ticks} y={yP} />
        <text x={X0 + 4} y={top - 4} className="ann">Price, A$/MWh</text>
        {pr.forecast_p10 && pr.forecast_p90 && <path d={bandPath(g, pr.forecast_p10, pr.forecast_p90, yP, FIRST)} style={{ fill: "var(--range)", opacity: 0.32 }}><title>{glNote("RANGE")}</title></path>}
        <path d={stepPath(g, p50, yP, FIRST)} style={{ fill: "none", stroke: "var(--price)", strokeWidth: 1.6, strokeDasharray: "5 3" }} />
        <path d={stepPath(g, pr.actual_before_0900, yP, 0, FIRST)} style={{ fill: "none", stroke: "var(--actual)", strokeWidth: 1.6 }} />
        {floor != null && (
          <g>
            {ladder
              ? <path d={stepPath(g, xo.lowest, yP, FIRST)} style={{ fill: "none", stroke: "var(--cond)", strokeWidth: 1.4, strokeDasharray: "6 3" }}><title>{glNote("EXTRAE5")}</title></path>
              : <line x1={g.X(FIRST)} x2={g.X(N)} y1={yP(floor)} y2={yP(floor)} style={{ stroke: "var(--cond)", strokeWidth: 1.4, strokeDasharray: "6 3" }} />}
            <text className="ann halo" x={g.X(N) - 4} y={yP(floor) - 5} textAnchor="end" style={{ fill: "var(--cond-text)" }}>floor A${nf(floor)}</text>
          </g>
        )}
        {o && <path d={stepPath(g, o.price_actual, yP)} style={{ fill: "none", stroke: "var(--actual)", strokeWidth: 1.6 }} />}
        {o && o.price_actual.map((v, k) => isNum(v) && v > hi ? (
          <g key={"o" + k}><line x1={g.X(k) + g.IW / 2} x2={g.X(k) + g.IW / 2} y1={top - 2} y2={top + 8} style={{ stroke: "var(--actual)", strokeWidth: 2 }} />
            <text className="ann halo" x={g.X(k) + g.IW / 2 + 4} y={top + 6} style={{ fill: "var(--actual)" }}>A${nf(v)}</text></g>) : null)}
        {o && o.offer_price_live.map((v, k) => isNum(v) && v <= hi ? <circle key={"lp" + k} cx={g.X(k) + g.IW / 2} cy={yP(v)} r={2.6} style={{ fill: "var(--cond)" }} /> : null)}
        {isNum(p50[kPeak]) && <text className="ann halo" x={g.X(kPeak) - 3} y={yP(p50[kPeak]!) - 6} textAnchor="end">peak A${nf(p50[kPeak]!)}</text>}
        {isNum(p50[kLow]) && kLow !== kPeak && <text className="ann halo" x={g.X(kLow) + g.IW / 2} y={Math.min(bot - 3, yP(p50[kLow]!) + 14)} textAnchor="middle">low A${nf(p50[kLow]!)}</text>}
        <DayAxis g={g} y={bot} />
        {cross(20, bot)}
        {hits(20, bot)}
      </svg>

      <svg viewBox={`0 0 ${W} 190`} role="img" aria-label="Energy stored through the day, planned at 09:00">
        <rect x={g.X(sel)} y={sTop} width={g.IW} height={sBot - sTop} className="selc" />
        <YGrid g={g} ticks={[0, 10, 20, 30, 40]} y={yS} />
        <text x={X0 + 4} y={sTop - 6} className="ann"><title>{glNote("SOC")}</title>Energy stored, MWh</text>
        <line x1={X0} x2={g.X(N)} y1={yS(E)} y2={yS(E)} style={{ stroke: "var(--axis)", strokeDasharray: "2 3" }} />
        <text x={g.X(N) - 2} y={yS(E) - 4} textAnchor="end">Full · {nf(E)} MWh</text>
        <path d={stepPath(g, floorS, yS, FIRST)} style={{ fill: "none", stroke: "var(--text-4)", strokeDasharray: "1 3" }}><title>{glNote("RESERVE")}</title></path>
        <text x={g.X(N) - 2} y={yS(Math.max(...floorS.filter(isNum), 0)) - 4} textAnchor="end"><title>{glNote("RESERVE")}</title>Minimum</text>
        <line x1={g.X(FIRST)} x2={g.X(FIRST)} y1={sTop} y2={sBot} style={{ stroke: "var(--mark)" }} />
        <path d={linePath(g, soc, yS, 0, FIRST)} style={{ fill: "none", stroke: "var(--soc)", strokeWidth: 2 }} />
        <path d={linePath(g, soc, yS, FIRST)} style={{ fill: "none", stroke: "var(--soc)", strokeWidth: 2, strokeDasharray: "5 3" }} />
        {o && <path d={linePath(g, o.soc_mwh, yS)} style={{ fill: "none", stroke: "var(--actual)", strokeWidth: 1.6 }} />}
        {isNum(soc[kMin]) && <text className="ann halo" x={g.X(kMin)} y={yS(soc[kMin]!) + 14} textAnchor="middle" style={{ fill: "var(--soc)" }}>{nf(soc[kMin]!, 1)} MWh</text>}
        {isNum(soc[N]) && <text className="ann halo" x={g.X(N) - 2} y={yS(soc[N]!) - 6} textAnchor="end" style={{ fill: "var(--soc)" }}>{nf(soc[N]!, 1)} MWh</text>}
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
function MeterChart({ p, o, sel, setSel, day, next }: { p: PlanT; o?: Outcome; sel: number; setSel: (k: number) => void; day: string; next: string }) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const g = geo(W);
  const [hover, setHover] = useState<number | null>(null);
  const [tip, setTip] = useState<TipData>(null);
  const s = p.site!;
  const cap = s.cmd_cap_mw ?? [];
  const vals = [...s.meter_mw, ...s.load_fc_mw, ...cap, ...(o?.site ? [...o.site.meter_mw, ...o.site.load_mw] : [])].filter(isNum);
  const lo = Math.min(0, ...vals), hi = Math.max(s.import_limit_mw, ...vals);
  const top = 22, bot = 130;
  const y = (v: number) => bot - ((v - lo) / (hi - lo)) * (bot - top);
  const ticks: number[] = [];
  for (let t = Math.ceil(lo / 3) * 3; t <= hi; t += 3) ticks.push(t);
  const capNow = cap.filter(isNum);
  const capMw = capNow.length ? Math.max(...capNow) : s.cmd_mw;
  const tipFor = (k: number, e: React.MouseEvent) => {
    const rows: [string, string][] = [["Import", isNum(s.meter_mw[k]) ? `${nf(s.meter_mw[k]!, 1)} MW` : "–"],
      ["Factory", isNum(s.load_fc_mw[k]) ? `${nf(s.load_fc_mw[k]!, 1)} MW` : "–"], ["Cap", isNum(cap[k]) ? `${nf(cap[k]!, 1)} MW` : "–"]];
    if (o?.site) rows.push(["Metered", isNum(o.site.meter_mw[k]) ? `${nf(o.site.meter_mw[k]!, 1)} MW` : "–"]);
    setTip({ x: e.clientX, y: e.clientY, title: `${hhmm(k)}–${hhmm(k + 1)}`, rows });
  };
  return (
    <div className="blk chart" ref={ref} style={{ marginTop: 0 }}>
      <svg viewBox={`0 0 ${W} 170`} role="img" aria-label="Factory meter import through the day, planned at 09:00, with the CMD cap">
        <rect x={g.X(sel)} y={top} width={g.IW} height={bot - top} className="selc" />
        <YGrid g={g} ticks={ticks} y={y} />
        <text x={X0 + 4} y={top - 6} className="ann"><title>{glNote("METER")}</title>Site import, MW</text>
        <path d={stepPath(g, s.load_fc_mw, y)} style={{ fill: "none", stroke: "var(--base)", strokeWidth: 1, strokeDasharray: "2 2" }}><title>{glNote("FACTORY")}</title></path>
        {s.ircr_cap_mw && <path d={stepPath(g, s.ircr_cap_mw, y)} style={{ fill: "none", stroke: "var(--warn)", strokeWidth: 2 }}><title>{glNote("IRCR")}</title></path>}
        <path d={stepPath(g, cap, y)} style={{ fill: "none", stroke: "var(--warn)", strokeWidth: 1.4, strokeDasharray: "6 3" }}><title>{glNote("CMD")}</title></path>
        <text className="ann halo" x={g.X(N) - 4} y={y(capMw) - 5} textAnchor="end" style={{ fill: "var(--warn)" }}><title>{glNote("CMD")}</title>CMD · {nf(s.cmd_mw)} MW</text>
        <line x1={g.X(FIRST)} x2={g.X(FIRST)} y1={top} y2={bot} style={{ stroke: "var(--mark)" }} />
        <path d={stepPath(g, s.meter_mw, y, 0, FIRST)} style={{ fill: "none", stroke: "var(--meter)", strokeWidth: 2 }} />
        <path d={stepPath(g, s.meter_mw, y, FIRST)} style={{ fill: "none", stroke: "var(--meter)", strokeWidth: 2, strokeDasharray: "5 3" }} />
        {o?.site && <path d={stepPath(g, o.site.meter_mw, y)} style={{ fill: "none", stroke: "var(--actual)", strokeWidth: 1.6 }} />}
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
function Detail({ p, o, k, segs }: { p: PlanT; o?: Outcome; k: number; segs: Seg_[] }) {
  const mw = p.plan.battery_mw[k] ?? 0;
  const s = segs.find((x) => k >= x.a && k < x.b);
  const shave = p.plan.action[k] === "shave";
  const act = k < FIRST ? "Before the plan" : Math.abs(mw) < 0.01 ? "Holding" : mw > 0 ? `${shave ? "Shaving" : "Discharging"} ${nf(mw, 1)} MW` : `Charging ${nf(-mw, 1)} MW`;
  const xo = extraOffer(p);
  const soc = p.plan.soc_mwh;
  const s_ = p.site;
  const rows: [ReactNode, ReactNode][] = [
    ["Stored", isNum(soc[k]) && isNum(soc[k + 1]) ? `${nf(soc[k]!, 1)} → ${nf(soc[k + 1]!, 1)} MWh` : "–"],
    [<Gl k="FCESS">Grid services</Gl>, `${nf(standby(p, k), 1)} MW standby`],
    [<Gl k={p.offers_0900?.rule === "ladder" ? "EXTRAE5" : "EXTRA"}>Sell extra if</Gl>, k >= FIRST && isNum(xo.lowest[k]) ? `≥ A$${nf(xo.lowest[k]!)} · up to ${nf(xo.mw[k], 1)} MW` : "–"],
    ["Forecast price", isNum(p.price.forecast_p50[k]) ? `A$${nf(p.price.forecast_p50[k]!)}/MWh` : "not yet issued"],
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
      <dl className="kvs">
        {o ? (
          <>
            <dt>Outcome <span className="tag out">REPLAYED</span></dt><dd></dd>
            <dt>Actual price</dt><dd>{isNum(o.price_actual[k]) ? `A$${nf(o.price_actual[k]!)}/MWh` : "–"}</dd>
            <dt>Battery</dt><dd>{nf(o.battery_mw[k] ?? 0, 1)} MW</dd>
            {o.site && <><dt>Site import</dt><dd>{nf(o.site.meter_mw[k] ?? 0, 1)} MW</dd></>}
            <dt>Sold extra</dt><dd>{(o.offer_cleared_mw[k] ?? 0) > 0.01 ? `${nf(o.offer_cleared_mw[k]!, 1)} MW · ${aud(o.offer_sold_aud[k])}` : "none"}</dd>
            <dt>Offer price</dt><dd>{isNum(o.offer_price_live[k]) ? `A$${nf(o.offer_price_live[k]!)}` : "not set"}</dd>
          </>
        ) : <><dt className="na">Outcome hidden</dt><dd></dd></>}
      </dl>
    </div>
  );
}

function FragmentKV({ a, b }: { a: ReactNode; b: ReactNode }) {
  return <><dt>{a}</dt><dd>{b}</dd></>;
}
