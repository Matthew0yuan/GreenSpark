import { tw } from "./chart";
import { useWidth } from "./ui";
import { aud, nf } from "../lib/format";

const WF: Record<string, [string, string]> = { e: ["var(--dis)", "var(--on-dis)"], f: ["var(--fcess)", "var(--on-fcess)"],
  s: ["var(--surface-3)", "var(--text-2)"], b: ["var(--meter)", "var(--on-meter)"] };

/** Gross parts side by side (2 px gaps), wear hanging under the end of the gross, net below. A part whose label does not
 *  fit inside its bar is named in a line under the bars. */
export function Waterfall({ parts, wear, net, dim }: { parts: [string, string, number][]; wear: number; net: number; dim?: boolean }) {
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
            {!hidden.includes(b) && <text className="ann" x={b.x + 7} y={15} style={{ fill: WF[b.c][1], fontWeight: 500 }}>{b.text}</text>}
          </g>
        ))}
        {showWear && <g tabIndex={0} data-note={`Wear: −${aud(wear)}`}>
          <rect x={wearX} y={yW} width={wearW} height={16} rx={2} style={{ fill: "var(--bad)", opacity: 0.85 }} />
          <text className="ann" x={wearX - 6} y={yW + 12} textAnchor="end" style={{ fill: "var(--bad)", fontWeight: 500 }}>Wear −{nf(wear)}</text>
        </g>}
        <rect x={0} y={yN} width={netW} height={22} rx={3} style={{ fill: net < 0 ? "var(--bad)" : "var(--zen)" }} />
        <text className="ann" x={netIn ? 8 : netW + 8} y={yN + 15} style={{ fill: netIn ? "var(--on-zen)" : "var(--text)", fontWeight: 500 }}>{netText}</text>
        {hidden.map((b) => {
          const at = kx;
          kx += 14 + tw(b.text, "ann") + 16;
          return <g key={"k" + b.l}><rect x={at} y={keyY - 9} width={10} height={10} rx={2} style={{ fill: WF[b.c][0] }} /><text className="ann" x={at + 14} y={keyY}>{b.text}</text></g>;
        })}
      </svg>
    </div>
  );
}
