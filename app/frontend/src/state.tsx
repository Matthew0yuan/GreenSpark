import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Connection, type DispatcherKey, type Job, type StorageSettings } from "./api";

export type Page = "plan" | "evidence" | "call" | "value" | "settings";
export const ASSET_OF: Record<Page, "storage" | "fleet" | null> = { plan: "storage", evidence: "storage", call: "fleet", value: "fleet", settings: null };
export const DEFAULT_SETTINGS: StorageSettings = { connection: "factory", dispatcher: "e5", fcess: ["reg_raise", "cont_raise", "reg_lower", "cont_lower"],
  startMwh: null, exportMw: null, importMw: null, energyMwh: null, cmdMw: null };
/** The dispatcher as the user calls it: e5 is v2's adopted version. */
export const DISP: Record<DispatcherKey, string> = { e5: "v2", v1: "v1" };
export const CONN: Record<Connection, string> = { factory: "factory meter", standalone: "own connection" };

/** The battery and CMD in use: Settings', else the tested ones (/meta; undefined until it has loaded). */
export function useBattery() {
  const { settings } = useApp();
  const a = useMeta().data?.storage?.asset;
  return { powerMw: a?.power_mw, energyMwh: settings.energyMwh ?? a?.energy_mwh, cmdMw: settings.cmdMw ?? a?.site?.cmd_mw,
    label: a ? `${a.power_mw} MW / ${settings.energyMwh ?? a.energy_mwh} MWh` : "" };
}

/** The parties and the asset named under each storage page's title (Who). */
export function useWho(): [string, string][] {
  const { settings } = useApp();
  const b = useBattery();
  return [["ZENITH", "Zenith, runs the network"], ["NETWORK", b.label ? `storage network, ${b.label}` : "storage network"],
    ...(settings.connection === "factory" ? [["FACTORY", b.cmdMw != null ? `factory, CMD ${b.cmdMw} MW` : "factory"] as [string, string]] : []),
    ["AEMO", "AEMO, market operator"], ["WEM", "WEM, WA wholesale market"]];
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
      const d = m.data_mode;
      if (d.switching || d.setup?.state === "running" || d.setup?.state === "queued") return 1500;
      return m.live ? 30_000 : false;
    } });
}

/** What the live data say about a day: the trading day in progress, or a complete day after the saved backtests. */
export function useLive(day?: string) {
  const m = useMeta().data;
  const sl = m?.storage?.live ?? null;
  const today = sl?.today && !sl.today.complete ? sl.today : null;
  return { on: !!m?.live, loaded: !!m?.storage, lastDay: m?.storage?.last_day ?? LAST_DAY, liveDay: today ? today.day : null,
    asOf: today?.as_of ?? null, builtAt: sl?.built_at ?? null, isToday: !!today && day === today.day,
    after: !!day && day > LAST_DAY };
}

type Ctx = {
  page: Page; go: (p: Page) => void; lastAsset: "storage" | "fleet"; setAsset: (a: "storage" | "fleet") => void;
  day: string; setDay: (d: string) => void; lastDay: string;
  settings: StorageSettings; setSettings: (s: StorageSettings) => void;
  offer: "ref" | "custom"; setOffer: (o: "ref" | "custom") => void;
  revealed: boolean; setRevealed: (v: boolean) => void;
  /** A battery and CMD's backtest under way: its settings apply when it is done, on whichever page the viewer is. */
  build: { job: Job | undefined; settings: StorageSettings } | null;
  startBuild: (id: number, s: StorageSettings) => void; clearBuild: () => void;
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
  const [settings, setSettingsS] = useState<StorageSettings>(() => settingsFrom(load<Partial<StorageSettings> | null>("settings", null)));
  const live = useLive();
  // a battery and CMD of the viewer's own has its backtest to the saved days only, no live days
  const ownBattery = !isTestedBattery(settings);
  const lastDay = ownBattery && live.lastDay > LAST_DAY ? LAST_DAY : live.lastDay;
  const on = live.on;
  useEffect(() => { if (ownBattery && day > LAST_DAY) setDayS(LAST_DAY); }, [ownBattery, day]);
  // on live data the app opens on the newest day, unless the viewer picked one before
  const picked = useRef(load<string | null>("day", null) != null);
  useEffect(() => { if (on && !picked.current) setDayS(lastDay); }, [on, lastDay]);
  const [revealed, setRevealed] = useState(false);
  const qc = useQueryClient();
  // the market data switched (Settings): every answer may differ. On to live, the newest day; a day past the data, the last
  const wasLive = useRef<boolean | null>(null);
  useEffect(() => {
    if (!live.loaded) return;
    if (wasLive.current !== null && wasLive.current !== on) {
      qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "meta" });
      setRevealed(false);
      if (on) setDayS(lastDay);
    }
    wasLive.current = on;
  }, [live.loaded, on, lastDay, qc]);
  useEffect(() => { if (live.loaded && day > lastDay) { setDayS(lastDay); setRevealed(false); } }, [live.loaded, day, lastDay]);
  const [offer, setOfferS] = useState<"ref" | "custom">(() => load("offer", "custom"));

  useEffect(() => {
    const f = () => { const p = pageFromHash(); setPage(p); if (ASSET_OF[p]) setLastAsset(ASSET_OF[p]!); };
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);

  const setSettings = (s: StorageSettings) => { setSettingsS(s); save("settings", s); };
  const [building, setBuilding] = useState<{ id: number; settings: StorageSettings } | null>(() => load("build", null));
  const keepBuild = (b: { id: number; settings: StorageSettings } | null) => { setBuilding(b); save("build", b); };
  const job = useQuery({ queryKey: ["job", building?.id], enabled: building != null, queryFn: () => api.job(building!.id), retry: false,
    refetchInterval: (q) => (q.state.data && !["queued", "running"].includes(q.state.data.state) ? false : 1500) });
  useEffect(() => {
    if (building && job.error) keepBuild(null);  // no such job any more
    if (building && job.data?.id === building.id && job.data.state === "done") {
      setSettings(building.settings);
      keepBuild(null);
      qc.invalidateQueries();  // a backtest built again keeps its query keys: Evidence would stay on the old one
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs on the job's news only
  }, [job.data?.state, job.error]);

  const go = (p: Page) => { location.hash = "/" + p; };
  const value: Ctx = {
    page, go, lastAsset,
    setAsset: (a) => go(a === "storage" ? "plan" : "call"),
    day, lastDay,
    setDay: (d) => {
      const x = d < FIRST_DAY ? FIRST_DAY : d > lastDay ? lastDay : d;
      picked.current = true; setDayS(x); save("day", x); setRevealed(false);
    },
    settings, setSettings,
    offer, setOffer: (o) => { setOfferS(o); save("offer", o); },
    revealed, setRevealed,
    build: building && { job: job.data?.id === building.id ? job.data : undefined, settings: building.settings },
    startBuild: (id, s) => keepBuild({ id, settings: s }), clearBuild: () => keepBuild(null),
  };
  return <C.Provider value={value}>{children}</C.Provider>;
}

export function useApp() {
  const c = useContext(C);
  if (!c) throw new Error("outside AppState");
  return c;
}

/** The battery and CMD the saved backtests were run with. */
export const isTestedBattery = (s: StorageSettings) => s.energyMwh == null && s.cmdMw == null;

/** No setting that perturbs the tested runs: each connection x dispatcher has its own saved backtest. */
export const isDefault = (s: StorageSettings) =>
  s.fcess.length === 4 && s.startMwh == null && s.exportMw == null && s.importMw == null && isTestedBattery(s);
