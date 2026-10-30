import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Connection, type DispatcherKey, type StorageSettings } from "./api";

export type Page = "plan" | "evidence" | "call" | "value" | "settings";
export const ASSET_OF: Record<Page, "storage" | "fleet" | null> = { plan: "storage", evidence: "storage", call: "fleet", value: "fleet", settings: null };
export const DEFAULT_SETTINGS: StorageSettings = { connection: "factory", dispatcher: "e5", fcess: ["reg_raise", "cont_raise", "reg_lower", "cont_lower"],
  startMwh: null, exportMw: null, importMw: null };
/** The dispatcher as the user calls it: e5 is v2's adopted version. */
export const DISP: Record<DispatcherKey, string> = { e5: "v2", v1: "v1" };
export const CONN: Record<Connection, string> = { factory: "factory meter", standalone: "own connection" };

/** The parties and the asset named under each storage page's title (Who). */
export function whoItems(connection: Connection): [string, string][] {
  return [["ZENITH", "Zenith · runs the network"], ["NETWORK", "storage network · 10 MW / 40 MWh"],
    ...(connection === "factory" ? [["FACTORY", "factory · CMD 9 MW"] as [string, string]] : []),
    ["AEMO", "AEMO · market operator"], ["WEM", "WEM · WA wholesale market"]];
}

/** Saved settings from before the connection option keep working: missing or unknown fields take the defaults. */
function settingsFrom(v: Partial<StorageSettings> | null): StorageSettings {
  const s = { ...DEFAULT_SETTINGS, ...(v ?? {}) };
  if (!(s.connection in CONN)) s.connection = DEFAULT_SETTINGS.connection;
  if (!(s.dispatcher in DISP)) s.dispatcher = DEFAULT_SETTINGS.dispatcher;
  return s;
}
export const FIRST_DAY = "2025-04-01";
/** The saved backtests' last day: the replay ends here, and live data (app/live) start the day after. */
export const LAST_DAY = "2026-10-22";

/** /meta, shared: polled while the engines warm up, and every 30 s on live data (new 5-minute prices, new days). */
export function useMeta() {
  return useQuery({ queryKey: ["meta"], queryFn: api.meta, staleTime: 0,
    refetchInterval: (q) => {
      const m = q.state.data;
      if (!m || !Object.values(m.engines).every((e) => e.state === "ready")) return 2000;
      return m.live ? 30_000 : false;
    } });
}

/** What the live data say about a day: the trading day in progress, or a complete day after the saved backtests. */
export function useLive(day?: string) {
  const m = useMeta().data;
  const sl = m?.storage?.live ?? null;
  const today = sl?.today && !sl.today.complete ? sl.today : null;
  return { on: !!m?.live, lastDay: m?.storage?.last_day ?? LAST_DAY, liveDay: today ? today.day : null,
    asOf: today?.as_of ?? null, builtAt: sl?.built_at ?? null, isToday: !!today && day === today.day,
    after: !!day && day > LAST_DAY };
}

type Ctx = {
  page: Page; go: (p: Page) => void; lastAsset: "storage" | "fleet"; setAsset: (a: "storage" | "fleet") => void;
  day: string; setDay: (d: string) => void; lastDay: string;
  settings: StorageSettings; setSettings: (s: StorageSettings) => void;
  offer: "ref" | "custom"; setOffer: (o: "ref" | "custom") => void;
  revealed: boolean; setRevealed: (v: boolean) => void;
};
const C = createContext<Ctx | null>(null);

/** Per-viewer conveniences only (day, settings); the app works without storage. */
function load<T>(k: string, d: T): T {
  try { const v = localStorage.getItem("gs:" + k); return v ? (JSON.parse(v) as T) : d; } catch { return d; }
}
function save(k: string, v: unknown) {
  try { localStorage.setItem("gs:" + k, JSON.stringify(v)); } catch { /* private window */ }
}

const pageFromHash = (): Page => {
  const h = location.hash.replace(/^#\/?/, "").split("?")[0];
  return (["plan", "evidence", "call", "value", "settings"].includes(h) ? h : "plan") as Page;
};

export function AppState({ children }: { children: ReactNode }) {
  const [page, setPage] = useState<Page>(pageFromHash);
  const [lastAsset, setLastAsset] = useState<"storage" | "fleet">(ASSET_OF[pageFromHash()] ?? "storage");
  const [day, setDayS] = useState<string>(() => load("day", LAST_DAY));
  const { lastDay, on } = useLive();
  // on live data the app opens on the newest day, unless the viewer picked one before
  const picked = useRef(load<string | null>("day", null) != null);
  useEffect(() => { if (on && !picked.current) setDayS(lastDay); }, [on, lastDay]);
  const [settings, setSettingsS] = useState<StorageSettings>(() => settingsFrom(load<Partial<StorageSettings> | null>("settings", null)));
  const [offer, setOfferS] = useState<"ref" | "custom">(() => load("offer", "custom"));
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    const f = () => { const p = pageFromHash(); setPage(p); if (ASSET_OF[p]) setLastAsset(ASSET_OF[p]!); };
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);

  const go = (p: Page) => { location.hash = "/" + p; };
  const value: Ctx = {
    page, go, lastAsset,
    setAsset: (a) => go(a === "storage" ? "plan" : "call"),
    day, lastDay,
    setDay: (d) => {
      const x = d < FIRST_DAY ? FIRST_DAY : d > lastDay ? lastDay : d;
      picked.current = true; setDayS(x); save("day", x); setRevealed(false);
    },
    settings, setSettings: (s) => { setSettingsS(s); save("settings", s); },
    offer, setOffer: (o) => { setOfferS(o); save("offer", o); },
    revealed, setRevealed,
  };
  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useApp() {
  const c = useContext(C);
  if (!c) throw new Error("outside AppState");
  return c;
}

/** No setting that perturbs the tested runs: each connection x dispatcher has its own saved backtest. */
export const isDefault = (s: StorageSettings) =>
  s.fcess.length === 4 && s.startMwh == null && s.exportMw == null && s.importMw == null;
