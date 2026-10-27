// Screenshots of every page against a running backend: node e2e/shots.mjs [outDir] [baseUrl] [day]
import { chromium } from "@playwright/test";

const out = process.argv[2] ?? "../shots";
const base = process.argv[3] ?? "http://127.0.0.1:8000";
const day = process.argv[4] ?? "2026-01-20";
const browser = await chromium.launch({ channel: "chrome" });
for (const [w, h] of [[1440, 900], [1280, 720], [1024, 900]]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  await page.goto(base + "/#/plan");
  await page.evaluate((d) => localStorage.setItem("gs:day", JSON.stringify(d)), day);
  for (const p of ["plan", "evidence", "call", "value", "settings"]) {
    await page.goto(`${base}/#/${p}`);
    await page.reload();
    await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]') && document.querySelector("main section"), null, { timeout: 60000 });
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${out}/${p}_${w}.png`, fullPage: w === 1440 });
  }
  await page.close();
}
await browser.close();
console.log("shots in", out);
