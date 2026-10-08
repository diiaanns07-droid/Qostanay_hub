// Exam-mode guard (owner: A06): engages/releases every restriction for ONE explicitly started session.
//
// Window/app scope (all platforms): kiosk + fullscreen + always-on-top, content protection (window
// excluded from screen capture where the OS supports it), close prevention, in-window key policy,
// focus-loss/display-change detection, clipboard cleared on entry and exit, emergency hotkey.
// OS scope (Windows only, optional): native helper (desktop/native) started in dry-run unless the
// operator explicitly enabled enforcement for a controlled test.
//
// Release is idempotent, never throws, and every step is independent (one failing step does not
// keep another restriction engaged). releaseSync() is usable from crash handlers.
// Electron objects are injected through small structural interfaces so this is tested in Node.
import type { EnforcementResult, EnvironmentAction } from "@contracts/qorgau-v1.generated";
import { logger } from "../log";
import type { Guard } from "../shell/state";
import type { EventSink } from "./events";
import { classifyExamKey, KeyEventThrottle, type KeyInput } from "./keyboard";

const log = logger("guard");

export interface GuardWindow {
  isDestroyed(): boolean;
  setKiosk(flag: boolean): void;
  setFullScreen(flag: boolean): void;
  setAlwaysOnTop(flag: boolean, level?: "normal" | "floating" | "torn-off-menu" | "modal-panel" | "main-menu" | "status" | "pop-up-menu" | "screen-saver"): void;
  setContentProtection(enable: boolean): void;
  setMinimizable(flag: boolean): void;
  setClosable(flag: boolean): void;
  setResizable(flag: boolean): void;
  setMovable(flag: boolean): void;
  isFocused(): boolean;
  focus(): void;
  show(): void;
  moveTop(): void;
  webContents: { isDevToolsOpened(): boolean; closeDevTools(): void };
}

export interface GuardPlatform {
  registerShortcut(accelerator: string, cb: () => void): boolean;
  isShortcutRegistered(accelerator: string): boolean;
  unregisterShortcut(accelerator: string): void;
  clearClipboard(): void;
  /** Subscribe to display changes; returns an unsubscribe function. */
  onDisplayChange(cb: (kind: "added" | "removed" | "metrics") => void): () => void;
  platform: NodeJS.Platform;
  /** Debug switches (remote debugging, inspector) make exam mode unsafe. */
  debugSwitches(): string[];
}

export interface NativeHelperHandle {
  /** Starts the helper bound to this session; resolves when its READY line arrived. */
  start(sessionId: string): Promise<{ mode: "dry_run" | "enforce" }>;
  stop(): Promise<void>;
  stopSync(): void;
  readonly running: boolean;
}

export interface GuardOptions {
  emergencyAccelerator: string;
  onEmergencyHotkey(): void;
  native?: NativeHelperHandle | null;
  /** Re-focus attempts after blur (not counted as prevention). */
  refocus?: boolean;
  now?: () => number;
}

export interface ShortcutRegistration {
  accelerator: string;
  purpose: "emergency_exit" | "print_screen";
  registered: boolean;
}

export class ExamGuard implements Guard {
  private activeSession: string | null = null;
  private blurAt: number | null = null;
  private unsubDisplay: (() => void) | null = null;
  private registrations: ShortcutRegistration[] = [];
  private throttle: KeyEventThrottle;
  private refocusTimer: NodeJS.Timeout | null = null;
  /** Last engage report (for the handoff/diagnostics; no user content). */
  lastEngage: { steps: Record<string, "ok" | "failed" | "skipped">; registrations: ShortcutRegistration[] } | null = null;

  constructor(
    private readonly win: () => GuardWindow | null,
    private readonly sink: EventSink,
    private readonly platform: GuardPlatform,
    private readonly opts: GuardOptions,
  ) {
    this.throttle = new KeyEventThrottle(500, opts.now);
  }

  get active(): boolean {
    return this.activeSession !== null;
  }

  private emit(action: EnvironmentAction, enforcement: EnforcementResult, mechanism: string, scope: "renderer" | "window" | "app" | "os_session", detail: { shortcut?: string; duration_ms?: number; process_name?: string } = {}): void {
    try {
      this.sink.emit({ action, enforcement, mechanism, scope, detail });
    } catch (err) {
      log.error("event sink failed", err);
    }
  }

