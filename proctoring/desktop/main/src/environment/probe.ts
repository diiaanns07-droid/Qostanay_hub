// Runtime self-test of the in-app restrictions (owner: A06). Runs once at startup, BEFORE any
// session, in a hidden window that has the same webPreferences/hardening as the exam window and the
// same key policy (classifyExamKey). Real Chromium input pipeline (sendInputEvent), real
// window.open/navigation/devtools/close handlers. A check passes only when the mechanism's RESULT is
// observed (page did not receive the key, no window appeared, URL unchanged, window still open);
// calling an API is never counted. If the pipeline itself cannot be exercised (control key never
// reaches the page) the key checks are "inconclusive" and the matrix stays "unverified".
// OS-level keys (Alt+Tab, Win, PrintScreen) cannot be tested this way and are not attempted.
import type { ProbeCheck, ProbeOutcome, ProbeResults } from "./capabilities";
import type { ContentAction } from "./content-policy";

export interface KeySpec {
  /** Electron accelerator-style keyCode for sendInputEvent */
  keyCode: string;
  modifiers: Array<"control" | "shift" | "alt" | "meta">;
  /** Counter key written by __probe.js: <C?><A?><S?>:<KeyboardEvent.code> */
  pageKey: string;
}

export interface ProbeDriver {
  load(): Promise<void>;
  /** keyDown + keyUp through the real input pipeline; resolves after delivery settled. */
  sendKey(spec: KeySpec): Promise<void>;
  /** How often the key policy (before-input-event) saw this spec. */
  policySaw(spec: KeySpec): number;
  pageCounts(): Promise<Record<string, number>>;
  tryWindowOpen(): Promise<{ returnedNull: boolean; windowsBefore: number; windowsAfter: number }>;
  tryNavigate(): Promise<{ urlBefore: string; urlAfter: string }>;
  tryDevTools(): Promise<{ openAfter: boolean }>;
  tryClose(): Promise<{ stillOpen: boolean }>;
  tryContent(id: ContentAction): Promise<{ prevented: boolean; reported: boolean }>;
  dispose(): void;
}

const key = (keyCode: string, modifiers: KeySpec["modifiers"], code: string): KeySpec => {
  const prefix = `${modifiers.includes("control") ? "C" : ""}${modifiers.includes("alt") ? "A" : ""}${modifiers.includes("shift") ? "S" : ""}`;
  return { keyCode, modifiers, pageKey: `${prefix}:${code}` };
};

export const CONTROL_KEY = key("A", [], "KeyA");
export const KEY_CHECKS: Array<{ check: ProbeCheck; specs: KeySpec[] }> = [
  { check: "key_ctrl_c", specs: [key("C", ["control"], "KeyC")] },
  { check: "key_ctrl_v", specs: [key("V", ["control"], "KeyV")] },
  { check: "key_shift_insert", specs: [key("Insert", ["shift"], "Insert")] },
  { check: "key_ctrl_x", specs: [key("X", ["control"], "KeyX")] },
  { check: "key_ctrl_tab", specs: [key("Tab", ["control"], "Tab")] },
  { check: "key_alt_f4", specs: [key("F4", ["alt"], "F4")] },
  { check: "key_devtools", specs: [key("F12", [], "F12"), key("I", ["control", "shift"], "KeyI")] },
  { check: "key_reload", specs: [key("F5", [], "F5"), key("R", ["control"], "KeyR")] },
  { check: "key_print", specs: [key("P", ["control"], "KeyP")] },
  { check: "key_save", specs: [key("S", ["control"], "KeyS")] },
  { check: "key_source", specs: [key("U", ["control"], "KeyU")] },
  { check: "key_zoom", specs: [key("=", ["control"], "Equal"), key("-", ["control"], "Minus"), key("0", ["control"], "Digit0")] },
  { check: "key_context_menu", specs: [key("F10", ["shift"], "F10")] },
];

export const CONTENT_CHECKS = ["print", "context_menu", "selection", "drag", "zoom"] as const;

const outcome = (status: ProbeOutcome["status"], detail: string): ProbeOutcome => ({ status, detail: detail.slice(0, 200) });

async function guarded(fn: () => Promise<ProbeOutcome>): Promise<ProbeOutcome> {
  try {
    return await fn();
  } catch (err) {
    return outcome("inconclusive", `error: ${err instanceof Error ? err.message : String(err)}`);
  }
}

export async function runSelfTest(driver: ProbeDriver): Promise<ProbeResults> {
  const results: ProbeResults = {};
  try {
    await driver.load();
  } catch (err) {
    const detail = `probe page did not load: ${err instanceof Error ? err.message : String(err)}`;
    for (const c of KEY_CHECKS) results[c.check] = outcome("inconclusive", detail);
    return results;
  }

  // keys: the control key must reach the page, otherwise nothing about blocking can be concluded
  await guarded(async () => {
    await driver.sendKey(CONTROL_KEY);
    return outcome("pass", "");
  });
  const base = await driver.pageCounts().catch(() => ({}) as Record<string, number>);
  const pipelineOk = (base[CONTROL_KEY.pageKey] ?? 0) > 0;
  for (const { check, specs } of KEY_CHECKS) {
    results[check] = await guarded(async () => {
      if (!pipelineOk) return outcome("inconclusive", "control key did not reach the page: input pipeline not exercised");
      for (const s of specs) await driver.sendKey(s);
      const counts = await driver.pageCounts();
      const leaked = specs.filter((s) => (counts[s.pageKey] ?? 0) > 0);
      const unseen = specs.filter((s) => driver.policySaw(s) === 0);
      if (leaked.length) return outcome("fail", `reached the page: ${leaked.map((s) => s.pageKey).join(",")}`);
      if (unseen.length) return outcome("inconclusive", `policy did not see: ${unseen.map((s) => s.pageKey).join(",")}`);
      return outcome("pass", `prevented before the page: ${specs.map((s) => s.pageKey).join(",")}`);
    });
  }

  results.window_open = await guarded(async () => {
    const r = await driver.tryWindowOpen();
    if (r.returnedNull && r.windowsAfter === r.windowsBefore) return outcome("pass", "window.open returned null, no new window");
    return outcome("fail", `returnedNull=${r.returnedNull} windows ${r.windowsBefore}->${r.windowsAfter}`);
  });
  for (const id of CONTENT_CHECKS) {
    results[`page_${id}`] = await guarded(async () => {
      const r = await driver.tryContent(id);
      return outcome(r.prevented && r.reported ? "pass" : "fail", `${id}: prevented=${r.prevented}, reported=${r.reported}`);
    });
  }
  results.navigation = await guarded(async () => {
    const r = await driver.tryNavigate();
    return r.urlAfter === r.urlBefore ? outcome("pass", "location change to a remote URL was cancelled") : outcome("fail", "page navigated away");
  });
  results.devtools = await guarded(async () => {
    const r = await driver.tryDevTools();
    return r.openAfter ? outcome("fail", "devtools stayed open") : outcome("pass", "devtools not open after openDevTools()");
  });
  results.window_close = await guarded(async () => {
    const r = await driver.tryClose();
    return r.stillOpen ? outcome("pass", "close() was cancelled by the close handler") : outcome("fail", "window closed");
  });
  return results;
}

export function probeSummary(r: ProbeResults): string {
  return Object.entries(r)
    .map(([k, v]) => `${k}=${v?.status}`)
    .join(" ");
}
