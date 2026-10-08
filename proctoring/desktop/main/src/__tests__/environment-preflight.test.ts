import assert from "node:assert/strict";
import { test } from "node:test";
import type { EnvironmentCapabilities } from "@contracts/qorgau-v1.generated";
import { withDisplayCheck, environmentBlockReason } from "../environment/preflight";
import { ExamGuard } from "../environment/guard";
import { FakePlatform, FakeWindow, RecordingSink } from "./helpers";

const base: EnvironmentCapabilities = { reported_at: new Date().toISOString(), platform: "test",
  shell_version: "test", exam_mode_supported: true, items: [] };

test("second monitor blocks preflight; fresh single-monitor snapshot clears it", () => {
  const two = withDisplayCheck(base, 2);
  assert.equal(two.exam_mode_supported, false);
  assert.equal(environmentBlockReason(two), "Отключите второй монитор, чтобы начать");
  const one = withDisplayCheck(base, 1);
  assert.equal(one.exam_mode_supported, true);
  assert.equal(environmentBlockReason(one), null);
  for (const count of [0, NaN, -1]) assert.ok(environmentBlockReason(withDisplayCheck(base, count)));
});

test("display polling every 2s, transition dedup, and release cleanup", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const platform = new FakePlatform();
  const sink = new RecordingSink();
  const guard = new ExamGuard(() => new FakeWindow(), sink, platform,
    { emergencyAccelerator: "Ctrl+Alt+Shift+F12", onEmergencyHotkey() {} });
  await guard.engage("s1");
  platform.displays = 2;
  t.mock.timers.tick(1999);
  assert.equal(sink.events.filter(e => e.action === "display_changed").length, 0);
  t.mock.timers.tick(1);
  assert.equal(sink.events.filter(e => e.action === "display_changed").length, 1);
  platform.displayCb?.("added");
  t.mock.timers.tick(4000);
  assert.equal(sink.events.filter(e => e.action === "display_changed").length, 1);
  platform.displays = 1;
  platform.displayCb?.("removed");
  assert.equal(sink.events.filter(e => e.action === "display_changed").length, 2);
  guard.releaseSync("pause");
  platform.displays = 2;
  t.mock.timers.tick(4000);
  assert.equal(sink.events.filter(e => e.action === "display_changed").length, 2);
  assert.equal(platform.displayCb, null);
});
