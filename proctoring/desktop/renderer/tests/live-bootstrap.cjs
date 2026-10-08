// QA-only wrapper around the real main entry. Isolated profile, no debug flags, no fake bridge.
const { app, BrowserWindow } = require("electron");
const { mkdirSync, writeFileSync } = require("node:fs");
const { resolve, join } = require("node:path");
const { release } = require("node:os");
const out = process.env.QORGAU_QA_OUTPUT;
if (!out) throw new Error("Run through renderer/tests/start-live.mjs");
const desktop = resolve(__dirname, "../..");
mkdirSync(join(out, "profile"), { recursive: true });
app.setPath("userData", join(out, "profile"));
app.setAppPath(desktop);
require(join(desktop, "dist/main/main.cjs"));
let busy = false;
let lastShot = "";
const interval = setInterval(async () => {
  if (busy) return;
  const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL() === "qorgau://app/index.html");
  if (!w || w.isDestroyed()) return;
  busy = true;
  try {
    const snapshot = await w.webContents.executeJavaScript(`(async () => {
      const bridge = window.qorgau;
      if (!bridge) return null;
      if (!window.__a07LiveEvidence) {
        window.__a07LiveEvidence = [];
        bridge.subscribeEvents((env) => {
          const m = env.message;
          if (m.type !== 'incident') return;
          const i = m.change.incident;
          window.__a07LiveEvidence.push({ incident_id:i.incident_id, rule_id:i.rule_id, category:i.category,
            state:i.state, t_start_ms:i.t_start_ms, t_end_ms:i.t_end_ms, duration_ms:i.duration_ms });
          if (window.__a07LiveEvidence.length > 200) window.__a07LiveEvidence.shift();
        });
      }
      const shell = await bridge.getShellState();
      const r = shell.session_id ? await bridge.getSession(shell.session_id) : null;
      const session = r?.ok ? r.data : null;
      return { session_id:session?.session_id, source_mode:session?.source_mode, state:session?.state,
        calibration:session?.calibration, incidents:window.__a07LiveEvidence,
        calibration_visible:!!document.querySelector('.calfs'), locked:!!document.querySelector('.lockscreen') };
    })()`);
    if (!snapshot) return;
    writeFileSync(join(out, "observed.json"), JSON.stringify({ ...snapshot, at:new Date().toISOString(),
      platform:process.platform, os_release:release(), fullscreen:w.isFullScreen(), native_helper:false,
      acceptance:"NOT_RECORDED: requires the participant's confirmation of the gaze gesture" }, null, 2));
    const target = snapshot.calibration?.current_target;
    if (snapshot.source_mode === "live" && snapshot.calibration_visible && w.isFullScreen() && target && target !== lastShot) {
      lastShot = target;
      writeFileSync(join(out, `calibration-${target}.png`), (await w.webContents.capturePage()).toPNG());
    }
  } catch (error) {
    writeFileSync(join(out, "qa-error.txt"), String(error));
  } finally { busy = false; }
}, 1500);
app.on("before-quit", () => clearInterval(interval));
