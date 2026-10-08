// Qorgau Exam — Electron main process (owner: A06). Entry: main/src/main.ts -> dist/main/main.cjs.
//
// Responsibilities: one secure window (contextIsolation, sandbox, no Node, CSP, no navigation/new
// windows/downloads/permissions), backend child process with READY handshake and restart policy,
// the fixed window.qorgau bridge (IPC with sender + argument validation), exam-mode guard driven by
// the session state, guaranteed release on finish/abort/pause/backend loss/renderer crash/quit.
import { readFile, writeFile } from "node:fs/promises";
import { hostname, release as osRelease, version as osVersion } from "node:os";
import { join } from "node:path";
import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  globalShortcut,
  ipcMain,
  Menu,
  protocol,
  screen,
  session as electronSession,
  type IpcMainEvent,
  type IpcMainInvokeEvent,
  type Session,
  type WebContents,
} from "electron";
import type { EnvironmentCapabilities, SessionInfo } from "@contracts/qorgau-v1.generated";
import { BackendClient } from "./backend/client";
import { BackendSupervisor, type BackendConnection } from "./backend/process";
import { BackendSocket } from "./backend/stream";
import { loadConfig } from "./config";
import { fail, shellError } from "./errors";
import { buildCapabilities, parseVerificationRecords, summarize, type NativeHelperInfo, type PlatformInfo, type ProbeResults, type VerificationRecord } from "./environment/capabilities";
import { EnvironmentEventQueue } from "./environment/events";
import { ExamGuard, type GuardPlatform } from "./environment/guard";
import { applyCalibrationFullscreen } from "./environment/window-fullscreen";
import { withDisplayCheck, withRemoteCheck } from "./environment/preflight";
import { checkRemoteEnvironment } from "./environment/remote";
import { checkVm, unknownVm, withVmCheck } from "./environment/vm";
import { prepareContentSession, protectContent } from "./environment/content";
import { checkHelper, NativeHelper } from "./environment/native";
import { createElectronProbeDriver } from "./environment/probe-electron";
import { probeSummary, runSelfTest } from "./environment/probe";
import { createApi } from "./ipc/api";
import { INVOKE, PUSH, SEND, type InvokeName } from "./ipc/channels";
import { logger } from "./log";
import { APP_ENTRY, APP_SCHEME, CSP, FALLBACK_HTML, PARTITION, PROBE_HTML, PROBE_JS, devCsp, isTrustedUrl, mimeFor, resolveAppFile } from "./security/web";
import { OperatorAuth } from "./shell/operator";
import { ShellStateMachine, TERMINAL_STATES } from "./shell/state";
import { ExamSurface, isExamWebContents } from "./exam/surface";
import { EXAM_CHANNEL } from "./exam/channels";
import { ClassLockController, ClassStateDelivery, LOCK_ACK_CHANNEL } from "./class-lock";
import { createClassAudio } from "./class-audio";
import { EmergencyQuit } from "./emergency-quit";

const log = logger("main");

// ---------------------------------------------------------------- before ready
protocol.registerSchemesAsPrivileged([
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: false, stream: false } },
]);
app.enableSandbox();
if (!app.requestSingleInstanceLock()) {
  log.warn("another Adal instance is running; exiting");
  app.quit();
}

const cfg = loadConfig(app.getAppPath(), process.env, app.isPackaged, process.platform);
for (const w of cfg.warnings) log.warn(w);
log.info(`teacher access policy: ${cfg.accessPolicy} (operator idle ${cfg.operatorIdleMs / 1000}s, max ${cfg.operatorMaxMs / 1000}s)`);
const devOrigin = cfg.devRendererUrl ? new URL(cfg.devRendererUrl).origin : null;
const platformInfo: PlatformInfo = {
  platform: process.platform,
  release: osRelease(),
  arch: process.arch,
  label: `${process.platform} ${osRelease()} ${process.arch} (${osVersion()})`.slice(0, 128),
};

