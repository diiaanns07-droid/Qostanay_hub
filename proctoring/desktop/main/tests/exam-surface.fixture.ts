// Actual Electron/Chromium, local HTTP fixtures only. No backend, camera, microphone, or OS guard.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { app, BrowserWindow, WebContentsView, protocol, session, type WebContents } from "electron";
import { ExamSurface, isExamWebContents } from "../src/exam/surface";
import type { ShellState } from "@contracts/bridge";
import { CONTENT_CHECKS, KEY_CHECKS, runSelfTest } from "../src/environment/probe";
import { contentProbeScript, createElectronProbeDriver } from "../src/environment/probe-electron";
import { classifyExamKey } from "../src/environment/keyboard";
import { APP_SCHEME, CSP, PROBE_HTML, PROBE_JS } from "../src/security/web";
import type { ContentAction } from "../src/environment/content-policy";

app.enableSandbox();
app.on("window-all-closed", () => {}); // the hidden startup probe closes before the exam fixture is created
protocol.registerSchemesAsPrivileged([{ scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
const hits: string[] = [];
let origin = "";
const server = createServer((req, res) => {
  hits.push(req.url ?? "");
  if (req.url === "/exam/auth") { res.writeHead(302, { location: "/auth/login" }); res.end(); return; }
  if (req.url === "/exam/escape") { res.writeHead(302, { location: "/outside/secret" }); res.end(); return; }
  if (req.url === "/exam/download") { res.writeHead(200, { "Content-Disposition": 'attachment; filename="forbidden.txt"' }); res.end("fixture"); return; }
  if (req.url === "/exam/script.js") { res.writeHead(200, { "Content-Type": "text/javascript" }); res.end("window.allowedScriptRan=true;"); return; }
  res.writeHead(200, { "Content-Type": "text/html", "Set-Cookie": "fixture=active; Path=/; SameSite=Lax" });
  res.end(`<html><body><h1>${req.url}</h1><script src="/exam/script.js"></script><img src="/outside/pixel"><script src="/outside/script.js"></script><iframe src="/outside/frame"></iframe><a id="bad" href="/outside/nav">bad</a><a id="popup" target="_blank" href="/auth/login">popup</a><script>window.allowedInlineRan=true;</script></body></html>`);
});
const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
async function until(fn: () => boolean, message: string): Promise<void> {
  const end = Date.now() + 10000;
  while (!fn() && Date.now() < end) await wait(25);
  assert.ok(fn(), message);
}

let window: BrowserWindow | null = null;
let surface: ExamSurface | null = null;
const checked: string[] = [];
const record = (name: string) => { checked.push(name); console.log(`PASS ${name}`); };
const run = async () => {
  await app.whenReady();
  const probeSession = session.fromPartition("content-probe");
  probeSession.protocol.handle(APP_SCHEME, req => new Response(req.url.endsWith('.js') ? PROBE_JS : PROBE_HTML,
    { headers: { 'content-security-policy': CSP, 'content-type': req.url.endsWith('.js') ? 'text/javascript' : 'text/html' } }));
  app.on('web-contents-created', (_e, contents) => {
    if (contents.session !== probeSession) return;
    contents.setWindowOpenHandler(() => ({ action: 'deny' }));
    contents.on('will-navigate', event => event.preventDefault());
  });
  const driver = createElectronProbeDriver(probeSession, false);
  const results = await runSelfTest(driver);
  driver.dispose();
  console.log('SELF_TEST ' + JSON.stringify(results));
  for (const id of CONTENT_CHECKS) assert.equal(results[`page_${id}`]?.status, 'pass', `self-test ${id}`);
  for (const {check} of KEY_CHECKS) assert.equal(results[check]?.status, 'pass', `self-test ${check}`);
  record('built-in Chromium self-test covers print/save/source/context/selection/drag/zoom/reload');
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  origin = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  let isolatedDetected = 0;
  app.on("web-contents-created", (_event, wc) => { if (isExamWebContents(wc)) isolatedDetected++; });
  window = new BrowserWindow({ show: false, width: 1200, height: 800, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
  await window.loadURL("data:text/html,<h1>trusted-shell-fixture</h1>");
  const contentEvents: ContentAction[] = [];
  surface = new ExamSurface(() => window, () => {}, input => !!classifyExamKey(input)?.prevent, id => contentEvents.push(id));
  const shell: ShellState = { mode: "exam", backend: "ready", exam_mode_active: true, operator_unlocked: false, session_id: "fixture-session", last_error: null, shell_version: "fixture", platform: "fixture" };
  const policy = { exam_id: "fixture", title: "Fixture website", mode: "url", allowed_urls: [`${origin}/exam/*`, `${origin}/auth/*`] };
  const classState = (extra: object = {}) => surface!.consumeClassState({ type: "class_state", connection: "connected", locked: false, exam: policy, ...extra });
  const view = (): WebContentsView => {
    const v = window!.contentView.children.find((child) => child instanceof WebContentsView && child.webContents !== window!.webContents);
    assert.ok(v instanceof WebContentsView, "native exam view attached"); return v;
  };
  const ready = () => until(() => surface!.status.phase === "ready", "exam content loaded");
  classState(); surface.setShell(shell); surface.setViewport({ x: 0, y: 160, width: 1180, height: 470 });
  await ready();
  let wc = view().webContents;
  assert.equal(isolatedDetected, 1);
  assert.notEqual(wc.session, window.webContents.session);
  assert.equal(wc.getLastWebPreferences().sandbox, true);
  assert.equal(wc.getLastWebPreferences().preload, undefined);
  assert.deepEqual(await wc.executeJavaScript("[typeof require,typeof process,typeof window.qorgau,typeof window.qorgauExam,typeof window.qorgauLock,window.allowedScriptRan,window.allowedInlineRan]"), ["undefined", "undefined", "undefined", "undefined", "undefined", true, true]);
  record("approved site/scripts load in isolated sandbox with zero app bridges");
  for (const id of CONTENT_CHECKS) {
    assert.equal(await wc.executeJavaScript(contentProbeScript(id)), true, `website ${id}`);
    assert.ok(contentEvents.includes(id), `website event ${id}`);
  }
  record('website print/context/selection/drag/wheel prevented and reported');
  await wc.executeJavaScript(`new Promise(resolve => {const f=document.createElement('iframe');f.src='/auth/frame';f.onload=resolve;document.body.append(f);})`);
  const frame = wc.mainFrame.frames.find(f => f.url.endsWith('/auth/frame'));
  assert.ok(frame);
  assert.equal(await frame.executeJavaScript('typeof window.__adalContentPolicy'), 'undefined');
  await wait(550);
  const beforeFramePrint = contentEvents.filter(id => id === 'print').length;
  assert.equal(await frame.executeJavaScript(`(() => {let fired=false;addEventListener('beforeprint',()=>{fired=true});print();return fired;})()`), false);
  await until(() => contentEvents.filter(id => id === 'print').length > beforeFramePrint, 'CSP iframe print reported');
  let nativeDrag = false, nativeContext = false;
  wc.on('before-mouse-event', (event, input) => {
    if (input.type === 'mouseMove' && input.button === 'left') nativeDrag = event.defaultPrevented;
    if (input.type === 'mouseDown' && input.button === 'right') nativeContext = event.defaultPrevented;
  });
  const rect = await wc.executeJavaScript(`(() => {const r=document.querySelector('iframe[src="/auth/frame"]').getBoundingClientRect();return {x:Math.ceil(r.x+20),y:Math.ceil(r.y+20)}})()`);
  wc.focus();
  wc.sendInputEvent({type:'mouseDown',button:'right',clickCount:1,...rect});
  wc.sendInputEvent({type:'mouseUp',button:'right',clickCount:1,...rect});
  wc.sendInputEvent({type:'mouseDown',button:'left',clickCount:1,...rect});
  wc.sendInputEvent({type:'mouseMove',modifiers:['leftbuttondown'],x:rect.x+40,y:rect.y});
  wc.sendInputEvent({type:'mouseUp',button:'left',clickCount:1,x:rect.x+40,y:rect.y});
  await until(() => nativeDrag && nativeContext, 'mouse guard applies before iframe receives drag/context input');
  record('subframe CSP blocks and reports print; native drag/right-click intercepted before iframe');
  await wait(200);
  assert.equal(hits.some((s) => s.startsWith("/outside")), false, JSON.stringify(hits));
  assert.equal(await wc.executeJavaScript(`fetch('${origin}/outside/api').then(()=>false,()=>true)`), true);
  assert.equal(await wc.executeJavaScript(`fetch('http://localhost:${new URL(origin).port}/exam/host-bypass').then(()=>false,()=>true)`), true);
  await wc.executeJavaScript("document.getElementById('bad').click()"); await wait(100);
  assert.equal(wc.getURL(), `${origin}/exam/`);
  record("wrong host/path resources, iframe, fetch and navigation blocked before server receipt");
  const windowsBefore = BrowserWindow.getAllWindows().length;
  await wc.executeJavaScript("document.getElementById('popup').click(); window.open('/auth/login')"); await wait(100);
  assert.equal(BrowserWindow.getAllWindows().length, windowsBefore);
  record("new windows denied even for approved authentication URLs");
  for (const target of ["file:///C:/Windows/win.ini", "data:text/html,escape", "qorgau://app/", "ms-settings:"]) {
    await wc.executeJavaScript(`{const a=document.createElement('a');a.href=${JSON.stringify(target)};document.body.append(a);a.click();}`); await wait(50);
    assert.equal(wc.getURL(), `${origin}/exam/`, target);
  }
  record("file/data/app/OS navigation never escapes exam view");
  await wc.loadURL(`${origin}/exam/auth`); await ready();
  assert.equal(wc.getURL(), `${origin}/auth/login`);
  record("listed same-window authentication redirect works");
  await wc.loadURL(`${origin}/exam/escape`).catch(() => {}); await wait(100);
  assert.equal(hits.includes("/outside/secret"), false);
  record("redirect outside approved paths blocked before HTTP request");
  let downloadPrevented = false;
  wc.session.on("will-download", (event) => { downloadPrevented = event.defaultPrevented; });
  wc.downloadURL(`${origin}/exam/download`);
  await until(() => downloadPrevented, "download intercepted and prevented");
  record("download response cancelled without writing a file");
  await wc.loadURL(`${origin}/exam/`); await ready();
  assert.deepEqual(await wc.executeJavaScript("Promise.all(['camera','microphone','geolocation'].map(name=>navigator.permissions.query({name}).then(x=>x.state)))"), ["denied", "denied", "denied"]);
  assert.equal(await wc.executeJavaScript("navigator.serviceWorker.register('/exam/worker.js').then(()=>false,()=>true)"), true);
  record("camera/microphone/geolocation denied without capture; workers disabled");
  await wc.executeJavaScript("history.pushState({},'', '/outside/history')").catch(() => {});
  await until(() => wc.isDestroyed(), "unapproved SPA address destroys exam view");
  assert.equal(window.contentView.children.length, 0);
  surface.reload(); await ready(); wc = view().webContents;
  assert.equal(wc.getURL(), `${origin}/exam/`);
  record("history API cannot leave an unapproved path visible; reload restores approved page");
  assert.ok((await wc.session.cookies.get({ name: "fixture" })).length > 0);
  surface.setBlocked("class-lock", true);
  assert.equal(window.contentView.children.length, 0);
  await until(() => wc.isDestroyed(), "locked contents destroyed");
  surface.setBlocked("class-lock", false); await ready();
  wc = view().webContents;
  assert.ok((await wc.session.cookies.get({ name: "fixture" })).length > 0);
  record("lock synchronously hides then destroys exam; unlock restores isolated authenticated session");
  surface.setViewport(null);
  assert.equal(window.contentView.children.length, 0);
  surface.setViewport({ x: 0, y: 0, width: 12000, height: 12000 });
  const bounds = view().getBounds(); const [, contentHeight = 0] = window.getContentSize();
  assert.ok(bounds.y >= 112 && bounds.y + bounds.height <= contentHeight - 64);
  record("shell dialogs remove native view; malicious bounds cannot cover header/footer");
  surface.setBlocked("backend-stream", true); await until(() => wc.isDestroyed(), "stream loss destroys exam contents");
  surface.setShell(shell); assert.equal(window.contentView.children.length, 0);
  classState(); await ready(); wc = view().webContents;
  record("backend stream loss stays hidden until fresh class-state replay");
  classState({ connection: "reconnecting" }); await until(() => wc.isDestroyed(), "disconnected contents destroyed");
  classState(); await ready(); wc = view().webContents;
  surface.setShell({ ...shell, mode: "preflight", exam_mode_active: false }); await until(() => wc.isDestroyed(), "paused contents destroyed");
  surface.setShell(shell); await ready(); wc = view().webContents;
  const lastSession = wc.session;
  surface.setShell({ ...shell, mode: "normal", exam_mode_active: false });
  await until(() => wc.isDestroyed(), "finished contents destroyed");
  await wait(100); assert.equal((await lastSession.cookies.get({ name: "fixture" })).length, 0);
  record("disconnect/pause/finish close exam, finish clears website session cookies");
  console.log(JSON.stringify({ test: "actual-electron-exam-surface", passed: checked.length, device_capture: false, native_guard: false, checks: checked }));
};
void run().then(() => cleanup(0), (error) => { console.error(error); cleanup(1); });
function cleanup(code: number): void {
  surface?.dispose(); window?.destroy(); server.close(); app.exit(code);
}
