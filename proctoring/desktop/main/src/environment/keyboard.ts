// In-window keyboard policy for exam mode (owner: A06). Pure function over Electron's `Input`
// (before-input-event). Decides: prevent or not, and which EnvironmentAction to report.
//
// Scope is the exam WINDOW only: preventDefault stops Chromium/page handling. It does NOT stop
// OS-level handling (Alt+Tab, Win, PrintScreen, Ctrl+Alt+Del) — those are reported as
// detected_only here and need an OS mechanism (native helper) to be blocked.
// Privacy: only fixed shortcut names are reported; never typed characters.
import type { EnforcementResult, EnvironmentAction } from "@contracts/qorgau-v1.generated";

/** Subset of Electron.Input used here (kept structural so tests need no Electron). */
export interface KeyInput {
  type: string; // "keyDown" | "keyUp" | "rawKeyDown" | "char"
  key: string;
  code: string;
  control: boolean;
  alt: boolean;
  shift: boolean;
  meta: boolean;
  isAutoRepeat?: boolean;
}

export interface KeyDecision {
  prevent: boolean;
  /** null = no observation; all blocked exam shortcuts currently have an action. */
  action: EnvironmentAction | null;
  enforcement: EnforcementResult;
  shortcut: string;
}

const lower = (s: string) => s.toLowerCase();

function isKey(input: KeyInput, ...names: string[]): boolean {
  const k = lower(input.key);
  const c = lower(input.code);
  return names.some((n) => lower(n) === k || lower(n) === c || `key${lower(n)}` === c);
}

const blocked = (action: EnvironmentAction | null, shortcut: string): KeyDecision => ({
  prevent: true,
  action,
  enforcement: "blocked",
  shortcut,
});

/** OS handles these before/without the page: preventDefault does not stop them. */
const detectedOnly = (action: EnvironmentAction, shortcut: string): KeyDecision => ({
  prevent: true,
  action,
  enforcement: "detected_only",
  shortcut,
});

/**
 * Exam-mode decision for one input event, or null when the key is allowed.
 * Matching uses `code` (physical key) as well as `key` so non-Latin layouts (ru/kk) are covered:
 * Ctrl+С on a Russian layout has key "с" but code "KeyC".
 */
export function classifyExamKey(input: KeyInput): KeyDecision | null {
  if (input.type !== "keyDown" && input.type !== "rawKeyDown" && input.type !== "keyUp") return null;
  const ctrl = input.control;
  const { alt, shift, meta } = input;

  // OS-level keys (window scope cannot block them)
  if (isKey(input, "Meta", "MetaLeft", "MetaRight", "OSLeft", "OSRight", "OS")) return detectedOnly("shortcut_win", "Win");
  if (isKey(input, "PrintScreen", "Snapshot")) return detectedOnly("shortcut_print_screen", "PrintScreen");
  if (alt && isKey(input, "Tab")) return detectedOnly("shortcut_alt_tab", "Alt+Tab");

  // devtools
  if (isKey(input, "F12")) return blocked("devtools_blocked", "F12");
  if (ctrl && shift && isKey(input, "i", "KeyI", "j", "KeyJ", "c", "KeyC")) return blocked("devtools_blocked", "Ctrl+Shift+I/J/C");

  // clipboard
  if (ctrl && !alt && isKey(input, "c", "KeyC")) return blocked("shortcut_ctrl_c", "Ctrl+C");
  if (ctrl && !alt && isKey(input, "Insert")) return blocked("shortcut_ctrl_c", "Ctrl+Insert");
  if (ctrl && !alt && isKey(input, "v", "KeyV")) return blocked("shortcut_ctrl_v", "Ctrl+V");
  if (shift && !ctrl && isKey(input, "Insert")) return blocked("shortcut_ctrl_v", "Shift+Insert");
  if (ctrl && !alt && isKey(input, "x", "KeyX")) return blocked("shortcut_ctrl_x", "Ctrl+X");
  if (shift && !ctrl && isKey(input, "Delete")) return blocked("shortcut_ctrl_x", "Shift+Delete");

  // tab switching (the shell has a single window and no tabs; still never let it act)
  if (ctrl && isKey(input, "Tab")) return blocked("shortcut_ctrl_tab", shift ? "Ctrl+Shift+Tab" : "Ctrl+Tab");
  if (ctrl && isKey(input, "PageUp", "PageDown")) return blocked("shortcut_ctrl_tab", `Ctrl+${input.code || input.key}`.slice(0, 32));

  // close
  if (alt && isKey(input, "F4")) return blocked("shortcut_alt_f4", "Alt+F4");
  if (ctrl && !alt && isKey(input, "w", "KeyW")) return blocked("shortcut_alt_f4", "Ctrl+W");

  // reload / history navigation
  if (isKey(input, "F5") || (ctrl && isKey(input, "r", "KeyR"))) return blocked("navigation_blocked", "Перезагрузка страницы");
  if (alt && isKey(input, "ArrowLeft", "ArrowRight")) return blocked("navigation_blocked", "Alt+Arrow");
  if (isKey(input, "BrowserBack", "BrowserForward", "BrowserRefresh", "BrowserHome")) return blocked("navigation_blocked", "BrowserKey");

  // new window / tab
  if (ctrl && !alt && isKey(input, "n", "KeyN", "t", "KeyT")) return blocked("new_window_blocked", "Ctrl+N/T");

  if (ctrl && !alt && isKey(input, "p")) return blocked("clipboard_blocked", "Печать (Ctrl+P)");
  if (ctrl && !alt && isKey(input, "s")) return blocked("clipboard_blocked", "Сохранение (Ctrl+S)");
  if (ctrl && !alt && isKey(input, "u")) return blocked("devtools_blocked", "Исходный код (Ctrl+U)");
  if (ctrl && !alt && isKey(input, "o", "f", "g")) return blocked("navigation_blocked", "Открытие/поиск страницы");
  if (ctrl && isKey(input, "Equal", "Minus", "Digit0", "NumpadAdd", "NumpadSubtract", "Numpad0", "+", "-", "=", "0")) {
    return blocked("navigation_blocked", "Масштаб страницы");
  }
  if (isKey(input, "ContextMenu") || (shift && isKey(input, "F10"))) return blocked("clipboard_blocked", "Контекстное меню");
  if (meta && !ctrl) return detectedOnly("shortcut_win", "Win+key");
  return null;
}

/** Throttle identical observations (auto-repeat, key held) so the event log is not flooded. */
export class KeyEventThrottle {
  private last = new Map<string, number>();
  constructor(
    private readonly windowMs = 500,
    private readonly now: () => number = Date.now,
  ) {}
  shouldReport(input: KeyInput, d: KeyDecision): boolean {
    if (d.action === null || input.isAutoRepeat || input.type === "keyUp") return false;
    const key = `${d.action}:${d.shortcut}`;
    const t = this.now();
    const prev = this.last.get(key);
    this.last.set(key, t);
    return prev === undefined || t - prev >= this.windowMs;
  }
}
