import assert from "node:assert/strict";
import { test } from "node:test";
import type { EnvironmentCapabilities } from "@contracts/qorgau-v1.generated";
import { withDisplayCheck, environmentBlockReason } from "../environment/preflight";
import { ExamGuard } from "../environment/guard";
import { FakeGuard, FakePlatform, FakeWindow, RecordingSink, sessionInfo } from "./helpers";
import { createApi } from "../ipc/api";
import type { BackendClient } from "../backend/client";
import { OperatorAuth, hashPin } from "../shell/operator";
import { ShellStateMachine } from "../shell/state";

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

test("bridge refreshes conditions before preflight/start/resume; stale PASS cannot start", async () => {
  const calls: string[] = [];
  let displays = 1;
  let caps = withDisplayCheck(base, displays);
  const machine = new ShellStateMachine(new FakeGuard(), { shell_version: "test", platform: "test" });
  await machine.bind(sessionInfo("s1", "ready"));
  const api = createApi({
    client: { json: async (_m: string, path: string) => { calls.push(path); return { ok: true, data: {} }; } } as unknown as BackendClient,
    machine, operator: new OperatorAuth({ QORGAU_OPERATOR_PIN_HASH: hashPin("2468", Buffer.alloc(16, 7)) }), capabilities: () => caps,
    refreshEnvironment: async () => { calls.push("refresh"); caps = withDisplayCheck(base, displays); },
    emergencyExit: async () => {}, saveFile: async () => null,
  });
  await api.runPreflight("s1");
  assert.deepEqual(calls, ["refresh", "/v1/sessions/s1/preflight"]);
  calls.length = 0;
  displays = 2;
  const start = await api.startExam("s1") as { ok: boolean; error: { code: string; message: string } };
  assert.equal(start.ok, false);
  assert.equal(start.error.code, "PREFLIGHT_FAILED");
  assert.equal(start.error.message, "Отключите второй монитор, чтобы начать");
  assert.deepEqual(calls, ["refresh"], "backend start is never called");
  calls.length = 0;
  await machine.observe(sessionInfo("s1", "paused"));
  await api.operatorUnlock("2468");
  const resume = await api.resumeExam("s1") as { ok: boolean };
  assert.equal(resume.ok, false);
  assert.deepEqual(calls, ["refresh"], "backend resume is never called");
});