let mainWindow: BrowserWindow | null = null;
let quitting = false;
let shutdownDone = false;
let shutdownStarted = false;
let emergencyQuit: EmergencyQuit | null = null;
let previewWanted = false;
let capabilities: EnvironmentCapabilities | null = null;
let vmSnapshot = unknownVm();
let probeResults: ProbeResults = {};
let probeRanAt: string | null = null;
let verification: VerificationRecord[] = [];
let nativeInfo: NativeHelperInfo = {
  available: false,
  enforce: false,
  detail: process.platform === "win32" ? "помощник не подключён в этой сборке" : "не Windows",
};

const supervisorTarget = () => {
  const c = supervisor?.connection;
  return c ? { port: c.port, token: c.token } : null;
};
const client = new BackendClient(supervisorTarget);
const classAudio = createClassAudio(client, trustedSender, () => mainWindow?.webContents ?? null, devOrigin);
const operator = new OperatorAuth(process.env);

// session id used for environment events = the bound session
let machine: ShellStateMachine;
const events = new EnvironmentEventQueue(client, () => machine?.state.session_id ?? null);

const guardPlatform: GuardPlatform = {
  registerShortcut: (acc, cb) => globalShortcut.register(acc, cb),
  isShortcutRegistered: (acc) => globalShortcut.isRegistered(acc),
  unregisterShortcut: (acc) => globalShortcut.unregister(acc),
  clearClipboard: () => clipboard.clear(),
  displayCount: () => screen.getAllDisplays().length,
  onDisplayChange: (cb) => {
    const added = () => cb("added");
    const removed = () => cb("removed");
    screen.on("display-added", added);
    screen.on("display-removed", removed);
    return () => {
      screen.removeListener("display-added", added);
      screen.removeListener("display-removed", removed);
    };
  },
  platform: process.platform,
  debugSwitches: () =>
    ["remote-debugging-port", "remote-debugging-pipe", "inspect", "inspect-brk", "js-flags"].filter((s) => app.commandLine.hasSwitch(s)),
};

const guard = new ExamGuard(() => mainWindow, events, guardPlatform, {
  enforce: cfg.nativeEnforce,
  vmSnapshot: process.platform === "win32" ? () => vmSnapshot : undefined,
  scanRemote: process.platform === "win32" ? () => checkRemoteEnvironment({ command: cfg.nativeHelperPath }) : undefined,
  emergencyAccelerator: cfg.emergencyAccelerator,
  onEmergencyHotkey: () => void emergencyExit("emergency_hotkey"),
});

machine = new ShellStateMachine(guard, { shell_version: app.getVersion(), platform: platformInfo.label });
const examSurface = new ExamSurface(() => mainWindow, (status) => push(EXAM_CHANNEL.status, status),
  (input) => guard.onBeforeInput(input), (id) => guard.onContentBlocked(id));
const classLock = new ClassLockController({
  client, window: () => mainWindow,
  setExamBlocked: (blocked) => examSurface.setBlocked("class-lock", blocked),
});
const classStateDelivery = new ClassStateDelivery({
  recover: (isCurrent) => classLock.rendererReady(isCurrent),
  send: (env) => push(PUSH.streamEvent, env),
  sessionId: () => machine.state.session_id,
  currentState: (msg) => classLock.isCurrentClassState(msg),
});

// ---------------------------------------------------------------- backend
const stream = new BackendSocket("stream", supervisorTarget, {
  onClose: () => {
    classStateDelivery.invalidate();
    examSurface.setBlocked("backend-stream", true);
    classLock.reset();
    classAudio.reset();
  },
  onEnvelope: (env) => {
    if (quitting) return;
    const msg = env.message;
    // Close the native website before forwarding a requested overlay to React.
    classLock.consumeClassState(msg);
    classAudio.observe(env);
    examSurface.consumeClassState(msg);
    if (msg.type === "session_state") void machine.observe(msg.session);
    if (!classStateDelivery.consume(env)) push(PUSH.streamEvent, env);
  },
});
const preview = new BackendSocket("preview", supervisorTarget, {
  onPreview: (meta, jpeg) => {
    if (previewWanted) push(PUSH.previewFrame, meta, new Uint8Array(jpeg));
  },
});

