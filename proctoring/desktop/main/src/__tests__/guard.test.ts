import assert from "node:assert/strict";
import { test } from "node:test";
import { ExamGuard, type NativeHelperHandle } from "../environment/guard";
import { FakePlatform, FakeWindow, RecordingSink } from "./helpers";

const mk = (platform = new FakePlatform("win32"), native: NativeHelperHandle | null = null, enforce = false) => {
  const win = new FakeWindow();
  const sink = new RecordingSink();
  let emergencies = 0;
  let now = 1_000;
  const guard = new ExamGuard(() => win, sink, platform, {
    emergencyAccelerator: "CommandOrControl+Alt+Shift+F12",
    onEmergencyHotkey: () => emergencies++,
    native,
    enforce,
    refocus: false,
    now: () => now,
  });
  return { win, sink, platform, guard, emergencies: () => emergencies, tick: (ms: number) => (now += ms) };
};

test("engage sets window restrictions; release restores all of them", async () => {
  const { win, platform, guard, sink } = mk();
  await guard.engage("s1");
  assert.equal(guard.active, true);
  assert.equal(win.flags.kiosk, true);
  assert.equal(win.flags.fullscreen, true);
  assert.equal(win.flags.alwaysOnTop, "screen-saver");
  assert.equal(win.flags.contentProtection, true);
  assert.equal(win.flags.closable, false);
  assert.ok(platform.registered.has("CommandOrControl+Alt+Shift+F12"));
  assert.ok(platform.registered.has("PrintScreen"));
  assert.equal(platform.clipboardClears, 1);
  await guard.release("session_finished");
  assert.equal(guard.active, false);
  assert.equal(win.flags.kiosk, false);
  assert.equal(win.flags.fullscreen, false);
  assert.equal(win.flags.alwaysOnTop, false);
  assert.equal(win.flags.contentProtection, false);
  assert.equal(win.flags.closable, true);
  assert.equal(platform.registered.size, 0, "every global shortcut unregistered");
  assert.equal(platform.clipboardClears, 2);
  assert.deepEqual(sink.actions(), ["exam_mode_engaged:allowed", "exam_mode_released:allowed"]);
});

test("release is idempotent and never throws even if window calls fail", async () => {
  const { win, guard, sink } = mk();
  await guard.engage("s1");
  win.throwOn = new Set(["kiosk", "alwaysOnTop", "closable"]);
  await guard.release("x");
  await guard.release("x");
  guard.releaseSync("y");
  assert.equal(guard.active, false);
  assert.equal(win.flags.contentProtection, false, "other steps still ran");
  assert.equal(sink.actions().filter((a) => a.startsWith("exam_mode_released")).length, 1);
});

test("a required engage step failing releases what was set and throws", async () => {
  const { win, guard, platform } = mk();
  win.throwOn = new Set(["alwaysOnTop"]);
  await assert.rejects(guard.engage("s1"), /alwaysOnTop failed/);
  assert.equal(guard.active, false);
  assert.equal(win.flags.kiosk, false);
  assert.equal(platform.registered.size, 0);
});

test("debug switches refuse exam mode", async () => {
  const p = new FakePlatform("win32");
  p.switches = ["remote-debugging-port"];
  const { guard, win } = mk(p);
  await assert.rejects(guard.engage("s1"), /debug switches/);
  assert.equal(guard.active, false);
  assert.equal(win.flags.kiosk, undefined);
});

test("emergency hotkey not registrable -> enforcement_error reported (registration result, not the call)", async () => {
  const p = new FakePlatform("win32");
  p.refuse.add("CommandOrControl+Alt+Shift+F12");
  const { guard, sink } = mk(p);
  await guard.engage("s1");
  assert.ok(sink.actions().includes("enforcement_error:failed"));
  assert.equal(guard.lastEngage?.registrations.find((r) => r.purpose === "emergency_exit")?.registered, false);
});

test("emergency hotkey callback reaches the shell", async () => {
  const { guard, platform, emergencies } = mk();
  await guard.engage("s1");
  platform.registered.get("CommandOrControl+Alt+Shift+F12")!();
  assert.equal(emergencies(), 1);
});

test("Enforce refuses unavailable or unverified emergency shortcut and releases partial restrictions", async () => {
  for (const failure of ["refused", "unverified", "throws"]) {
    const p = new FakePlatform("win32");
    if (failure === "refused") p.refuse.add("CommandOrControl+Alt+Shift+F12");
    if (failure === "unverified") p.isShortcutRegistered = () => false;
    if (failure === "throws") p.registerShortcut = () => { throw new Error("OS refused registration"); };
    let starts = 0;
    const native: NativeHelperHandle = { running: false, start: async () => { starts++; return { mode: "enforce" }; },
      stop: async () => {}, stopSync: () => {} };
    const { guard, win, sink } = mk(p, native, true);
    await assert.rejects(guard.engage("s1"), /emergency hotkey unavailable/);
    assert.equal(starts, 0, "no native hook may start without a verified emergency route");
    assert.equal(guard.active, false);
    for (const flag of ["kiosk", "fullscreen", "alwaysOnTop", "contentProtection"]) assert.equal(win.flags[flag], false);
    assert.equal(win.flags.closable, true);
    assert.equal(p.registered.size, 0);
    assert.ok(sink.actions().includes("enforcement_error:failed"));
    assert.ok(!sink.actions().includes("exam_mode_engaged:allowed"));
  }
});

