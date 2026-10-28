/* End-to-end checks against the real backend. The page rules come from ROUND6.md section 1 (the round-6 prototype's
   standard): decision chips <= 4 and <= 4 words each, first-screen text <= 75 words, hover tables <= 5 rows with
   labels of <= 2 words, chart text only names, axes, actions and "number + word" labels, no overlapping chart text.
   The default connection is behind the factory meter with dispatcher v2 (e5); tests that need the other set it. */
import { expect, test, type Page } from "@playwright/test";

const DAY = "2026-01-20";     // in-sample, Hot Season: a flagged day the model calls
const NOCALL = "2026-01-06";  // a Hot Season day for the operator test

async function ready(page: Page) {
  await expect.poll(async () => (await page.request.get("/api/v1/meta")).json().then((m) =>
    Object.values(m.engines as Record<string, { state: string }>).every((e) => e.state === "ready")), { timeout: 120_000 }).toBe(true);
}

async function open(page: Page, p: string, day = DAY, settings?: object) {
  await page.goto("/#/plan");
  await page.evaluate(([d, s]) => {
    localStorage.clear();
    localStorage.setItem("gs:day", JSON.stringify(d));
    if (s) localStorage.setItem("gs:settings", JSON.stringify(s));
  }, [day, settings ?? null] as const);
  await page.goto(`/#/${p}`);
  await page.reload();
  await page.waitForFunction(() => document.querySelector("main section") && !document.querySelector('[aria-busy="true"]'), null, { timeout: 60_000 });
}

/** Words of visible text in the first screen under the top bar, SVG text excluded (ROUND6.md 1). */
function firstScreenWords(page: Page) {
  return page.evaluate(() => {
    const words: string[] = [];
    const walker = document.createTreeWalker(document.querySelector("main")!, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const n = walker.currentNode as Text;
      const el = n.parentElement!;
      if (el.closest("svg") || el.closest("details:not([open]) > :not(summary)") || el.closest(".ctip,.note-pop")) continue;
      const r = el.getBoundingClientRect();
      if (r.bottom <= 56 || r.top >= window.innerHeight || r.width === 0 || getComputedStyle(el).visibility === "hidden") continue;
      words.push(...(n.textContent || "").split(/\s+/).filter((w) => /[A-Za-z0-9]/.test(w)));
    }
    return words;
  });
}

test.beforeAll(async ({ browser }) => { const p = await browser.newPage(); await ready(p); await p.close(); });

test("shell: asset switch, pages, no script errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await open(page, "plan");
  await expect(page.locator("nav.pages button")).toHaveText(["Plan", "Evidence"]);
  await page.getByRole("tab", { name: /Customer batteries/ }).click();
  await expect(page.locator("nav.pages button")).toHaveText(["Call", "Value"]);
  for (const p of ["evidence", "call", "value", "settings"]) {
    await page.goto(`/#/${p}`);
    await page.waitForFunction(() => document.querySelector("main section") && !document.querySelector('[aria-busy="true"]'), null, { timeout: 60_000 });
  }
  expect(errors).toEqual([]);
});

for (const p of ["plan", "evidence", "call", "value"]) {
  test(`${p}: first screen ≤ 75 words, decision chips ≤ 4 × ≤ 4 words`, async ({ page }) => {
    await open(page, p);
    const words = await firstScreenWords(page);
    expect(words.length, words.join(" ")).toBeLessThanOrEqual(75);
    const chips = await page.locator(".dec .chip").allInnerTexts();
    expect(chips.length).toBeLessThanOrEqual(4);
    for (const c of chips) expect(c.split(/\s+/).filter((w) => /[A-Za-z0-9]/.test(w)).length, c).toBeLessThanOrEqual(4);
  });
}

