import assert from "node:assert/strict";
import { resolve } from "node:path";
import { test } from "node:test";
import { checkHelper, NativeHelper, parseHelperLine, type NativeObservation } from "../environment/native";

const FAKE = resolve(__dirname, "..", "..", "main", "tests", "fake-helper.mjs");
const cmd = (flags: string[] = []) => ({ command: process.execPath, prefixArgs: [FAKE, ...flags] });

test("parseHelperLine maps keys/foreground/error and ignores junk", () => {
  assert.equal(parseHelperLine('{"type":"key","key":"win","swallowed":true}')?.obs?.enforcement, "blocked");
  assert.equal(parseHelperLine('{"type":"key","key":"alt_tab","swallowed":false}')?.obs?.enforcement, "detected_only");
  assert.equal(parseHelperLine('{"type":"key","key":"nope"}')?.obs, null);
  const fg = parseHelperLine('{"type":"foreground","foreign":true,"process":"chrome.exe"}')?.obs;
  assert.equal(fg?.action, "foreign_window_foreground");
  assert.equal(fg?.detail.process_name, "chrome.exe");
  assert.deepEqual(parseHelperLine('{"type":"foreground","foreign":true,"process":"bad title!"}')?.obs?.detail, {});
  assert.equal(parseHelperLine('{"type":"foreground","foreign":false}')?.obs, null);
  assert.equal(parseHelperLine('{"type":"error","code":"hook_failed"}')?.obs?.action, "enforcement_error");
  assert.equal(parseHelperLine("not json"), null);
  assert.equal(parseHelperLine('{"no":"type"}'), null);
  assert.equal(parseHelperLine("x".repeat(2000)), null);
});

test("checkHelper runs --self-check against the fake helper", async () => {
  const info = await checkHelper(cmd(), "linux", false);
  assert.equal(info.available, true);
  assert.match(info.detail, /self-check ok/);
  assert.equal(info.enforce, false);
});

test("start reports ready (dry-run), delivers observations, heartbeats, stops cleanly", async () => {
  const obs: NativeObservation[] = [];
  const helper = new NativeHelper(cmd(["--emit-keys"]), { enforce: false, maxMinutes: 1, onObservation: (o) => obs.push(o), readyTimeoutMs: 3_000 });
  const r = await helper.start("s1");
  assert.equal(r.mode, "dry_run");
  assert.equal(helper.running, true);
  await new Promise((res) => setTimeout(res, 300));
  const actions = obs.map((o) => `${o.action}:${o.enforcement}`);
  assert.ok(actions.includes("shortcut_win:detected_only"));
  assert.ok(actions.includes("shortcut_alt_tab:detected_only"));
  assert.ok(obs.some((o) => o.detail.process_name === "chrome.exe"));
  assert.ok(!obs.some((o) => o.action === "enforcement_error"), "clean run has no error observation");
  await helper.stop();
  assert.equal(helper.running, false);
  assert.equal(helper.lastBye, "stop_requested");
});

test("enforce mode is honored only when opted in", async () => {
  const obs: NativeObservation[] = [];
  const helper = new NativeHelper(cmd(["--emit-keys"]), { enforce: true, maxMinutes: 1, onObservation: (o) => obs.push(o) });
  const r = await helper.start("s1");
  assert.equal(r.mode, "enforce");
  await new Promise((res) => setTimeout(res, 200));
  assert.ok(obs.some((o) => o.action === "shortcut_win" && o.enforcement === "blocked"));
  await helper.stop();
});

test("helper claiming enforce WITHOUT opt-in is rejected and stopped", async () => {
  const helper = new NativeHelper(cmd(["--claim-enforce"]), { enforce: false, maxMinutes: 1, onObservation: () => undefined });
  await assert.rejects(helper.start("s1"), /unexpected helper mode/);
  assert.equal(helper.running, false);
});

test("no ready within timeout -> rejected, process killed", async () => {
  const helper = new NativeHelper(cmd(["--no-ready"]), { enforce: false, maxMinutes: 1, onObservation: () => undefined, readyTimeoutMs: 400 });
  await assert.rejects(helper.start("s1"), /did not report ready/);
  assert.equal(helper.running, false);
});

test("unexpected helper exit after ready -> enforcement_error observation", async () => {
  const obs: NativeObservation[] = [];
  const helper = new NativeHelper(cmd(["--crash"]), { enforce: false, maxMinutes: 1, onObservation: (o) => obs.push(o) });
  await helper.start("s1");
  await new Promise((res) => setTimeout(res, 300));
  assert.ok(obs.some((o) => o.action === "enforcement_error" && o.detail.shortcut === "helper_exit"));
});

test("stopSync kills immediately and stop() is then a no-op", async () => {
  const helper = new NativeHelper(cmd(), { enforce: false, maxMinutes: 1, onObservation: () => undefined });
  await helper.start("s1");
  helper.stopSync();
  assert.equal(helper.running, false);
  await helper.stop(); // must not hang or throw
});