  async engage(sessionId: string): Promise<void> {
    if (this.activeSession === sessionId) return;
    if (this.activeSession !== null) this.releaseSync("rebind");
    const w = this.win();
    if (!w || w.isDestroyed()) throw new Error("exam window is not available");
    const dbg = this.platform.debugSwitches();
    if (dbg.length > 0) throw new Error(`debug switches present (${dbg.join(", ")}): exam mode refused`);

    const steps: Record<string, "ok" | "failed" | "skipped"> = {};
    const step = (name: string, fn: () => void, required: boolean) => {
      try {
        fn();
        steps[name] = "ok";
      } catch (err) {
        steps[name] = "failed";
        log.error(`engage step ${name} failed`, err);
        if (required) throw err;
      }
    };
    this.activeSession = sessionId; // set first: release paths below must see an engaged guard
    try {
      step("clipboard_clear", () => this.platform.clearClipboard(), false);
      step("devtools_close", () => {
        if (w.webContents.isDevToolsOpened()) w.webContents.closeDevTools();
      }, true);
      step("kiosk", () => {
        w.setKiosk(true);
        w.setFullScreen(true);
      }, true);
      step("always_on_top", () => w.setAlwaysOnTop(true, "screen-saver"), true);
      step("content_protection", () => w.setContentProtection(true), false);
      step("window_flags", () => {
        w.setMinimizable(false);
        w.setResizable(false);
        w.setMovable(false);
        w.setClosable(false);
      }, false);
      step("focus", () => {
        w.show();
        w.moveTop();
        w.focus();
      }, false);
      this.registrations = [];
      step("emergency_hotkey", () => this.register(this.opts.emergencyAccelerator, "emergency_exit", () => this.opts.onEmergencyHotkey()), false);
      if (this.platform.platform === "win32") {
        step("print_screen_hotkey", () =>
          this.register("PrintScreen", "print_screen", () =>
            this.emit("shortcut_print_screen", "detected_only", "electron.global_shortcut", "os_session", { shortcut: "PrintScreen" }),
          ), false);
      } else {
        steps.print_screen_hotkey = "skipped";
      }
      step("display_watch", () => {
        this.unsubDisplay = this.platform.onDisplayChange(() =>
          this.emit("display_changed", "detected_only", "electron.screen_events", "os_session"),
        );
      }, false);
    } catch (err) {
      this.releaseSync("engage_failed");
      throw err;
    }
    if (this.opts.native) {
      try {
        const r = await this.opts.native.start(sessionId);
        steps[`native_helper_${r.mode}`] = "ok";
      } catch (err) {
        steps.native_helper = "failed";
        log.warn(`native helper not started: ${err instanceof Error ? err.message : String(err)}`);
        this.emit("enforcement_error", "failed", "native.helper", "os_session", { shortcut: "helper_start" });
      }
    } else {
      steps.native_helper = "skipped";
    }
    if (this.activeSession !== sessionId) throw new Error("released while engaging");
    this.lastEngage = { steps, registrations: [...this.registrations] };
    const emergency = this.registrations.find((r) => r.purpose === "emergency_exit");
    if (!emergency?.registered) {
      this.emit("enforcement_error", "failed", "electron.global_shortcut", "app", { shortcut: "emergency_hotkey" });
    }
    this.emit("exam_mode_engaged", "allowed", "electron.kiosk", "window");
    log.info(`engaged for ${sessionId}: ${JSON.stringify(steps)}`);
  }

  private register(accelerator: string, purpose: ShortcutRegistration["purpose"], cb: () => void): void {
    let registered = false;
    try {
      registered = this.platform.registerShortcut(accelerator, cb) && this.platform.isShortcutRegistered(accelerator);
    } catch (err) {
      log.warn(`register ${accelerator} threw`, err);
    }
    // A false result means another application owns it: the API call itself is not evidence.
    this.registrations.push({ accelerator, purpose, registered });
    if (!registered) log.warn(`global shortcut ${accelerator} (${purpose}) NOT registered`);
  }

