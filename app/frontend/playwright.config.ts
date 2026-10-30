import { defineConfig } from "@playwright/test";

// The app on 127.0.0.1:8000 (backend + built frontend). Started here unless already running; the engines take ~30 s
// to load. Uses the installed Chrome (no browser download). The tests are written for the replay (to 2026-10-22): a
// server started here runs with GS_LIVE=0; an app already running on live data fails the day-range tests.
export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:8000", channel: "chrome", viewport: { width: 1440, height: 900 } },
  webServer: {
    command: process.platform === "win32" ? ".venv\\Scripts\\python.exe -m app.backend --port 8000" : ".venv/bin/python -m app.backend --port 8000",
    cwd: "../..",
    env: { GS_LIVE: "0" },
    url: "http://127.0.0.1:8000/api/v1/meta",
    reuseExistingServer: true,
    timeout: 180_000,
  },
});
