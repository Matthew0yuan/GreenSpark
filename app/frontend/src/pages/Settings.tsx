import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Chip, Gl, Note, Seg } from "../components/ui";
import { aud, signed } from "../lib/format";
import { glNote } from "../lib/gloss";
import { DEFAULT_SETTINGS, DISP, isDefault, useApp } from "../state";

const SERV: [string, string][] = [["reg_raise", "Regulation raise"], ["reg_lower", "Regulation lower"], ["cont_raise", "Contingency raise"], ["cont_lower", "Contingency lower"]];

export default function Settings() {
  const { settings, setSettings, offer, setOffer } = useApp();
  const [s, setS] = useState(settings);
  useEffect(() => setS(settings), [settings]);
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const verdict = meta.data?.storage?.verdicts?.[s.connection];
  const factory = s.connection === "factory";
  const where = factory ? "Meter" : "Connection";
  const [exp0, imp0] = factory ? [10, 12] : [10, 10];
  const issues: [string, "ok" | "warn" | "bad"][] = [];
  if (s.fcess.length < 4) issues.push(["Grid services reduced: the plan is recomputed, not tested beforehand", "warn"]);
  if (s.startMwh != null) issues.push([`Start energy set to ${s.startMwh} MWh: the day no longer follows the backtest`, "warn"]);
  if (s.exportMw != null && s.exportMw < exp0) issues.push([`${where} export ${s.exportMw} MW is below ${exp0} MW: plans that need more are blocked`, "bad"]);
  if (s.importMw != null && s.importMw < imp0) issues.push([`${where} import ${s.importMw} MW is below ${imp0} MW: plans that charge faster are blocked`, "bad"]);
  if (!issues.length) issues.push(["Default settings: the tested configuration", "ok"]);
  if (verdict && s.dispatcher === "e5" && !verdict.passed) issues.push([`v2 on this connection did not pass the comparison with v1 (training period ${signed(verdict.e5_minus_v1_aud_per_day)}/day, 95% range ${signed(verdict.lo95)} to ${signed(verdict.hi95)})`, "warn"]);
  if (verdict && s.dispatcher === "v1" && verdict.passed) issues.push([`v1 is the baseline here: v2 passed the comparison on this connection (training period ${signed(verdict.e5_minus_v1_aud_per_day)}/day)`, "warn"]);
  if (factory) issues.push(["Factory load is synthetic [assumed]: network bill and IRCR savings are a magnitude only", "warn"]);
  const dirty = JSON.stringify(s) !== JSON.stringify(settings);
  const num = (v: string) => (v === "" ? null : Number(v));

  return (
    <section aria-label="Settings">
      <header className="ph">
        <div className="ph-line"><h1>Settings</h1>{!isDefault(settings) && <Chip cls="unv" note={glNote("UNVALIDATED")}>unvalidated scenario</Chip>}</div>
        <div className="ctx">Changes apply to every page. Live settings recompute; locked ones would need a new value table.</div>
      </header>
      <div className="form">
        <div>
          <fieldset className="fs">
            <h2><Gl k="NETWORK">Storage network</Gl></h2>
            <div className="fld"><span><Gl k={factory ? "FACTORY" : "OWNCONN"}>Connection</Gl></span>
              <Seg label="Connection" value={s.connection} onChange={(v) => setS({ ...s, connection: v, exportMw: null, importMw: null })}
                options={[["factory", "Factory meter"], ["standalone", "Own connection"]]} /></div>
            <div className="fld"><span><Gl k="DISPATCHER">Dispatcher</Gl></span>
              <Seg label="Dispatcher" value={s.dispatcher} onChange={(v) => setS({ ...s, dispatcher: v })}
                options={[["e5", `${DISP.e5} (e5)`], ["v1", DISP.v1]]} /></div>
            <div className="fld"><span><Gl k="FCESS">Grid services offered</Gl></span>
              <span className="boxes">{SERV.map(([k, l]) => (
                <label key={k}><input type="checkbox" checked={s.fcess.includes(k)} onChange={(e) =>
                  setS({ ...s, fcess: e.target.checked ? [...s.fcess, k] : s.fcess.filter((x) => x !== k) })} /> {l}</label>))}</span></div>
            <div className="fld"><span>Energy stored at 08:00, MWh <span className="lock">blank = from the backtest</span></span>
              <input type="number" min={0} max={40} step={1} value={s.startMwh ?? ""} onChange={(e) => setS({ ...s, startMwh: num(e.target.value) })} /></div>
            <div className="fld"><span>{where} export limit, MW <span className="lock">check only</span></span>
              <input type="number" min={0.5} max={20} step={0.5} value={s.exportMw ?? ""} placeholder={String(exp0)} onChange={(e) => setS({ ...s, exportMw: num(e.target.value) })} /></div>
            <div className="fld"><span>{where} import limit, MW <span className="lock">check only</span></span>
              <input type="number" min={0.5} max={20} step={0.5} value={s.importMw ?? ""} placeholder={String(imp0)} onChange={(e) => setS({ ...s, importMw: num(e.target.value) })} /></div>
            {[["Power", "10 MW"], ["Energy", "40 MWh"], ["Round trip", "85%"], ["Wear", "A$30 per MWh discharged"],
              ...(factory ? [["CMD", "9 MW"], ["Factory load", "synthetic × 10"]] : [])].map(([a, b]) => (
              <div className="fld" key={a}><span>{a}</span><span><input type="text" disabled value={b} style={{ width: 200 }} /> <Note note="The value table and the backtest were solved for this battery; changing it needs a new table (vpp.v2.value) and a new backtest." cls="lock tipc">locked</Note></span></div>
            ))}
            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button className="btn pri" disabled={!dirty} onClick={() => setSettings(s)}>Apply</button>
              <button className="btn" disabled={isDefault(s)} onClick={() => {
                const d = { ...DEFAULT_SETTINGS, connection: s.connection, dispatcher: s.dispatcher }; setS(d); setSettings(d); }}>Reset to tested</button>
            </div>
          </fieldset>
          <Rerun />
          <fieldset className="fs">
            <h2><Gl k="VPP">Customer batteries</Gl></h2>
            <div className="fld"><span>Offer used on the Call page</span>
              <select value={offer} onChange={(e) => setOffer(e.target.value as "ref" | "custom")}>
                <option value="custom">Suggested offer (model, not a contract)</option><option value="ref">Reference terms (Synergy, not a Zenith tariff)</option></select></div>
            <ResetDecisions offer={offer} />
          </fieldset>
        </div>
        <aside>
          <h2 style={{ fontSize: 15, marginBottom: 10 }}>Input check</h2>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>
            {issues.map(([t, k]) => <li key={t} className={"ck " + (k === "ok" ? "pass" : k === "bad" ? "fail" : "")} style={{ whiteSpace: "normal" }}>
              <i>{k === "ok" ? "✓" : "!"}</i><span style={{ whiteSpace: "normal" }}>{t}</span></li>)}
          </ul>
        </aside>
      </div>
    </section>
  );
}