const supervisor: BackendSupervisor = new BackendSupervisor(
  { python: cfg.python, cwd: cfg.proctoringRoot, readyTimeoutMs: cfg.readyTimeoutMs },
  {
    onState: (state, detail) => {
      log.info(`backend ${state}: ${detail}`);
      machine.setBackend(state, detail);
    },
    onReady: (conn) => void onBackendReady(conn),
    onLost: (reason) => {
      classStateDelivery.invalidate();
      classLock.reset();
      classAudio.reset();
      stream.close();
      preview.close();
      void machine.backendLost(reason);
    },
  },
);

async function onBackendReady(conn: BackendConnection): Promise<void> {
  if (quitting) return;
  const h = await client.json("GET", BackendClient.path("health"));
  if (!h.ok) log.warn(`health after READY failed: ${h.error.message}`);
  await reportCapabilities();
  if (quitting) return;
  stream.open();
  if (previewWanted) preview.open();
  log.info(`backend connected (launch #${conn.launchId})`);
}

async function reportCapabilities(strict = false): Promise<void> {
  capabilities = buildCapabilities({
    platform: platformInfo,
    shellVersion: app.getVersion(),
    probe: probeResults,
    probeRanAt,
    records: verification,
    helper: nativeInfo,
  });
  let displayCount = 0;
  try { displayCount = screen.getAllDisplays().length; } catch { /* unknown is a preflight failure */ }
  capabilities = withDisplayCheck(capabilities, displayCount);
  if (process.platform === "win32") {
    capabilities = withRemoteCheck(capabilities, await checkRemoteEnvironment({ command: cfg.nativeHelperPath }));
    vmSnapshot = await checkVm({ command: cfg.nativeHelperPath });
    capabilities = withVmCheck(capabilities, vmSnapshot);
  }
  log.info(`capability matrix: ${JSON.stringify(summarize(capabilities))}`);
  if (!supervisor.connection) return;
  const r = await client.json("PUT", BackendClient.path("environment", "capabilities"), capabilities);
  if (!r.ok) {
    log.error(`PUT capabilities failed: ${r.error.code} ${r.error.message}`);
    if (strict) throw new Error("Не удалось обновить проверку защиты среды");
  }
}

// ---------------------------------------------------------------- emergency exit / release
async function emergencyExit(reason: string): Promise<void> {
  const sid = machine.state.session_id;
  const st = machine.boundSessionState;
  if (reason === "emergency_hotkey") {
    emergencyQuit ??= new EmergencyQuit({
      release: [
        () => { quitting = true; guard.preventEngage(); if (sid) machine.forbidEngage(sid); },
        () => guard.releaseSync("emergency_hotkey"),
        () => classStateDelivery.invalidate(true),
        () => classLock.reset(),
        () => classAudio.reset(),
        () => examSurface.dispose(),
        () => stream.close(),
        () => preview.close(),
      ],
      cleanup: [
        async () => { log.warn(`EMERGENCY EXIT (emergency_hotkey) session=${sid ?? "-"}`); },
        () => machine.releaseTo("normal", "emergency_exit", null),
        () => events.flush(),
        async () => {
          if (sid && st && !TERMINAL_STATES.includes(st) && supervisor.connection) {
            const r = await client.json("POST", BackendClient.path("sessions", sid, "abort"),
              { reason: "emergency_exit: emergency_hotkey" }, 1_500);
            if (!r.ok) log.error(`emergency abort failed: ${r.error.code}`);
          }
        },
      ],
      quit: () => app.quit(),
      forceExit: () => app.exit(1),
      report: (error) => log.error("emergency cleanup failed", error),
    });
    return emergencyQuit.request();
  }
  // The existing in-app panic action releases the exam and retains the recovery UI.
  log.warn(`EMERGENCY EXIT (${reason}) session=${sid ?? "-"}`);
  if (sid) machine.forbidEngage(sid);
  guard.releaseSync("emergency_exit");
  await machine.releaseTo("normal", "emergency_exit", null); // never depends on the backend
  await Promise.race([events.flush(), delay(2_000)]);
  if (sid && st && !TERMINAL_STATES.includes(st) && supervisor.connection) {
    const r = await client.json<SessionInfo>("POST", BackendClient.path("sessions", sid, "abort"), {
      reason: `emergency_exit: ${reason}`.slice(0, 200),
    });
    if (r.ok) await machine.observe(r.data);
    else log.error(`abort after emergency exit failed: ${r.error.code}`);
  }
}

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms).unref());

