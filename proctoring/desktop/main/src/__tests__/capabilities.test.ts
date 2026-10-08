import assert from "node:assert/strict";
import { test } from "node:test";
import type { EnvironmentCapability } from "@contracts/qorgau-v1.generated";
import { buildCapabilities, MATRIX_ACTIONS, parseVerificationRecords, summarize, type PlatformInfo, type ProbeResults, type VerificationRecord } from "../environment/capabilities";

const linux: PlatformInfo = { platform: "linux", release: "6.0.0", arch: "x64", label: "linux 6.0.0 x64 (test)" };
const win: PlatformInfo = { platform: "win32", release: "10.0.22631", arch: "x64", label: "win32 10.0.22631 Pro x64" };
const noHelper = { available: false, enforce: false, detail: "not built" };
const pass = (detail = ""): ProbeResults[keyof ProbeResults] => ({ status: "pass" as const, detail });

const item = (caps: { items: EnvironmentCapability[] }, action: string) => caps.items.find((i) => i.action === action)!;

test("matrix reports every action with a valid status and <=64 items", () => {
  const caps = buildCapabilities({ platform: win, shellVersion: "0.1.0", probe: {}, probeRanAt: null, records: [], helper: noHelper });
  assert.equal(caps.items.length, MATRIX_ACTIONS.length);
  assert.ok(caps.items.length <= 64);
  assert.equal(caps.exam_mode_supported, true);
  for (const i of caps.items) {
    assert.ok(["blocked", "detected_only", "unsupported", "unverified"].includes(i.status));
    assert.ok(i.mechanism.length <= 64);
    assert.ok((i.note_ru ?? "").length <= 300);
  }
});

test("no self-test, no records -> in-app items unverified, OS items unverified on Windows", () => {
  const caps = buildCapabilities({ platform: win, shellVersion: "0.1.0", probe: {}, probeRanAt: null, records: [], helper: noHelper });
  assert.equal(item(caps, "shortcut_ctrl_c").status, "unverified");
  assert.equal(item(caps, "shortcut_alt_tab").status, "unverified");
  assert.equal(item(caps, "focus_lost").status, "unverified");
});

test("passing self-test marks in-app items blocked, with verified_on from this machine", () => {
  const probe: ProbeResults = {
    key_ctrl_c: pass(),
    key_ctrl_v: pass(),
    key_shift_insert: pass(),
    key_ctrl_x: pass(),
    key_ctrl_tab: pass(),
    key_alt_f4: pass(),
    key_devtools: pass(),
    key_reload: pass(),
    key_print: pass(), key_save: pass(), key_source: pass(), key_zoom: pass(), key_context_menu: pass(),
    page_print: pass(), page_context_menu: pass(), page_selection: pass(), page_drag: pass(), page_zoom: pass(),
    window_close: pass(),
    window_open: pass(),
    navigation: pass(),
    devtools: pass(),
  };
  const caps = buildCapabilities({ platform: win, shellVersion: "0.1.0", probe, probeRanAt: "2026-10-08T00:00:00Z", records: [], helper: noHelper });
  for (const a of ["shortcut_ctrl_c", "shortcut_ctrl_v", "shortcut_ctrl_x", "shortcut_ctrl_tab", "new_window_blocked", "navigation_blocked", "devtools_blocked", "clipboard_blocked"]) {
    assert.equal(item(caps, a).status, "blocked", a);
    assert.match(item(caps, a).verified_on ?? "", /self-test/);
  }
  // Alt+F4 needs both close and the key check
  assert.equal(item(caps, "shortcut_alt_f4").status, "blocked");
  // OS-level keys are never blocked by a self-test
  assert.equal(item(caps, "shortcut_alt_tab").status, "unverified");
});

test("failed self-test check -> unsupported, not silently blocked", () => {
  const probe: ProbeResults = { key_ctrl_v: { status: "fail", detail: "reached page" }, key_shift_insert: pass() };
  const caps = buildCapabilities({ platform: win, shellVersion: "0.1.0", probe, probeRanAt: "t", records: [], helper: noHelper });
  assert.equal(item(caps, "shortcut_ctrl_v").status, "unsupported");
  assert.match(item(caps, "shortcut_ctrl_v").note_ru ?? "", /не пройдена/);
});

