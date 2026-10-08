// Test doubles shared by A06 tests (no Electron).
import type { SessionInfo, SessionState } from "@contracts/qorgau-v1.generated";
import { fixtures } from "@contracts/fixtures.generated";
import type { EnvEventInput, EventSink } from "../environment/events";
import type { GuardPlatform, GuardWindow } from "../environment/guard";
import type { Guard } from "../shell/state";

export class FakeGuard implements Guard {
  active = false;
  calls: string[] = [];
  failNextEngage = false;
  engageDelayMs = 0;
  async engage(sessionId: string): Promise<void> {
    this.calls.push(`engage:${sessionId}`);
    if (this.engageDelayMs) await new Promise((r) => setTimeout(r, this.engageDelayMs));
    if (this.failNextEngage) {
      this.failNextEngage = false;
      throw new Error("kiosk refused");
    }
    this.active = true;
  }
  async release(reason: string): Promise<void> {
    this.calls.push(`release:${reason}`);
    this.active = false;
  }
}

export function sessionInfo(id: string, state: SessionState): SessionInfo {
  const base = fixtures["SessionInfo.running"] as unknown as SessionInfo;
  return { ...structuredClone(base), session_id: id, state };
}

export class RecordingSink implements EventSink {
  events: EnvEventInput[] = [];
  emit(e: EnvEventInput): void {
    this.events.push(e);
  }
  actions(): string[] {
    return this.events.map((e) => `${e.action}:${e.enforcement}`);
  }
}

export class FakeWindow implements GuardWindow {
  flags: Record<string, unknown> = {};
  log: string[] = [];
  destroyed = false;
  focused = true;
  throwOn = new Set<string>();
  devtoolsOpen = false;
  private rec(name: string, value: unknown) {
    if (this.throwOn.has(name)) throw new Error(`${name} failed`);
    this.flags[name] = value;
    this.log.push(`${name}=${String(value)}`);
  }
  isDestroyed() {
    return this.destroyed;
  }
  setKiosk(f: boolean) {
    this.rec("kiosk", f);
  }
  setFullScreen(f: boolean) {
    this.rec("fullscreen", f);
  }
  setAlwaysOnTop(f: boolean, level?: string) {
    this.rec("alwaysOnTop", f ? level ?? "floating" : false);
  }
  setContentProtection(f: boolean) {
    this.rec("contentProtection", f);
  }
  setMinimizable(f: boolean) {
    this.rec("minimizable", f);
  }
  setClosable(f: boolean) {
    this.rec("closable", f);
  }
  setResizable(f: boolean) {
    this.rec("resizable", f);
  }
  setMovable(f: boolean) {
    this.rec("movable", f);
  }
  isFocused() {
    return this.focused;
  }
  focus() {
    this.log.push("focus");
  }
  show() {
    this.log.push("show");
  }
  moveTop() {
    this.log.push("moveTop");
  }
  webContents = {
    isDevToolsOpened: () => this.devtoolsOpen,
    closeDevTools: () => {
      this.devtoolsOpen = false;
      this.log.push("closeDevTools");
    },
  };
}

export class FakePlatform implements GuardPlatform {
  registered = new Map<string, () => void>();
  refuse = new Set<string>();
  clipboardClears = 0;
  displays = 1;
  displayCount() { return this.displays; }
  displayCb: ((k: "added" | "removed" | "metrics") => void) | null = null;
  switches: string[] = [];
  constructor(public platform: NodeJS.Platform = "win32") {}
  registerShortcut(acc: string, cb: () => void) {
    if (this.refuse.has(acc)) return false;
    this.registered.set(acc, cb);
    return true;
  }
  isShortcutRegistered(acc: string) {
    return this.registered.has(acc);
  }
  unregisterShortcut(acc: string) {
    this.registered.delete(acc);
  }
  clearClipboard() {
    this.clipboardClears += 1;
  }
  onDisplayChange(cb: (k: "added" | "removed" | "metrics") => void) {
    this.displayCb = cb;
    return () => {
      this.displayCb = null;
    };
  }
  debugSwitches() {
    return this.switches;
  }
}