// ---------------------------------------------------------------- renderer push
function push(channel: string, ...args: unknown[]): void {
  const w = mainWindow;
  if (!w || w.isDestroyed() || w.webContents.isDestroyed()) return;
  w.webContents.send(channel, ...args);
}
let lastBoundSessionId: string | null = null;
machine.onChange((s) => {
  if (s.session_id !== lastBoundSessionId) { lastBoundSessionId = s.session_id; classStateDelivery.invalidate(); }
  examSurface.setShell(s); push(PUSH.shellState, s);
});

// ---------------------------------------------------------------- IPC
function trustedSender(event: IpcMainInvokeEvent | IpcMainEvent): boolean {
  const w = mainWindow;
  const frame = event.senderFrame;
  return (
    !quitting && !!w &&
    !w.isDestroyed() &&
    event.sender === w.webContents &&
    !!frame &&
    frame === event.sender.mainFrame &&
    isTrustedUrl(frame.url, devOrigin)
  );
}

function registerIpc(): void {
  classAudio.register(ipcMain);
  ipcMain.handle(LOCK_ACK_CHANNEL, (event, body: unknown) => trustedSender(event)
    ? classLock.confirmApplied(body) : { accepted: false, reason: "untrusted_sender" });
  ipcMain.handle(EXAM_CHANNEL.getStatus, (event) => trustedSender(event) ? examSurface.status : null);
  ipcMain.on(EXAM_CHANNEL.viewport, (event, rect: unknown) => { if (trustedSender(event)) examSurface.setViewport(rect); });
  ipcMain.on(EXAM_CHANNEL.reload, (event) => { if (trustedSender(event)) examSurface.reload(); });
  const api = createApi({
    client,
    machine,
    operator,
    capabilities: () => capabilities,
    refreshEnvironment: () => reportCapabilities(true),
    healthMetadata: () => ({
      computer_name: hostname().slice(0, 64),
      class_configured: !!(process.env.QORGAU_CLASS_SERVER?.trim() && process.env.QORGAU_CLASS_CODE?.trim()),
    }),
    emergencyExit,
    flushEvents: () => events.flush(),
    accessPolicy: cfg.accessPolicy,
    operatorTimeouts: { idleMs: cfg.operatorIdleMs, maxMs: cfg.operatorMaxMs },
    saveFile: async (defaultName, bytes, filters) => {
      const w = mainWindow;
      const opts = { defaultPath: join(app.getPath("documents"), defaultName), filters };
      const r = w ? await dialog.showSaveDialog(w, opts) : await dialog.showSaveDialog(opts);
      if (r.canceled || !r.filePath) return null;
      await writeFile(r.filePath, bytes);
      return r.filePath;
    },
  });
  for (const name of Object.keys(INVOKE) as InvokeName[]) {
    ipcMain.handle(INVOKE[name], async (event, ...args: unknown[]) => {
      if (!trustedSender(event)) {
        log.warn(`IPC ${name} from an untrusted sender rejected`);
        return fail(shellError("FORBIDDEN_ORIGIN", "untrusted_sender", "IPC sender is not the Adal window"));
      }
      return api[name](...args);
    });
  }
  ipcMain.on(SEND.previewSubscribed, (event, flag: unknown) => {
    if (!trustedSender(event) || typeof flag !== "boolean") return;
    previewWanted = flag;
    if (flag && supervisor.connection) preview.open();
    if (!flag) preview.close();
  });
  ipcMain.on(SEND.eventsSubscribed, (event, flag: unknown) => {
    if (!trustedSender(event) || typeof flag !== "boolean") return;
    void classStateDelivery.subscribed(flag);
  });
  // A07-student (A01-approved minimal IPC): full-screen calibration window. Exam mode keeps its own full screen.
  ipcMain.on(SEND.windowFullscreen, (event, flag: unknown) => {
    const w = mainWindow;
    if (!trustedSender(event) || typeof flag !== "boolean" || !w || w.isDestroyed()) return;
    applyCalibrationFullscreen(w, flag, guard.active, machine.state.exam_mode_active);
  });
}