test("inconclusive self-test (no Electron pipeline) stays unverified", () => {
  const probe: ProbeResults = { key_ctrl_c: { status: "inconclusive", detail: "no pipeline" } };
  const caps = buildCapabilities({ platform: win, shellVersion: "0.1.0", probe, probeRanAt: "t", records: [], helper: noHelper });
  assert.equal(item(caps, "shortcut_ctrl_c").status, "unverified");
});

test("OS-level items are unsupported on non-Windows", () => {
  const caps = buildCapabilities({ platform: linux, shellVersion: "0.1.0", probe: {}, probeRanAt: null, records: [], helper: { available: false, enforce: false, detail: "не Windows" } });
  for (const a of ["shortcut_alt_tab", "shortcut_win", "shortcut_print_screen", "foreign_window_foreground"]) {
    assert.equal(item(caps, a).status, "unsupported", a);
  }
});

test("a manual verification record only counts when platform AND os release match exactly", () => {
  const rec: VerificationRecord = {
    action: "shortcut_alt_tab",
    status: "blocked",
    mechanism: "native.ll_keyboard_hook",
    platform: "win32",
    os_release: "10.0.22631",
    verified_on: "Windows 11 Pro 22631, helper enforce, controlled test",
    date: "2026-10-10",
  };
  const helper = { available: true, enforce: true, detail: "ok" };
  const match = buildCapabilities({ platform: win, shellVersion: "0.1.0", probe: {}, probeRanAt: null, records: [rec], helper });
  assert.equal(item(match, "shortcut_alt_tab").status, "blocked");
  assert.match(item(match, "shortcut_alt_tab").verified_on ?? "", /controlled test/);
  const other = buildCapabilities({ platform: { ...win, release: "10.0.19045" }, shellVersion: "0.1.0", probe: {}, probeRanAt: null, records: [rec], helper });
  assert.equal(item(other, "shortcut_alt_tab").status, "unverified", "different OS build does not inherit the record");
  // a blocked native record is ignored when the helper cannot enforce right now
  const noEnforce = buildCapabilities({ platform: win, shellVersion: "0.1.0", probe: {}, probeRanAt: null, records: [rec], helper: { available: true, enforce: false, detail: "dry-run" } });
  assert.equal(item(noEnforce, "shortcut_alt_tab").status, "unverified");
});

test("summarize counts statuses", () => {
  const caps = buildCapabilities({ platform: win, shellVersion: "0.1.0", probe: {}, probeRanAt: null, records: [], helper: noHelper });
  const s = summarize(caps);
  assert.equal(s.blocked + s.detected_only + s.unsupported + s.unverified, caps.items.length);
});

test("verification JSON parsing is defensive", () => {
  assert.deepEqual(parseVerificationRecords("not json").records, []);
  assert.deepEqual(parseVerificationRecords('{"records":[]}').records, []);
  const good = JSON.stringify({
    records: [
      { action: "shortcut_win", status: "blocked", mechanism: "native.ll_keyboard_hook", platform: "win32", os_release: "10.0.22631", verified_on: "x", date: "2026-10-10" },
      { action: "shortcut_win", status: "guilty", mechanism: "x", platform: "win32", os_release: "1", verified_on: "x", date: "d" },
      { action: "not_an_action", status: "blocked", mechanism: "x", platform: "win32", os_release: "1", verified_on: "x", date: "d" },
    ],
  });
  const { records, invalid } = parseVerificationRecords(good);
  assert.equal(records.length, 1);
  assert.equal(invalid, 2);
});

test("helper self-check/ready alone never promotes an OS-level item (needs a measured record)", () => {
  const helper = { available: true, enforce: true, detail: "self-check ok" };
  const caps = buildCapabilities({ platform: win, shellVersion: "0.1.0", probe: {}, probeRanAt: null, records: [], helper });
  for (const a of ["shortcut_alt_tab", "shortcut_win", "shortcut_print_screen", "foreign_window_foreground"]) {
    assert.equal(item(caps, a).status, "unverified", a);
    assert.equal(item(caps, a).verified_on, null, a);
  }
});
