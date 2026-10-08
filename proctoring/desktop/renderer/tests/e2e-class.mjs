// A07-student: class-mode screens on the labelled FixtureBridge (FIXTURE: class_state is emitted by the fixture
// panel, NOT by a class server or the C2 uplink). Same setup as e2e-fixture.mjs:
//   VITE_QORGAU_FIXTURE=1 npx vite build --outDir "$PWD/dist/renderer-fixture"
//   npx vite preview --outDir "$PWD/dist/renderer-fixture" --port 4173 &   then   node renderer/tests/e2e-class.mjs [outDir]
// Env: PLAYWRIGHT_MODULE, CHROMIUM_PATH, QORGAU_RENDERER_URL.
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");
const BASE = process.env.QORGAU_RENDERER_URL ?? "http://127.0.0.1:4173/";
const OUT = process.argv[2] ?? "e2e-class-shots";
mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
};
const FORBIDDEN = /списыва|нарушител|вероятност/i;

async function panel(page, fn) {
  await page.getByRole("button", { name: "FIXTURE · сбои" }).click();
  await fn();
  await page.getByRole("button", { name: "FIXTURE · сбои" }).click();
}

async function run(viewport) {
  const tag = `${viewport.width}x${viewport.height}`;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await (await browser.newContext({ viewport, locale: "ru-RU", reducedMotion: "reduce" })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  const shot = (n) => page.screenshot({ path: `${OUT}/${tag}-${n}.png` });
  try {
    await page.goto(`${BASE}?bridge=fixture`);
    await page.getByText("FIXTURE-режим").waitFor();

    // 4. Class block: no data yet -> each connection state as text + badge
    await page.getByRole("heading", { name: "Класс" }).waitFor();
    await page.getByText("Сервер класса не настроен.", { exact: false }).waitFor();
    check(`[${tag}] class block reports no server from health`, await page.getByText("нет сервера", { exact: true }).isVisible());
    check(`[${tag}] computer name is available without uplink`, await page.getByTestId("class-computer").textContent() === "FIXTURE-PC");
    const states = { connected: "подключено", reconnecting: "переподключение: сервер класса не отвечает", rejected: "неверный код подключения", connecting: "подключение…" };
    for (const [value, text] of Object.entries(states)) {
      await panel(page, () => page.getByLabel("Связь с классом (FIXTURE)").selectOption(value));
      const shown = (await page.getByTestId("class-connection").textContent())?.trim();
      check(`[${tag}] class block: ${value} -> «${text}»`, shown === text, shown ?? "");
      if (value === "rejected") {
        check(`[${tag}] rejected shows the uplink message`, await page.getByText("Сервер класса отклонил код подключения").isVisible());
        await shot("01b-class-rejected");
      }
    }
    await panel(page, () => page.getByLabel("Связь с классом (FIXTURE)").selectOption("connected"));
    check(`[${tag}] class block shows server`, await page.getByText("fixture-class:8765").isVisible());
    check(`[${tag}] computer name remains after class_state without computer_name`, await page.getByTestId("class-computer").textContent() === "FIXTURE-PC");
    await shot("01-class-block");

    // 2. Lock screen
    await panel(page, () => page.getByLabel("Заблокировать экзамен").check());
    const lock = page.getByRole("alertdialog");
    await lock.waitFor();
    check(`[${tag}] lock screen title`, await page.getByText("Преподаватель приостановил ваш экзамен").isVisible());
    check(`[${tag}] lock screen shows the teacher's reason`, await page.getByText("Телефон на столе (FIXTURE)").isVisible());
    check(`[${tag}] lock screen has no button (student cannot close it)`, (await lock.getByRole("button").count()) === 0);
    const cover = await page.evaluate(() => {
      const b = document.querySelector(".lockscreen").getBoundingClientRect();
      const hit = document.elementFromPoint(innerWidth / 2, innerHeight - 20);
      return { full: b.width >= innerWidth - 1 && b.height >= innerHeight - 1, top: !!hit?.closest(".lockscreen"), inert: document.querySelector(".app").inert === true };
    });
    check(`[${tag}] lock screen covers the window and is on top`, cover.full && cover.top, JSON.stringify(cover));
    check(`[${tag}] app underneath is inert (no clicks/keyboard)`, cover.inert);
    await page.keyboard.press("Escape");
    for (let i = 0; i < 5; i++) await page.keyboard.press("Tab");
    const focusInside = await page.evaluate(() => !!document.activeElement?.closest(".lockscreen, .fx-panel"));
    check(`[${tag}] Escape/Tab do not leave the lock screen`, focusInside && (await lock.isVisible()));
    await shot("02-lock-screen");

    // 3. Mic banner above the lock screen too
    await panel(page, () => page.getByLabel("Микрофон включён преподавателем").check());
    const banner = page.getByText("Микрофон включён преподавателем для проверки");
    await banner.waitFor();
    const mb = await page.evaluate(() => {
      const b = document.querySelector(".micbanner").getBoundingClientRect();
      const hit = document.elementFromPoint(innerWidth / 2, 10);
      return { top: b.top, w: b.width, onTop: !!hit?.closest(".micbanner"), buttons: document.querySelectorAll(".micbanner button").length };
    });
    check(`[${tag}] mic banner at the top, full width, above the lock screen`, mb.top === 0 && mb.w >= viewport.width - 1 && mb.onTop, JSON.stringify(mb));
    check(`[${tag}] mic banner cannot be hidden (no button)`, mb.buttons === 0);
    await shot("03-lock-and-mic");

    await panel(page, () => page.getByLabel("Заблокировать экзамен").uncheck());
    await lock.waitFor({ state: "detached" });
    check(`[${tag}] unlock removes the lock screen`, (await page.locator(".lockscreen").count()) === 0 && !(await page.evaluate(() => document.querySelector(".app").inert)));
    const hdr = await page.evaluate(() => document.querySelector(".topbar").getBoundingClientRect().top);
    check(`[${tag}] mic banner does not cover the app header`, hdr >= 40, String(hdr));
    await shot("04-mic-only");
    await panel(page, () => page.getByLabel("Микрофон включён преподавателем").uncheck());
    await banner.waitFor({ state: "detached" });
    check(`[${tag}] mic banner removed when mic_active=false`, true);

    // Lock above the full-screen calibration layer
    await page.getByText("синтетический тест", { exact: true }).click();
    await page.getByText("Студент ознакомлен").click();
    await page.getByRole("button", { name: "Создать сессию и проверить" }).click();
    await page.getByText("Обязательные проверки пройдены").waitFor();
    await page.getByRole("button", { name: "К калибровке" }).click();
    await page.getByText("Смотрите на точку глазами, голову держите прямо.").waitFor();
    check(`[${tag}] full-screen calibration identifies the fixture`, await page.getByText("FIXTURE · имитация калибровки, без камеры").isVisible());
    await page.getByRole("button", { name: "Пропустить…", exact: true }).click();
    await page.getByLabel("PIN", { exact: true }).waitFor();
    const pinOnTop = await page.getByLabel("PIN", { exact: true }).evaluate((input) => {
      const r = input.getBoundingClientRect();
      return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2) === input;
    });
    check(`[${tag}] teacher PIN appears above full-screen calibration`, pinOnTop);
    await page.keyboard.press("Escape");
    await page.getByLabel("PIN", { exact: true }).waitFor({ state: "detached" });
    check(`[${tag}] dismissing PIN keeps calibration available`, await page.getByRole("button", { name: "Начать калибровку", exact: true }).isVisible());
    await panel(page, () => page.getByLabel("Заблокировать экзамен").check());
    await lock.waitFor();
    const onCal = await page.evaluate(() => !!document.elementFromPoint(innerWidth / 2, innerHeight / 2)?.closest(".lockscreen"));
    check(`[${tag}] lock screen is above the calibration layer`, onCal);
    await shot("05-lock-over-calibration");
    await panel(page, () => page.getByLabel("Заблокировать экзамен").uncheck());
    await lock.waitFor({ state: "detached" });

    const text = await page.evaluate(() => document.body.innerText);
    check(`[${tag}] no forbidden wording`, !FORBIDDEN.test(text));
    check(`[${tag}] no page errors`, errors.length === 0, errors.join(" | "));
  } catch (e) {
    await shot("zz-failure").catch(() => {});
    check(`[${tag}] flow completed`, false, String(e).split("\n")[0]);
  } finally {
    await browser.close();
  }
}

for (const vp of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) await run(vp);
const passed = results.filter((r) => r.ok).length;
console.log(`\n${passed}/${results.length} checks passed`);
process.exit(passed === results.length ? 0 : 1);
