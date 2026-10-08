// Actual C1 panel, its login cookie/CSP and T03 UI; no route mocks or media permissions.
// The Python companion injects explicitly synthetic incidents and uploads burned-in TEST clips.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const [, , phase, base, pin, sid, out] = process.argv;

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.ADAL_REVIEW_CHROME ? { executablePath: process.env.ADAL_REVIEW_CHROME } : {}),
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const report = { phase, requests: [], errors: [], csp: "", media: null, frames: null };
  page.on("pageerror", error => report.errors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error" && /Content Security Policy|Refused to/i.test(message.text())) {
      report.errors.push(message.text());
    }
  });
  page.on("response", response => {
    if (response.url().includes("/api/teacher/clips/")) report.requests.push({
      status: response.status(), range: response.request().headers()["range"] ?? null,
    });
    if (response.url() === `${base}/`) report.csp = response.headers()["content-security-policy"] ?? "";
  });
  try {
    await page.goto(`${base}/login`);
    await page.locator('input[name="pin"]').fill(pin);
    await page.locator('button[type="submit"]').click();
    await page.locator(`.card-hit[data-id="${sid}"]`).click();
    await page.locator(".t3-item").first().waitFor();
    assert.match(await page.locator(".t3-detail").innerText(), /СИНТЕТИЧЕСКИЙ ЭПИЗОД/);
    assert.match(report.csp, /media-src 'self' blob:/);
    if (phase === "initial") {
      await page.locator(".t3-clip button", { hasText: "Запросить клип" }).click();
    }
    await page.locator(".t3-clip video").waitFor({ timeout: 30000 });
    assert.match(await page.locator(".t3-clip").innerText(), /СИНТЕТИКА/);
    await page.waitForFunction(() => {
      const v = document.querySelector(".t3-clip video");
      return v && v.readyState >= 2 && v.videoWidth > 0;
    });
    report.media = await page.evaluate(async () => {
      const video = document.querySelector(".t3-clip video");
      video.muted = true;
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("video seek timed out")), 10000);
        video.addEventListener("seeked", () => { clearTimeout(timer); resolve(); }, { once: true });
        video.currentTime = 1.5;
      });
      return { duration: video.duration, time: video.currentTime, width: video.videoWidth,
        error: video.error?.code ?? null };
    });
    assert.equal(report.media.error, null);
    assert.ok(report.media.duration >= 3.9 && Math.abs(report.media.time - 1.5) < 0.25);
    assert.equal(report.media.width, 320);
    // Decoded frames advancing proves playback, not merely a recognizable MP4 header.
    report.frames = await page.evaluate(async () => {
      const video = document.querySelector(".t3-clip video");
      video.dataset.testIdentity = "preserved";
      const frames = [];
      await video.play();
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("decoded video frames did not advance")), 10000);
        const frame = (_, metadata) => {
          frames.push(metadata.mediaTime);
          if (frames.length === 3) { clearTimeout(timer); resolve(); }
          else video.requestVideoFrameCallback(frame);
        };
        video.requestVideoFrameCallback(frame);
      });
      video.pause();
      return frames;
    });
    assert.ok(report.frames[2] > report.frames[0]);
    // Wait through T03 polling: it must keep the same video element and seek position.
    await page.waitForTimeout(5500);
    assert.equal(await page.locator(".t3-clip video").getAttribute("data-test-identity"), "preserved");
    assert.ok(report.requests.some(request => request.status === 206 && request.range));
    if (phase === "initial") {
      await page.locator(".t3-choice", { hasText: "Недостаточно данных" }).click();
      await page.locator(".t3-note").fill("TEST: review synthetic frames");
      await page.locator(".t3-save").click();
      await page.locator(".t3-history li").first().waitFor();
      await page.locator(".t3-choice", { hasText: "Отклонить" }).click();
      await page.locator(".t3-note").fill("TEST: <b>synthetic only</b>");
      await page.locator(".t3-save").click();
    }
    await page.locator(".t3-history li").nth(1).waitFor();
    assert.match(await page.locator(".t3-history").innerText(), /TEST: <b>synthetic only<\/b>/);
    assert.equal(await page.locator(".t3-history b").count(), 0);
    // A history.changed event should refresh the real adapter's StudentCard metadata.
    const unreviewed = page.locator(".dr-kv .kv", { has: page.locator("dt", { hasText: "Без решения преподавателя" }) }).locator("dd");
    await page.waitForFunction(() => [...document.querySelectorAll(".dr-kv .kv")]
      .some(row => row.querySelector("dt")?.textContent === "Без решения преподавателя" && row.querySelector("dd")?.textContent === "0"));
    assert.equal(await unreviewed.textContent(), "0");
    assert.deepEqual(report.errors, []);
    await page.screenshot({ path: path.join(out, `c1-review-${phase}.png`), fullPage: true });
    fs.writeFileSync(path.join(out, `c1-review-${phase}.json`), JSON.stringify(report, null, 2));
    process.stdout.write(`${JSON.stringify(report)}\n`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
