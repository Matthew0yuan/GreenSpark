import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { NotePopover } from "./components/ui";
import Call from "./pages/Call";
import Evidence from "./pages/Evidence";
import Plan from "./pages/Plan";
import Settings from "./pages/Settings";
import Value from "./pages/Value";
import { ASSET_OF, isDefault, useApp, type Page } from "./state";

const PAGES: Record<"storage" | "fleet", [Page, string][]> = {
  storage: [["plan", "Plan"], ["evidence", "Evidence"]],
  fleet: [["call", "Call"], ["value", "Value"]],
};

export default function App() {
  const { page, go, lastAsset, setAsset, settings } = useApp();
  const asset = ASSET_OF[page] ?? lastAsset;
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta,
    refetchInterval: (q) => (q.state.data && Object.values(q.state.data.engines).every((e) => e.state === "ready") ? false : 2000) });
  const eng = meta.data?.engines ?? {};
  const states = Object.values(eng).map((e) => e.state);
  const ready = states.length > 0 && states.every((s) => s === "ready");
  const failed = states.includes("failed");

  return (
    <>
      <header className="topbar">
        <div className="tb-in">
          <div className="brand"><span className="mk" aria-hidden="true">GS</span>GreenSpark</div>
          <div className="assets" role="tablist" aria-label="Asset">
            <button role="tab" aria-selected={asset === "storage"} className={asset === "storage" ? "on" : ""} onClick={() => setAsset("storage")}>
              <b>Storage network</b><small>10 MW / 40 MWh</small></button>
            <button role="tab" aria-selected={asset === "fleet"} className={asset === "fleet" ? "on" : ""} onClick={() => setAsset("fleet")}>
              <b>Customer batteries</b><small>VPP calls</small></button>
          </div>
          <nav className="pages" aria-label="Pages">
            {PAGES[asset].map(([p, l]) => <button key={p} className={page === p ? "on" : ""} aria-current={page === p ? "page" : undefined} onClick={() => go(p)}>{l}</button>)}
          </nav>
          <span className="sp" />
          <span className={"engine" + (failed ? " bad" : ready ? "" : " warm")} data-note="Every result is a replay of past trading days (to 22 Oct 2026) by the model; nothing is live and no device is controlled." tabIndex={0}>
            <i />{failed ? "engine failed" : ready ? (isDefault(settings) ? "replay · up to date" : "replay · changed settings") : "engine warming up"}
          </span>
          <button className={"gear" + (page === "settings" ? " on" : "")} aria-label="Settings" onClick={() => go("settings")}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z" /></svg>
          </button>
        </div>
      </header>
      <main className="wrap">
        {failed && <div className="blocked" role="alert">Engine failed to start: {Object.values(eng).map((e) => e.error).filter(Boolean).join("; ")}</div>}
        {page === "plan" && <Plan />}
        {page === "evidence" && <Evidence />}
        {page === "call" && <Call />}
        {page === "value" && <Value />}
        {page === "settings" && <Settings />}
      </main>
      <NotePopover />
    </>
  );
}
