import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type ValueRow, type ValueSummary } from "../api";
import { Chip, Failed, Gl, Loading, Note, useWidth, Who } from "../components/ui";
import { aud, isNum, nf, signed } from "../lib/format";
import { glNote } from "../lib/gloss";
import { useApp } from "../state";

export default function Value() {
  const q = useQuery({ queryKey: ["value"], queryFn: api.value });
  return (
    <section aria-label="Value by party">
      <header className="ph">
        <div className="ph-line"><h1>Value by party</h1><span className="chips"><Chip cls="step">2 · Improve</Chip></span></div>
        <div className="ctx">Customer batteries · <Gl k="CUSTOMOFFER">suggested offer</Gl> vs <Gl k="REFTERMS">reference terms</Gl></div>
        <Who items={[["ZENITH", "Zenith · the retailer"], ["VPP", "customer batteries (VPP)"], ["AEMO", "AEMO · market operator"], ["WEM", "WEM · WA wholesale market"]]} />
      </header>
      {q.isLoading && <Loading what="the accounts" />}
      {q.error && <Failed error={q.error} />}
      {q.data && <Body v={q.data} />}
    </section>
  );
}

function Body({ v }: { v: ValueSummary }) {
  const { offer } = useApp();
  const ref = v.offers.ref, cus = v.offers.custom;
  const diff = v.difference_custom_minus_ref_aud ?? 0;
  const cur = v.offers[offer];
  const shown: ValueRow = cur.your_calls ?? cur.model_calls;
  const positive = (cus.model_calls.parties_aud.zenith ?? 0) > 0;
  const worst = (o: ValueSummary["offers"]["ref"]) => o.holdouts.length ? Math.min(...o.holdouts.map((h) => h.held_out_profit)) : null;
  return (
    <>
      <div className="chips" style={{ marginBottom: 8 }}><Chip cls="hyp" note={glNote("S2a")}>today's rule (S2a): no business case</Chip></div>
      <div className="dec">
        <div className="dec-l">
          <div className="statusline">{positive ? <span className="pill ok" data-note={glNote("S2b")} tabIndex={0}>✓ Positive in S2b</span> : <span className="pill bad">No business case</span>}</div>
          <div className="title"><span className={"big " + (diff >= 0 ? "pos" : "neg")} data-note={`Extra Zenith profit of the suggested offer over the reference terms in contract year 2025-26 under S2b, each with the model's own calls.`}>{signed(diff)}</span> vs <Gl k="REFTERMS">reference terms</Gl></div>
          <div className="cells4">
            <span className="cell ok"><i>✓</i>Customers better off</span>
            <span className={"cell " + (cus.model_calls.customer_limit_breaches === 0 ? "ok" : "warn")} data-note="Calls never took a battery below the customers' 20% floor, past its power or outside its window." tabIndex={0}>
              <i>{cus.model_calls.customer_limit_breaches === 0 ? "✓" : "!"}</i>Customer limits held</span>
            <span className="cell warn" data-note={glNote("S2b")} tabIndex={0}><i>!</i>Assumed 2027 rule</span>
            <span className="cell na" data-note="No Hot Season in the test period: the first blind test is the 2026-27 Hot Season (1 Dec 2026 – 31 Mar 2027)." tabIndex={0}><i>–</i>Not yet tested blind</span>
          </div>
        </div>
        <div className="dec-r">
          <div className="chips" style={{ justifyContent: "flex-end" }}>
            <Chip note={`${glNote("CONTRACTYEAR")} 1 Jul 2025 – 30 Jun 2026, 365 days.`}>contract year 2025-26</Chip>
            <Chip cls="hyp" note={glNote("VPP")}>household-sized · magnitude</Chip>
          </div>
        </div>
      </div>

      <div className="blk">
        <div className="blk-h"><h2>Against the alternatives</h2></div>
        <Alternatives rows={[
          { name: "No calls", sub: "baseline", note: "Customers' batteries in normal self-use: the comparison for every row.", z: 0, worst: null, lines: ["0 calls"] },
          { name: "Reference terms", sub: `${nf(ref.terms.pay_c_per_kwh ?? 0)} c/kWh · ${ref.terms.max_calls} calls`, note: `${glNote("REFTERMS")}`, z: ref.model_calls.parties_aud.zenith, worst: worst(ref),
            lines: [`${ref.model_calls.calls} of ${ref.terms.max_calls} calls`, `${ref.model_calls.peak_days_covered} of ${ref.model_calls.peak_days} peak days`, `customers ${signed(ref.model_calls.per_customer_aud.customers, 2)} /customer/yr`] },
          { name: "Suggested offer", sub: `A$${nf(cus.terms.pay_aud_per_event ?? 0, 2)} a call · ${cus.terms.max_calls} calls`, note: glNote("CUSTOMOFFER"), z: cus.model_calls.parties_aud.zenith, worst: worst(cus), hl: true,
            lines: [`${cus.model_calls.calls} of ${cus.terms.max_calls} calls`, `${cus.model_calls.peak_days_covered} of ${cus.model_calls.peak_days} peak days`, `customers ${signed(cus.model_calls.per_customer_aud.customers, 2)} /customer/yr`] },
          ...(cur.your_calls ? [{ name: "Your calls", sub: cur.name, note: glNote("YOURCALL"), z: cur.your_calls.parties_aud.zenith, worst: null, mine: true,
            lines: [`${cur.your_calls.calls} calls · ${cur.your_calls.operator_days} days decided by you`, `${cur.your_calls.peak_days_covered} of ${cur.your_calls.peak_days} peak days`, `customers ${signed(cur.your_calls.per_customer_aud.customers, 2)} /customer/yr`] }] : []),
        ]} />
      </div>

      <div className="blk rule">
        <div className="blk-h"><h2>Who gets what</h2><span className="sub">{cur.name}{cur.your_calls ? " · your calls" : ""}</span>
          <Chip note="Zenith's extra profit against having no customer batteries called.">Zenith vs no fleet</Chip>
          <Chip note="Customers' gain against their battery's normal self-use.">customers vs self-use</Chip>
          <Chip note={`${glNote("TRANSFER")} Zenith pays customers ${aud(shown.transfers_aud)} in this year; ${nf(shown.enrolled ?? 0)} enrolled, ${nf(shown.responding ?? 0)} respond to a call on average.`}>payments are transfers</Chip></div>
        <Parties r={shown} />
      </div>

      <details className="more"><summary>Full accounts by party</summary><Ledger r={shown} /></details>
      <details className="more"><summary>Season-by-season tests</summary><Holdouts v={v} /></details>
      <details className="more"><summary>Try other payment terms</summary><Terms v={v} /></details>
    </>
  );
}

