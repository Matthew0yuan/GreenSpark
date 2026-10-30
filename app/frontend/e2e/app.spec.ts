/* End-to-end checks against the real backend. The page rules come from ROUND6.md section 1 (the round-6 prototype's
   standard): decision chips <= 4 and <= 4 words each, first-screen text <= 75 words, hover tables <= 5 rows with
   labels of <= 2 words, chart text only names, axes, actions and "number + word" labels. Layout checks on every page,
   at 1440 and 1024 px: no chart text on another text, on a data or reference line, or outside its chart; no header
   blocks on top of each other, no clipped text, no sideways scroll.
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

/** Every chart on the page: texts that overlap, texts a data line (.series) or reference line (.ref) passes through,
 *  texts outside their SVG. Boxes come from getBBox; a line hits a text when any point along it, every 0.75 px, falls
 *  inside the text's box. */
function chartDefects(page: Page) {
  return page.evaluate(() => {
    const bad: string[] = [];
    document.querySelectorAll(".chart svg").forEach((svg) => {
      const s = svg as SVGSVGElement;
      const name = s.getAttribute("aria-label")?.slice(0, 30) ?? "chart";
      const vb = s.viewBox.baseVal;
      const texts = Array.from(s.querySelectorAll("text")).map((t) => ({
        t: Array.from(t.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent).join(""), b: (t as SVGTextElement).getBBox() }))
        .filter((x) => x.b.width > 0);
      for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {
        const a = texts[i].b, b = texts[j].b;
        if (a.x < b.x + b.width - 1 && b.x < a.x + a.width - 1 && a.y < b.y + b.height - 1 && b.y < a.y + a.height - 1) bad.push(`${name}: "${texts[i].t}" on "${texts[j].t}"`);
      }
      for (const { t, b } of texts)
        if (b.x < vb.x - 1 || b.x + b.width > vb.x + vb.width + 1 || b.y < vb.y - 1 || b.y + b.height > vb.y + vb.height + 1) bad.push(`${name}: "${t}" outside the chart`);
      // walk every data and reference line in 0.75 px steps; a point inside a text's box (inset 0.5 px) is a hit
      const hits = new Set<number>();
      for (const l of Array.from(s.querySelectorAll(".series, .ref")) as SVGGeometryElement[]) {
        const len = l.getTotalLength();
        for (let d = 0; d <= len; d += 0.75) {
          const p = l.getPointAtLength(d);
          texts.forEach(({ b }, i) => { if (p.x > b.x + 0.5 && p.x < b.x + b.width - 0.5 && p.y > b.y + 0.5 && p.y < b.y + b.height - 0.5) hits.add(i); });
        }
      }
      hits.forEach((i) => bad.push(`${name}: a line crosses "${texts[i].t}"`));
    });
    return bad;
  });
}

/** Page layout: blocks in the header, decision row, section heads and control strips that overlap; text clipped by its
 *  box (ellipsis or overflow); a page wider than the window. */
