import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { app, BrowserWindow, Menu, WebContentsView } from "electron";
import type { ShellState } from "@contracts/bridge";
import { ExamSurface } from "../src/exam/surface";
import { classifyExamKey } from "../src/environment/keyboard";
import { CONTENT_ACTIONS, type ContentAction } from "../src/environment/content-policy";

app.enableSandbox();
app.on("window-all-closed", () => cleanup(0));
const show = process.argv.includes("--show");
const rows: object[] = [];
const checks: string[] = [];
let win: BrowserWindow | null = null;
let surface: ExamSurface | null = null;
let localOrigin = "";
let cleaned = false;
const started = new Date().toISOString();
let entry = "";
let allowed: string[] = [];
const page = `<!doctype html><html lang="ru"><meta charset="utf-8"><title>Adal — сайт, без enforce</title>
<style>body{margin:0;background:#eef3f9;font:16px Segoe UI,sans-serif;color:#132943}header{padding:16px 22px}h2{margin:0 0 10px}a{display:inline-block;padding:7px 12px;margin-right:8px;background:white;color:#123;border:1px solid #acbcd0;border-radius:7px;text-decoration:none}footer{position:fixed;bottom:0;padding:8px 22px;font-size:14px}#status{margin:8px 0}</style>
<header><h2>Adal · сайт экзамена · демонстрация без enforce</h2>
<a href="/action?name=external">Посторонний адрес</a><a href="/action?name=popup">Новое окно</a><a href="/action?name=print">Печать</a><a href="/action?name=exit">Завершить</a>
<p id="status">Загрузка разрешённого сайта…</p></header><footer id="events">Alt+Tab и закрытие окна доступны. Автовыход через 5 минут.</footer></html>`;
const server = createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  if (req.url?.startsWith("/exam/")) {
    res.end('<html lang="ru"><meta charset="utf-8"><h1>Тестовый экзамен Adal</h1><p>Эта страница входит в белый список.</p><input placeholder="Ответ"><p><a href="https://example.com/">Посторонний адрес</a></p><a target="_blank" href="/exam/">Новое окно</a><button onclick="print()">window.print()</button></html>');
  } else res.end(page);
});
const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms));
async function until(fn: () => boolean, label: string, ms = 30_000): Promise<void> {
  const deadline = Date.now() + ms;
  while (!fn() && Date.now() < deadline) await sleep(50);
  assert.ok(fn(), label);
}
function view(): WebContentsView {
  const found = win?.contentView.children.find(v => v instanceof WebContentsView && v.webContents !== win?.webContents);
  assert.ok(found instanceof WebContentsView, "website view attached");
  return found;
}
function status(text: string): void {
  if (!win || win.isDestroyed()) return;
  void win.webContents.executeJavaScript(`document.getElementById('status').textContent=${JSON.stringify(text)}`).catch(() => {});
}
function report(id: ContentAction): void {
  const item = CONTENT_ACTIONS[id];
  rows.push({ action: item.action, enforcement: "blocked", mechanism: `electron.content.${id}`, detail: { shortcut: item.label } });
  console.log(`BLOCKED ${item.label}`);
  if (win && !win.isDestroyed()) void win.webContents.executeJavaScript(`document.getElementById('events').textContent=${JSON.stringify(item.label + " — заблокировано; Alt+Tab доступен")}`).catch(() => {});
}
async function action(name: string): Promise<void> {
  if (name === "exit") { cleanup(0); return; }
  const wc = view().webContents;
  if (name === "external") await wc.executeJavaScript("location.href='https://example.com/'; true", true);
  if (name === "popup") await wc.executeJavaScript("window.open(location.href); true", true);
  if (name === "print") await wc.executeJavaScript("window.print(); true", true);
}
async function run(): Promise<void> {
  await app.whenReady();
  Menu.setApplicationMenu(null);
  await new Promise<void>(r => server.listen(0, "127.0.0.1", r));
  localOrigin = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  entry = process.env.ADAL_WEBSITE_URL === "local" ? `${localOrigin}/exam/` : (process.env.ADAL_WEBSITE_URL ?? "https://school.moodledemo.net/");
  allowed = process.env.ADAL_WEBSITE_ALLOWED_URLS ? JSON.parse(process.env.ADAL_WEBSITE_ALLOWED_URLS) as string[] :
    entry.startsWith(localOrigin) ? [entry, `${localOrigin}/exam/*`] : [entry, "https://school.moodledemo.net/*"];
  assert.equal(allowed[0], entry, "first allowlist entry must be the landing URL");
  win = new BrowserWindow({ show, width: 1240, height: 850, title: "Adal website demo — no enforce",
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, devTools: false } });
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (event, url) => {
    event.preventDefault();
    const u = new URL(url);
    if (u.origin === localOrigin && u.pathname === "/action") void action(u.searchParams.get("name") ?? "").catch(e => status(String(e)));
  });
  await win.loadURL(localOrigin + "/shell");
  surface = new ExamSurface(() => win, s => status(`${s.origin ?? ""} · ${s.phase} · ${s.message}`), input => {
    const d = classifyExamKey(input);
    if (d?.prevent && d.enforcement === "blocked" && input.type !== "keyUp") {
      rows.push({ action: d.action, enforcement: d.enforcement, detail: { shortcut: d.shortcut } });
    }
    return !!d?.prevent;
  }, report);
  const resize = () => { if (win && !win.isDestroyed()) { const [width, height] = win.getContentSize(); surface?.setViewport({ x: 0, y: 150, width, height: height - 220 }); } };
  win.on("resize", resize);
  const shell: ShellState = { mode: "exam", backend: "ready", exam_mode_active: true, operator_unlocked: false,
    session_id: "website-component-demo", last_error: null, shell_version: "demo", platform: process.platform };
  surface.consumeClassState({ type: "class_state", connection: "connected", locked: false,
    exam: { mode: "url", exam_id: "website-demo", title: "Сайт экзамена", allowed_urls: allowed } });
  surface.setShell(shell); resize();
  await until(() => surface?.status.phase === "ready" || surface?.status.phase === "error", "website load completed");
  assert.equal(surface.status.phase, "ready", surface.status.message);
  const wc = view().webContents;
  const initialUrl = wc.getURL();
  assert.ok(await wc.executeJavaScript("document.body.innerText.length > 30"), "website has rendered body");
  if (entry === "https://moodle.org/demo/") {
    assert.match(wc.getTitle(), /Moodle/i);
    assert.equal(await wc.executeJavaScript("document.body.innerText.includes('Try Moodle')"), true, 'Moodle demo content rendered');
  }
  if (entry === "https://school.moodledemo.net/") {
    assert.match(wc.getTitle(), /Mount Orange/i);
    assert.equal(await wc.executeJavaScript("document.body.innerText.includes('Mount Orange')"), true, 'Moodle school demo content rendered');
    checks.push("verified Mount Orange title and page content (not a challenge/error page)");
  }
  checks.push("allowed website loaded/rendered");
  const windows = BrowserWindow.getAllWindows().length;
  const clickDemoButton = async (name: string) => {
    await win!.webContents.executeJavaScript(`document.querySelector('a[href="/action?name=${name}"]').click(); true`);
    await sleep(350);
  };
  await clickDemoButton("external");
  assert.equal(wc.getURL(), initialUrl);
  assert.ok(rows.some(r => (r as { mechanism?: string }).mechanism === "electron.content.navigation"));
  checks.push("foreign address cancelled; URL unchanged; event emitted");
  await clickDemoButton("popup");
  assert.equal(BrowserWindow.getAllWindows().length, windows);
  assert.ok(rows.some(r => (r as { mechanism?: string }).mechanism === "electron.content.new_window"));
  checks.push("new window denied; event emitted");
  await clickDemoButton("print");
  assert.ok(rows.some(r => (r as { mechanism?: string }).mechanism === "electron.content.print"));
  checks.push("window.print prevented and reported");
  assert.equal(win.isKiosk(), false); assert.equal(win.isAlwaysOnTop(), false);
  checks.push("no kiosk or always-on-top; no native helper instantiated");
  save(true);
  if (show) { status(`${new URL(initialUrl).origin} · готово · разрешённый сайт; используйте кнопки сверху`); setTimeout(() => cleanup(0), 300_000); }
  else cleanup(0);
}
function save(ok: boolean, error?: string): void {
  const result = { started_at: started, completed_at: new Date().toISOString(), ok, entry, allowed_urls: allowed,
    mode: "production ExamSurface with injected class/shell state", enforce: false, backend: false, capture: false,
    checks, events: rows, ...(error ? { error } : {}) };
  const path = resolve(process.env.ADAL_WEBSITE_REPORT ?? "dist/website-demo-result.json");
  mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, JSON.stringify(result, null, 2) + "\n", "utf8");
  console.log(JSON.stringify(result));
}
function cleanup(code: number): void {
  if (cleaned) return;
  cleaned = true;
  surface?.dispose(); win?.destroy(); server.close(); app.exit(code);
}
void run().catch(error => { console.error(error); save(false, String(error)); cleanup(1); });