// ---------------------------------------------------------------- web hardening
function hardenSession(ses: Session): void {
  classAudio.installPermissions(ses);
  ses.setDevicePermissionHandler(() => false);
  ses.setDisplayMediaRequestHandler((_req, callback) => callback({}));
  ses.setSpellCheckerEnabled(false);
  ses.on("will-download", (event) => {
    event.preventDefault();
    log.warn("download blocked");
  });
  // The renderer never talks to the network: only qorgau:// (and the dev server when configured).
  ses.webRequest.onBeforeRequest({ urls: ["http://*/*", "https://*/*", "ws://*/*", "wss://*/*"] }, (details, cb) => {
    const allowed = devOrigin !== null && (details.url.startsWith(devOrigin + "/") || details.url.startsWith(devOrigin.replace(/^http/, "ws")));
    if (!allowed) log.warn(`network request blocked: ${new URL(details.url).origin}`);
    cb({ cancel: !allowed });
  });
  if (devOrigin) {
    ses.webRequest.onHeadersReceived({ urls: [`${devOrigin}/*`] }, (details, cb) => {
      cb({ responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [devCsp(devOrigin)] } });
    });
  }
  ses.protocol.handle(APP_SCHEME, async (request) => {
    if (request.method !== "GET") return new Response("method not allowed", { status: 405 });
    const headers = { "content-security-policy": CSP, "x-content-type-options": "nosniff", "cache-control": "no-store" };
    const path = new URL(request.url).pathname;
    if (path === "/__probe.html") return new Response(PROBE_HTML, { headers: { ...headers, "content-type": "text/html; charset=utf-8" } });
    if (path === "/__probe.js") return new Response(PROBE_JS, { headers: { ...headers, "content-type": "text/javascript; charset=utf-8" } });
    const file = resolveAppFile(request.url, cfg.rendererDir);
    if (!file) return new Response("not found", { status: 404 });
    try {
      const data = await readFile(file);
      return new Response(data, { headers: { ...headers, "content-type": mimeFor(file) } });
    } catch {
      if (path === "/" || path === "/index.html") {
        return new Response(FALLBACK_HTML, { headers: { ...headers, "content-type": "text/html; charset=utf-8" } });
      }
      return new Response("not found", { status: 404 });
    }
  });
}

function hardenWebContents(wc: WebContents): void {
  wc.setWindowOpenHandler(() => {
    if (mainWindow && wc === mainWindow.webContents) guard.onWindowOpenBlocked();
    log.warn("window.open blocked");
    return { action: "deny" };
  });
  const onNav = (event: { preventDefault(): void }, url: string) => {
    if (isTrustedUrl(url, devOrigin)) return;
    event.preventDefault();
    if (mainWindow && wc === mainWindow.webContents) guard.onNavigationBlocked();
    log.warn("navigation blocked");
  };
  wc.on("will-navigate", onNav);
  wc.on("will-redirect", onNav);
  wc.on("will-frame-navigate", (details) => {
    if (!isTrustedUrl(details.url, devOrigin)) details.preventDefault();
  });
  wc.on("will-attach-webview", (event) => event.preventDefault());
}

app.on("web-contents-created", (_e, wc) => { if (!isExamWebContents(wc)) hardenWebContents(wc); });