test("plan: chart text is only names, axes, actions and number + word labels; nothing overlaps", async ({ page }) => {
  await open(page, "plan");
  // the text drawn, without the <title> hover notes some labels carry
  const texts = await page.locator(".chart svg text").evaluateAll((els) =>
    els.map((t) => Array.from(t.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent).join("")));
  const allowed = [
    /^(Plan|Sell extra if|Grid services|Price, A\$\/MWh|Energy stored, MWh|Site import, MW|PLAN MADE 09:00|Minimum|Full · \d+ MWh)$/,
    /^-?[\d,]+$/, /^\d{2}:\d{2}$/, /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d+ \w+$/,
    /^(Discharge|Charge|Shave) [\d.]+ MW$/, /^Hold$/, /^[\d.]+$/, /^CMD · \d+ MW$/,
    /^(peak|low|floor) A\$[\d,]+$/, /^earns A\$[\d,]+$/, /^[\d.]+ MWh$/, /^A\$[\d,]+$/,
    /^price ≥ A\$\d+: \+up to \d+ MW( · sold \d+ · A\$[\d,]+)?$/,
  ];
  for (const t of texts) expect(allowed.some((r) => r.test(t.trim())), `chart text "${t}"`).toBe(true);
  await expect(page.locator('.chart svg[aria-label^="Factory meter"]')).toHaveCount(1);
  const overlaps = await page.evaluate(() => {
    const svgs = [document.querySelectorAll(".chart svg")[0], document.querySelector('.chart svg[aria-label^="Factory meter"]')!];
    const bad: string[] = [];
    for (const svg of svgs) {
      const boxes = Array.from(svg.querySelectorAll("text")).map((t) => ({ t: t.childNodes[0]?.textContent, b: (t as SVGTextElement).getBBox() }))
        .filter((x) => x.b.width > 0);
      for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i].b, b = boxes[j].b;
        if (a.x < b.x + b.width - 1 && b.x < a.x + a.width - 1 && a.y < b.y + b.height - 1 && b.y < a.y + a.height - 1) bad.push(`${boxes[i].t} / ${boxes[j].t}`);
      }
    }
    return bad;
  });
  expect(overlaps).toEqual([]);
});

test("plan: hover table ≤ 5 rows, labels ≤ 2 words; outcome only after asking", async ({ page }) => {
  const outcome: string[] = [];
  page.on("request", (r) => { if (r.url().includes("/outcome")) outcome.push(r.url()); });
  await open(page, "plan");
  const hit = page.locator(".chart svg").first().locator("rect.hit").nth(22);
  await hit.hover();
  const tip = page.locator(".ctip");
  await expect(tip).toBeVisible();
  const rows = await tip.locator("tr td:first-child").allInnerTexts();
  expect(rows.length).toBeLessThanOrEqual(5);
  for (const r of rows) expect(r.split(/\s+/).length, r).toBeLessThanOrEqual(2);
  expect(await tip.innerText()).not.toContain("Actual");
  expect(outcome).toEqual([]);
  await page.getByRole("radio", { name: "Plan + what happened" }).click();
  await expect(page.locator(".wf-h", { hasText: "What happened" })).toBeVisible({ timeout: 30_000 });
  expect(outcome.length).toBeGreaterThan(0);
  await hit.hover();
  await expect(page.locator(".ctip")).toContainText("Actual");
});

test("plan: a 3 MW connection blocks the plan; Inspect jumps to the first breach", async ({ page }) => {
  await open(page, "plan", "2026-02-02", { connection: "standalone", dispatcher: "e5", fcess: ["reg_raise", "cont_raise", "reg_lower", "cont_lower"],
    startMwh: null, exportMw: 3, importMw: null });
  await expect(page.locator(".pill.bad")).toContainText("Blocked");
  await expect(page.locator(".dec .title")).toContainText("Resolve");
  await expect(page.locator(".dec .big")).toHaveClass(/dim/);
  await expect(page.locator(".bad-box").first()).toBeVisible();
  await page.getByRole("button", { name: "Inspect" }).click();
  const first = await page.locator(".bad-box").first().getAttribute("x");
  const selx = await page.locator(".chart svg").first().locator("rect.selc").getAttribute("x");
  expect(Number(selx)).toBeCloseTo(Number(first), 1);
  await expect(page.locator(".ck.fail")).toHaveCount(1);
});

test("plan: arrow keys move the selected half-hour", async ({ page }) => {
  await open(page, "plan");
  await expect(page.locator(".detail .t")).toContainText("18:00–18:30");
  await page.locator(".blk.chart").first().focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.locator(".detail .t")).toContainText("18:30–19:00");
});

