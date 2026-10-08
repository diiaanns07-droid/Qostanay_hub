import assert from "node:assert/strict";
import { test } from "node:test";
import { ExamGuard } from "../environment/guard";
import { FakePlatform, FakeWindow, RecordingSink } from "./helpers";

test("clipboard is cleared each second while engaged, never read, timer removed on release", async t => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const platform = new FakePlatform();
  const guard = new ExamGuard(() => new FakeWindow(), new RecordingSink(), platform,
    { emergencyAccelerator: "Ctrl+Alt+Shift+F12", onEmergencyHotkey() {} });
  t.mock.timers.tick(5000);
  assert.equal(platform.clipboardClears, 0);
  await guard.engage("s1");
  assert.equal(platform.clipboardClears, 1);
  t.mock.timers.tick(999);
  assert.equal(platform.clipboardClears, 1);
  t.mock.timers.tick(1);
  assert.equal(platform.clipboardClears, 2);
  t.mock.timers.tick(2000);
  assert.equal(platform.clipboardClears, 4);
  guard.releaseSync("pause");
  assert.equal(platform.clipboardClears, 5); // existing exit clear
  t.mock.timers.tick(5000);
  assert.equal(platform.clipboardClears, 5);
  await guard.engage("s1");
  t.mock.timers.tick(1000);
  assert.equal(platform.clipboardClears, 7);
  guard.releaseSync("finish");
});

test("clipboard errors are reported once while failing and don't prevent release", async t => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const platform = new FakePlatform(), sink = new RecordingSink();
  const guard = new ExamGuard(() => new FakeWindow(), sink, platform,
    { emergencyAccelerator: "Ctrl+Alt+Shift+F12", onEmergencyHotkey() {} });
  await guard.engage("s1");
  platform.clearClipboard = () => { throw new Error("unavailable"); };
  t.mock.timers.tick(3000);
  assert.equal(sink.events.filter(e => e.mechanism === "electron.clipboard_clear").length, 1);
  guard.releaseSync("emergency");
  assert.equal(guard.active, false);
});
