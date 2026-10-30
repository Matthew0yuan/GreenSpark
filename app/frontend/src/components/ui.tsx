import { useEffect, useRef, useState, type ReactNode } from "react";
import { GLOSS, glNote } from "../lib/gloss";
import { addDays, dayLabel } from "../lib/format";
import { FIRST_DAY, useApp } from "../state";

/** A term with its glossary note on hover (keyboard-focusable). */
export function Gl({ k, children, cls = "tipc" }: { k: string; children?: ReactNode; cls?: string }) {
  return <span className={cls} tabIndex={0} data-note={glNote(k)}>{children ?? GLOSS[k]?.[0] ?? k}</span>;
}

/** Any text with a hover note. */
export function Note({ note, children, cls = "tipc" }: { note: string; children: ReactNode; cls?: string }) {
  return <span className={cls} tabIndex={0} data-note={note}>{children}</span>;
}

export function Chip({ children, note, cls = "" }: { children: ReactNode; note?: string; cls?: string }) {
  return note ? <span className={"chip " + cls} tabIndex={0} data-note={note}>{children}</span> : <span className={"chip " + cls}>{children}</span>;
}

/** The single floating note for every [data-note] element (hover or focus). */
export function NotePopover() {
  const [s, setS] = useState<{ t: string; x: number; y: number } | null>(null);
  useEffect(() => {
    const show = (e: Event) => {
      const el = (e.target as HTMLElement).closest?.("[data-note]") as HTMLElement | null;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setS({ t: el.dataset.note || "", x: Math.min(r.left, window.innerWidth - 336), y: r.bottom + 6 });
    };
    const hide = (e: Event) => { if ((e.target as HTMLElement).closest?.("[data-note]")) setS(null); };
    document.addEventListener("mouseover", show);
    document.addEventListener("focusin", show);
    document.addEventListener("mouseout", hide);
    document.addEventListener("focusout", hide);
    return () => {
      document.removeEventListener("mouseover", show); document.removeEventListener("focusin", show);
      document.removeEventListener("mouseout", hide); document.removeEventListener("focusout", hide);
    };
  }, []);
  if (!s) return null;
  const y = s.y + 120 > window.innerHeight ? s.y - 130 : s.y;
  return <div className="note-pop" style={{ left: Math.max(8, s.x), top: y }} role="tooltip">{s.t}</div>;
}

/** Previous / next day, and the date itself opens the calendar (the native field stays hidden behind it). */
export function DayPicker({ day, setDay }: { day: string; setDay: (d: string) => void }) {
  const { lastDay: LAST_DAY } = useApp();
  const inp = useRef<HTMLInputElement>(null);
  const pick = () => { const el = inp.current; if (!el) return; try { el.showPicker(); } catch { el.focus(); } };
  return (
    <span className="daypick">
      <button className="arr" aria-label="Previous trading day" disabled={day <= FIRST_DAY} onClick={() => setDay(addDays(day, -1))}>‹</button>
      <span className="cal">
        <button className="d" onClick={pick} aria-label={`${dayLabel(day)}, pick a trading day`}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="2" /><path d="M3.5 10h17M8 3v4M16 3v4" /></svg>
          {dayLabel(day)}
        </button>
        <input ref={inp} type="date" tabIndex={-1} aria-label="Pick a trading day" min={FIRST_DAY} max={LAST_DAY} value={day} onChange={(e) => e.target.value && setDay(e.target.value)} />
      </span>
      <button className="arr" aria-label="Next trading day" disabled={day >= LAST_DAY} onClick={() => setDay(addDays(day, 1))}>›</button>
    </span>
  );
}

export function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: [T, ReactNode][]; onChange: (v: T) => void; label: string }) {
  return (
    <span className="seg" role="radiogroup" aria-label={label}>
      {options.map(([v, l]) => (
        <button key={v} role="radio" aria-checked={v === value} className={v === value ? "on" : ""} onClick={() => onChange(v)}>{l}</button>
      ))}
    </span>
  );
}

export function Loading({ what }: { what: string }) {
  return <div className="state" aria-busy="true"><div className="skel" style={{ maxWidth: 420, margin: "0 auto 10px" }} />Computing {what}…</div>;
}

export function Failed({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="state err" role="alert">Not available · {msg}</div>;
}

/** Width of a container, for SVG charts drawn in pixel units; drawn again once the web font has loaded, because labels
 *  are placed by their measured width. */
export function useWidth<T extends HTMLElement>(min = 640): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(1208);
  const [, setFonts] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(min, Math.floor(el.clientWidth))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [min]);
  useEffect(() => { let on = true; document.fonts?.ready.then(() => on && setFonts((n) => n + 1)); return () => { on = false; }; }, []);
  return [ref, w];
}

/** The chart hover table: a title (time or name) and at most 5 one- or two-word rows. */
export type TipData = { x: number; y: number; title: string; rows: [string, string][] } | null;
export function ChartTip({ tip }: { tip: TipData }) {
  if (!tip) return null;
  const left = Math.min(tip.x + 14, window.innerWidth - 244);
  return (
    <div className="ctip" style={{ left, top: tip.y + 14 }} role="tooltip">
      <h4>{tip.title}</h4>
      <table><tbody>{tip.rows.slice(0, 5).map(([a, b]) => <tr key={a}><td>{a}</td><td>{b}</td></tr>)}</tbody></table>
    </div>
  );
}

/** Page header: title and chips, one context line ending in "Who is who" under it; day and view controls on the right
 *  (under both when the page is narrow). */
export function PageHead({ title, chips, ctx, who, right }: { title: string; chips?: ReactNode; ctx?: ReactNode; who?: [string, string][]; right?: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <header className="ph">
      <div className="ph-main">
        <div className="ph-line"><h1>{title}</h1>{chips && <span className="chips">{chips}</span>}</div>
        {(ctx || who) && (
          <div className="ctx">
            {ctx}
            {who && <>{ctx ? " · " : null}<button className="who-b" aria-expanded={open} onClick={() => setOpen(!open)}>Who is who</button></>}
          </div>
        )}
        {who && open && <div className="chips who-chips">{who.map(([k, l]) => <Chip key={k} note={glNote(k)}>{l}</Chip>)}</div>}
      </div>
      {right && <div className="ph-r">{right}</div>}
    </header>
  );
}