function Rerun() {
  const { settings } = useApp();
  const [id, setId] = useState<number | null>(null);
  const start = useMutation({ mutationFn: () => api.startBacktest(settings), onSuccess: (r) => setId(r.id) });
  const job = useQuery({ queryKey: ["job", id], enabled: id != null, queryFn: () => api.job(id!),
    refetchInterval: (q) => (q.state.data && ["done", "failed", "cancelled"].includes(q.state.data.state) ? false : 1500) });
  const j = job.data;
  return (
    <fieldset className="fs">
      <h2>Whole backtest with these settings</h2>
      <p className="sub" style={{ marginTop: 0 }}>Runs the dispatcher through all 570 trading days (a few minutes) and compares with the tested settings, period by period.</p>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button className="btn" onClick={() => start.mutate()} disabled={start.isPending || j?.state === "running"}>Run backtest</button>
        {j?.state === "running" && <button className="btn" onClick={() => api.cancelJob(j.id)}>Cancel</button>}
        {j && <span className="sub">{j.state}{j.message ? ` · ${j.message}` : ""}</span>}
      </div>
      {j && j.state === "running" && <div className="prog" aria-label="progress"><i style={{ width: `${Math.round(100 * j.progress)}%` }} /></div>}
      {j?.state === "done" && j.result && (
        <table className="t" style={{ marginTop: 10 }}>
          <thead><tr><th>Period</th><th className="r">These settings</th><th className="r">Tested</th><th className="r">Difference</th></tr></thead>
          <tbody>{j.result.periods.map((p) => <tr key={p.period.kind}><td>{p.period.label} · {p.period.days} d</td>
            <td className="r">{aud(p.per_day_aud)}</td><td className="r">{aud(p.default_per_day_aud)}</td><td className="r">{signed(p.difference_aud)}</td></tr>)}</tbody>
        </table>
      )}
      {start.error && <div className="state err">{(start.error as Error).message}</div>}
    </fieldset>
  );
}

function ResetDecisions({ offer }: { offer: string }) {
  const qc = useQueryClient();
  const m = useMutation({ mutationFn: () => api.resetDecisions(offer), onSuccess: () => qc.invalidateQueries() });
  return (
    <div className="fld"><span>Your call decisions</span>
      <span><button className="btn" onClick={() => m.mutate()}>Clear all · follow the model</button>{m.isSuccess && <span className="sub"> cleared</span>}</span></div>
  );
}
