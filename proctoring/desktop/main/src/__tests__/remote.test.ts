import assert from "node:assert/strict";
import { test } from "node:test";
import { resolve } from "node:path";
import { checkRemoteEnvironment, parseRemoteSnapshot, REMOTE_NAMES, type RemoteSnapshot } from "../environment/remote";
import { withRemoteCheck, environmentBlockReason } from "../environment/preflight";
import { ExamGuard } from "../environment/guard";
import { FakePlatform, FakeWindow, RecordingSink } from "./helpers";

test("remote helper parser rejects paths/junk, keeps only exact basenames and RDP", () => {
  assert.equal(parseRemoteSnapshot("junk").available, false);
  assert.equal(parseRemoteSnapshot(JSON.stringify({ type: "environment", processes: ["C:/anydesk.exe"], remote_session: false })).available, false);
  const parsed = parseRemoteSnapshot(JSON.stringify({ type: "environment", processes: [...REMOTE_NAMES].map(n => n.toUpperCase() + ".EXE"), remote_session: true }));
  assert.equal(parsed.processes.length, 11);
  assert.equal(parsed.remoteSession, true);
  const base = { reported_at: new Date().toISOString(), platform: "test", shell_version: "test", exam_mode_supported: true, items: [] };
  const caps = withRemoteCheck(base, { available: true, processes: ["anydesk.exe"], remoteSession: false });
  assert.equal(caps.exam_mode_supported, false);
  assert.equal(environmentBlockReason(caps), "Закройте anydesk.exe, чтобы начать");
  assert.equal(environmentBlockReason(withRemoteCheck(base, { available: true, processes: [], remoteSession: true })), "Завершите удалённый сеанс RDP, чтобы начать");
  assert.ok(environmentBlockReason(withRemoteCheck(base, { available: false, processes: [], remoteSession: false })));
});

test("read-only subprocess protocol via fake (no hooks)", async () => {
  const fake = resolve(__dirname, "..", "..", "main", "tests", "fake-helper.mjs");
  const result = await checkRemoteEnvironment({ command: process.execPath, prefixArgs: [fake, "--remote-found", "--rdp"] });
  assert.deepEqual(result, { available: true, processes: ["anydesk.exe"], remoteSession: true });
});

test("5s remote polling emits once per appearance, resumes after disappearance, stops on release", async t => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  let snapshot: RemoteSnapshot = { available: true, processes: ["rustdesk.exe"], remoteSession: true };
  let scans = 0;
  const sink = new RecordingSink();
  const guard = new ExamGuard(() => new FakeWindow(), sink, new FakePlatform(), {
    emergencyAccelerator: "Ctrl+Alt+Shift+F12", onEmergencyHotkey() {},
    scanRemote: async () => { scans++; return snapshot; },
  });
  await guard.engage("s1");
  await Promise.resolve();
  const detected = () => sink.events.filter(e => e.mechanism === "native.remote_access" && e.action === "foreign_window_foreground");
  assert.deepEqual(detected().map(e => e.detail?.process_name), ["rustdesk.exe", "RDP"]);
  t.mock.timers.tick(4999); await Promise.resolve();
  assert.equal(scans, 1);
  t.mock.timers.tick(1); await Promise.resolve();
  assert.equal(scans, 2);
  assert.equal(detected().length, 2);
  snapshot = { available: true, processes: [], remoteSession: false };
  t.mock.timers.tick(5000); await Promise.resolve();
  snapshot = { available: true, processes: ["rustdesk.exe"], remoteSession: false };
  t.mock.timers.tick(5000); await Promise.resolve();
  assert.equal(detected().length, 3);
  guard.releaseSync("finish");
  const count = scans;
  t.mock.timers.tick(10000); await Promise.resolve();
  assert.equal(scans, count);
});

test("remote result arriving after release cannot emit into next session", async () => {
  let finish!: (s: RemoteSnapshot) => void;
  const sink = new RecordingSink();
  const guard = new ExamGuard(() => new FakeWindow(), sink, new FakePlatform(), {
    emergencyAccelerator: "Ctrl+Alt+Shift+F12", onEmergencyHotkey() {},
    scanRemote: () => new Promise(resolve => { finish = resolve; }),
  });
  await guard.engage("s1");
  guard.releaseSync("pause");
  finish({ available: true, processes: ["anydesk.exe"], remoteSession: true });
  await Promise.resolve();
  assert.equal(sink.events.filter(e => e.mechanism === "native.remote_access").length, 0);
});