test("evidence: three periods; clicking one changes the headline", async ({ page }) => {
  await open(page, "evidence");
  const rows = page.locator(".chart svg g[role=button]");
  await expect(rows).toHaveCount(3);
  await expect(page.locator(".dec")).toContainText("Test · reviewed");
  await rows.nth(0).click();
  await expect(page.locator(".dec")).toContainText("Training period");
  await expect(page.locator(".dec")).toContainText("v2 passed here");
});

test("settings: the connection switch changes the plan; v2 on its own connection is marked not passed", async ({ page }) => {
  await open(page, "plan");
  await expect(page.locator(".ph .ctx")).toContainText("factory meter");
  await expect(page.locator(".ph .ctx")).toContainText("dispatcher v2");
  await expect(page.locator('.chart svg[aria-label^="Factory meter"]')).toHaveCount(1);
  await page.goto("/#/settings");
  await page.getByRole("radio", { name: "Own connection" }).click();
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.locator("aside")).toContainText("did not pass the comparison");
  await page.goto("/#/plan");
  await expect(page.locator(".ph .ctx")).toContainText("own connection", { timeout: 60_000 });
  await expect(page.locator(".ph .ctx")).toContainText("not passed here");
  await expect(page.locator('.chart svg[aria-label^="Factory meter"]')).toHaveCount(0);
  await expect(page.locator(".ph .chip.unv")).toHaveCount(0);  // a tested connection, not a changed setting
});

test("call: the operator's decision changes the count, survives a reload, and undo returns to the model", async ({ page }) => {
  await open(page, "call", NOCALL);
  await page.request.delete(`/api/v1/fleet/days/${NOCALL}/decision?offer=custom`);
  await page.reload();
  await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]') && document.querySelector(".callbadge"));
  const was = (await page.locator(".callbadge").innerText()).trim();
  const used = await page.locator(".budget .n b").first().innerText();
  await page.getByRole("radio", { name: was === "CALL" ? "No call" : /^Call/ }).click();
  await expect(page.locator(".callbadge")).toHaveClass(/mine/);
  await expect(page.locator(".callbadge")).toHaveText(was === "CALL" ? "NO CALL" : "CALL");
  await expect(page.locator(".budget .n b").first()).not.toHaveText(used);
  await page.reload();
  await expect(page.locator(".callbadge")).toHaveClass(/mine/, { timeout: 30_000 });
  await page.goto("/#/value");
  await expect(page.locator(".chart svg text", { hasText: "Your calls" })).toBeVisible({ timeout: 30_000 });
  await page.goto("/#/call");
  await page.getByRole("button", { name: /Undo/ }).click();
  await expect(page.locator(".callbadge")).not.toHaveClass(/mine/);
  await expect(page.locator(".callbadge")).toHaveText(was);
});

test("call: the 09:00 view hides later days of the season", async ({ page }) => {
  await open(page, "call", DAY);
  const r = await page.request.get(`/api/v1/fleet/season?offer=custom&through=${DAY}`);
  const s = await r.json();
  const later = s.days.filter((d: { day: string }) => d.day > DAY);
  expect(later.every((d: { called: boolean; actual_peak_mw: number | null }) => !d.called && d.actual_peak_mw === null)).toBe(true);
});

test("value: headline against the reference terms; new terms are evaluated and flagged unvalidated", async ({ page }) => {
  await open(page, "value");
  await expect(page.locator(".dec .title")).toContainText("vs reference terms");
  await page.getByText("Try other payment terms").click();
  await page.getByRole("button", { name: "Evaluate" }).click();
  await expect(page.locator(".strip")).toContainText("unvalidated", { timeout: 30_000 });
});

test("settings: grid services off recomputes the plan and marks it changed", async ({ page }) => {
  await open(page, "settings");
  for (const l of ["Regulation raise", "Regulation lower", "Contingency raise", "Contingency lower"]) await page.getByLabel(l).uncheck();
  await page.getByRole("button", { name: "Apply" }).click();
  await page.getByRole("button", { name: "Settings" }).click();
  await page.goto("/#/plan");
  await expect(page.locator(".ph .chip.unv")).toContainText("changed settings", { timeout: 60_000 });
  await expect(page.locator(".topbar .engine")).toContainText("changed settings");
});
