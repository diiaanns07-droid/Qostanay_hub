import { app, ipcMain, type Session, type WebContents } from "electron";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { CONTENT_PRELOAD, isContentAction, type ContentAction } from "./content-policy";

const gates = new WeakMap<WebContents, { active: () => boolean; report: (id: ContentAction) => void }>();
const prepared = new WeakSet<Session>();
let listening = false;

export function prepareContentSession(ses: Session): void {
  if (!listening) {
    ipcMain.on("qorgau:content-policy", (event, id: unknown) => {
      const gate = gates.get(event.sender);
      const enabled = !!gate?.active() && isContentAction(id);
      event.returnValue = enabled;
      if (enabled && isContentAction(id)) gate!.report(id);
    });
    listening = true;
  }
  if (prepared.has(ses)) return;
  // Included in the main bundle: no separate build/packaging entry or source-tree dependency.
  const dir = join(app.getPath("userData"), "shell-preloads");
  mkdirSync(dir, { recursive: true });
  const path = join(dir, "content-policy-v1.cjs");
  writeFileSync(path, CONTENT_PRELOAD, "utf8");
  ses.registerPreloadScript({ type: "frame", id: "adal-content-policy", filePath: path });
  prepared.add(ses);
}

export function protectContent(wc: WebContents, active: () => boolean, report: (id: ContentAction) => void): void {
  const last = new Map<ContentAction, number>();
  const reportOnce = (id: ContentAction) => {
    const now = Date.now();
    if (now - (last.get(id) ?? -Infinity) < 500) return;
    last.set(id, now);
    report(id);
  };
  gates.set(wc, { active, report: reportOnce });
  let leftDown = false;
  wc.on("context-menu", event => { if (active()) { event.preventDefault(); reportOnce("context_menu"); } });
  wc.on("before-mouse-event", (event, mouse) => {
    if (mouse.type === "mouseDown" && mouse.button === "left") leftDown = true;
    if (mouse.type === "mouseUp" || mouse.type === "mouseLeave") leftDown = false;
    if (!active()) { leftDown = false; return; }
    if (mouse.button === "right" && (mouse.type === "mouseDown" || mouse.type === "mouseUp")) {
      event.preventDefault(); reportOnce("context_menu");
    }
    // Also covers subframes, before Chromium starts selecting/dragging a payload.
    if (mouse.type === "mouseMove" && (leftDown || mouse.button === "left" || mouse.modifiers?.includes("leftbuttondown"))) {
      event.preventDefault(); reportOnce("drag");
    }
  });
  wc.on("console-message", details => {
    // Chromium's CSP also blocks print in subframes without a preload. Store only the operation id.
    if (active() && /print/i.test(details.message) && /sandbox/i.test(details.message) && /allow-modals/.test(details.message)) reportOnce("print");
    if (active() && /sandbox/i.test(details.message) && /allow-popups/.test(details.message)) reportOnce("new_window");
  });
  wc.on("zoom-changed", event => { if (active()) { event.preventDefault(); wc.setZoomFactor(1); reportOnce("zoom"); } });
  void wc.setVisualZoomLevelLimits(1, 1);
}
