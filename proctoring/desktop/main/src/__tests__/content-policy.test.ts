import assert from "node:assert/strict";
import { test } from "node:test";
import { CONTENT_ACTIONS, isContentAction, type ContentAction } from "../environment/content-policy";
import { ExamGuard } from "../environment/guard";
import { FakePlatform, FakeWindow, RecordingSink } from "./helpers";

test("content labels map to existing fusion blocked actions only, without captured text", async () => {
  assert.equal(isContentAction("__proto__"), false);
  assert.equal(isContentAction("arbitrary page text"), false);
  const sink = new RecordingSink();
  const guard = new ExamGuard(() => new FakeWindow(), sink, new FakePlatform(), {
    emergencyAccelerator: "Ctrl+Alt+Shift+F12", onEmergencyHotkey() {},
  });
  guard.onContentBlocked("print");
  assert.equal(sink.events.length, 0);
  await guard.engage("content-test");
  try {
    for (const id of Object.keys(CONTENT_ACTIONS) as ContentAction[]) {
      guard.onContentBlocked(id);
      const event = sink.events.at(-1)!;
      assert.equal(event.enforcement, "blocked");
      assert.ok(["clipboard_blocked", "navigation_blocked", "new_window_blocked"].includes(event.action));
      assert.equal(event.detail?.shortcut, CONTENT_ACTIONS[id].label);
      assert.ok(CONTENT_ACTIONS[id].label.length <= 32);
      assert.equal(event.detail?.process_name, undefined);
    }
  } finally { guard.releaseSync("test_done"); }
  const count = sink.events.length;
  guard.onContentBlocked("print");
  assert.equal(sink.events.length, count);
});
