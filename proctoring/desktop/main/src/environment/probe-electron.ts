// Electron implementation of the self-test driver (owner: A06). Hidden offscreen window, same
// session/partition and hardening as the exam window (web-contents-created handlers apply), same key
// policy. No preload: the probe page gets no bridge. Destroyed right after the test.
import { BrowserWindow, type Session } from "electron";
import { APP_ORIGIN } from "../security/web";
import { classifyExamKey } from "./keyboard";
import type { KeySpec, ProbeDriver } from "./probe";

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function pageKeyOf(input: Electron.Input): string {
  return `${input.control ? "C" : ""}${input.alt ? "A" : ""}${input.shift ? "S" : ""}:${input.code}`;
}

export function createElectronProbeDriver(ses: Session, devTools: boolean): ProbeDriver {
  const win = new BrowserWindow({
    show: false,
    width: 480,
    height: 320,
    webPreferences: {
      session: ses,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webviewTag: false,
      webSecurity: true,
      devTools,
      offscreen: true,
      backgroundThrottling: false,
    },
  });
  const wc = win.webContents;
  const seen = new Map<string, number>();
  let disposing = false;
  wc.on("before-input-event", (event, input) => {
    const d = classifyExamKey(input);
    if (!d?.prevent) return;
    event.preventDefault();
    if (input.type === "keyDown" || input.type === "rawKeyDown") {
      const k = pageKeyOf(input);
      seen.set(k, (seen.get(k) ?? 0) + 1);
    }
  });
  wc.on("devtools-opened", () => wc.closeDevTools());
  win.on("close", (event) => {
    if (!disposing) event.preventDefault();
  });

  return {
    async load() {
      await win.loadURL(`${APP_ORIGIN}/__probe.html`);
      wc.focus();
    },
    async sendKey(spec: KeySpec) {
      wc.sendInputEvent({ type: "keyDown", keyCode: spec.keyCode, modifiers: spec.modifiers });
      if (spec.modifiers.length === 0 && spec.keyCode.length === 1) wc.sendInputEvent({ type: "char", keyCode: spec.keyCode.toLowerCase() });
      wc.sendInputEvent({ type: "keyUp", keyCode: spec.keyCode, modifiers: spec.modifiers });
      await delay(80);
    },
    policySaw(spec: KeySpec) {
      return seen.get(spec.pageKey) ?? 0;
    },
    async pageCounts() {
      const raw = (await wc.executeJavaScript("JSON.stringify(window.__qorgauProbe ? window.__qorgauProbe.keys : {})")) as string;
      return JSON.parse(raw) as Record<string, number>;
    },
    async tryWindowOpen() {
      const windowsBefore = BrowserWindow.getAllWindows().length;
      // userGesture=true so the popup blocker is not what stops it: the shell's handler must
      const returnedNull = (await wc.executeJavaScript(`window.open("${APP_ORIGIN}/__probe.html") === null`, true)) as boolean;
      await delay(200);
      return { returnedNull, windowsBefore, windowsAfter: BrowserWindow.getAllWindows().length };
    },
    async tryNavigate() {
      const urlBefore = wc.getURL();
      await wc.executeJavaScript(`location.href = "https://example.invalid/"; true`, true);
      await delay(300);
      return { urlBefore, urlAfter: wc.getURL() };
    },
    async tryDevTools() {
      wc.openDevTools({ mode: "detach" });
      await delay(300);
      return { openAfter: wc.isDevToolsOpened() };
    },
    async tryClose() {
      win.close();
      await delay(100);
      return { stillOpen: !win.isDestroyed() };
    },
    dispose() {
      disposing = true;
      if (!win.isDestroyed()) win.destroy();
    },
  };
}
