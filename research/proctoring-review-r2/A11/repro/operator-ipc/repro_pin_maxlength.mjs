// A11 isolated repro: A07 PIN input (App.tsx:330 maxLength={12}) vs A06 PIN rule 4..64 (validate.ts:162, hash-pin.mjs:11).
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { join, resolve, extname } from "node:path";
const require = createRequire(import.meta.url);
const { chromium } = require("/opt/node22/lib/node_modules/playwright");
const dist = resolve(process.argv[2], "dist/renderer");
const browser = await chromium.launch();
const page = await browser.newPage();
await page.route("http://qorgau.test/**", (route) => {
  const p = new URL(route.request().url()).pathname;
  const f = join(dist, p === "/" ? "index.html" : p);
  const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
  try { route.fulfill({ status: 200, contentType: MIME[extname(f)] ?? "application/octet-stream", body: readFileSync(f) }); } catch { route.fulfill({ status: 404, body: "" }); }
});
await page.goto("http://qorgau.test/index.html?bridge=fixture");
await page.getByRole("button", { name: "Режим преподавателя", exact: true }).click();
const pin16 = "Teacher2026Pin16"; // valid for A06: /^[0-9A-Za-z]{4,64}$/
await page.getByLabel("PIN").click();
await page.keyboard.type(pin16);
const v = await page.getByLabel("PIN").inputValue();
console.log(`typed ${pin16.length} chars (A06-valid PIN), A07 PIN field holds ${v.length}: "${v}" -> operatorUnlock would receive a different PIN`);
await browser.close();
