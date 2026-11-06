import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type PeriodMoney, type StorageSettings } from "../api";
import { Chip, Gl, Note, PageHead, Seg } from "../components/ui";
import { aud, dayLabel, signed } from "../lib/format";
import { glNote } from "../lib/gloss";
import { DEFAULT_SETTINGS, DISP, isDefault, isTestedBattery, useApp, useMeta } from "../state";

const SERV: [string, string][] = [["reg_raise", "Regulation raise"], ["reg_lower", "Regulation lower"], ["cont_raise", "Contingency raise"], ["cont_lower", "Contingency lower"]];
const ENERGY = [10, 80], CMD = [5, 12];  // as app/engine/configs.py, which refuses anything else

export default function Settings() {
  const { settings, setSettings, offer, setOffer, build, startBuild, clearBuild } = useApp();
  const [s, setS] = useState(settings);
  useEffect(() => setS(settings), [settings]);
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const asset = meta.data?.storage?.asset;
  const [energy0, cmd0] = [asset?.energy_mwh ?? 40, asset?.site?.cmd_mw ?? 9];
  const verdict = meta.data?.storage?.verdicts?.[s.connection];
  const factory = s.connection === "factory";
  const where = factory ? "Meter" : "Connection";
  const [exp0, imp0] = factory ? [10, 12] : [10, 10];
  // as the API takes them: the tested values are no change, and a connection of its own has no CMD
  const next: StorageSettings = { ...s, energyMwh: s.energyMwh === energy0 ? null : s.energyMwh, cmdMw: !factory || s.cmdMw === cmd0 ? null : s.cmdMw };
  const own = !isTestedBattery(next);
  const energy = next.energyMwh ?? energy0;
  const badEnergy = next.energyMwh != null && (!Number.isInteger(next.energyMwh) || next.energyMwh < ENERGY[0] || next.energyMwh > ENERGY[1]);
  const badCmd = next.cmdMw != null && (!Number.isInteger(next.cmdMw * 2) || next.cmdMw < CMD[0] || next.cmdMw > CMD[1]);
  const badStart = s.startMwh != null && s.startMwh > energy;
  const state = useQuery({ queryKey: ["config", next.connection, next.dispatcher, next.energyMwh, next.cmdMw], enabled: own && !badEnergy && !badCmd, queryFn: () => api.config(next) });
  const needsRun = own && state.data?.ready === false;
  const issues: [string, "ok" | "warn" | "bad"][] = [];
  if (badEnergy) issues.push([`Energy must be a whole number from ${ENERGY[0]} to ${ENERGY[1]} MWh`, "bad"]);
  if (badCmd) issues.push([`CMD must be from ${CMD[0]} to ${CMD[1]} MW, in steps of 0.5`, "bad"]);
  if (badStart) issues.push([`Start energy ${s.startMwh} MWh is more than the battery's ${energy} MWh`, "bad"]);
  if (own && !badEnergy && !badCmd) issues.push([needsRun ? `${energy} MWh${factory ? `, CMD ${next.cmdMw ?? cmd0} MW` : ""}: needs its own backtest, 3–15 min` : `${energy} MWh${factory ? `, CMD ${next.cmdMw ?? cmd0} MW` : ""}: backtest saved`, "warn"]);
  if (own) issues.push(["Compare on the Training period: a choice made from Test days is no longer tested", "warn"]);
  if (next.cmdMw != null) issues.push(["A low CMD looks cheap: a month above it pays about what a contract at its peak would", "warn"]);
  if (own && meta.data?.live) issues.push(["On live data your own energy and CMD stop at 22 Oct, and need a new backtest each morning", "warn"]);
  if (s.fcess.length < 4) issues.push(["Grid services reduced: the plan is recomputed, not tested beforehand", "warn"]);
  if (s.startMwh != null) issues.push([`Start energy set to ${s.startMwh} MWh: the day no longer follows the backtest`, "warn"]);
  if (s.exportMw != null && s.exportMw < exp0) issues.push([`${where} export ${s.exportMw} MW is below ${exp0} MW: plans that need more are blocked`, "bad"]);
  if (s.importMw != null && s.importMw < imp0) issues.push([`${where} import ${s.importMw} MW is below ${imp0} MW: plans that charge faster are blocked`, "bad"]);
  if (!issues.length) issues.push(["Default settings: the tested configuration", "ok"]);
  // the comparison in /meta is of the tested battery and CMD
  if (!own && verdict && s.dispatcher === "e5" && !verdict.passed) issues.push([`v2 on this connection did not pass the comparison with v1 (training period ${signed(verdict.e5_minus_v1_aud_per_day)}/day, 95% range ${signed(verdict.lo95)} to ${signed(verdict.hi95)})`, "warn"]);
  if (!own && verdict && s.dispatcher === "v1" && verdict.passed) issues.push([`v1 is the baseline here: v2 passed the comparison on this connection (training period ${signed(verdict.e5_minus_v1_aud_per_day)}/day)`, "warn"]);
  if (factory) issues.push(["Factory load is synthetic [assumed]: network bill and IRCR savings are a magnitude only", "warn"]);
  const dirty = JSON.stringify(next) !== JSON.stringify(settings);
  const running = build?.job == null ? !!build : ["queued", "running"].includes(build.job.state);
  const apply = useMutation({ mutationFn: () => api.startConfig(next), onSuccess: (r) => { if (r.ready) setSettings(next); else startBuild(r.id!, next); } });
  const num = (v: string) => (v === "" ? null : Number(v));

  return (
    <section aria-label="Settings">
      <PageHead title="Settings" chips={!isDefault(settings) ? <Chip cls="unv" note={glNote("UNVALIDATED")}>unvalidated scenario</Chip> : undefined}
        ctx="Changes apply to every page. A new energy or CMD runs its own backtest first." />
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
            <div className="fld"><label htmlFor="energy">Energy, MWh<span className="hint">tested {energy0}</span></label>
              <input id="energy" className="inp" type="number" min={ENERGY[0]} max={ENERGY[1]} step={1} value={s.energyMwh ?? ""} placeholder={String(energy0)} onChange={(e) => setS({ ...s, energyMwh: num(e.target.value) })} /></div>
            {factory && <div className="fld"><label htmlFor="cmd"><Gl k="CMD">CMD</Gl>, MW<span className="hint">tested {cmd0}</span></label>
              <input id="cmd" className="inp" type="number" min={CMD[0]} max={CMD[1]} step={0.5} value={s.cmdMw ?? ""} placeholder={String(cmd0)} onChange={(e) => setS({ ...s, cmdMw: num(e.target.value) })} /></div>}
            <div className="fld"><span>Energy stored at 08:00, MWh<span className="hint">blank = from the backtest</span></span>
              <input className="inp" type="number" min={0} max={energy} step={1} value={s.startMwh ?? ""} onChange={(e) => setS({ ...s, startMwh: num(e.target.value) })} /></div>
            <div className="fld"><span>{where} export limit, MW<span className="hint">check only</span></span>
              <input className="inp" type="number" min={0.5} max={20} step={0.5} value={s.exportMw ?? ""} placeholder={String(exp0)} onChange={(e) => setS({ ...s, exportMw: num(e.target.value) })} /></div>
            <div className="fld"><span>{where} import limit, MW<span className="hint">check only</span></span>
              <input className="inp" type="number" min={0.5} max={20} step={0.5} value={s.importMw ?? ""} placeholder={String(imp0)} onChange={(e) => setS({ ...s, importMw: num(e.target.value) })} /></div>
            {[["Power", "10 MW"], ["Round trip", "85%"], ["Wear", "A$30 per MWh discharged"],
              ...(factory ? [["Factory load", "synthetic × 10"]] : [])].map(([a, b]) => (
              <div className="fld" key={a}><span>{a}</span><span className="ro"><b>{b}</b><Note note="The value table and the backtest were solved for this battery; changing it needs a new table (vpp.v2.value) and a new backtest." cls="lock tipc">
                {LOCK}locked</Note></span></div>
            ))}
            <div className="actions">
              <button className="btn pri" disabled={(!dirty && !needsRun) || badEnergy || badCmd || badStart || running || apply.isPending}
                onClick={() => (own ? apply.mutate() : setSettings(next))}>{needsRun ? "Run backtest and apply" : "Apply"}</button>
              <button className="btn" disabled={isDefault(s)} onClick={() => {
                if (build?.job && running) api.cancelJob(build.job.id);  // or it would apply itself when done
                clearBuild();
                const d = { ...DEFAULT_SETTINGS, connection: s.connection, dispatcher: s.dispatcher }; setS(d); setSettings(d); }}>Reset to tested</button>
            </div>
            <Building />
            {apply.error && <div className="state err">{(apply.error as Error).message}</div>}
          </fieldset>
          <MarketData />
          <Rerun />
          <fieldset className="fs">
            <h2><Gl k="VPP">Customer batteries</Gl></h2>
            <div className="fld"><span>Offer used on the Call page</span>
              <select className="sel" style={{ width: 340, maxWidth: "100%" }} value={offer} onChange={(e) => setOffer(e.target.value as "ref" | "custom")}>
                <option value="custom">Suggested offer (model, not a contract)</option><option value="ref">Reference terms (Synergy, not a Zenith tariff)</option></select></div>
            <ResetDecisions offer={offer} />
          </fieldset>
        </div>
        <aside>
          <h2>Input check</h2>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }}>
            {issues.map(([t, k]) => <li key={t} className={"ck " + (k === "ok" ? "pass" : k === "bad" ? "fail" : "")} style={{ whiteSpace: "normal" }}>
              <i>{k === "ok" ? "✓" : "!"}</i><span style={{ whiteSpace: "normal" }}>{t}</span></li>)}
          </ul>
        </aside>
      </div>
    </section>
  );
}