function layoutDefects(page: Page) {
  return page.evaluate(() => {
    const bad: string[] = [];
    const visible = (e: Element) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const label = (e: Element) => `${e.className || e.tagName} "${(e.textContent || "").trim().slice(0, 24)}"`;
    for (const sel of [".ph", ".ph-line", ".ph-r", ".chips", ".dec", ".statusline", ".blk-h", ".opctl", ".daypick", ".budget", ".cells4"]) {
      document.querySelectorAll("main " + sel).forEach((box) => {
        const kids = Array.from(box.children).filter(visible);
        for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) {
          const a = kids[i].getBoundingClientRect(), b = kids[j].getBoundingClientRect();
          if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) bad.push(`${sel}: ${label(kids[i])} on ${label(kids[j])}`);
        }
      });
    }
    document.querySelectorAll("main .title, main .big, main .chip, main .ck span, main .cell, main .pill, main .seg button, main .btn, main .fld > span").forEach((e) => {
      const el = e as HTMLElement;
      if (visible(el) && el.scrollWidth > el.clientWidth + 1) bad.push(`clipped: ${label(el)}`);
    });
    document.querySelectorAll("main .dec-l, main .dec-r, main .ph-main, main .ph-r").forEach((e) => {
      const r = e.getBoundingClientRect();
      e.querySelectorAll("*").forEach((k) => { const q = k.getBoundingClientRect(); if (q.width && (q.right > r.right + 1 || q.left < r.left - 1)) bad.push(`out of its column: ${label(k)}`); });
    });
    if (document.documentElement.scrollWidth > window.innerWidth + 1) bad.push(`page scrolls sideways: ${document.documentElement.scrollWidth} > ${window.innerWidth}`);
    return Array.from(new Set(bad));
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

test("plan: chart text is only names, axes, actions and number + word labels; no layout defects", async ({ page }) => {
  await open(page, "plan");
  // the text drawn, without the <title> hover notes some labels carry
  const texts = await page.locator(".chart svg text").evaluateAll((els) =>
    els.map((t) => Array.from(t.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent).join("")));
  const allowed = [
    /^(Plan|Sell extra if|Grid services|Price, A\$\/MWh|Energy stored, MWh|Site import, MW|PLAN MADE 09:00|Minimum|Full · \d+ MWh)$/,
    /^-?[\d,]+$/, /^\d{2}:\d{2}$/, /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d+ \w+$/,
    /^(Discharge|Charge|Shave) [\d.]+ MW$/, /^Hold$/, /^[\d.]+$/, /^CMD · \d+ MW$/,
    /^(peak|low|floor) A\$[\d,]+$/, /^earns A\$[\d,]+$/, /^[\d.]+ MWh$/, /^A\$[\d,]+$/,
    /^price ≥ A\$\d+: \+up to \d+ MW( · sold \d+ · A\$[\d,]+)?$/, /^no extra offer$/,
    /^(Energy|Grid services|Stored|Network bill) −?[\d,]+$/, /^Wear −[\d,]+$/, /^Net −?A\$[\d,]+$/,
  ];
  for (const t of texts) expect(allowed.some((r) => r.test(t.trim())), `chart text "${t}"`).toBe(true);
  await expect(page.locator('.chart svg[aria-label^="Factory meter"]')).toHaveCount(1);
  expect(await chartDefects(page)).toEqual([]);
});

/* Each page in the states a user sees: plan only and with the outcome, the call at 09:00 and in hindsight, on a day
   the model calls (labels for the call window and the hold appear). */
const VIEWS: [string, string, string | null][] = [
  ["plan", "plan only", null], ["plan", "plan + outcome", "Plan + what happened"], ["evidence", "", null],
  ["call", "09:00", null], ["call", "hindsight", "Whole season · hindsight"], ["value", "", null], ["settings", "", null],
];
for (const width of [1440, 1024]) {
  for (const [p, label, radio] of VIEWS) {
    test(`layout ${width}: ${p} ${label}: no text on text or on a line, nothing clipped or overlapping`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await open(page, p);
      if (radio) {
        await page.getByRole("radio", { name: radio }).click();
        await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'), null, { timeout: 60_000 });
        if (p === "plan") await expect(page.locator(".wf-h", { hasText: "What happened" })).toBeVisible({ timeout: 30_000 });
        if (p === "call") await page.waitForTimeout(1500);
      }
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(300);
      expect(await chartDefects(page)).toEqual([]);
      expect(await layoutDefects(page)).toEqual([]);
    });
  }
}

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

