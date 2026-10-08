import assert from "node:assert/strict";
import { test } from "node:test";
import { ExamGuard, type NativeHelperHandle } from "../environment/guard";
import { applyCalibrationFullscreen } from "../environment/window-fullscreen";
import { ShellStateMachine } from "../shell/state";
import { FakePlatform, FakeWindow, RecordingSink, sessionInfo } from "./helpers";

function fixture(mode: "dry_run" | "enforce") {
  let ready!: (value: { mode: "dry_run" | "enforce" }) => void;
  let started!: () => void;
  const starting = new Promise<void>(resolve => { started = resolve; });
  const startup = new Promise<{ mode: "dry_run" | "enforce" }>(resolve => { ready = resolve; });
  const native: NativeHelperHandle = {
    running: false,
    start: () => { started(); return startup; },
    stop: async () => {},
    stopSync: () => {},
  };
  const window = new FakeWindow();
  const guard = new ExamGuard(() => window, new RecordingSink(), new FakePlatform("win32"), {
    native, enforce: mode === "enforce", refocus: false,
    emergencyAccelerator: "CommandOrControl+Alt+Shift+F12", onEmergencyHotkey: () => {},
  });
  const machine = new ShellStateMachine(guard, { shell_version: "test", platform: "test" });
  const calibration = (flag: boolean) => applyCalibrationFullscreen(window, flag, guard.active, machine.state.exam_mode_active);
  return { window, guard, machine, calibration, starting, ready: () => ready({ mode }) };
}

for (const mode of ["dry_run", "enforce"] as const) {
  test(`calibration unmount cannot undo fullscreen during deferred ${mode} startup; pause and abort still release`, async () => {
    const f = fixture(mode);
    try {
      await f.machine.bind(sessionInfo("s1", "ready"));
      f.calibration(true);
      f.calibration(false);
      assert.equal(f.window.flags.fullscreen, false, "normal calibration may enter and leave fullscreen");

      // The stream publishes RUNNING and React leaves Calibration while native.start awaits READY.
      const transition = f.machine.observe(sessionInfo("s1", "running"));
      await f.starting;
      assert.equal(f.guard.active, true);
      assert.equal(f.machine.state.exam_mode_active, false, "shell publication is still pending");
      assert.equal(f.window.flags.fullscreen, true);
      const fullscreenCalls = f.window.log.filter(x => x.startsWith("fullscreen="));
      f.calibration(false);
      assert.deepEqual(f.window.log.filter(x => x.startsWith("fullscreen=")), fullscreenCalls,
        "the actual calibration request must not reach setFullScreen(false) while the guard engages");

      f.ready();
      await transition;
      assert.equal(f.machine.state.exam_mode_active, true);
      assert.equal(f.window.flags.fullscreen, true);
      assert.equal(f.window.flags.kiosk, true);
      assert.equal(f.window.flags.alwaysOnTop, "screen-saver");
      f.calibration(false);
      assert.equal(f.window.flags.fullscreen, true, "exam retains ownership after startup");

      await f.machine.observe(sessionInfo("s1", "paused"));
      assert.equal(f.window.flags.fullscreen, false);
      f.calibration(true);
      f.calibration(false);
      assert.equal(f.window.flags.fullscreen, false, "calibration works again after release");
      await f.machine.observe(sessionInfo("s1", "running"));
      await f.machine.observe(sessionInfo("s1", "aborted"));
      assert.equal(f.machine.state.mode, "normal");
      assert.equal(f.window.flags.fullscreen, false);
      assert.equal(f.window.flags.kiosk, false);
      assert.equal(f.window.flags.alwaysOnTop, false);
    } finally {
      f.ready();
      await f.machine.settle();
      f.guard.releaseSync("test_cleanup");
    }
  });
}

test("emergency release during deferred startup still exits fullscreen synchronously and cannot re-engage", async () => {
  const f = fixture("enforce");
  try {
    await f.machine.bind(sessionInfo("s1", "ready"));
    const transition = f.machine.observe(sessionInfo("s1", "running"));
    await f.starting;
    f.calibration(false);
    assert.equal(f.window.flags.fullscreen, true);
    f.guard.preventEngage();
    f.machine.forbidEngage("s1");
    f.guard.releaseSync("emergency_hotkey");
    const releasing = f.machine.releaseTo("normal", "emergency_exit", null);
    assert.equal(f.window.flags.fullscreen, false, "main's emergency release is never gated by calibration policy");
    f.ready();
    await transition;
    await releasing;
    await f.machine.observe(sessionInfo("s1", "running"));
    assert.equal(f.machine.state.exam_mode_active, false);
    assert.equal(f.guard.active, false);
    assert.equal(f.window.flags.fullscreen, false);
  } finally {
    f.ready();
    await f.machine.settle();
    f.guard.releaseSync("test_cleanup");
  }
});

test("published exam mode independently protects fullscreen until the state machine clears it", () => {
  const window = new FakeWindow();
  window.setFullScreen(true);
  applyCalibrationFullscreen(window, false, false, true);
  assert.equal(window.flags.fullscreen, true);
  applyCalibrationFullscreen(window, false, false, false);
  assert.equal(window.flags.fullscreen, false);
});
