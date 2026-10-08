// Renderer origin, CSP and per-webContents hardening (owner: A06).
//
// The renderer is served from the privileged custom scheme qorgau://app/ (standard + secure), not
// file://, so CSP headers apply and no file path is reachable. Pure helpers are exported for tests;
// Electron-specific wiring is in main.ts.
import { extname, resolve, sep } from "node:path";

export const APP_SCHEME = "qorgau";
export const APP_ORIGIN = "qorgau://app";
export const APP_ENTRY = `${APP_ORIGIN}/index.html`;
export const PARTITION = "qorgau-exam"; // in-memory: no persistent cookies/cache on disk

export const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "media-src 'self' blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-src 'none'",
  "frame-ancestors 'none'",
].join("; ");

export function devCsp(devOrigin: string): string {
  const ws = devOrigin.replace(/^http/, "ws");
  return [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "media-src 'self' blob:",
    `connect-src 'self' ${ws}`,
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-src 'none'",
  ].join("; ");
}

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".map": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".txt": "text/plain; charset=utf-8",
};

export function mimeFor(path: string): string {
  return MIME[extname(path).toLowerCase()] ?? "application/octet-stream";
}

/**
 * Map a qorgau://app/... URL to a file inside rendererDir, or null (404).
 * Rejects other hosts, encoded traversal, backslashes, NUL and anything resolving outside the dir.
 */
export function resolveAppFile(rawUrl: string, rendererDir: string): string | null {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }
  if (url.protocol !== `${APP_SCHEME}:` || url.host !== "app") return null;
  let rel: string;
  try {
    rel = decodeURIComponent(url.pathname);
  } catch {
    return null;
  }
  if (rel.includes("\0") || rel.includes("\\")) return null;
  if (rel === "" || rel === "/") rel = "/index.html";
  const root = resolve(rendererDir);
  const file = resolve(root, "." + rel);
  if (!file.startsWith(root + sep)) return null;
  return file;
}

/** True when `url` belongs to the trusted renderer (app origin or the configured dev origin). */
export function isTrustedUrl(url: string, devOrigin: string | null): boolean {
  try {
    const u = new URL(url);
    if (u.protocol === `${APP_SCHEME}:` && u.host === "app") return true;
    if (devOrigin && u.origin === new URL(devOrigin).origin) return true;
  } catch {
    return false;
  }
  return false;
}

export const FALLBACK_HTML = `<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Qorgau Exam</title>
<style>body{font-family:system-ui,sans-serif;margin:3rem;max-width:44rem;color:#1b1b1b;background:#fff}
code{background:#f1f1f1;padding:.1rem .3rem}</style></head>
<body><h1>Qorgau Exam — оболочка запущена</h1>
<p>Интерфейс (renderer, A07) ещё не собран: нет <code>desktop/dist/renderer/index.html</code>.</p>
<p>Соберите его командой <code>npm run build</code>. Экзаменационный режим не включается без явного начала сессии.</p>
</body></html>`;

export const PROBE_HTML = `<!doctype html>
<html><head><meta charset="utf-8"><title>qorgau self-test</title><script src="__probe.js"></script></head>
<body><input id="field" autofocus value=""></body></html>`;

/** Counts keydown events that reach the page (the self-test checks that blocked keys never do). */
export const PROBE_JS = `"use strict";
window.__qorgauProbe = { keys: {}, total: 0 };
window.addEventListener("keydown", function (e) {
  var k = (e.ctrlKey ? "C" : "") + (e.altKey ? "A" : "") + (e.shiftKey ? "S" : "") + ":" + e.code;
  window.__qorgauProbe.keys[k] = (window.__qorgauProbe.keys[k] || 0) + 1;
  window.__qorgauProbe.total += 1;
}, true);
`;
