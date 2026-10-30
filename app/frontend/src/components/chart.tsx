/* Shared geometry for the trading-day charts: one x axis, 08:00 to 08:00 the next day, 48 half-hours, so every chart
   on a page lines up (STYLE_GUIDE.md section 5). Row names sit left of X0; an axis title sits in its own band above the
   plot at x = 0, never inside it. */
import type { ReactNode } from "react";
import { hhmm, isNum, N } from "../lib/format";

export { N };
export const X0 = 104;
export const RM = 56;

export type Geo = { W: number; X: (k: number) => number; IW: number };
export function geo(W: number): Geo {
  const IW = (W - RM - X0) / N;
  return { W, IW, X: (k) => X0 + k * IW };
}

/** Step line through half-hour values (each value spans its half-hour); gaps at nulls. */
export function stepPath(g: Geo, vals: (number | null)[], y: (v: number) => number, from = 0, to = N) {
  let d = "", open = false;
  for (let k = from; k < to; k++) {
    const v = vals[k];
    if (!isNum(v)) { open = false; continue; }
    const yy = y(v).toFixed(2);
    d += (open ? "L" : "M") + g.X(k).toFixed(2) + "," + yy + "L" + g.X(k + 1).toFixed(2) + "," + yy;
    open = true;
  }
  return d;
}

/** Band between two step series (e.g. P10–P90). */
export function bandPath(g: Geo, lo: (number | null)[], hi: (number | null)[], y: (v: number) => number, from = 0, to = N) {
  let d = "";
  let k = from;
  while (k < to) {
    if (!isNum(lo[k]) || !isNum(hi[k])) { k++; continue; }
    let e = k;
    while (e < to && isNum(lo[e]) && isNum(hi[e])) e++;
    let top = "", bot = "";
    for (let j = k; j < e; j++) top += `L${g.X(j).toFixed(2)},${y(hi[j]!).toFixed(2)}L${g.X(j + 1).toFixed(2)},${y(hi[j]!).toFixed(2)}`;
    for (let j = e - 1; j >= k; j--) bot += `L${g.X(j + 1).toFixed(2)},${y(lo[j]!).toFixed(2)}L${g.X(j).toFixed(2)},${y(lo[j]!).toFixed(2)}`;
    d += "M" + top.slice(1) + bot + "Z";
    k = e;
  }
  return d;
}

/** Line through points at half-hour boundaries (stored energy: 49 points). */
export function linePath(g: Geo, vals: (number | null)[], y: (v: number) => number, from = 0, to = N) {
  let d = "", open = false;
  for (let b = from; b <= to; b++) {
    const v = vals[b];
    if (!isNum(v)) { open = false; continue; }
    d += (open ? "L" : "M") + g.X(b).toFixed(2) + "," + y(v).toFixed(2);
    open = true;
  }
  return d;
}

export function Hatch({ id }: { id: string }) {
  return (
    <pattern id={id} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="6" height="6" style={{ fill: "var(--hatch-bg)" }} />
      <line x1="0" y1="0" x2="0" y2="6" style={{ stroke: "var(--hatch-ln)" }} strokeWidth="2" />
    </pattern>
  );
}

/** Time axis under a panel: ticks every 4 hours, dates under the first and the midnight tick. */
export function DayAxis({ g, y, day, next }: { g: Geo; y: number; day?: string; next?: string }) {
  const out: ReactNode[] = [<line key="ax" x1={X0} x2={g.X(N)} y1={y} y2={y} style={{ stroke: "var(--axis)" }} />];
  for (let k = 0; k <= N; k += 8) {
    out.push(<line key={"t" + k} x1={g.X(k)} x2={g.X(k)} y1={y} y2={y + 4} style={{ stroke: "var(--axis)" }} />);
    out.push(<text key={"l" + k} x={g.X(k)} y={y + 15} textAnchor={k === 0 ? "start" : k === N ? "end" : "middle"}>{hhmm(k)}</text>);
  }
  if (day) out.push(<text key="d0" x={g.X(0)} y={y + 28}>{day}</text>);
  if (next) out.push(<text key="d1" x={g.X(32)} y={y + 28} textAnchor="middle">{next}</text>);
  return <g>{out}</g>;
}

/** Light horizontal grid with value labels at the left. */
export function YGrid({ g, ticks, y, fmt = (v: number) => String(v) }: { g: Geo; ticks: number[]; y: (v: number) => number; fmt?: (v: number) => string }) {
  return (
    <g>
      {ticks.map((t) => (
        <g key={t}>
          <line className="gl" x1={X0} x2={g.X(N)} y1={y(t)} y2={y(t)} />
          <text x={X0 - 8} y={y(t) + 3.5} textAnchor="end">{fmt(t)}</text>
        </g>
      ))}
    </g>
  );
}

/* ------------------------------------------------------------------------- measured labels, placed clear of lines */
/** Size and weight of each chart text style, as in styles.css (.chart text, .ann, .lt, .act, .mk9, .ttl). */
export const FONT = { tick: [10.5, 400], ann: [11, 500], annB: [11, 600], lt: [12, 500], ltB: [12, 600], act: [11, 600], mk9: [10, 700], ttl: [11, 600] } as const;
export type Font = keyof typeof FONT;
let cv: CanvasRenderingContext2D | null | undefined;

/** Rendered width of a label in px, measured in the chart font rather than guessed from its length. */
export function tw(s: string, f: Font = "tick") {
  const [size, weight] = FONT[f];
  if (cv === undefined) cv = document.createElement("canvas").getContext("2d");
  if (!cv) return s.length * size * 0.6;
  cv.font = `${weight} ${size}px Inter, "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif`;
  return cv.measureText(s).width + (f === "mk9" ? s.length * size * 0.04 : 0);
}

