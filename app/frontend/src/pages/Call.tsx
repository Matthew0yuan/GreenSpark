import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type CallDay, type Season } from "../api";
import { DayAxis, geo, Hatch, N, X0, YGrid } from "../components/chart";
import { ChartTip, Chip, DayPicker, Failed, Gl, Loading, Note, Seg, useWidth, Who, type TipData } from "../components/ui";
import { addDays, aud, dayLabel, hhmm, isNum, nf, periodOf } from "../lib/format";
import { glNote } from "../lib/gloss";
import { useApp } from "../state";

const slotOf = (t: string | null) => {
  if (!t) return null;
  const [h, m] = t.split(":").map(Number);
  return (((h - 8 + 24) % 24) * 2 + m / 30);
};

export default function Call() {
  const { day, setDay, offer } = useApp();
  const [view, setView] = useState<"decision" | "hindsight">("decision");
  const hind = view === "hindsight";
  const call = useQuery({ queryKey: ["call", day, offer, hind], queryFn: () => api.call(day, offer, hind) });
  const season = useQuery({ queryKey: ["season", offer, day, hind], queryFn: () => api.season(offer, day, hind) });
  const c = call.data;
  return (
    <section aria-label="Tonight's call">
      <header className="ph">
        <div className="ph-line">
          <h1>Tonight's call</h1>
          <span className="chips">
            <Chip cls="step">1 · Decide</Chip>
            <Chip note={glNote(periodOf(day))}>{periodOf(day) === "TRAIN" ? "training-period day" : periodOf(day) === "TREV" ? "test · reviewed" : "test · blind"}</Chip>
            <Chip cls={offer === "custom" ? "hyp" : ""} note={glNote(offer === "custom" ? "CUSTOMOFFER" : "REFTERMS")}>{offer === "custom" ? "model suggestion" : "reference terms"}</Chip>
          </span>
        </div>
        <div className="ctx">
          {c ? <>{c.terms.pay_aud_per_event ? `A$${nf(c.terms.pay_aud_per_event, 2)} a call` : `${nf(c.terms.pay_c_per_kwh ?? 0)} c/kWh`} · {c.terms.max_calls} calls a year · </> : null}
          <Gl k="DECISION">decided 09:00</Gl>
        </div>
        <Who items={[["ZENITH", "Zenith · the retailer"], ["VPP", "customer batteries (VPP)"], ["AEMO", "AEMO · market operator"], ["WEM", "WEM · WA wholesale market"]]} />
      </header>
      <div className="blk-h" style={{ justifyContent: "space-between" }}>
        <DayPicker day={day} setDay={setDay} />
        <Seg label="View" value={view} onChange={setView} options={[["decision", "Known at 09:00"], ["hindsight", "Whole season · hindsight"]]} />
      </div>
      {call.isLoading && <Loading what="the 09:00 call" />}
      {call.error && <Failed error={call.error} />}
      {c && <CallBody c={c} hind={hind} />}
      {season.data && c && <SeasonChart s={season.data} c={c} hind={hind} onPick={setDay} />}
    </section>
  );
}

