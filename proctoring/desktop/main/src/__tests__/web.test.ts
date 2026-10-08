import assert from "node:assert/strict";
import { join, resolve } from "node:path";
import { test } from "node:test";
import { loadConfig } from "../config";
import { APP_ENTRY, CSP, devCsp, isTrustedUrl, resolveAppFile } from "../security/web";

const dir = resolve("/srv/qorgau/dist/renderer");

test("app protocol serves only files inside the renderer dir", () => {
  assert.equal(resolveAppFile(APP_ENTRY, dir), join(dir, "index.html"));
  assert.equal(resolveAppFile("qorgau://app/", dir), join(dir, "index.html"));
  assert.equal(resolveAppFile("qorgau://app/assets/a.js", dir), join(dir, "assets", "a.js"));
  // URL parsing normalizes plain dot segments, so they can never leave the root
  assert.equal(resolveAppFile("qorgau://app/../../secret.txt", dir), join(dir, "secret.txt"));
  assert.equal(resolveAppFile("qorgau://app/%2e%2e/secret.txt", dir), join(dir, "secret.txt"));
  // an encoded slash survives URL parsing and decodes to ../ -> rejected by the root check
  for (const bad of [
    "qorgau://app/%2e%2e%2fsecret.txt",
    "qorgau://app/..%5csecret.txt",
    "qorgau://app/a%00.js",
    "qorgau://other/index.html",
    "file:///etc/passwd",
    "qorgau://app/%E0%A4%A",
    "not a url",
  ]) {
    assert.equal(resolveAppFile(bad, dir), null, bad);
  }
});

test("trusted origin is the app scheme (and the configured dev origin only)", () => {
  assert.equal(isTrustedUrl(APP_ENTRY, null), true);
  assert.equal(isTrustedUrl("qorgau://evil/index.html", null), false);
  assert.equal(isTrustedUrl("https://example.com/", null), false);
  assert.equal(isTrustedUrl("http://127.0.0.1:5173/", null), false);
  assert.equal(isTrustedUrl("http://127.0.0.1:5173/src/main.tsx", "http://127.0.0.1:5173"), true);
  assert.equal(isTrustedUrl("http://127.0.0.1:5174/", "http://127.0.0.1:5173"), false);
  assert.equal(isTrustedUrl("file:///index.html", null), false);
});

test("production CSP: no inline/eval scripts, no remote origins, no frames/objects", () => {
  assert.match(CSP, /script-src 'self'(;|$)/);
  assert.doesNotMatch(CSP, /unsafe-eval/);
  assert.doesNotMatch(CSP, /script-src[^;]*unsafe-inline/);
  assert.doesNotMatch(CSP, /https?:/);
  assert.match(CSP, /object-src 'none'/);
  assert.match(CSP, /frame-ancestors 'none'/);
  assert.match(CSP, /default-src 'none'/);
  assert.match(devCsp("http://127.0.0.1:5173"), /connect-src 'self' ws:\/\/127\.0\.0\.1:5173/);
});

test("config: defaults, dev URL and devtools only when unpackaged", () => {
  const c = loadConfig("/app/desktop", {}, false, "win32");
  assert.equal(c.proctoringRoot, resolve("/app"));
  assert.equal(c.python, join(resolve("/app"), ".venv", "Scripts", "python.exe"));
  assert.equal(c.allowDevTools, false);
  assert.equal(c.nativeEnforce, false, "native enforcement is opt-in");
  assert.equal(c.devRendererUrl, null);
  const dev = loadConfig("/app/desktop", { QORGAU_SHELL_DEV_RENDERER_URL: "http://127.0.0.1:5173", QORGAU_SHELL_ALLOW_DEVTOOLS: "1" }, false, "linux");
  assert.equal(dev.devRendererUrl, "http://127.0.0.1:5173/");
  assert.equal(dev.allowDevTools, true);
  assert.equal(dev.python, join(resolve("/app"), ".venv", "bin", "python"));
  const packaged = loadConfig("/app/desktop", { QORGAU_SHELL_DEV_RENDERER_URL: "http://127.0.0.1:5173", QORGAU_SHELL_ALLOW_DEVTOOLS: "1" }, true, "win32");
  assert.equal(packaged.devRendererUrl, null);
  assert.equal(packaged.allowDevTools, false);
  assert.equal(packaged.warnings.length, 2);
  const remote = loadConfig("/app/desktop", { QORGAU_SHELL_DEV_RENDERER_URL: "http://192.168.1.5:5173" }, false, "win32");
  assert.equal(remote.devRendererUrl, null);
});
