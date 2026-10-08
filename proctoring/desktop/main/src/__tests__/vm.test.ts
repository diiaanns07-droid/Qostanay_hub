import assert from "node:assert/strict";
import { test } from "node:test";
import { resolve } from "node:path";
import { checkVm, parseVmSnapshot, unknownVm, VM_PLATFORMS, withVmCheck } from "../environment/vm";
import { environmentBlockReason, withDisplayCheck, withRemoteCheck } from "../environment/preflight";
import { ExamGuard } from "../environment/guard";
import { FakePlatform, FakeWindow, RecordingSink } from "./helpers";

test("VM protocol accepts only known platforms and complete tri-state output", () => {
  for (const platform of VM_PLATFORMS) {
    assert.deepEqual(parseVmSnapshot(JSON.stringify({ type: "vm", state: "detected", platform })), { state: "detected", platform });
  }
  assert.deepEqual(parseVmSnapshot('{"type":"vm","state":"not_detected","platform":null}'), { state: "not_detected", platform: null });
  for (const text of ["junk", "null", "{}", '{"type":"vm","state":"detected","platform":"other"}',
    '{"type":"vm","state":"not_detected"}', '{"type":"vm","state":"not_detected","platform":"VMware"}']) {
    assert.deepEqual(parseVmSnapshot(text), unknownVm());
  }
});

test("VM advisory preserves display/remote gates and never blocks on its own", () => {
  const base = { reported_at: new Date().toISOString(), platform: "test", shell_version: "test", exam_mode_supported: true, items: [] };
  const vm = { state: "detected" as const, platform: "Hyper-V" as const };
  for (const snapshot of [vm, unknownVm()]) {
    const caps = withVmCheck(base, snapshot);
    assert.equal(caps.exam_mode_supported, true);
    assert.equal(environmentBlockReason(caps), null);
    assert.equal(withVmCheck(caps, snapshot).items.length, 1);
    const displays = withVmCheck(withDisplayCheck(base, 2), snapshot);
    assert.equal(displays.exam_mode_supported, false);
    assert.ok(environmentBlockReason(displays));
    const remote = withVmCheck(withRemoteCheck(base, { available: true, processes: ["anydesk.exe"], remoteSession: false }), snapshot);
    assert.equal(remote.exam_mode_supported, false);
    assert.ok(environmentBlockReason(remote));
  }
  assert.equal(withVmCheck(base, vm).items[0]?.note_ru, "Экзамен запущен в виртуальной машине (Hyper-V)");
});

test("VM subprocess result and missing executable do not become clean", async () => {
  const fake = resolve(__dirname, "..", "..", "main", "tests", "fake-helper.mjs");
  assert.deepEqual(await checkVm({ command: process.execPath, prefixArgs: [fake, "--vm-found"] }), { state: "detected", platform: "Hyper-V" });
  assert.deepEqual(await checkVm({ command: "missing-vm-helper-324324", prefixArgs: [] }), unknownVm());
});

test("VM is informational once per session, survives pause/resume and logs next session", async () => {
  const sink = new RecordingSink();
  const guard = new ExamGuard(() => new FakeWindow(), sink, new FakePlatform(), {
    emergencyAccelerator: "Ctrl+Alt+Shift+F12", onEmergencyHotkey() {},
    vmSnapshot: () => ({ state: "detected", platform: "Hyper-V" }),
  });
  try {
    await guard.engage("s1");
    guard.releaseSync("pause");
    await guard.engage("s1");
    let vm = sink.events.filter(e => e.mechanism === "native.vm_check.detected");
    assert.equal(vm.length, 1);
    assert.ok(vm[0]);
    assert.equal(vm[0].action, "exam_mode_engaged");
    assert.equal(vm[0].enforcement, "allowed");
    assert.equal(vm[0].detail?.shortcut, "Hyper-V");
    guard.releaseSync("finish");
    await guard.engage("s2");
    vm = sink.events.filter(e => e.mechanism === "native.vm_check.detected");
    assert.equal(vm.length, 2);
    guard.releaseSync("rebind");
    await guard.engage("s1");
    assert.equal(sink.events.filter(e => e.mechanism === "native.vm_check.detected").length, 2);
  } finally { guard.releaseSync("test_done"); }
});
