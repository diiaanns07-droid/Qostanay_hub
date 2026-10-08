// Real-renderer check of an A08 HTML report (owner: A08). Used by test_evidence_report.py when
// Node + Playwright + Chromium are available; skipped otherwise.
//   node render_check.cjs <report.html> <out.pdf> <out.png>
// Loads the file in Chromium with the network forced offline, records every request, console
// message and page error, prints the page to an A4 PDF and screenshots it at phone width.
const path = require("path");
const { chromium } = require("playwright");

(async () => {
  const [, , htmlPath, pdfPath, pngPath] = process.argv;
  const browser = await chromium.launch(process.env.QORGAU_CHROMIUM ? { executablePath: process.env.QORGAU_CHROMIUM } : {});
  const context = await browser.newContext({ offline: true });
  const page = await context.newPage();
  const requests = [];
  const messages = [];
  page.on("request", (r) => requests.push(r.url().slice(0, 120)));
  page.on("console", (m) => messages.push(`${m.type()}: ${m.text()}`.slice(0, 300)));
  page.on("pageerror", (e) => messages.push(`pageerror: ${e.message}`.slice(0, 300)));
  await page.goto("file://" + path.resolve(htmlPath), { waitUntil: "load" });
  const info = await page.evaluate(() => ({
    title: document.title,
    scripts: document.scripts.length,
    links: document.querySelectorAll("a[href], link[href], iframe, object, embed, form").length,
    images: Array.from(document.images).map((i) => ({ loaded: i.complete && i.naturalWidth > 0, scheme: i.src.split(":")[0] })),
    sections: Array.from(document.querySelectorAll("h2")).map((h) => h.textContent),
    banner: (document.querySelector(".banner") || {}).textContent || "",
  }));
  await page.emulateMedia({ media: "print" });
  await page.pdf({ path: pdfPath, format: "A4", printBackground: true });
  await page.emulateMedia({ media: "screen" });
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  await page.screenshot({ path: pngPath, fullPage: false });
  await browser.close();
  console.log(JSON.stringify({ requests, messages, info, mobileOverflow }));
})().catch((e) => {
  console.error(String(e && e.stack ? e.stack : e).slice(0, 2000));
  process.exit(2);
});