  /** Synchronous part of release: safe in crash handlers. Never throws. */
  releaseSync(reason: string): void {
    const wasActive = this.activeSession !== null;
    this.activeSession = null;
    if (this.refocusTimer) clearTimeout(this.refocusTimer);
    this.refocusTimer = null;
    const w = this.win();
    const attempt = (name: string, fn: () => void) => {
      try {
        fn();
      } catch (err) {
        log.error(`release step ${name} failed`, err);
      }
    };
    for (const r of this.registrations) attempt(`unregister ${r.accelerator}`, () => this.platform.unregisterShortcut(r.accelerator));
    this.registrations = [];
    attempt("display_watch", () => this.unsubDisplay?.());
    this.unsubDisplay = null;
    if (w && !w.isDestroyed()) {
      attempt("closable", () => w.setClosable(true));
      attempt("always_on_top", () => w.setAlwaysOnTop(false));
      attempt("kiosk", () => w.setKiosk(false));
      attempt("fullscreen", () => w.setFullScreen(false));
      attempt("content_protection", () => w.setContentProtection(false));
      attempt("window_flags", () => {
        w.setMinimizable(true);
        w.setResizable(true);
        w.setMovable(true);
      });
    }
    attempt("native_helper", () => this.opts.native?.stopSync());
    if (wasActive) {
      attempt("clipboard_clear", () => this.platform.clearClipboard());
      this.emit("exam_mode_released", "allowed", "electron.kiosk", "window", { shortcut: reason.slice(0, 32) });
      log.info(`released (${reason})`);
    }
  }

  async release(reason: string): Promise<void> {
    const native = this.opts.native;
    this.releaseSync(reason);
    if (native) {
      try {
        await native.stop();
      } catch (err) {
        log.error("native helper stop failed", err);
      }
    }
  }

  // ------------------------------------------------------------- window hooks (wired by main)

  /** before-input-event of the exam window. Returns true when the event must be prevented. */
  onBeforeInput(input: KeyInput): boolean {
    if (!this.active) return false;
    const d = classifyExamKey(input);
    if (!d) return false;
    if (d.action && this.throttle.shouldReport(input, d)) {
      this.emit(d.action, d.enforcement, "electron.before_input_event", "window", { shortcut: d.shortcut });
      if (d.action === "shortcut_ctrl_v" || d.action === "shortcut_ctrl_c" || d.action === "shortcut_ctrl_x") {
        if (d.enforcement === "blocked") this.emit("clipboard_blocked", "blocked", "electron.before_input_event", "window", { shortcut: d.shortcut });
      }
    }
    return d.prevent;
  }

  /** `close` event of the exam window. Returns true when closing must be prevented. */
  onCloseRequest(): boolean {
    if (!this.active) return false;
    this.emit("shortcut_alt_f4", "blocked", "electron.window_close_event", "window", { shortcut: "close" });
    return true;
  }

  onBlur(): void {
    if (!this.active) return;
    this.blurAt = (this.opts.now ?? Date.now)();
    this.emit("focus_lost", "detected_only", "electron.browser_window_blur", "window");
    if (this.opts.refocus !== false) {
      if (this.refocusTimer) clearTimeout(this.refocusTimer);
      this.refocusTimer = setTimeout(() => {
        this.refocusTimer = null;
        const w = this.win();
        if (!this.active || !w || w.isDestroyed() || w.isFocused()) return;
        try {
          w.show();
          w.moveTop();
          w.focus();
        } catch (err) {
          log.debug("refocus failed", err);
        }
      }, 100);
    }
  }

  onFocus(): void {
    if (!this.active || this.blurAt === null) return;
    const duration = Math.max(0, (this.opts.now ?? Date.now)() - this.blurAt);
    this.blurAt = null;
    this.emit("focus_regained", "allowed", "electron.browser_window_focus", "window", { duration_ms: duration });
  }

  onDevToolsOpened(): void {
    if (!this.active) return;
    const w = this.win();
    try {
      w?.webContents.closeDevTools();
    } catch {
      /* ignore */
    }
    this.emit("devtools_blocked", "blocked", "electron.devtools_closed", "window");
  }

  onNavigationBlocked(): void {
    if (this.active) this.emit("navigation_blocked", "blocked", "electron.will_navigate", "app");
  }

  onWindowOpenBlocked(): void {
    if (this.active) this.emit("new_window_blocked", "blocked", "electron.window_open_handler", "app");
  }

  /** Report OS-level observations from the native helper (only while engaged). */
  onNativeObservation(action: EnvironmentAction, enforcement: EnforcementResult, mechanism: string, detail: { shortcut?: string; process_name?: string }): void {
    if (this.active) this.emit(action, enforcement, mechanism, "os_session", detail);
  }

  /** Diagnostics for STATUS/QA (no user content). */
  get shortcutRegistrations(): readonly ShortcutRegistration[] {
    return this.registrations;
  }
}