// ---------------------------------------------------------------- window
function createWindow(ses: Session): BrowserWindow {
  prepareContentSession(ses);
  const w = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 640,
    show: false,
    title: "Adal",
    backgroundColor: "#ffffff",
    autoHideMenuBar: true,
    webPreferences: {
      preload: cfg.preloadPath,
      session: ses,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      webviewTag: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      experimentalFeatures: false,
      navigateOnDragDrop: false,
      spellcheck: false,
      safeDialogs: true,
      devTools: cfg.allowDevTools,
      backgroundThrottling: false,
    },
  });
  w.once("ready-to-show", () => w.show());
  protectContent(w.webContents, () => guard.active, id => guard.onContentBlocked(id));
  w.on("resize", () => examSurface.resized());
  w.webContents.on("did-start-navigation", (_event, _url, inPlace, isMainFrame) => {
    if (isMainFrame && !inPlace) {
      classStateDelivery.invalidate(true);
      machine.setOperator(false);
      examSurface.setViewport(null);
      examSurface.setBlocked("renderer-loading", true);
      classAudio.reset();
      void classLock.rendererLost();
    }
  });
  w.webContents.on("did-finish-load", () => examSurface.setBlocked("renderer-loading", false));
  w.webContents.on("before-input-event", (event, input) => {
    if (guard.onBeforeInput(input)) event.preventDefault();
  });
  w.on("close", (event) => {
    if (!quitting && guard.onCloseRequest()) event.preventDefault();
  });
  w.on("blur", () => guard.onBlur());
  w.on("focus", () => guard.onFocus());
  w.webContents.on("devtools-opened", () => guard.onDevToolsOpened());
  w.webContents.on("context-menu", (event) => event.preventDefault());

  let unresponsiveTimer: NodeJS.Timeout | null = null;
  w.on("unresponsive", () => {
    examSurface.setBlocked("renderer-unresponsive", true);
    log.warn("renderer unresponsive");
    unresponsiveTimer ??= setTimeout(() => {
      unresponsiveTimer = null;
      events.emit({ action: "enforcement_error", enforcement: "failed", mechanism: "electron.watchdog", scope: "app", detail: { shortcut: "renderer_unresponsive" } });
      void machine.releaseTo("error", "renderer_unresponsive", shellError("INTERNAL", "enforcement_error", "Renderer unresponsive: restrictions released", true));
    }, 5_000);
  });
  w.on("responsive", () => {
    examSurface.setBlocked("renderer-unresponsive", false);
    if (unresponsiveTimer) clearTimeout(unresponsiveTimer);
    unresponsiveTimer = null;
    void refreshBoundSession();
  });
  const crashes: number[] = [];
  w.webContents.on("render-process-gone", (_e, details) => {
    classStateDelivery.invalidate(true);
    machine.setOperator(false);
    examSurface.setBlocked("renderer-loading", true);
    classAudio.reset();
    void classLock.rendererLost();
    log.error(`renderer gone: ${details.reason} (exit ${details.exitCode})`);
    events.emit({ action: "enforcement_error", enforcement: "failed", mechanism: "electron.watchdog", scope: "app", detail: { shortcut: "renderer_gone" } });
    void machine.releaseTo("error", "renderer_gone", shellError("INTERNAL", "enforcement_error", `Renderer ${details.reason}: restrictions released`, true));
    if (quitting || details.reason === "clean-exit") return;
    const now = Date.now();
    crashes.push(now);
    while (crashes.length && now - crashes[0]! > 120_000) crashes.shift();
    const loop = crashes.length >= 3;
    if (loop) {
      // crash loop: stop toggling restrictions; the session continues unrestricted and is recorded
      const sid = machine.state.session_id;
      if (sid) machine.forbidEngage(sid);
      log.error("renderer crash loop: exam restrictions will not be re-engaged for this session");
    }
    setTimeout(() => {
      if (w.isDestroyed()) return;
      // after the reload, main itself re-reads the bound session: a RUNNING session re-engages
      // (unless latched) without relying on the renderer to ask
      if (!loop) w.webContents.once("did-finish-load", () => void refreshBoundSession());
      void w.loadURL(cfg.devRendererUrl ?? APP_ENTRY);
    }, 1_000);
  });
  w.on("closed", () => {
    examSurface.dispose();
    mainWindow = null;
    if (!quitting) app.quit();
  });
  void w.loadURL(cfg.devRendererUrl ?? APP_ENTRY);
  return w;
}

/** After a renderer recovery: re-read the bound session; RUNNING re-engages exam mode explicitly. */
async function refreshBoundSession(): Promise<void> {
  const sid = machine.state.session_id;
  if (!sid || !supervisor.connection) return;
  const r = await client.json<SessionInfo>("GET", BackendClient.path("sessions", sid));
  if (r.ok) await machine.observe(r.data);
}

async function loadVerification(): Promise<void> {
  try {
    const { records, invalid } = parseVerificationRecords(await readFile(cfg.verificationPath, "utf8"));
    verification = records;
    if (invalid) log.warn(`${invalid} invalid verification record(s) ignored`);
  } catch {
    verification = [];
  }
}

