// T03 module inside the REAL T02 panel (b493b47) via T02's serve.mjs proxy -> T03 DEV harness.
const { chromium } = require("playwright");
const [, , panelBase, pin] = process.argv;
(async () => {
  const out = { steps: [], console: [] };
  const step = (n, ok, d = "") => out.steps.push({ n, ok: !!ok, d: String(d).slice(0, 200) });
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1400, height: 1000 } });
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") out.console.push(m.text().slice(0, 200)); });
  try {
    const r = await page.request.post(panelBase + "/api/teacher/login", { data: { pin } });
    step("login_through_t02_proxy", r.ok(), r.status());
    await page.goto(panelBase + "/?adapter=real");
    await page.locator(".card-hit").first().waitFor({ timeout: 15000 });
    await page.locator(".card-hit").first().click({ force: true });
    await page.locator('.slot-history [data-module="t03-review"]').waitFor({ timeout: 10000 });
    step("t03_mounted_in_history_slot", true, await page.locator(".slot-history h3").textContent());
    await page.locator(".t3-item").nth(2).waitFor({ timeout: 10000 });
    step("episodes_in_panel", (await page.locator(".t3-item").count()) === 3);
    await page.locator(".t3-item", { hasText: "Телефон в кадре" }).click();
    const btn = page.locator(".t3-clip button", { hasText: "Запросить клип" });
    if (await btn.count()) await btn.click();
    await page.locator(".t3-clip video").waitFor({ timeout: 30000 });
    const v = await page.evaluate(async () => {
      const v = document.querySelector(".t3-clip video");
      const ev = v.readyState >= 1 ? "meta" : await new Promise((res) => { v.addEventListener("loadedmetadata", () => res("meta"), { once: true }); v.addEventListener("error", () => res("error:" + (v.error && v.error.code)), { once: true }); setTimeout(() => res("timeout"), 8000); });
      if (ev !== "meta") return { ev };
      v.currentTime = 4; await new Promise((r) => { v.addEventListener("seeked", r, { once: true }); setTimeout(r, 4000); });
      return { ev, t: v.currentTime, d: v.duration };
    });
    step("video_plays_and_seeks_in_panel", v.ev === "meta" && Math.abs(v.t - 4) < 0.6, JSON.stringify(v));
    await page.screenshot({ path: process.argv[4], fullPage: false });
  } catch (e) { step("exception", false, e.message); }
  await b.close();
  console.log(JSON.stringify(out));
})();
