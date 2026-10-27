import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { StorageSettings } from "./api";

export type Page = "plan" | "evidence" | "call" | "value" | "settings";
export const ASSET_OF: Record<Page, "storage" | "fleet" | null> = { plan: "storage", evidence: "storage", call: "fleet", value: "fleet", settings: null };
export const DEFAULT_SETTINGS: StorageSettings = { fcess: ["reg_raise", "cont_raise", "reg_lower", "cont_lower"], startMwh: null, exportMw: null, importMw: null };
export const FIRST_DAY = "2025-04-01";
export const LAST_DAY = "2026-10-22";

type Ctx = {
  page: Page; go: (p: Page) => void; lastAsset: "storage" | "fleet"; setAsset: (a: "storage" | "fleet") => void;
  day: string; setDay: (d: string) => void;
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
  const [settings, setSettingsS] = useState<StorageSettings>(() => load("settings", DEFAULT_SETTINGS));
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
    day, setDay: (d) => { const x = d < FIRST_DAY ? FIRST_DAY : d > LAST_DAY ? LAST_DAY : d; setDayS(x); save("day", x); setRevealed(false); },
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

export const isDefault = (s: StorageSettings) =>
  s.fcess.length === 4 && s.startMwh == null && s.exportMw == null && s.importMw == null;