// ---------------------------------------------------------------- lifecycle
async function shutdown(reason: string): Promise<void> {
  classAudio.reset();
  classLock.reset();
  examSurface.dispose();
  log.info(`shutdown: ${reason}`);
  await machine.releaseTo("normal", "app_quit", null);
  await Promise.race([events.flush(), delay(2_000)]);
  stream.close();
  preview.close();
  await supervisor.stop(); // stdin "shutdown" -> backend aborts the active session and exits
  globalShortcut.unregisterAll();
}

app.on("second-instance", () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

app.on("before-quit", (event) => {
  quitting = true;
  if (shutdownDone) return;
  event.preventDefault();
  if (shutdownStarted) return;
  shutdownStarted = true;
  const hard = setTimeout(() => {
    log.error("shutdown timed out; exiting");
    guard.releaseSync("shutdown_timeout");
    app.exit(1);
  }, 20_000);
  void shutdown("before-quit").catch(error => log.error("shutdown failed", error)).finally(() => {
    clearTimeout(hard);
    shutdownDone = true;
    app.quit();
  });
});

app.on("will-quit", () => {
  emergencyQuit?.complete();
  guard.releaseSync("will_quit");
  globalShortcut.unregisterAll();
});

app.on("window-all-closed", () => {
  // The hidden startup self-test window closes before the exam window exists (QA-WIN-007);
  // closing the real main window already quits via its "closed" handler.
  if (mainWindow !== null) app.quit();
});

for (const sig of ["SIGINT", "SIGTERM"] as const) {
  process.on(sig, () => {
    log.warn(`${sig}: quitting`);
    app.quit();
  });
}

process.on("uncaughtException", (err) => {
  log.error("uncaught exception in main", err);
  try {
    events.emit({ action: "enforcement_error", enforcement: "failed", mechanism: "electron.watchdog", scope: "app", detail: { shortcut: "main_exception" } });
  } catch {
    /* reporting must never block the release */
  }
  // main is in an unknown state: release now; if an exam was in progress, never re-engage that
  // session (no engage/crash loop). A session still in preflight is not latched: its later start
  // engages normally.
  const sid = machine.state.session_id;
  if (sid && (guard.active || machine.boundSessionState === "running")) machine.forbidEngage(sid);
  guard.releaseSync("main_exception");
  void machine.releaseTo("error", "engage_failed", shellError("INTERNAL", "enforcement_error", "Shell error: restrictions released", true));
});
process.on("unhandledRejection", (err) => log.error("unhandled rejection in main", err));

/** Hidden-window self-test of the in-app restrictions (real input pipeline). Never during a session. */
async function runStartupSelfTest(ses: Session): Promise<void> {
  if (!cfg.selfTest) {
    log.info("self-test disabled (QORGAU_SHELL_SELFTEST=0); in-app items stay unverified");
    return;
  }
  const driver = createElectronProbeDriver(ses, cfg.allowDevTools);
  try {
    probeResults = await runSelfTest(driver);
    probeRanAt = new Date().toISOString();
    log.info(`self-test: ${probeSummary(probeResults)}`);
  } catch (err) {
    log.error("self-test failed to run", err);
    probeResults = {};
  } finally {
    driver.dispose();
  }
}

/** Detect the optional Windows helper (self-check only; never engaged outside a session). */
async function detectNativeHelper(): Promise<void> {
  if (process.platform !== "win32") return;
  nativeInfo = await checkHelper({ command: cfg.nativeHelperPath }, process.platform, cfg.nativeEnforce);
  log.info(`native helper: ${nativeInfo.detail}`);
  if (!nativeInfo.available) return;
  const helper = new NativeHelper(
    { command: cfg.nativeHelperPath },
    {
      enforce: cfg.nativeEnforce,
      maxMinutes: 240,
      onObservation: (o) => guard.onNativeObservation(o.action, o.enforcement, o.mechanism, o.detail),
    },
  );
  guard.setNative(helper);
}

void app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  const ses = electronSession.fromPartition(PARTITION, { cache: false });
  hardenSession(ses);
  registerIpc();
  await loadVerification();
  await runStartupSelfTest(ses); // before the exam window exists; feeds the capability matrix
  await detectNativeHelper();
  mainWindow = createWindow(ses);
  await reportCapabilities(); // matrix now includes self-test + helper results
  await supervisor.start();
});
