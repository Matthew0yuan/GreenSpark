// Screenshots of every page against a running backend: node e2e/shots.mjs [outDir] [baseUrl] [day] [factory|standalone]
// (standalone shots are named <page>_<width>_own.png)
import { chromium } from "@playwright/test";

const out = process.argv[2] ?? "../shots";
const base = process.argv[3] ?? "http://127.0.0.1:8000";
const day = process.argv[4] ?? "2026-01-20";
const conn = process.argv[5] ?? "factory";
const settings = { connection: conn, dispatcher: "e5", fcess: ["reg_raise", "cont_raise", "reg_lower", "cont_lower"], startMwh: null, exportMw: null, importMw: null };
const browser = await chromium.launch({ channel: "chrome" });
for (const [w, h] of [[1440, 900], [1280, 720], [1024, 900]]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  await page.goto(base + "/#/plan");
  await page.evaluate(([d, s]) => { localStorage.setItem("gs:day", JSON.stringify(d)); localStorage.setItem("gs:settings", JSON.stringify(s)); }, [day, settings]);
  for (const p of ["plan", "evidence", "call", "value", "settings"]) {
    await page.goto(`${base}/#/${p}`);
    await page.reload();
    await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]') && document.querySelector("main section"), null, { timeout: 60000 });
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${out}/${p}_${w}${conn === "factory" ? "" : "_own"}.png`, fullPage: w === 1440 });
  }
  await page.close();
}
await browser.close();
console.log("shots in", out);
