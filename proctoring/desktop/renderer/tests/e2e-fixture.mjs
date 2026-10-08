// A07 renderer flow check on the labelled FixtureBridge (no backend, no camera).
// Usage (from proctoring/desktop, after `npm run build:renderer`):
//   npx vite preview &   then   node renderer/tests/e2e-fixture.mjs [outDir]
// Requires a Playwright install (not a project dependency; see handoffs/A07/DEPENDENCIES.txt).
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch {
  ({ chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright"));
}

const BASE = process.env.QORGAU_RENDERER_URL ?? "http://127.0.0.1:4173/";
const OUT = process.argv[2] ?? "e2e-shots";
mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
};

async function noOverflow(page, label) {
  const o = await page.evaluate(() => ({
    sw: document.documentElement.scrollWidth,
    cw: document.documentElement.clientWidth,
  }));
  check(`no horizontal overflow: ${label}`, o.sw <= o.cw + 1, `${o.sw}/${o.cw}`);
}

async function run(viewport) {
  const tag = `${viewport.width}x${viewport.height}`;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const ctx = await browser.newContext({ viewport, acceptDownloads: true, locale: "ru-RU" });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  await page.addInitScript(() => {
    window.__gum = 0;
    const md = navigator.mediaDevices;
    if (md) md.getUserMedia = async () => { window.__gum += 1; throw new Error("blocked by test"); };
  });
  const shot = (n) => page.screenshot({ path: `${OUT}/${tag}-${n}.png` });
  try {
    await flow(page, tag, shot, consoleErrors);
  } catch (e) {
    await shot("zz-failure").catch(() => {});
    throw e;
  } finally {
    await browser.close();
  }
}

async function flow(page, tag, shot, consoleErrors) {

  await page.goto(`${BASE}?bridge=fixture`);
  await page.getByText("FIXTURE-режим").waitFor();
  check(`[${tag}] fixture label visible`, true);
  await page.getByText("в норме").first().waitFor();
  await shot("01-preflight-form");
  await noOverflow(page, `${tag} preflight`);

  // LIVE must fail preflight on the fixture bridge (no silent substitution).
  await page.getByText("камера (live)").click();
  await page.getByText("Студент ознакомлен").click();
  await page.getByRole("button", { name: "Создать сессию и проверить" }).click();
  await page.getByText("Начать нельзя").waitFor();
  check(`[${tag}] LIVE preflight blocked`, await page.getByRole("button", { name: "К калибровке" }).isDisabled());
  await shot("02-preflight-live-blocked");
  await page.getByRole("button", { name: "Отменить сессию" }).click();
  await page.getByRole("button", { name: "Новая сессия" }).or(page.getByRole("button", { name: "Режим преподавателя…" })).first().waitFor();

  // Back to a new session: aborted session shows StudentDone; teacher → summary → new session.
  await page.getByRole("button", { name: "Режим преподавателя", exact: true }).click();
  await page.getByLabel("PIN").fill("0000");
  await page.getByRole("button", { name: "Открыть" }).click();
  await page.getByRole("button", { name: "К итогу →" }).click();
  await page.getByRole("button", { name: "Новая сессия" }).click();

  // SYNTHETIC session.
  await page.getByText("синтетический тест").click();
  await page.getByText("Студент ознакомлен").click();
  await page.getByRole("button", { name: "Создать сессию и проверить" }).click();
  await page.getByText("Обязательные проверки пройдены").waitFor();
  await shot("03-preflight-ready");
  await page.getByRole("button", { name: "К калибровке" }).click();
  await page.getByText("Калибровка взгляда").waitFor();
  await shot("04-calibration-collecting");
  await page.getByRole("button", { name: "Повторить точку" }).waitFor({ timeout: 20000 });
  check(`[${tag}] calibration failure surfaced`, true);
  await shot("05-calibration-failed-target");
  await page.getByRole("button", { name: "Повторить точку" }).click();
  const finishCal = page.getByRole("button", { name: "Завершить калибровку" });
  await page.waitForFunction(() => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent?.includes("Завершить калибровку"));
    return b && !b.disabled;
  }, null, { timeout: 20000 });
  await finishCal.click();
  await page.getByText("Всё готово к началу").waitFor();
  await shot("06-ready");
  await page.getByRole("button", { name: "Начать экзамен" }).click();

  // Student exam.
  await page.getByText("Демонстрационный тест").first().waitFor();
  await page.getByText("1024").click();
  await page.getByText("✓ Сохранено").waitFor();
  check(`[${tag}] answer autosaved`, true);
  await page.getByRole("button", { name: "Далее →" }).click();
  await page.getByLabel("Ваш ответ").fill("HTTP");
  await page.getByText("✓ Сохранено").waitFor();
  await shot("07-exam-student");
  await noOverflow(page, `${tag} exam`);

  // Save failure → visible error, retried after recovery.
  await page.getByRole("button", { name: "FIXTURE · сбои" }).click();
  await page.getByText("Сбой сохранения ответов").click();
  await page.getByRole("button", { name: "Далее →" }).click();
  await page.getByText("Клавиатура").click();
  await page.getByText("Не сохранено").waitFor();
  await shot("08-exam-save-error");
  await page.getByText("Сбой сохранения ответов").click();
  await page.getByText("✓ Сохранено").waitFor({ timeout: 10000 });
  check(`[${tag}] failed save recovered`, true);
  await page.getByRole("button", { name: "FIXTURE · сбои" }).click();

  // Teacher console during the exam (PIN).
  await page.getByRole("button", { name: "Режим преподавателя", exact: true }).click();
  await page.getByLabel("PIN").fill("1234");
  await page.getByRole("button", { name: "Открыть" }).click();
  await page.getByText("Неверный PIN").waitFor();
  await page.getByLabel("PIN").fill("0000");
  await page.getByRole("button", { name: "Открыть" }).click();
  await page.getByText("Наблюдение за сессией").waitFor();
  await page.locator(".preview-box img").waitFor();
  await page.locator(".inc-item").first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(9000);
  await shot("09-operator-live");
  await noOverflow(page, `${tag} operator`);
  await page.locator(".inc-item").first().click();
  await page.getByText("Решение преподавателя").waitFor();
  await page.getByRole("radio", { name: "Отклонить" }).click();
  await page.getByLabel("Комментарий").fill("Телефон лежал экраном вниз (тест)");
  await page.getByLabel("Проверяющий").fill("T-1");
  await page.getByRole("button", { name: "Записать решение" }).click();
  await page.locator(".review-history li").first().waitFor();
  check(`[${tag}] review recorded`, true);
  await shot("10-incident-reviewed");

  // Backend loss and recovery.
  await page.getByRole("button", { name: "FIXTURE · сбои" }).click();
  await page.getByText("Потеря связи с сервисом").click();
  await page.getByText("Нет связи с локальным сервисом").first().waitFor();
  await shot("11-backend-lost");
  await page.getByText("Потеря связи с сервисом").click();
  await page.getByText("на связи").waitFor({ timeout: 10000 });
  check(`[${tag}] backend loss shown and recovered`, true);
  // Camera loss → technical episode.
  await page.getByText("Потеря источника кадров").click();
  await page.getByText("Наблюдение ухудшено").first().waitFor({ timeout: 10000 });
  await shot("12-camera-lost");
  await page.getByText("Потеря источника кадров").click();
  await page.getByRole("button", { name: "FIXTURE · сбои" }).click();

  // Pause / resume.
  await page.getByRole("button", { name: "Пауза" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Пауза" }).click();
  await page.getByRole("button", { name: "Продолжить" }).waitFor();
  await page.getByRole("button", { name: "Продолжить" }).click();

  // Finish → review → summary.
  await page.getByRole("button", { name: "Завершить экзамен" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Завершить" }).click();
  await page.getByText("Проверка эпизодов").first().waitFor();
  await shot("13-review");
  await page.getByRole("button", { name: "К итогу →" }).click();
  await page.getByText("Покрытие наблюдением").waitFor();
  await shot("14-summary");
  await noOverflow(page, `${tag} summary`);
  await page.getByRole("button", { name: "Сохранить отчёт HTML" }).click();
  await page.getByText("NOT_IMPLEMENTED").waitFor();
  check(`[${tag}] HTML export not-implemented surfaced`, true);
  const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Экспорт JSON" }).click()]);
  check(`[${tag}] JSON export downloaded`, /fixture\.json$/.test(dl.suggestedFilename()), dl.suggestedFilename());

  const gum = await page.evaluate(() => window.__gum);
  check(`[${tag}] getUserMedia never called`, gum === 0, String(gum));
  check(`[${tag}] no console errors`, consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));
}

for (const vp of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
  try {
    await run(vp);
  } catch (e) {
    check(`flow ${vp.width}x${vp.height} completed`, false, String(e).slice(0, 300));
  }
}
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed`);
process.exit(failed ? 1 : 0);