function CallBody({ c, hind }: { c: CallDay; hind: boolean }) {
  const qc = useQueryClient();
  const done = (d: CallDay) => {
    qc.setQueryData(["call", c.day, c.offer, false], d);
    qc.invalidateQueries({ queryKey: ["call"] });
    qc.invalidateQueries({ queryKey: ["season"] });
    qc.invalidateQueries({ queryKey: ["value"] });
  };
  const [start, setStart] = useState<string>(c.model.start ?? "17:30");
  const decide = useMutation({ mutationFn: (call: boolean) => api.decide(c.day, c.offer, call, call ? start : null), onSuccess: done });
  const undo = useMutation({ mutationFn: () => api.undo(c.day, c.offer), onSuccess: done });
  const d = c.decision, mine = d.source === "operator";
  const left = c.calls.cap - c.calls.used_before;
  const err = (decide.error || undo.error) as Error | null;

  const title = d.call ? `Customer batteries discharge ${d.start}–${d.end}`
    : c.model.reason === "outside Hot Season" ? "No call · outside the Hot Season" : c.model.reason === "cap reached" ? "No call · no calls left" : "No call today";
  return (
    <>
      <div className="dec">
        <div className="dec-l">
          <div className="title">
            <span className={"callbadge" + (d.call ? "" : " no") + (mine ? " mine" : "")} data-note={mine ? glNote("YOURCALL") : glNote("CALL")} tabIndex={0}>{d.call ? "CALL" : "NO CALL"}</span>
            {title}
          </div>
          <div className="chips dec-chips">
            {d.call && d.standby_from && <Chip note={`${glNote("HOLDCHARGE")} The ${c.reserve_pct}% of each battery is never discharged.`}>hold charge {d.standby_from}–{d.start}</Chip>}
            {c.model.kind === "ircr" && !mine && <Chip note={glNote("FLAGGED")}>demand rule, not price</Chip>}
            {mine && <Chip cls="unv" note={glNote("YOURCALL")}>your decision</Chip>}
          </div>
        </div>
        <div className="dec-r">
          <div className="lbl">Expected customer payment</div>
          <div className={"big" + (d.call ? "" : " dim")} data-note={`${c.payment.basis}. ${nf(c.payment.billed_kwh_per_customer ?? 0, 1)} kWh billed per customer.`}>{d.call ? aud(c.payment.fleet_aud) : "A$0"}</div>
          <div className="chips dec-chips" style={{ justifyContent: "flex-end" }}>
            <Chip note={glNote("TRANSFER")}>transfer payment</Chip>
            <Chip note={`${glNote("RESPONSE")} ${nf(c.responding ?? 0)} of ${nf(c.enrolled ?? 0)} enrolled customers.`}>{nf(c.responding ?? 0)} respond · average</Chip>
          </div>
        </div>
      </div>

      <div className="opctl" role="group" aria-label="Your decision for this day">
        <b>Your decision</b>
        <Seg label="Call or not" value={d.call ? "call" : "no"} onChange={(v) => decide.mutate(v === "call")}
          options={[["call", left > 0 || d.call ? "Call" : "Call · none left"], ["no", "No call"]]} />
        <label><select aria-label="Event start" value={d.call && d.start ? d.start : start} onChange={(e) => { setStart(e.target.value); if (d.call) api.decide(c.day, c.offer, true, e.target.value).then(done); }}>
          {c.start_options.map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
        <span className="who-dec">Model: {c.model.call ? `call ${c.model.start}–${c.model.end}` : `no call · ${c.model.reason}`}</span>
        {mine && <button className="lnk" onClick={() => undo.mutate()}>Undo · follow the model</button>}
        {err && <span style={{ color: "var(--bad)" }}>{err.message}</span>}
      </div>

      <div className="budget">
        <div className="cells" aria-label={`${c.calls.used_with_today} of ${c.calls.cap} calls used`}>
          {Array.from({ length: c.calls.cap }, (_, i) => <i key={i} className={i < c.calls.used_before ? "u" : i < c.calls.used_with_today ? "t" : ""} />)}
        </div>
        <span className="n"><Note note={`Calls used in contract year ${c.contract_year} (1 Jul – 30 Jun) up to and including this day, of the cap.`}><b>{c.calls.used_with_today}</b> of {c.calls.cap} used</Note>
          {c.hot_season && <> · <Note note={glNote("HOTSEASON")}><b>{c.calls.hot_season_days_left}</b> season days left</Note></>}</span>
      </div>

      <DemandChart c={c} hind={hind} />
    </>
  );
}

function DemandChart({ c, hind }: { c: CallDay; hind: boolean }) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const g = geo(W);
  const [tip, setTip] = useState<TipData>(null);
  const fc = c.demand_forecast_mw ?? [];
  const act = hind && c.hindsight ? c.hindsight.demand_actual_mw : [];
  const thr = c.forecast?.threshold_mw ?? null;
  const all = [...fc, ...act, thr].filter(isNum) as number[];
  if (!all.length) return <div className="blk"><div className="blk-h"><h2>Demand forecast</h2></div><div className="state">Not available · no AEMO forecast at 09:00 for this day</div></div>;
  const lo = Math.floor(Math.min(...all) / 500) * 500, hi = Math.ceil(Math.max(...all) / 250) * 250 + 100;
  const top = 26, bot = 196;
  const y = (v: number) => bot - ((v - lo) / (hi - lo)) * (bot - top);
  const ticks: number[] = [];
  for (let t = lo; t <= hi; t += 500) ticks.push(t);
  let kPeak = 2;
  fc.forEach((v, k) => { if (isNum(v) && (!isNum(fc[kPeak]) || v > fc[kPeak]!)) kPeak = k; });
  const d = c.decision, s0 = slotOf(d.standby_from), s1 = slotOf(d.start), s2 = slotOf(d.end);
  const cx = (k: number) => g.X(k) + g.IW / 2;
  const line = (a: (number | null)[]) => {
    let p = "", open = false;
    a.forEach((v, k) => { if (!isNum(v)) { open = false; return; } p += (open ? "L" : "M") + cx(k).toFixed(1) + "," + y(v).toFixed(1); open = true; });
    return p;
  };
  return (
    <div className="blk chart" ref={ref}>
      <div className="blk-h"><h2><Note note="AEMO's pre-dispatch forecast of the energy requirement, published before 09:00 on the day; the call rule compares its daily peak with the threshold.">Demand forecast</Note></h2></div>
      <svg viewBox={`0 0 ${W} ${bot + 40}`} role="img" aria-label={`Demand forecast at 09:00${isNum(fc[kPeak]) ? `, peak ${nf(fc[kPeak]!)} MW` : ""}${isNum(thr) ? `, threshold ${nf(thr!)} MW` : ""}`}>
        <defs><Hatch id="dpre" /></defs>
        <YGrid g={g} ticks={ticks} y={y} fmt={(v) => nf(v)} />
        <text className="ann halo" x={g.X(2) + 6} y={top + 12}>Demand, MW</text>
        <rect x={X0} y={top} width={g.X(2) - X0} height={bot - top} fill="url(#dpre)" />
        {d.call && s0 != null && s1 != null && <rect x={g.X(s0)} y={top} width={g.X(s1) - g.X(s0)} height={bot - top} style={{ fill: "var(--cust-soft)", opacity: 0.5 }} />}
        {d.call && s1 != null && s2 != null && <rect x={g.X(s1)} y={top} width={g.X(s2) - g.X(s1)} height={bot - top} style={{ fill: "var(--cust-soft)" }} />}
        {d.call && s0 != null && s1 != null && <text className="ann halo" x={(g.X(s0) + g.X(s1)) / 2} y={bot - 22} textAnchor="middle" style={{ fill: "var(--cust-text)" }}>Hold charge</text>}
        {d.call && s1 != null && s2 != null && <text className="ann" x={(g.X(s1) + g.X(s2)) / 2} y={bot - 6} textAnchor="middle" style={{ fill: "var(--cust-text)", fontWeight: 700 }}>Call {d.start}–{d.end}</text>}
        {isNum(thr) && <>
          <line x1={X0} x2={g.X(N)} y1={y(thr!)} y2={y(thr!)} style={{ stroke: "var(--peak)", strokeDasharray: "2 3" }}>
            <title>{`threshold = 4th-highest daily peak so far this season (${nf(c.forecast?.fourth_highest_mw ?? 0)} MW) − ${nf(c.forecast?.margin_mw ?? 0)} MW margin`}</title></line>
          <text className="ann halo" x={g.X(N) - 2} y={y(thr!) - 5} textAnchor="end" style={{ fill: "var(--peak)" }}>threshold {nf(thr!)} MW</text>
        </>}
        <line x1={g.X(2)} x2={g.X(2)} y1={top - 10} y2={bot} style={{ stroke: "var(--mark)" }} />
        <text className="mk9" x={g.X(2)} y={top - 14} textAnchor="middle">09:00 DECISION</text>
        <path d={line(fc)} style={{ fill: "none", stroke: "var(--peak)", strokeWidth: 1.8, strokeDasharray: "5 3" }} />
        {act.length > 0 && <path d={line(act)} style={{ fill: "none", stroke: "var(--actual)", strokeWidth: 1.6 }} />}
        {isNum(fc[kPeak]) && <>
          <circle cx={cx(kPeak)} cy={y(fc[kPeak]!)} r={3.5} style={{ fill: "var(--surface)", stroke: "var(--peak)", strokeWidth: 1.5 }} />
          <text className="ann halo" x={cx(kPeak) + 8} y={y(fc[kPeak]!) - 8} style={{ fill: "var(--peak)" }}>peak {nf(fc[kPeak]!)} MW</text>
        </>}
        <DayAxis g={g} y={bot} day={dayLabel(c.day, false)} next={dayLabel(addDays(c.day, 1), false)} />
        {Array.from({ length: N }, (_, k) => (
          <rect key={k} className="hit" x={g.X(k)} y={top} width={g.IW} height={bot - top}
            onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, title: `${hhmm(k)}–${hhmm(k + 1)}`, rows: [
              ["Forecast", isNum(fc[k]) ? `${nf(fc[k]!)} MW` : "–"],
              ...(hind ? [["Actual", isNum(act[k]) ? `${nf(act[k]!)} MW` : "–"] as [string, string]] : []),
              ["Threshold", isNum(thr) ? `${nf(thr!)} MW` : "–"],
              ["4th-highest", isNum(c.forecast?.fourth_highest_mw) ? `${nf(c.forecast!.fourth_highest_mw!)} − ${nf(c.forecast!.margin_mw ?? 0)} MW` : "–"],
              ["Batteries", d.call && s0 != null && s1 != null && s2 != null ? (k >= s1 && k < s2 ? "called" : k >= s0 && k < s1 ? "hold charge" : "self-use") : "self-use"],
            ] })} onMouseLeave={() => setTip(null)} />
        ))}
      </svg>
      <ChartTip tip={tip} />
    </div>
  );
}

