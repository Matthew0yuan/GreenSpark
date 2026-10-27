/* Shared geometry for the trading-day charts: one x axis, 08:00 to 08:00 the next day, 48 half-hours, so every chart
   on a page lines up (STYLE_GUIDE.md section 5). Row names sit left of X0. */
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