/** The backtest of a new battery and CMD: it applies itself when done (state.tsx). */
function Building() {
  const { build, clearBuild } = useApp();
  const asset = useMeta().data?.storage?.asset;
  const j = build?.job;
  if (!build) return null;
  const s = build.settings;
  const what = `${s.energyMwh ?? asset?.energy_mwh} MWh${s.connection === "factory" ? `, CMD ${s.cmdMw ?? asset?.site?.cmd_mw} MW` : ""}`;
  if (j && !["queued", "running"].includes(j.state)) {
    return <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10 }} role="alert">
      <span className="sub">{what}: {j.message ? j.message.trim().split("\n").pop() : j.state}</span>
      <button className="btn" onClick={clearBuild}>Dismiss</button></div>;
  }
  return <div style={{ marginTop: 10 }}>
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <span className="sub">{what}: {j?.message ?? "starting"}</span>
      {j && <button className="btn" onClick={() => api.cancelJob(j.id)}>Cancel</button>}
    </div>
    <div className="prog" aria-label="progress"><i style={{ width: `${Math.round(100 * (j?.progress ?? 0))}%` }} /></div>
  </div>;
}

const LOCK = <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>;

/** Replay or live: for the whole server, so it applies at once and not with Apply. */
function MarketData() {
  const m = useMeta().data;
  const qc = useQueryClient();
  const change = useMutation({ mutationFn: (on: boolean) => api.setLive(on), onSettled: () => qc.invalidateQueries({ queryKey: ["meta"] }) });
  const d = m?.data_mode;
  if (!d) return null;
  const j = d.setup;
  const setting = j && ["queued", "running"].includes(j.state);
  const last = m.storage?.live?.complete_through ?? m.storage?.last_day;
  const now = last ? `${d.live ? "Live, complete days to" : "Replay, to"} ${dayLabel(last)}` : d.live ? "Live" : "Replay";
  const ended = j && !setting ? (j.message ?? j.state).trim().split("\n").pop()!.replace(/^\w*Error: /, "") : null;
  return (
    <fieldset className="fs">
      <h2>Market data</h2>
      <div className="fld"><span>In use{!d.live && !d.locked && !setting && <span className="hint">first time: about 1 GB, a few minutes</span>}</span>
        <span style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}><b>{now}</b>
          {d.locked ? <Note note={`Not switchable: ${d.locked}.`} cls="lock tipc">{LOCK}locked</Note>
            : setting ? <button className="btn" onClick={() => api.cancelJob(j!.id).then(() => qc.invalidateQueries({ queryKey: ["meta"] }))}>Cancel</button>
              : <button className="btn" disabled={d.switching || change.isPending} onClick={() => change.mutate(!d.live)}>{d.live ? "Back to replay" : "Turn on live data"}</button>}
        </span></div>
      {d.switching && <p className="sub" style={{ marginTop: 10 }}>Switching: the new engines are loading, about a minute</p>}
      {setting && <div style={{ marginTop: 10 }}><span className="sub">{j!.message ?? "starting"}</span>
        <div className="prog" aria-label="progress"><i style={{ width: `${Math.round(100 * j!.progress)}%` }} /></div></div>}
      {(d.error || ended || change.error) && <p className="state err" role="alert" style={{ padding: "10px 0 0", textAlign: "left" }}>
        {d.error ?? (change.error as Error | null)?.message ?? ended}</p>}
    </fieldset>
  );
}

