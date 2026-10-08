// Safe fixture: actual audio IPC+permission policy, no production main, CV or OS guards.
import { app, BrowserWindow, ipcMain, protocol, session } from "electron";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { BackendClient } from "../../../main/src/backend/client";
import { createClassAudio } from "../../../main/src/class-audio";
import { CSP } from "../../../main/src/security/web";

app.commandLine.appendSwitch("use-fake-device-for-media-stream");
app.commandLine.appendSwitch("use-fake-ui-for-media-stream");
app.commandLine.appendSwitch("use-file-for-fake-audio-capture", join(__dirname, "tone.wav"));
app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");
app.setPath("userData", join(__dirname, "profile"));
protocol.registerSchemesAsPrivileged([{ scheme: "qorgau", privileges: { standard: true, secure: true } }]);
let win: BrowserWindow;
const client = new BackendClient(() => ({ port: Number(process.env.AUDIO_FIXTURE_PORT), token: process.env.AUDIO_FIXTURE_TOKEN! }));
const audio = createClassAudio(client, (event) => event.sender === win.webContents && event.senderFrame === win.webContents.mainFrame,
  () => win?.webContents ?? null, null);
app.whenReady().then(async () => {
  const ses = session.fromPartition("audio-fixture");
  await ses.protocol.handle("qorgau", (request) => {
    const name = new URL(request.url).pathname === "/renderer.js" ? "renderer.js" : "index.html";
    return new Response(readFileSync(join(__dirname, name)), { headers: { "Content-Type": name.endsWith("js") ? "text/javascript" : "text/html", "Content-Security-Policy": CSP } });
  });
  win = new BrowserWindow({ width: 960, height: 650, show: false, webPreferences: { partition: "audio-fixture", preload: join(__dirname, "preload.cjs"),
    sandbox: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false } });
  audio.installPermissions(ses);
  win.webContents.setAudioMuted(true);
  ses.setDevicePermissionHandler(() => false);
  ses.setDisplayMediaRequestHandler((_request, callback) => callback({}));
  audio.register(ipcMain);
  await win.loadURL("qorgau://app/index.html");
  let after = 0;
  let busy = false;
  setInterval(async () => {
    if (busy || win.isDestroyed()) return;
    busy = true;
    try {
      const response = await client.json<{ events: any[] }>("GET", `/v1/test/events?after=${after}`);
      if (response.ok) for (const envelope of response.data.events) {
        after = envelope.seq; audio.observe(envelope); win.webContents.send("fixture:events", envelope);
      }
      else { audio.reset(); win.webContents.send("fixture:events", { message: { type: "class_state", connection: "stopped" } }); }
    } finally { busy = false; }
  }, 30).unref();
  (globalThis as any).fixture = { win, audio, session: ses };
});
