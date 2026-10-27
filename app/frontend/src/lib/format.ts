export const N = 48;

export const nf = (v: number, d = 0) =>
  Number(v).toLocaleString("en-AU", { minimumFractionDigits: d, maximumFractionDigits: d });

/** A$ amount with a real minus sign. */
export const aud = (v: number | null | undefined, d = 0) =>
  v == null || !isFinite(v) ? "–" : (v < -0.0005 ? "−A$" : "A$") + nf(Math.abs(v), d);

export const signed = (v: number | null | undefined, d = 0) =>
  v == null || !isFinite(v) ? "–" : (v < 0 ? "−A$" : "+A$") + nf(Math.abs(v), d);

const p2 = (n: number) => (n < 10 ? "0" : "") + n;
/** Clock time at the start of half-hour k of the trading day (k = 0 is 08:00). */
export const hhmm = (k: number) => {
  const m = (480 + 30 * k) % 1440;
  return p2(Math.floor(m / 60)) + ":" + p2(m % 60);
};

export const isNum = (v: unknown): v is number => typeof v === "number" && isFinite(v);

export const sum = (a: (number | null)[]) => a.reduce<number>((t, v) => t + (v ?? 0), 0);

export function dayLabel(iso: string, year = true) {
  const d = new Date(iso + "T00:00:00Z");
  return d.toLocaleDateString("en-AU", { weekday: "short", day: "numeric", month: "short", year: year ? "numeric" : undefined, timeZone: "UTC" }).replace(",", "");
}

export function addDays(iso: string, n: number) {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export const periodOf = (iso: string) => (iso < "2026-07-01" ? "TRAIN" : iso < "2026-10-16" ? "TREV" : "TBLIND");
