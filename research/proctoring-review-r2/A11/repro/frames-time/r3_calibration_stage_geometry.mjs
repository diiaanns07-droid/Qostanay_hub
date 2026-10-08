// ISOLATED REPRO (renderer only, FixtureBridge, no backend/Electron/camera): where do A07's five calibration
// targets land on the SCREEN? A04 (handoffs/A04/INTERFACE.md §calibration) expects points near the screen edges
// (e.g. 5 % from the edge) and the screen center. Measures .cal-dot centers as fractions of the viewport
// (the Electron exam window is the full screen).
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");
const BASE = process.env.QORGAU_RENDERER_URL ?? "http://127.0.0.1:4173/";
const OUT = process.argv[2] ?? ".";

for (const viewport of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport, locale: "ru-RU" })).newPage();
  await page.goto(`${BASE}?bridge=fixture`);
  await page.getByText("FIXTURE-режим").waitFor();
  await page.getByText("синтетический тест").click();
  await page.getByText("Студент ознакомлен").click();
  await page.getByRole("button", { name: "Создать сессию и проверить" }).click();
  await page.getByText("Обязательные проверки пройдены").waitFor();
  await page.getByRole("button", { name: "К калибровке" }).click();
  await page.getByText("Калибровка взгляда").waitFor();
  await page.locator(".cal-dot").first().waitFor();
  const r = await page.evaluate(() => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const stage = document.querySelector(".cal-stage").getBoundingClientRect();
    const dots = {};
    for (const el of document.querySelectorAll(".cal-dot")) {
      const t = [...el.classList].find((c) => /^cal-(center|left|right|up|down)$/.test(c)).slice(4);
      const b = el.getBoundingClientRect();
      dots[t] = { x: +((b.left + b.width / 2) / vw).toFixed(3), y: +((b.top + b.height / 2) / vh).toFixed(3) };
    }
    return { vw, vh, stage: { x0: +(stage.left / vw).toFixed(3), x1: +(stage.right / vw).toFixed(3), y0: +(stage.top / vh).toFixed(3), y1: +(stage.bottom / vh).toFixed(3) }, dots };
  });
  console.log(JSON.stringify({ viewport: `${viewport.width}x${viewport.height}`, ...r }));
  await page.screenshot({ path: `${OUT}/r3-calibration-${viewport.width}x${viewport.height}.png` });
  await browser.close();
}

// Part 2: where does the student's exam content sit on the same screen (full-window exam screen)?
for (const viewport of [{ width: 1366, height: 768 }, { width: 1920, height: 1080 }]) {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport, locale: "ru-RU" })).newPage();
  await page.goto(`${BASE}?bridge=fixture`);
  await page.getByText("FIXTURE-режим").waitFor();
  await page.getByText("синтетический тест").click();
  await page.getByText("Студент ознакомлен").click();
  await page.getByRole("button", { name: "Создать сессию и проверить" }).click();
  await page.getByText("Обязательные проверки пройдены").waitFor();
  await page.getByRole("button", { name: "К калибровке" }).click();
  await page.getByRole("button", { name: "Повторить точку" }).waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "Повторить точку" }).click();
  await page.waitForFunction(() => {
    const b = [...document.querySelectorAll("button")].find((x) => x.textContent?.includes("Завершить калибровку"));
    return b && !b.disabled;
  }, null, { timeout: 20000 });
  await page.getByRole("button", { name: "Завершить калибровку" }).click();
  await page.getByText("Всё готово к началу").waitFor();
  await page.getByRole("button", { name: "Начать экзамен" }).click();
  await page.getByText("Демонстрационный тест").first().waitFor();
  const r = await page.evaluate(() => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const frac = (el) => { if (!el) return null; const b = el.getBoundingClientRect(); return { x0: +(b.left / vw).toFixed(3), x1: +(b.right / vw).toFixed(3), y0: +(b.top / vh).toFixed(3), y1: +(b.bottom / vh).toFixed(3) }; };
    const next = [...document.querySelectorAll("button")].find((x) => x.textContent?.includes("Далее"));
    return { question: frac(document.querySelector(".question")), prompt: frac(document.querySelector(".q-prompt")), timer: frac(document.querySelector(".timer")), next: frac(next), progress: frac(document.querySelector(".exam-progress")), foot: frac(document.querySelector(".exam-foot")) };
  });
  console.log(JSON.stringify({ viewport: `${viewport.width}x${viewport.height}`, exam: r }));
  await page.screenshot({ path: `${OUT}/r3-exam-${viewport.width}x${viewport.height}.png` });
  await browser.close();
}
