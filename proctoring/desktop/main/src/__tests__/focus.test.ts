import assert from "node:assert/strict";
import { test } from "node:test";
import { ExamGuard } from "../environment/guard";
import { FakePlatform, FakeWindow, RecordingSink } from "./helpers";

test("enforce returns focus at 400ms, retries <=2/s, blur storms don't postpone it", async t => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1000 });
  const win = new FakeWindow(), sink = new RecordingSink();
  const guard = new ExamGuard(() => win, sink, new FakePlatform(),
    { emergencyAccelerator: "Ctrl+Alt+Shift+F12", enforce: true, onEmergencyHotkey() {} });
  await guard.engage("s1");
  win.log.length = 0;
  win.focused = false;
  guard.onNativeObservation("foreign_window_foreground", "detected_only", "native.foreground_watch", { process_name: "Taskmgr.exe" });
  t.mock.timers.tick(200);
  guard.onBlur();
  t.mock.timers.tick(199);
  assert.equal(win.log.filter(x => x === "focus").length, 0);
  t.mock.timers.tick(1);
  assert.deepEqual(win.log, ["moveTop", "focus"]);
  t.mock.timers.tick(499);
  assert.equal(win.log.filter(x => x === "focus").length, 1);
  t.mock.timers.tick(1);
  assert.equal(win.log.filter(x => x === "focus").length, 2);
  assert.equal(sink.events.filter(e => e.action === "focus_lost").length, 1);
  assert.equal(sink.events.find(e => e.action === "foreign_window_foreground")?.detail?.process_name, "Taskmgr.exe");
  guard.releaseSync("emergency");
  const count = win.log.length;
  t.mock.timers.tick(5000);
  assert.equal(win.log.length, count);
});

test("dry-run observes foreign window without refocus; regained focus cancels enforce retry", async t => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1000 });
  for (const enforce of [false, true]) {
    const win = new FakeWindow(), sink = new RecordingSink();
    const guard = new ExamGuard(() => win, sink, new FakePlatform(),
      { emergencyAccelerator: "Ctrl+Alt+Shift+F12", enforce, onEmergencyHotkey() {} });
    await guard.engage("s1");
    win.log.length = 0;
    win.focused = false;
    guard.onBlur();
    if (enforce) { win.focused = true; guard.onFocus(); }
    t.mock.timers.tick(2000);
    assert.equal(win.log.length, 0);
    guard.releaseSync("pause");
  }
});