test("emergency exit permanently prevents queued and new sessions from engaging", async () => {
  const { guard, win, platform } = mk(new FakePlatform("win32"), null, true);
  await guard.engage("s1");
  guard.preventEngage(); guard.releaseSync("emergency_hotkey");
  for (const id of ["s1", "s2"]) await assert.rejects(guard.engage(id), /application is exiting/);
  assert.equal(guard.active, false);
  assert.equal(win.flags.kiosk, false);
  assert.equal(platform.registered.size, 0);
});

test("native start completing after emergency release is stopped again and cannot engage", async () => {
  let ready!: (value: { mode: "enforce" }) => void;
  let stops = 0;
  const native: NativeHelperHandle = { running: false, start: () => new Promise(resolve => { ready = resolve; }),
    stop: async () => {}, stopSync: () => { stops++; } };
  const { guard, sink } = mk(new FakePlatform("win32"), native, true);
  const engaging = guard.engage("s1");
  guard.preventEngage(); guard.releaseSync("emergency_hotkey");
  ready({ mode: "enforce" });
  await assert.rejects(engaging, /released while engaging/);
  assert.equal(stops, 2);
  assert.equal(guard.active, false);
  assert.ok(!sink.actions().includes("exam_mode_engaged:allowed"));
});

test("non-Windows: no PrintScreen hotkey attempt", async () => {
  const { guard, platform } = mk(new FakePlatform("linux"));
  await guard.engage("s1");
  assert.equal(platform.registered.has("PrintScreen"), false);
  assert.equal(guard.lastEngage?.steps.print_screen_hotkey, "skipped");
});

test("key policy only while engaged; events carry no typed text", async () => {
  const { guard, sink } = mk();
  const ctrlV = { type: "keyDown", key: "v", code: "KeyV", control: true, alt: false, shift: false, meta: false };
  assert.equal(guard.onBeforeInput(ctrlV), false, "normal mode: nothing prevented");
  await guard.engage("s1");
  assert.equal(guard.onBeforeInput(ctrlV), true);
  assert.equal(guard.onBeforeInput({ ...ctrlV, key: "a", code: "KeyA", control: false }), false);
  const ev = sink.events.filter((e) => e.action === "shortcut_ctrl_v");
  assert.equal(ev.length, 1);
  assert.equal(ev[0]!.enforcement, "blocked");
  assert.equal(ev[0]!.scope, "window");
  assert.equal(ev[0]!.detail?.shortcut, "Ctrl+V");
  assert.ok(sink.events.some((e) => e.action === "clipboard_blocked"));
  for (const e of sink.events) assert.ok(!JSON.stringify(e).includes('"a"'));
});

test("close prevented only while engaged", async () => {
  const { guard, sink } = mk();
  assert.equal(guard.onCloseRequest(), false);
  await guard.engage("s1");
  assert.equal(guard.onCloseRequest(), true);
  assert.ok(sink.actions().includes("shortcut_alt_f4:blocked"));
  await guard.release("done");
  assert.equal(guard.onCloseRequest(), false);
});

test("focus loss is detected_only with measured duration", async () => {
  const { guard, sink, tick } = mk();
  await guard.engage("s1");
  guard.onBlur();
  tick(2_500);
  guard.onFocus();
  const lost = sink.events.find((e) => e.action === "focus_lost")!;
  const back = sink.events.find((e) => e.action === "focus_regained")!;
  assert.equal(lost.enforcement, "detected_only");
  assert.equal(back.detail?.duration_ms, 2_500);
});

test("devtools opened during exam are closed and reported", async () => {
  const { guard, win, sink } = mk();
  await guard.engage("s1");
  win.devtoolsOpen = true;
  guard.onDevToolsOpened();
  assert.equal(win.devtoolsOpen, false);
  assert.ok(sink.actions().includes("devtools_blocked:blocked"));
});

test("native helper failure does not block engage but is reported; release stops it", async () => {
  const calls: string[] = [];
  const native: NativeHelperHandle = {
    running: false,
    start: async () => {
      calls.push("start");
      throw new Error("helper missing");
    },
    stop: async () => void calls.push("stop"),
    stopSync: () => void calls.push("stopSync"),
  };
  const { guard, sink } = mk(new FakePlatform("win32"), native);
  await guard.engage("s1");
  assert.equal(guard.active, true);
  assert.ok(sink.actions().includes("enforcement_error:failed"));
  await guard.release("done");
  assert.deepEqual(calls, ["start", "stopSync", "stop"]);
});
