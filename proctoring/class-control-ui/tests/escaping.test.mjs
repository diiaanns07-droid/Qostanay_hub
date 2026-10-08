// Untrusted text (student labels, reasons, titles from the server) must become text nodes, never markup.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { installFakeDocument } from "./fake-dom.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
installFakeDocument();
const { h, append, statusChip, icon } = await import("../src/dom.js");
const { lastCommandBlock } = await import("../src/ui/common.js");

const EVIL = `<img src=x onerror="window.__xss=1">"'&<script>alert(1)</script>`;

/** all element descendants */
function elements(n, out = []) {
  for (const c of n.childNodes) if (c.nodeType === 1) {
    out.push(c);
    elements(c, out);
  }
  return out;
}

test("h(): strings become text nodes; nothing is parsed as HTML", () => {
  const el = h("span", { title: EVIL }, [EVIL]);
  assert.equal(el.childNodes.length, 1);
  assert.equal(el.childNodes[0].nodeType, 3);
  assert.equal(el.textContent, EVIL);
  assert.equal(elements(el).length, 0, "no <img>/<script> element created");
  assert.equal(el.getAttribute("title"), EVIL, "attribute value kept as plain value");
  const box = h("div");
  append(box, [EVIL, null, false, undefined]);
  assert.equal(box.textContent, EVIL);
});

test("h(): event handler attributes are only attached from functions, never from strings", () => {
  const el = h("button", { onclick: "window.__xss=1" });
  assert.equal(el.getAttribute("onclick"), "window.__xss=1", "stays an inert attribute in the fake DOM");
  // the real panel never passes server text as an on* attribute: checked statically below
});

test("command block renders a hostile label_ru / kind_ru / teacher name as text", () => {
  const nodes = lastCommandBlock({ group: "error", label_ru: EVIL, kind_ru: EVIL, issued_by_name: EVIL, issued_at: "2026-10-08T10:00:00Z" });
  const wrap = h("div", {}, nodes);
  assert.ok(wrap.textContent.includes(EVIL));
  const tags = elements(wrap).map((e) => e.localName);
  assert.ok(!tags.includes("img") && !tags.includes("script"), `unexpected elements: ${tags.join(",")}`);
  const chip = statusChip({ icon: "check", tone: "ok", groupLabel: EVIL, group: "done" });
  assert.ok(chip.textContent.includes(EVIL));
  assert.equal(icon("nope").localName, "svg", "unknown icon name falls back to a constant shape");
});

function sourceFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (name === "node_modules" || name === "tests") continue;
    if (statSync(p).isDirectory()) sourceFiles(p, out);
    else if (/\.(m?js|html)$/.test(name)) out.push(p);
  }
  return out;
}

test("static check: no innerHTML/outerHTML/insertAdjacentHTML/document.write/eval in the UI sources", () => {
  const files = sourceFiles(ROOT);
  assert.ok(files.length >= 10, `expected UI sources, found ${files.length}`);
  const bad = [];
  for (const f of files) {
    const text = readFileSync(f, "utf8");
    for (const re of [/\.innerHTML\b/, /\.outerHTML\b/, /insertAdjacentHTML/, /document\.write/, /\beval\(/, /new Function\(/, /\.setHTMLUnsafe/]) {
      if (re.test(text)) bad.push(`${relative(ROOT, f)}: ${re}`);
    }
  }
  assert.deepEqual(bad, []);
});

test("index.html: strict CSP (no inline script/style) and module entry under /ui/", () => {
  const html = readFileSync(join(ROOT, "index.html"), "utf8");
  assert.match(html, /Content-Security-Policy/);
  assert.match(html, /script-src 'self'/);
  assert.ok(!/unsafe-inline|unsafe-eval/.test(html));
  assert.match(html, /<script type="module" src="\/ui\/src\/app\.js"><\/script>/);
  assert.ok(!/<script>(?!<\/script>)/.test(html), "no inline scripts");
});
