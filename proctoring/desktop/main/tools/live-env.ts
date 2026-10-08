// Captain-approved component LIVE: actual Electron + unchanged integration ExamGuard/native helper.
// No backend exam/camera/microphone is started. No automatic acceptance promotion.
import { app, BrowserWindow, clipboard, globalShortcut, screen } from "electron";
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { release as osRelease } from "node:os";
import { ExamGuard, type GuardPlatform } from "../src/environment/guard";
import { NativeHelper } from "../src/environment/native";
import { checkRemoteEnvironment } from "../src/environment/remote";
import { withDisplayCheck, withRemoteCheck, environmentBlockReason } from "../src/environment/preflight";

const args = process.argv.slice(2);
const value = (key: string) => args[args.indexOf(key) + 1];
const preflightOnly = args.includes("--preflight-only");
if (!preflightOnly && (!args.includes("--approved-live") || value("--max-minutes") !== "2")) {
  throw new Error("LIVE needs --approved-live --max-minutes 2");
}
if (!args.includes("--out") || !args.includes("--root") || !args.includes("--source-sha")) throw new Error("out/root/source-sha required");
const root = resolve(value("--root"));
const out = resolve(value("--out"));
mkdirSync(out, { recursive: true });
const started = performance.now();
function record(type: string, detail: object = {}) {
  const event = { type, t_ms: Math.round(performance.now() - started), wall_time: new Date().toISOString(), ...detail };
  appendFileSync(resolve(out, "events.jsonl"), JSON.stringify(event) + "\n");
  console.log(JSON.stringify(event));
}
let win: BrowserWindow | null = null;
let guard: ExamGuard | null = null;
let ending = false;
let endTimer: NodeJS.Timeout | undefined;
const command = { command: resolve(root, "desktop/native/qorgau_guard.py") };

async function end(reason: string) {
  if (ending) return;
  ending = true;
  if (endTimer) clearTimeout(endTimer);
  await guard?.release(reason);
  record("released", { reason, guard_active: guard?.active ?? false });
  globalShortcut.unregisterAll();
  if (win && !win.isDestroyed()) win.destroy();
  app.quit();
}
app.on("before-quit", () => guard?.releaseSync("app_quit"));
process.on("uncaughtException", error => { record("error", { message: error.message }); void end("exception"); });
process.on("unhandledRejection", error => { record("error", { message: String(error) }); void end("rejection"); });

void app.whenReady().then(async () => {
  const displayCount = screen.getAllDisplays().length;
  const remote = await checkRemoteEnvironment(command);
  const caps = withRemoteCheck(withDisplayCheck({ reported_at: new Date().toISOString(), platform: process.platform,
    shell_version: "a06-live-harness", exam_mode_supported: true, items: [] }, displayCount), remote);
  const machine = { source_sha: value("--source-sha"), platform: process.platform, os_release: osRelease(),
    electron: process.versions.electron, mode: preflightOnly ? "preflight_only" : "enforce", max_minutes: 2,
    display_count: displayCount, remote, environment_block_reason: environmentBlockReason(caps),
    display_condition_pass: displayCount === 1,
    scope: "component LIVE: unchanged integration ExamGuard + NativeHelper, not full backend exam",
    acceptance: "pending operator observations; method calls alone do not prove focus return" };
  writeFileSync(resolve(out, "machine.json"), JSON.stringify(machine, null, 2));
  record("preflight", machine);
  if (preflightOnly) { app.quit(); return; }
  if (environmentBlockReason(caps)) { record("refused", { reason: environmentBlockReason(caps) }); app.quit(); return; }
  win = new BrowserWindow({ width: 1000, height: 720, title: "ADAL — A06 LIVE (2 minutes)",
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, devTools: false } });
  win.setMenuBarVisibility(false);
  const native = new NativeHelper(command, { enforce: true, maxMinutes: 2,
    onObservation: o => {
      guard?.onNativeObservation(o.action, o.enforcement, o.mechanism, o.detail);
      if (o.action === "enforcement_error" && o.detail.shortcut === "helper_exit") void end("helper_exit");
    } });
  const platform: GuardPlatform = {
    platform: process.platform, debugSwitches: () => [],
    clearClipboard: () => clipboard.clear(), displayCount: () => screen.getAllDisplays().length,
    registerShortcut: (key, cb) => globalShortcut.register(key, cb),
    isShortcutRegistered: key => globalShortcut.isRegistered(key), unregisterShortcut: key => globalShortcut.unregister(key),
    onDisplayChange: cb => {
      const added = () => cb("added"), removed = () => cb("removed");
      screen.on("display-added", added); screen.on("display-removed", removed);
      return () => { screen.removeListener("display-added", added); screen.removeListener("display-removed", removed); };
    },
  };
  guard = new ExamGuard(() => win, { emit: event => record("environment", event) }, platform, {
    enforce: true, native, emergencyAccelerator: "CommandOrControl+Alt+Shift+F12",
    onEmergencyHotkey: () => void end("emergency_hotkey"), scanRemote: () => checkRemoteEnvironment(command),
  });
  const focus = win.focus.bind(win), moveTop = win.moveTop.bind(win);
  win.focus = () => { record("focus_attempt", { focused_before: win!.isFocused() }); focus(); };
  win.moveTop = () => { record("move_top_attempt"); moveTop(); };
  win.on("blur", () => { record("window_blur"); guard?.onBlur(); });
  win.on("focus", () => { record("window_focus", { focused: win!.isFocused() }); guard?.onFocus(); });
  win.on("close", event => { if (guard?.onCloseRequest()) event.preventDefault(); });
  win.on("unresponsive", () => void end("unresponsive"));
  win.webContents.on("render-process-gone", () => void end("renderer_gone"));
  win.webContents.on("before-input-event", (event, input) => { if (guard?.onBeforeInput(input)) event.preventDefault(); });
  await win.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(`<!doctype html><html lang="ru"><meta charset="utf-8"><title>ADAL A06 LIVE</title>
    <body style="background:#102238;color:#fff;font:26px system-ui;padding:60px;line-height:1.5">
    <h1>ADAL · Проверка защиты среды</h1><p>Лимит: 2 минуты. Закрытие — автоматически.</p>
    <p>1. Нажмите Alt+Tab. В enforce переключение может блокироваться.</p>
    <p>2. Нажмите Ctrl+Alt+Del → Диспетчер задач.<br>После возврата с системного экрана наблюдайте, вернулся ли фокус сюда.</p>
    <p>3. Для выхода нажмите <b>Ctrl+Alt+Shift+F12</b>.</p>
    <p>После закрытия проверьте обычный Alt+Tab и ввод текста, затем сообщите результат в чате.</p>
    <p>Записываются только события и имя процесса. Буфер очищается без чтения.</p></body></html>`));
  if (ending) return;
  // Limit all Electron restrictions as well as the native hook to two minutes.
  endTimer = setTimeout(() => void end("max_minutes_2"), 120_000);
  await guard.engage("a06-live-integration");
  if (!guard.shortcutRegistrations.some(r => r.purpose === "emergency_exit" && r.registered)) {
    await end("emergency_registration_failed"); return;
  }
  record("engaged", { max_minutes: 2, steps: guard.lastEngage });
});