type Alt = { name: string; sub: string; note: string; z: number | null; worst: number | null; lines: string[]; hl?: boolean; mine?: boolean };
function Alternatives({ rows }: { rows: Alt[] }) {
  const [ref, W] = useWidth<HTMLDivElement>(600);
  const L = 330, R = 210, rh = 84;
  const all = rows.flatMap((r) => [r.z ?? 0, r.worst ?? 0]);
  const lo = Math.min(0, ...all), hi = Math.max(1, ...all);
  const x = (v: number) => L + ((v - lo) / (hi - lo)) * (W - L - R);
  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${W} ${rows.length * rh + 30}`} role="group" aria-label="Extra Zenith profit of each way of calling, contract year 2025-26, with the worst season">
        <text className="ann" x={L - 4} y={12} textAnchor="end">Extra Zenith profit, A$</text>
        <line x1={x(0)} x2={x(0)} y1={18} y2={rows.length * rh + 18} style={{ stroke: "var(--axis)" }} />
        {rows.map((r, i) => {
          const y = 22 + i * rh;
          return (
            <g key={r.name}>
              {(r.hl || r.mine) && <rect x={0} y={y} width={W} height={rh - 6} rx={6} style={{ fill: r.mine ? "var(--cust-soft)" : "var(--accent-soft)", opacity: 0.7 }} />}
              <text className="lt" x={12} y={y + 18} style={{ fontWeight: 600 }}><title>{r.note}</title>{r.name}</text>
              <text x={12 + r.name.length * 7.4 + 8} y={y + 18}>{r.sub}</text>
              {r.lines.map((l, j) => <text key={j} x={12} y={y + 34 + j * 14} style={{ fill: "var(--text-2)" }}>{l}</text>)}
              {isNum(r.z) && <>
                <rect x={Math.min(x(0), x(r.z))} y={y + 12} width={Math.abs(x(r.z) - x(0))} height={16} style={{ fill: (r.z ?? 0) >= 0 ? "var(--zen)" : "var(--bad)" }} />
                <text className="ann" x={Math.max(x(0), x(r.z)) + 6} y={y + 24}>{r.name === "No calls" ? "A$0 baseline" : aud(r.z)}</text>
              </>}
              {isNum(r.worst) && <>
                <rect x={Math.min(x(0), x(r.worst!))} y={y + 34} width={Math.abs(x(r.worst!) - x(0))} height={12} style={{ fill: r.worst! >= 0 ? "var(--zen)" : "var(--bad)", opacity: r.worst! >= 0 ? 0.4 : 0.85 }} />
                <text x={Math.max(x(0), x(r.worst!)) + 6} y={y + 44}><title>Lowest Zenith profit when one Hot Season (2023-24, 2024-25 or 2025-26) was left out of the choice and then tested; only 2025-26 is in time order.</title>{aud(r.worst)} worst season</text>
              </>}
            </g>
          );
        })}
        {[lo, 0, hi].filter((v, i, a) => a.indexOf(v) === i).map((t) => <text key={t} x={x(t)} y={rows.length * rh + 28} textAnchor="middle">{t === 0 ? "0" : aud(t)}</text>)}
      </svg>
    </div>
  );
}

function Parties({ r }: { r: ValueRow }) {
  const rows: [string, string, number | null, number | null, string][] = [
    ["Zenith", "var(--zen)", r.parties_aud.zenith, r.per_customer_aud.zenith, "Zenith's profit: wholesale and capacity value, less payments and operating costs."],
    ["Customers", "var(--cust)", r.parties_aud.customers, r.per_customer_aud.customers, "Payments and credits, less the extra retail bill, battery wear and inconvenience."],
    ["Other parties", "var(--oth)", r.parties_aud.retailer, null, "The customers' retail bill change, kept out of Zenith's profit (conservative)."],
  ];
  const [ref, W] = useWidth<HTMLDivElement>(480);
  const max = Math.max(1, ...rows.map((x) => Math.abs(x[2] ?? 0)));
  const L = 180, w = (v: number | null) => Math.max(3, (Math.abs(v ?? 0) / max) * (W - L - 280));
  return (
    <div className="chart" ref={ref}>
      <svg viewBox={`0 0 ${W} ${rows.length * 28 + 4}`} role="img" aria-label={rows.map((x) => `${x[0]} ${aud(x[2])}`).join(", ")}>
        {rows.map(([n, col, v, pc, note], i) => {
          const y = 4 + i * 28, neg = (v ?? 0) < 0;
          return (
            <g key={n}>
              <circle cx={6} cy={y + 9} r={4} style={{ fill: col }} />
              <text className="lt" x={16} y={y + 13} style={{ fontWeight: 600 }}><title>{note}</title>{n}</text>
              <rect x={L} y={y + 2} width={w(v)} height={14} style={{ fill: neg ? "var(--bad)" : col }} />
              <text className="ann" x={L + w(v) + 8} y={y + 14} style={{ fontWeight: 600, fill: neg ? "var(--bad)" : undefined }}>{aud(v)}</text>
              {isNum(pc) && <text x={L + w(v) + 96} y={y + 14}>· {aud(pc, 2)} /customer/yr</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function Ledger({ r }: { r: ValueRow }) {
  return (
    <table className="t">
      <thead><tr><th>Item</th><th>Kind</th><th>Payer</th><th>Receiver</th><th className="r">A$</th></tr></thead>
      <tbody>{r.ledger.map((l) => <tr key={l.item}><td>{l.label}</td><td>{l.kind.replace("_", " ")}</td><td>{l.payer}</td><td>{l.receiver ?? "–"}</td><td className="r">{aud(l.aud)}</td></tr>)}</tbody>
    </table>
  );
}

function Holdouts({ v }: { v: ValueSummary }) {
  return (
    <table className="t">
      <thead><tr><th>Offer</th><th>Season left out, then tested</th><th>In time order</th><th className="r">Zenith A$</th><th className="r">Peak days covered</th></tr></thead>
      <tbody>{(["ref", "custom"] as const).flatMap((o) => v.offers[o].holdouts.map((h) => (
        <tr key={o + h.held_out}><td>{v.offers[o].name}</td><td>{h.held_out}</td>
          <td><Note note={h.chronological ? glNote("CONTRACTYEAR") : "Tuned on later seasons and tested on this earlier one: weaker evidence than a forward test."}>{h.chronological ? "yes" : "no"}</Note></td>
          <td className="r" style={{ color: h.held_out_profit < 0 ? "var(--bad)" : undefined }}>{aud(h.held_out_profit)}</td><td className="r">{h.covered_held_out}</td></tr>)))}
      </tbody>
    </table>
  );
}

function Terms({ v }: { v: ValueSummary }) {
  const [o, setO] = useState<"ref" | "custom">("custom");
  const t = v.offers[o].terms;
  const [f, setF] = useState({ pay_c_per_kwh: t.pay_c_per_kwh ?? 0, pay_aud_per_event: t.pay_aud_per_event ?? 0, max_calls: t.max_calls });
  const m = useMutation({ mutationFn: () => api.evaluate({ offer: o, ...f }) });
  const base = v.offers[o].model_calls.parties_aud.zenith;
  return (
    <div style={{ display: "grid", gap: 8, maxWidth: 640 }}>
      <div className="fld"><span>Offer</span><span><select value={o} onChange={(e) => { const k = e.target.value as "ref" | "custom"; setO(k); const tt = v.offers[k].terms; setF({ pay_c_per_kwh: tt.pay_c_per_kwh ?? 0, pay_aud_per_event: tt.pay_aud_per_event ?? 0, max_calls: tt.max_calls }); }}>
        <option value="custom">Suggested offer</option><option value="ref">Reference terms</option></select></span></div>
      <div className="fld"><span>Payment per kWh exported, c</span><input type="number" min={0} max={500} value={f.pay_c_per_kwh} onChange={(e) => setF({ ...f, pay_c_per_kwh: +e.target.value })} /></div>
      <div className="fld"><span>Payment per call, A$</span><input type="number" min={0} max={500} step={0.5} value={f.pay_aud_per_event} onChange={(e) => setF({ ...f, pay_aud_per_event: +e.target.value })} /></div>
      <div className="fld"><span>Calls per contract year, cap</span><input type="number" min={1} max={120} value={f.max_calls} onChange={(e) => setF({ ...f, max_calls: +e.target.value })} /></div>
      <div><button className="btn pri" onClick={() => m.mutate()} disabled={m.isPending}>{m.isPending ? "Computing…" : "Evaluate"}</button></div>
      {m.error && <div className="state err">{(m.error as Error).message}</div>}
      {m.data && (
        <div className="strip">
          <b>{aud(m.data.parties_aud.zenith)}</b> Zenith ({signed((m.data.parties_aud.zenith ?? 0) - (base ?? 0))} vs saved terms) · customers {aud(m.data.parties_aud.customers)} ·
          {" "}{m.data.calls} calls · {nf(m.data.enrolled ?? 0)} enrolled <Chip cls="unv" note={glNote("UNVALIDATED")}>unvalidated</Chip>
        </div>
      )}
      <span className="sub">Contract year 2025-26 under S2b. The call rule is refitted on the training data; event length and reserve stay as the offer's.</span>
    </div>
  );
}
