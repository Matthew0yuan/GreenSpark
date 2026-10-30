import type { XPoint, XValue } from "../api";

/** The numbers a reason compares: value in bold, its short label after, operators between; a label with more to say
 *  carries a hover note. */
export function Nums({ nums }: { nums: XPoint["nums"] }) {
  if (!nums.length) return null;
  return (
    <span className="nums">
      {nums.map((n, i) => ("op" in n
        ? <span key={i} className="op">{n.op}</span>
        : <Value key={i} x={n} />))}
    </span>
  );
}

function Value({ x }: { x: XValue }) {
  return x.note
    ? <span className="nm tipc" tabIndex={0} data-note={x.note}><b>{x.v}</b><i>{x.l}</i></span>
    : <span className="nm"><b>{x.v}</b><i>{x.l}</i></span>;
}

/** One reason on one line: lead, plain phrase, numbers (right-aligned, below the phrase when narrow). */
export function Point({ p }: { p: XPoint }) {
  return (
    <div className="pt">
      {p.say && <b className="pt-s">{p.say}</b>}
      <span className="pt-w">{p.why}</span>
      <Nums nums={p.nums} />
    </div>
  );
}

/** The panel head shared by the Why panels: title, context, close. */
export function WhyHead({ title, sub, extra, close }: { title: string; sub?: string; extra?: React.ReactNode; close: () => void }) {
  return (
    <header className="wp-h">
      <h3>{title}</h3>
      {sub && <span className="sub">{sub}</span>}
      {extra}
      <span className="sp" />
      <button className="wp-x" aria-label="Close" onClick={close}>×</button>
    </header>
  );
}
