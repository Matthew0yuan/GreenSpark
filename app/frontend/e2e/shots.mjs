// Screenshots of every page against a running backend: node e2e/shots.mjs [outDir] [baseUrl] [day] [factory|standalone]
// Names: <page>_<width>.png; "_out" Plan with the outcome shown, "_hind" Call in hindsight, "_dark" the dark theme at 1440;
// standalone shots end in "_own".
import { chromium } from "@playwright/test";

const out = process.argv[2] ?? "../shots";
const base = process.argv[3] ?? "http://127.0.0.1:8000";
const day = process.argv[4] ?? "2026-01-20";
const conn = process.argv[5] ?? "factory";
const own = conn === "factory" ? "" : "_own";
const settings = { connection: conn, dispatcher: "e5", fcess: ["reg_raise", "cont_raise", "reg_lower", "cont_lower"], startMwh: null, exportMw: null, importMw: null };
const browser = await chromium.launch({ channel: "chrome" });

async function settle(page) {
  await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]') && document.querySelector("main section"), null, { timeout: 60000 });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(600);
}

async function shoot(width, height, scheme, suffix, pages) {
  const page = await browser.newPage({ viewport: { width, height }, colorScheme: scheme });
  await page.goto(base + "/#/plan");
  await page.evaluate(([d, s]) => { localStorage.setItem("gs:day", JSON.stringify(d)); localStorage.setItem("gs:settings", JSON.stringify(s)); }, [day, settings]);
  const full = width === 1440;
  for (const p of pages) {
    await page.goto(`${base}/#/${p}`);
    await page.reload();
    await settle(page);
    await page.screenshot({ path: `${out}/${p}_${width}${suffix}${own}.png`, fullPage: full });
    const extra = p === "plan" ? ["Plan + what happened", "_out"] : p === "call" ? ["Whole season · hindsight", "_hind"] : null;
    if (extra) {
      await page.getByRole("radio", { name: extra[0] }).click();
      await page.waitForTimeout(800);
      await settle(page);
      await page.screenshot({ path: `${out}/${p}_${width}${extra[1]}${suffix}${own}.png`, fullPage: full });
    }
  }
  await page.close();
}

const ALL = ["plan", "evidence", "call", "value", "settings"];
for (const [w, h] of [[1440, 900], [1280, 720], [1024, 900]]) await shoot(w, h, "light", "", ALL);
await shoot(1440, 900, "dark", "_dark", ALL);
await browser.close();
console.log("shots in", out);