export type Anchor = "start" | "middle" | "end";
export type Box = { x0: number; y0: number; x1: number; y1: number };
/** The box an SVG text takes at baseline y (Inter: ascent 0.97 em, descent 0.25 em, as getBBox reports it). */
export function textBox(s: string, f: Font, x: number, y: number, a: Anchor = "start"): Box {
  const w = tw(s, f), size = FONT[f][0];
  const x0 = a === "start" ? x : a === "middle" ? x - w / 2 : x - w;
  return { x0, x1: x0 + w, y0: y - size * 0.97, y1: y + size * 0.25 };
}

/** A drawn line as points; null breaks it. */
export type Pt = [number, number] | null;
export const stepPts = (g: Geo, vals: (number | null)[], y: (v: number) => number, from = 0, to = N): Pt[] => {
  const out: Pt[] = [];
  for (let k = from; k < to; k++) {
    const v = vals[k];
    if (isNum(v)) out.push([g.X(k), y(v)], [g.X(k + 1), y(v)]); else out.push(null);
  }
  return out;
};
export const linePts = (g: Geo, vals: (number | null)[], y: (v: number) => number, from = 0, to = N): Pt[] =>
  Array.from({ length: to - from + 1 }, (_, i) => { const v = vals[from + i]; return isNum(v) ? [g.X(from + i), y(v)] as Pt : null; });
export const segPts = (x0: number, y0: number, x1: number, y1: number): Pt[] => [[x0, y0], [x1, y1]];

/** Does a line pass through a box? Exact for straight segments, including vertical ones. */
function crosses(pts: Pt[], b: Box) {
  for (let i = 1; i < pts.length; i++) {
    const p = pts[i - 1], q = pts[i];
    if (!p || !q) continue;
    let [xa, ya] = p, [xb, yb] = q;
    if (xa > xb) [xa, ya, xb, yb] = [xb, yb, xa, ya];
    if (xb < b.x0 || xa > b.x1) continue;
    let y0 = ya, y1 = yb;
    if (xb > xa) {
      const at = (x: number) => ya + ((yb - ya) * (x - xa)) / (xb - xa);
      y0 = at(Math.max(xa, b.x0)); y1 = at(Math.min(xb, b.x1));
    }
    if (Math.max(y0, y1) >= b.y0 && Math.min(y0, y1) <= b.y1) return true;
  }
  return false;
}

/** Places a chart's labels in priority order: each takes the first candidate spot inside the bounds that covers no
 *  placed label, no blocked box and no drawn line. A label with no free spot is left out; its value stays in the hover
 *  table. */
export class Placer {
  private boxes: Box[] = [];
  private lines: Pt[][] = [];
  private bounds: Box;
  private pad: number;
  constructor(bounds: Box, pad = 2) { this.bounds = bounds; this.pad = pad; }
  block(b: Box) { this.boxes.push(b); return this; }
  line(pts: Pt[]) { this.lines.push(pts); return this; }
  free(b: Box) {
    const p = this.pad, B = this.bounds;
    if (b.x0 < B.x0 || b.x1 > B.x1 || b.y0 < B.y0 || b.y1 > B.y1) return false;
    if (this.boxes.some((o) => b.x0 < o.x1 + p && o.x0 < b.x1 + p && b.y0 < o.y1 + p && o.y0 < b.y1 + p)) return false;
    const q = { x0: b.x0 - p, x1: b.x1 + p, y0: b.y0 - p, y1: b.y1 + p };
    return !this.lines.some((l) => crosses(l, q));
  }
  place(s: string, f: Font, cands: [number, number, Anchor][]): { x: number; y: number; a: Anchor } | null {
    for (const [x, y, a] of cands) {
      const b = textBox(s, f, x, y, a);
      if (this.free(b)) { this.boxes.push(b); return { x, y, a }; }
    }
    return null;
  }
}

/** Candidate spots for a label on a point: above or below (centred, then to either side), then beside it. */
export function around(x: number, y: number, f: Font = "ann", below = false, gap = 5): [number, number, Anchor][] {
  const size = FONT[f][0];
  const up = y - gap - size * 0.25, dn = y + gap + size * 0.97, mid = y + size * 0.36;
  const a: [number, number, Anchor][] = [[x, up, "middle"], [x + 2, up, "start"], [x - 2, up, "end"]];
  const b: [number, number, Anchor][] = [[x, dn, "middle"], [x + 2, dn, "start"], [x - 2, dn, "end"]];
  return [...(below ? [...b, ...a] : [...a, ...b]), [x + gap + 3, mid, "start"], [x - gap - 3, mid, "end"]];
}

/** A horizontal bar from base to end, rounded at the data end only, square at the baseline. */
export function hbar(base: number, end: number, y: number, h: number, r = 3) {
  const w = Math.abs(end - base);
  if (w < 0.5) return "";
  r = Math.min(r, w, h / 2);
  const s = end >= base ? 1 : -1, e = end - s * r;
  return `M${base},${y}H${e}Q${end},${y} ${end},${y + r}V${y + h - r}Q${end},${y + h} ${e},${y + h}H${base}Z`;
}

/** A column from base up to top, rounded at the top only. */
export function vbar(x: number, w: number, top: number, base: number, r = 2) {
  const h = base - top;
  if (h < 0.5) return "";
  r = Math.min(r, h, w / 2);
  return `M${x},${base}V${top + r}Q${x},${top} ${x + r},${top}H${x + w - r}Q${x + w},${top} ${x + w},${top + r}V${base}Z`;
}