function Compared({ periods }: { periods: PeriodMoney[] }) {
  return (
    <table className="t" style={{ marginTop: 10 }}>
      <thead><tr><th>Period</th><th className="r">These settings</th><th className="r">Tested</th><th className="r">Difference</th></tr></thead>
      <tbody>{periods.map((p) => <tr key={p.period.kind}><td>{p.period.label}, {p.period.days} d</td>
        <td className="r">{aud(p.per_day_aud)}</td><td className="r">{aud(p.default_per_day_aud)}</td><td className="r">{signed(p.difference_aud)}</td></tr>)}</tbody>
    </table>
  );
}

function Rerun() {
  const { settings } = useApp();
  const [id, setId] = useState<number | null>(null);
  const start = useMutation({ mutationFn: () => api.startBacktest(settings), onSuccess: (r) => setId(r.id) });
  const job = useQuery({ queryKey: ["job", id], enabled: id != null, queryFn: () => api.job(id!),
    refetchInterval: (q) => (q.state.data && ["done", "failed", "cancelled"].includes(q.state.data.state) ? false : 1500) });
  const saved = useQuery({ queryKey: ["config", settings.connection, settings.dispatcher, settings.energyMwh, settings.cmdMw],
    enabled: !isTestedBattery(settings), queryFn: () => api.config(settings) });
  const j = job.data;
  const periods = j?.state === "done" && j.result ? j.result.periods : saved.data?.periods;
  return (
    <fieldset className="fs">
      <h2>Whole backtest with these settings</h2>
      <p className="sub">Runs the dispatcher through all 570 trading days (a few minutes) and compares with the tested settings, period by period.</p>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button className="btn" onClick={() => start.mutate()} disabled={start.isPending || j?.state === "running"}>Run backtest</button>
        {j?.state === "running" && <button className="btn" onClick={() => api.cancelJob(j.id)}>Cancel</button>}
        {j && <span className="sub">{j.state}{j.message ? `: ${j.message}` : ""}</span>}
      </div>
      {j && j.state === "running" && <div className="prog" aria-label="progress"><i style={{ width: `${Math.round(100 * j.progress)}%` }} /></div>}
      {periods && <Compared periods={periods} />}
      {start.error && <div className="state err">{(start.error as Error).message}</div>}
    </fieldset>
  );
}

function ResetDecisions({ offer }: { offer: string }) {
  const qc = useQueryClient();
  const m = useMutation({ mutationFn: () => api.resetDecisions(offer), onSuccess: () => qc.invalidateQueries() });
  return (
    <div className="fld"><span>Your call decisions</span>
      <span><button className="btn" onClick={() => m.mutate()}>Clear all, follow the model</button>{m.isSuccess && <span className="sub"> cleared</span>}</span></div>
  );
}