function SeasonChart({ s, c, hind, onPick }: { s: Season; c: CallDay; hind: boolean; onPick: (d: string) => void }) {
  const [ref, W] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<TipData>(null);
  const days = s.days;
  const L = 70, R = 20, top = 30, bot = 170;
  const bw = (W - L - R) / days.length;
  const vals = days.flatMap((d) => [d.actual_peak_mw, d.forecast_mw, d.threshold_mw]).filter(isNum) as number[];
  const lo = 2500, hi = Math.max(4200, ...vals) + 100;
  const y = (v: number) => bot - ((Math.max(lo, v) - lo) / (hi - lo)) * (bot - top);
  const ti = days.findIndex((d) => d.day === c.day);
  const firstFuture = days.findIndex((d) => !hind && d.day > c.day);
  const thr = c.forecast?.threshold_mw;
  return (
    <div className="blk chart rule" ref={ref}>
      <div className="blk-h"><h2><Gl k="HOTSEASON">Hot Season {s.season}</Gl></h2>
      </div>
      <svg viewBox={`0 0 ${W} ${bot + 46}`} role="img" aria-label={`Hot Season ${s.season}: daily peak demand, flagged days and calls`}>
        <defs><Hatch id="sfut" /></defs>
        {[3000, 3500, 4000].map((t) => <g key={t}><line className="gl" x1={L} x2={W - R} y1={y(t)} y2={y(t)} /><text x={L - 8} y={y(t) + 3.5} textAnchor="end">{nf(t)}</text></g>)}
        <text x={L - 8} y={top - 10} textAnchor="end" className="ann">MW</text>
        <g transform={`translate(${W - R - (hind ? 330 : 250)},${top - 18})`}>
          <rect width={10} height={8} y={-7} style={{ fill: "var(--bar)" }} /><text x={14}>Daily peak</text>
          <rect x={86} width={10} height={8} y={-7} style={{ fill: "var(--peak)" }} /><text x={100}><title>{glNote("FLAGGED")}</title>Flagged</text>
          <circle cx={164} cy={-3} r={3.5} style={{ fill: "var(--cust)" }} /><text x={172}><title>{glNote("CALL")}</title>Called</text>
          {hind && <><text x={228} style={{ fill: "var(--peak)" }}>★</text><text x={240}><title>{glNote("PEAKDAY")}</title>Peak day</text></>}
        </g>
        {days.map((d, i) => {
          const x = L + i * bw;
          const v = d.actual_peak_mw;
          return (
            <g key={d.day} style={{ cursor: "pointer" }} onClick={() => onPick(d.day)}
              onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, title: dayLabel(d.day), rows: [
                ["Peak", isNum(v) ? `${nf(v!)} MW` : "not yet known"], ["Forecast", isNum(d.forecast_mw) ? `${nf(d.forecast_mw!)} MW` : "–"],
                ["Flagged", d.flagged ? "yes" : "no"], ["Called", d.called ? (d.operator ? "yes · yours" : "yes") : d.operator ? "no · yours" : "no"],
                ...(hind ? [["Peak day", d.official_peak_day ? "yes" : "no"] as [string, string]] : [])] })}
              onMouseLeave={() => setTip(null)}>
              <rect x={x} y={top} width={bw} height={bot - top + 30} style={{ fill: "transparent" }} />
              {isNum(v) && <rect x={x + bw * 0.12} y={y(v!)} width={bw * 0.76} height={bot - y(v!)} style={{ fill: d.flagged ? "var(--peak)" : "var(--bar)", opacity: d.flagged ? 0.85 : 1 }} />}
              {d.called && <circle cx={x + bw / 2} cy={bot + 14} r={Math.min(3.2, bw / 2.4)} style={{ fill: d.operator ? "var(--surface)" : "var(--cust)", stroke: "var(--cust)", strokeWidth: 1.6 }} />}
              {hind && d.official_peak_day && <text x={x + bw / 2} y={bot + 28} textAnchor="middle" style={{ fill: "var(--peak)", fontSize: 11 }}>★</text>}
            </g>
          );
        })}
        <text x={L - 8} y={bot + 17} textAnchor="end">Calls</text>
        {firstFuture > 0 && <rect x={L + firstFuture * bw} y={top - 6} width={W - R - (L + firstFuture * bw)} height={bot - top + 6} fill="url(#sfut)" style={{ pointerEvents: "none" }} />}
        {ti >= 0 && <>
          <line x1={L + (ti + 0.5) * bw} x2={L + (ti + 0.5) * bw} y1={top - 8} y2={bot + 22} style={{ stroke: "var(--mark)", strokeDasharray: "2 2" }} />
          <text className="ann" x={L + (ti + 0.5) * bw} y={top - 12} textAnchor="middle">Today</text>
          {isNum(thr) && <><line x1={L} x2={L + (ti + 1) * bw} y1={y(thr!)} y2={y(thr!)} style={{ stroke: "var(--peak)", strokeDasharray: "2 3" }} />
            <text className="ann halo" x={L + (ti + 1) * bw + 4} y={y(thr!) + 4} style={{ fill: "var(--peak)" }}>threshold {nf(thr!)} MW</text></>}
        </>}
        {["2025-12-01", "2026-01-01", "2026-02-01", "2026-03-01"].map((m) => {
          const i = days.findIndex((d) => d.day === m);
          return i >= 0 ? <text key={m} x={L + i * bw} y={bot + 42}>{dayLabel(m).split(" ").slice(2).join(" ")}</text> : null;
        })}
      </svg>
      <ChartTip tip={tip} />
    </div>
  );
}
