// T03 e2e in a real Chromium (Playwright). Driven by test_review_e2e.py; prints one JSON line.
//   node e2e_review.cjs <phase: main|after-restart> <baseUrl> <pin> <outDir>
// Data are SYNTHETIC (fake_student.py): labelled test episodes and burned-in "TEST CLIP" videos.
const { chromium } = require("playwright");
const path = require("path");

const [, , phase, base, pin, outDir] = process.argv;
const res = { phase, steps: [], clipResponses: [], console: [] };
const step = (name, ok, detail = "") => res.steps.push({ name, ok: !!ok, detail: String(detail).slice(0, 300) });

async function login(page) {
  await page.goto(base + "/");
  await page.locator(".t3-login input").fill(pin);
  await page.locator(".t3-login button").click();
  await page.locator(".t3-students button").first().waitFor({ timeout: 15000 });
}

async function openStudent(page) {
  await page.locator(".t3-students button").first().click();
  await page.locator(".t3-item").nth(2).waitFor({ timeout: 15000 });
}

const item = (page, text) => page.locator(".t3-item", { hasText: text });

async function seekCheck(page, at) {
  return page.evaluate(async (at) => {
    const v = document.querySelector(".t3-clip video");
    if (!v) return { error: "no video" };
    if (v.readyState < 1) await new Promise((r, j) => { v.addEventListener("loadedmetadata", r, { once: true }); v.addEventListener("error", () => j(new Error("video error " + (v.error && v.error.code))), { once: true }); setTimeout(() => j(new Error("metadata timeout")), 15000); });
    v.currentTime = at;
    await new Promise((r) => { v.addEventListener("seeked", r, { once: true }); setTimeout(r, 5000); });
    return { duration: v.duration, currentTime: v.currentTime, width: v.videoWidth, error: v.error && v.error.code };
  }, at);
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.on("console", (m) => { if (m.type() === "error") res.console.push(m.text().slice(0, 200)); });
  page.on("response", (r) => { if (r.url().includes("/api/teacher/clips/")) res.clipResponses.push({ status: r.status(), range: r.request().headers()["range"] || null }); });
  try {
    await login(page);
    res.console = []; // the first GET before login answers 401 by design (shows the PIN form)
    await openStudent(page);
    const labels = await page.locator(".t3-item .t3-rule").allTextContents();
    step("three_episodes_listed", labels.length === 3, labels.join(" | "));
    step("timeline_bars", (await page.locator(".t3-bar").count()) === 3);

    if (phase === "main") {
      step("zone_reported_red", (await page.locator(".t3-zones").textContent()).includes("Проверить в первую очередь"));
      await item(page, "Второе лицо").click();
      const unavailable = await page.locator(".t3-clip").textContent();
      step("not_recorded_is_unavailable", unavailable.includes("Клип недоступен") && (await page.locator(".t3-clip button").count()) === 0, unavailable);

      await item(page, "Телефон в кадре").click();
      step("not_requested_state", (await page.locator(".t3-clip").textContent()).includes("Клип не запрашивался"));
      await page.locator(".t3-clip button", { hasText: "Запросить клип" }).click();
      await page.locator(".t3-clip-loading, .t3-clip video").first().waitFor({ timeout: 15000 });
      const sawLoading = await page.locator(".t3-clip").textContent();
      step("loading_or_available_after_request", /загружается|ТЕСТОВЫЙ/.test(sawLoading), sawLoading);
      await page.locator(".t3-clip video").waitFor({ timeout: 30000 });
      step("test_clip_labelled", (await page.locator(".t3-test-label").textContent()).includes("ТЕСТОВЫЙ КЛИП"));
      const seek = await seekCheck(page, 6.0);
      step("video_seek_to_6s", !seek.error && Math.abs(seek.currentTime - 6) < 0.6 && seek.duration > 9, JSON.stringify(seek));
      // polling must not recreate the playing <video>
      await page.evaluate(() => { const v = document.querySelector(".t3-clip video"); v.dataset.marker = "kept"; v.muted = true; return v.play(); });
      await page.waitForTimeout(6000);
      const kept = await page.evaluate(() => { const v = document.querySelector(".t3-clip video"); return { marker: v && v.dataset.marker, t: v && v.currentTime }; });
      step("video_survives_polling", kept.marker === "kept" && kept.t > 6, JSON.stringify(kept));
      await page.screenshot({ path: path.join(outDir, "t03-player.png") });

      await item(page, "Долгий взгляд").click();
      await page.locator(".t3-clip button", { hasText: "Запросить клип" }).click();
      await page.locator(".t3-clip", { hasText: "Клип недоступен" }).waitFor({ timeout: 20000 });
      const refused = await page.locator(".t3-clip").textContent();
      step("refused_clip_unavailable_with_reason", refused.includes("ТЕСТ: клип не сохранён") && refused.includes("Запросить снова"), refused);

      await item(page, "Второе лицо").click();
      await page.locator(".t3-choice", { hasText: "Отклонить" }).click();
      await page.locator(".t3-note").fill("<b>Фото на стене</b>, не человек");
      await page.locator(".t3-save").click();
      await page.locator(".t3-dec-msg", { hasText: "Решение сохранено" }).waitFor({ timeout: 10000 });
      await page.locator(".t3-history li").first().waitFor({ timeout: 10000 });
      const journal = await page.locator(".t3-history").textContent();
      step("decision_logged_and_escaped", journal.includes("<b>Фото на стене</b>") && (await page.locator(".t3-history b").count()) === 0, journal);
      const zones = await page.locator(".t3-zones").textContent();
      step("dismissed_changes_zone_after_review", zones.includes("С учётом решений преподавателя: Требует внимания"), zones);

      await item(page, "Телефон в кадре").click();
      await page.locator(".t3-choice", { hasText: "Недостаточно данных" }).click();
      await page.locator(".t3-save").click();
      await page.locator(".t3-dec-msg", { hasText: "Решение сохранено" }).waitFor({ timeout: 10000 });
      await page.locator(".t3-choice", { hasText: "Подтвердить" }).click();
      await page.locator(".t3-save").click();
      await page.locator(".t3-history li").nth(1).waitFor({ timeout: 10000 });
      const hist = await page.locator(".t3-history li").allTextContents();
      step("decision_changed_history_kept", hist.length === 2 && hist[0].includes("Подтверждено") && hist[0].includes("изменило предыдущее") && hist[1].includes("Недостаточно данных"), hist.join(" || "));
      await page.screenshot({ path: path.join(outDir, "t03-decisions.png"), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      step("no_horizontal_scroll_on_phone", (await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)) <= 0);
    } else {
      await item(page, "Телефон в кадре").click();
      const hist = await page.locator(".t3-history li").allTextContents();
      step("decisions_persist_after_restart", hist.length === 2 && hist[0].includes("Подтверждено"), hist.join(" || "));
      await page.locator(".t3-clip video").waitFor({ timeout: 15000 });
      const seek = await seekCheck(page, 3.0);
      step("clip_plays_after_restart", !seek.error && Math.abs(seek.currentTime - 3) < 0.6, JSON.stringify(seek));
      await item(page, "Долгий взгляд").click();
      step("refused_state_persists", (await page.locator(".t3-clip").textContent()).includes("ТЕСТ: клип не сохранён"));
      await item(page, "Второе лицо").click();
      step("dismissal_persists", (await page.locator(".t3-decision h5").first().textContent()).includes("Отклонено"));
    }
  } catch (err) {
    step("exception", false, err && err.stack ? err.stack : err);
  } finally {
    await browser.close();
    console.log(JSON.stringify(res));
  }
})();