test("plan: each decision is explained; Why this plan lists the reasons by time, a click selects one; outcome reasons after asking", async ({ page }) => {
  await open(page, "plan");
  const why = page.locator(".detail .why-hh");
  await expect(why).toContainText(/why/i);
  await expect(why).not.toContainText(/what happened/i);
  await expect(why.locator(".pt .pt-w").first()).not.toBeEmpty();
  await page.getByRole("button", { name: "Why this plan" }).click();
  const panel = page.locator("#why-plan");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("capacity peak day");  // 2026-01-20 is a flagged day behind the factory meter
  expect(await panel.locator(".ys .y").count()).toBeGreaterThanOrEqual(3);
  const rows = panel.locator("li.wr");
  const n = await rows.count();
  expect(n).toBeGreaterThan(1);
  for (let i = 0; i < n; i++) {  // every row: an action, a forecast, a plain reason and the numbers it compares
    await expect(rows.nth(i).locator(".act-pill")).toHaveCount(1);
    await expect(rows.nth(i).locator(".pt-w").first()).not.toBeEmpty();
    expect(await rows.nth(i).locator(".nm").count()).toBeGreaterThan(0);
  }
  const t = await rows.nth(1).locator(".wr-t").innerText();
  await rows.nth(1).click();
  await expect(page.locator(".detail .t")).toContainText(t.slice(0, 5));
  await expect(rows.nth(1)).toHaveClass(/on/);
  expect(await layoutDefects(page)).toEqual([]);
  await panel.getByRole("button", { name: "Close" }).click();
  await expect(panel).toHaveCount(0);
  await page.getByRole("radio", { name: "Plan + what happened" }).click();
  await expect(why).toContainText(/what happened/i, { timeout: 30_000 });
  // what happened: the decision in effect at the half-hour's start (e5 re-plans then) and the actual result
  await expect(why.locator(".pt-s", { hasText: /^Decided \d\d:\d\d$/ })).toHaveCount(1);
  await expect(why.locator(".pt-s", { hasText: /^Actual$/ })).toHaveCount(1);
  expect(await layoutDefects(page)).toEqual([]);
});

test("call: Why? gives the rule's verdict and the comparison behind it", async ({ page }) => {
  await open(page, "call", DAY);
  await page.getByRole("button", { name: "Why?" }).click();
  const panel = page.locator("#why-call");
  await expect(panel).toBeVisible();
  await expect(panel.locator(".pt-s").first()).toHaveText(/^(Call|No call)/);
  await expect(panel.locator(".nm", { hasText: "threshold" })).toHaveCount(1);
  await expect(panel.locator(".op").first()).toHaveText(/[<>]/);
  expect(await layoutDefects(page)).toEqual([]);
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
  const selx = await page.locator(".chart svg").first().locator("rect.selc").first().getAttribute("x");
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

/* Live data (app/live). Runs only against a server on live data (after `python -m app.live.run --init`, without
   GS_LIVE=0); on the replay it is skipped. */
test("live: opens on the newest day; today's outcome stops at the last published half-hour; no layout defects", async ({ page }) => {
  const m = await (await page.request.get("/api/v1/meta")).json();
  test.skip(!m.live, "the server runs the replay");
  const t = m.storage.live.today;
  await page.goto("/#/plan");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.waitForFunction(() => document.querySelector("main section .dec") && !document.querySelector('[aria-busy="true"]'), null, { timeout: 90_000 });
  await expect(page.locator(".topbar .engine")).toContainText(/^live · /);
  const label = new Date(m.storage.last_day + "T00:00:00Z").toLocaleDateString("en-AU",
    { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).replace(",", "");
  await expect(page.locator(".daypick .d")).toHaveText(label);
  if (t && !t.complete && t.ready) {
    await expect(page.locator(".ph .chip.live")).toHaveText(/^live · to \d{2}:\d{2}$/);
    await page.getByRole("radio", { name: "Plan + what happened" }).click();
    await expect(page.locator(".wf-h", { hasText: "so far" })).toBeVisible({ timeout: 90_000 });
    const o = await (await page.request.get(`/api/v1/storage/days/${t.day}/outcome`)).json();
    const k = o.live.known_halfhours;
    expect(o.price_actual.slice(0, k).every((v: number | null) => v !== null)).toBe(true);
    expect(o.price_actual.slice(k).every((v: number | null) => v === null)).toBe(true);
  }
  expect(await layoutDefects(page)).toEqual([]);
  expect(await chartDefects(page)).toEqual([]);
  await page.goto("/#/call");
  await expect(page.locator(".state.err")).toContainText("covers trading days to", { timeout: 60_000 });
});
