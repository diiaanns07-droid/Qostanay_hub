import assert from "node:assert/strict";
import { test } from "node:test";
import { EmergencyQuit } from "../emergency-quit";

class Clock {
  now = 0;
  timers = new Map<() => void, number>();
  schedule = (callback: () => void, milliseconds: number) => {
    this.timers.set(callback, this.now + milliseconds);
    return () => { this.timers.delete(callback); };
  };
  tick(milliseconds: number) {
    this.now += milliseconds;
    for (const [callback, at] of [...this.timers]) {
      if (at <= this.now) { this.timers.delete(callback); callback(); }
    }
  }
}
const never = () => new Promise<void>(() => {});

test("emergency releases synchronously, does not await stuck backend/transition, and bounds app shutdown", async () => {
  const clock = new Clock(), actions: string[] = [];
  const exit = new EmergencyQuit({
    release: [() => { actions.push("latch"); }, () => { actions.push("release"); }, () => { actions.push("media_reset"); }],
    cleanup: [never, never], quit: () => { actions.push("quit"); }, forceExit: () => { actions.push("force"); },
    report: () => {}, schedule: clock.schedule,
  });
  const pending = exit.request();
  assert.deepEqual(actions, ["latch", "release", "media_reset"]);
  assert.equal(exit.request(), pending, "repeat hotkeys share one shutdown");
  clock.tick(2_000); await pending;
  assert.deepEqual(actions, ["latch", "release", "media_reset", "quit"]);
  clock.tick(4_000);
  assert.deepEqual(actions.slice(-4), ["latch", "release", "media_reset", "force"]);
});

test("successful cleanup quits early; only actual will-quit completion cancels final deadline", async () => {
  const clock = new Clock(), actions: string[] = [];
  const exit = new EmergencyQuit({ release: [() => { actions.push("release"); }],
    cleanup: [async () => { actions.push("abort"); }, async () => { actions.push("flush"); }],
    quit: () => { actions.push("quit"); }, forceExit: () => { actions.push("force"); },
    report: () => {}, schedule: clock.schedule });
  await exit.request();
  assert.deepEqual(actions, ["release", "abort", "flush", "quit"]);
  assert.equal(clock.timers.size, 1, "app.quit is a request, not proof of exit");
  exit.complete(); clock.tick(10_000);
  assert.equal(clock.timers.size, 0);
  assert.ok(!actions.includes("force"));
});

test("throwing cleanup cannot prevent later releases or the final exit fallback", async () => {
  const clock = new Clock(), actions: string[] = [], errors: unknown[] = [];
  const exit = new EmergencyQuit({ release: [() => { throw new Error("broken window"); }, () => { actions.push("native_release"); }],
    cleanup: [() => { throw new Error("broken abort"); }], quit: () => { throw new Error("broken quit"); },
    forceExit: () => { actions.push("force"); }, report: error => { errors.push(error); }, schedule: clock.schedule });
  await exit.request();
  assert.deepEqual(actions, ["native_release"]);
  clock.tick(6_000);
  assert.deepEqual(actions, ["native_release", "native_release", "force"]);
  assert.ok(errors.some(error => String(error).includes("broken quit")));
});

test("a broken diagnostic stream cannot interrupt emergency cleanup", async () => {
  const clock = new Clock();
  let released = false, quit = false;
  const exit = new EmergencyQuit({ release: [() => { throw new Error("window destroyed"); }, () => { released = true; }],
    cleanup: [], quit: () => { quit = true; }, forceExit: () => {},
    report: () => { throw new Error("stderr unavailable"); }, schedule: clock.schedule });
  await exit.request();
  assert.equal(released, true);
  assert.equal(quit, true);
  exit.complete();
});
