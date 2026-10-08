// Integration against the REAL backend process (python -m proctor serve --token-stdin), synthetic
// sessions only. Needs proctoring/.venv (or QORGAU_PYTHON). Electron is not involved: these are the
// same main-process modules main.ts wires together.
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { after, before, describe, test } from "node:test";
import { fixtures } from "@contracts/fixtures.generated";
import type { BackendProcessState, ShellState } from "@contracts/bridge";
import type { DeskScanResult, EnvironmentCapabilities, HealthReport, SessionInfo, StreamEnvelope } from "@contracts/qorgau-v1.generated";
import { BackendClient } from "../backend/client";
import { BackendProcess, BackendSupervisor, type BackendConnection } from "../backend/process";
import { BackendSocket } from "../backend/stream";
import { buildCapabilities } from "../environment/capabilities";
import { EnvironmentEventQueue } from "../environment/events";
import { createApi } from "../ipc/api";
import { hashPin, OperatorAuth } from "../shell/operator";
import { ShellStateMachine } from "../shell/state";
import { FakeGuard } from "./helpers";

const root = resolve(__dirname, "..", "..", ".."); // desktop/dist/test-main -> proctoring/
const python = process.env.QORGAU_PYTHON ?? join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const haveBackend = existsSync(python) && existsSync(join(root, "backend", "proctor", "__main__.py"));
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function until<T>(fn: () => T | Promise<T>, ms = 15_000, step = 50): Promise<T> {
  const end = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) throw new Error("condition not met in time");
    await sleep(step);
  }
}

const QUIET_ENV = { ...process.env, QORGAU_LOG_LEVEL: "WARNING", QORGAU_DATA_DIR: mkdtempSync(join(tmpdir(), "qorgau-a06-")) };

describe("backend process + bridge API (real backend)", { skip: haveBackend ? false : `no backend venv at ${python}` }, () => {
  const states: string[] = [];
  const lost: string[] = [];
  const ready: BackendConnection[] = [];
  let sup: BackendSupervisor;
  const target = () => (sup.connection ? { port: sup.connection.port, token: sup.connection.token } : null);
  const client = new BackendClient(target);
  const guard = new FakeGuard();
  const machine = new ShellStateMachine(guard, { shell_version: "0.1.0", platform: "linux-test" });
  const salt = Buffer.alloc(16, 7);
  const operator = new OperatorAuth({ QORGAU_OPERATOR_PIN_HASH: hashPin("2468", salt) });
  const events = new EnvironmentEventQueue(client, () => machine.state.session_id, { flushDelayMs: 10 });
  const api = createApi({
    client,
    machine,
    operator,
    capabilities: () => null,
    emergencyExit: async () => undefined,
    saveFile: async () => null,
    flushEvents: () => events.flush(),
  });

  before(async () => {
    sup = new BackendSupervisor(
      { python, cwd: root, env: QUIET_ENV, readyTimeoutMs: 60_000 },
      {
        onState: (s: BackendProcessState) => {
          states.push(s);
          machine.setBackend(s);
        },
        onReady: (c) => ready.push(c),
        onLost: (r) => {
          lost.push(r);
          void machine.backendLost(r);
        },
      },
      { maxRestarts: 3, windowMs: 60_000, backoffMs: [100] },
    );
    const conn = await sup.start();
    assert.ok(conn, "backend became ready");
  });

  after(async () => {
    await sup?.stop();
  });

  test("READY handshake: loopback port, token never on the command line or in the environment", async () => {
    const c = sup.connection!;
    assert.ok(c.port > 0 && c.port < 65536);
    assert.equal(c.ready.contract, "qorgau.v1");
    assert.equal(c.token.length, 64);
    assert.deepEqual(states.slice(0, 2), ["starting", "ready"]);
    if (process.platform === "linux") {
      const pid = sup.pid!;
      const cmdline = readFileSync(`/proc/${pid}/cmdline`, "utf8");
      const environ = readFileSync(`/proc/${pid}/environ`, "utf8");
      assert.ok(!cmdline.includes(c.token), "token not in argv");
      assert.ok(!environ.includes(c.token), "token not in env");
      assert.ok(cmdline.includes("--token-stdin"));
    }
    const h = await client.json<HealthReport>("GET", BackendClient.path("health"));
    assert.ok(h.ok);
    const wrong = new BackendClient(() => ({ port: c.port, token: "x".repeat(64) }));
    const denied = await wrong.json("GET", BackendClient.path("health"));
    assert.equal(denied.ok, false);
    assert.equal(!denied.ok && denied.error.code, "UNAUTHORIZED");
  });

  test("capabilities PUT is accepted by the backend contract", async () => {
    const caps = buildCapabilities({
      platform: { platform: "linux", release: "6.0", arch: "x64", label: "linux 6.0 x64 (test)" },
      shellVersion: "0.1.0",
      probe: { key_ctrl_c: { status: "pass", detail: "" } },
      probeRanAt: new Date().toISOString(),
      records: [],
      helper: { available: false, enforce: false, detail: "test" },
    });
    const r = await client.json<EnvironmentCapabilities>("PUT", BackendClient.path("environment", "capabilities"), caps);
    assert.ok(r.ok, JSON.stringify(!r.ok && r.error));
    const back = await client.json<EnvironmentCapabilities | null>("GET", BackendClient.path("environment", "capabilities"));
    assert.ok(back.ok && back.data?.items.length === caps.items.length);
  });

  test("bridge API drives a synthetic session; exam mode only while RUNNING; operator gating", async () => {
    const envelopes: StreamEnvelope[] = [];
    const sock = new BackendSocket("stream", target, { onEnvelope: (e) => envelopes.push(e) });
    sock.open();
    try {
      await until(() => envelopes.some((e) => e.message.type === "hello"));

      const bad = (await api.createSession({ ...fixtures["SessionCreate.synthetic"], extra: 1 })) as { ok: boolean; error?: { code: string } };
      assert.equal(bad.ok, false);
      assert.equal(bad.error?.code, "INVALID_ARGUMENT");

      const created = (await api.createSession(structuredClone(fixtures["SessionCreate.synthetic"]))) as { ok: true; data: SessionInfo };
      assert.ok(created.ok, JSON.stringify(created));
      const sid = created.data.session_id;
      assert.equal(machine.state.mode, "preflight");
      assert.equal(guard.active, false);

      const pf = (await api.runPreflight(sid)) as { ok: boolean; data: { ready: boolean } };
      assert.ok(pf.ok && pf.data.ready, JSON.stringify(pf));

      // A15 desk scan through the bridge. The model may be absent in this clone: then the result is
      // "failed" with a message (never "clear" by default); with weights it is clear/objects_found.
      const ds = (await api.startDeskScan(sid, { duration_s: 5, mode: "usb" })) as { ok: boolean; data: DeskScanResult };
      assert.ok(ds.ok && ds.data.state === "recording", JSON.stringify(ds));
      assert.equal(((await api.startDeskScan(sid, { duration_s: 5, mode: "phone" })) as { ok: boolean }).ok, false);
      const dsDone = await until(async () => {
        const r = (await api.getDeskScan(sid)) as { ok: boolean; data: DeskScanResult };
        return r.ok && r.data.state !== "recording" ? r.data : null;
      }, 30_000, 250);
      assert.ok(["clear", "objects_found", "failed"].includes(dsDone.state), JSON.stringify(dsDone));
      assert.ok(dsDone.message_ru?.startsWith("Вариант: USB-камера."), JSON.stringify(dsDone));
      const dsSkipLocked = (await api.skipDeskScan(sid, { reason: "fixed_camera_teacher_check" })) as { ok: boolean; error?: { details: Record<string, unknown> } };
      assert.equal(dsSkipLocked.error?.details.shell_code, "operator_locked", "skipping the desk scan is a teacher decision");

      const skipLocked = (await api.calibrationSkip(sid, { reason: "integration test" })) as { ok: boolean; error?: { details: Record<string, unknown> } };
      assert.equal(skipLocked.error?.details.shell_code, "operator_locked", "skipping calibration is a teacher decision");
      assert.ok(((await api.operatorUnlock("2468")) as { ok: boolean }).ok);
      const skip = (await api.calibrationSkip(sid, { reason: "integration test" })) as { ok: boolean };
      assert.ok(skip.ok, JSON.stringify(skip));
      const dsSkip = (await api.skipDeskScan(sid, { reason: "fixed_camera_teacher_check" })) as { ok: boolean; data: DeskScanResult };
      assert.ok(dsSkip.ok && dsSkip.data.state === "skipped" && dsSkip.data.skip_reason === "fixed_camera_teacher_check", JSON.stringify(dsSkip));
      assert.equal(guard.active, false, "nothing engaged before start");

      const startOther = (await api.startExam("not-bound")) as { ok: boolean; error?: { details: Record<string, unknown> } };
      assert.equal(startOther.ok, false);
      assert.equal(startOther.error?.details.shell_code, "session_not_bound");

      const started = (await api.startExam(sid)) as { ok: boolean; data: SessionInfo };
      assert.ok(started.ok, JSON.stringify(started));
      assert.equal(machine.state.mode, "exam");
      assert.equal(machine.state.exam_mode_active, true);
      assert.equal(guard.active, true);
      assert.equal(machine.state.operator_unlocked, false, "unlock from preflight is cleared when the exam starts");
      const dsRunning = (await api.startDeskScan(sid, { duration_s: 5, mode: "laptop" })) as { ok: boolean; error?: { code: string } };
      assert.equal(dsRunning.error?.code, "INVALID_STATE", "no desk scan while RUNNING");

      // environment events reach the session (accepted, client_seq increasing)
      events.emit({ action: "shortcut_ctrl_v", enforcement: "blocked", mechanism: "electron.before_input_event", scope: "window", detail: { shortcut: "Ctrl+V" } });
      events.emit({ action: "focus_lost", enforcement: "detected_only", mechanism: "electron.browser_window_blur", scope: "window" });
      await events.flush();
      assert.equal(events.sent, 2);
      assert.equal(events.pending, 0);
      await until(() => envelopes.some((e) => e.message.type === "observation" && (e.message as { observation: { kind: string } }).observation.kind === "environment"));

      // history/review routes are closed during the exam; pause needs the operator
      const inc = (await api.listIncidents(sid)) as { ok: boolean; error?: { details: Record<string, unknown> } };
      assert.equal(inc.error?.details.shell_code, "exam_mode_active");
      const pauseLocked = (await api.pauseExam(sid, { reason: "check" })) as { ok: boolean; error?: { details: Record<string, unknown> } };
      assert.equal(pauseLocked.error?.details.shell_code, "operator_locked");
      const wrongPin = (await api.operatorUnlock("0000")) as { ok: boolean };
      assert.equal(wrongPin.ok, false);
      const unlocked = (await api.operatorUnlock("2468")) as { ok: boolean; data: ShellState };
      assert.ok(unlocked.ok && unlocked.data.operator_unlocked);

      const paused = (await api.pauseExam(sid, { reason: "operator check" })) as { ok: boolean };
      assert.ok(paused.ok, JSON.stringify(paused));
      assert.equal(guard.active, false, "pause releases restrictions");
      assert.equal(machine.state.mode, "preflight");
      const resumed = (await api.resumeExam(sid)) as { ok: boolean };
      assert.ok(resumed.ok);
      assert.equal(guard.active, true);

      // an event queued right before finish is delivered first (flush), not lost
      const sentBefore = events.sent;
      events.emit({ action: "shortcut_ctrl_c", enforcement: "blocked", mechanism: "electron.before_input_event", scope: "window", detail: { shortcut: "Ctrl+C" } });
      const finished = (await api.finishExam(sid)) as { ok: boolean; data: SessionInfo };
      assert.equal(events.sent, sentBefore + 1, "queued event delivered before finish");
      assert.ok(finished.ok);
      assert.equal(finished.data.state, "finished");
      assert.equal(guard.active, false);
      assert.equal(machine.state.mode, "normal");
      assert.deepEqual(guard.calls, [`engage:${sid}`, "release:session_paused", `engage:${sid}`, "release:session_finished"]);

      const incAfter = (await api.listIncidents(sid)) as { ok: boolean };
      assert.ok(incAfter.ok, "review routes open again after finish");
      await until(() => envelopes.some((e) => e.message.type === "session_state" && (e.message as { session: SessionInfo }).session.state === "finished"));

      // New shortcut actions after finish are rejected (409), counted and never retried.
      // Do not use focus_lost here: SessionRuntime intentionally accepts final focus/release
      // notifications during its 5-second shutdown grace period (A06 #6, report tail).
      events.emit({ action: "shortcut_ctrl_v", enforcement: "blocked", mechanism: "electron.before_input_event", scope: "window", detail: { shortcut: "Ctrl+V" } });
      await events.flush();
      assert.equal(events.pending, 0);
      assert.equal(events.dropped, 1);
    } finally {
      sock.close();
    }
  });

  test("teacher access: default closes review during the exam; opt-in live review is bound-session + PIN + expiring", async () => {
    let now = 1_000_000;
    const g2 = new FakeGuard();
    const m2 = new ShellStateMachine(g2, { shell_version: "0.1.0", platform: "linux-test" });
    const live = createApi({
      client,
      machine: m2,
      operator,
      capabilities: () => null,
      emergencyExit: async () => undefined,
      saveFile: async () => null,
      accessPolicy: "operator_live_review",
      operatorTimeouts: { idleMs: 60_000, maxMs: 600_000 },
      now: () => now,
    });
    type R = { ok: boolean; data?: unknown; error?: { code: string; details: Record<string, unknown> } };
    const created = (await live.createSession(structuredClone(fixtures["SessionCreate.synthetic"]))) as { ok: true; data: SessionInfo };
    const sid = created.data.session_id;
    await live.runPreflight(sid);
    await live.operatorUnlock("2468");
    await live.calibrationSkip(sid, { reason: "access test" });
    assert.ok(((await live.startExam(sid)) as R).ok);
    assert.equal(m2.state.exam_mode_active, true);

    const lockedTry = (await live.listIncidents(sid)) as R;
    assert.equal(lockedTry.error?.details.shell_code, "operator_locked", "exam start cleared the unlock");
    assert.ok(((await live.operatorUnlock("2468")) as R).ok);
    const inc = (await live.listIncidents(sid)) as R;
    assert.ok(inc.ok, JSON.stringify(inc));
    assert.ok(Array.isArray(inc.data));
    const summary = (await live.getSummary(sid)) as R;
    assert.ok(summary.ok, "summary of the running session is readable by the unlocked teacher");
    const other = (await live.listIncidents("some-older-session")) as R;
    assert.equal(other.error?.details.shell_code, "session_not_bound");
    const hist = (await live.listSessions()) as R;
    assert.equal(hist.error?.details.shell_code, "exam_mode_active", "history stays closed during the exam");
    const exp = (await live.exportReport(sid, "json")) as R;
    assert.equal(exp.error?.details.shell_code, "exam_mode_active", "export stays closed during the exam");
    assert.equal(g2.active, true, "reading does not release restrictions");

    now += 61_000; // teacher walked away
    const expired = (await live.listIncidents(sid)) as R;
    assert.equal(expired.error?.details.shell_code, "operator_locked");
    assert.equal(m2.state.operator_unlocked, false, "expiry is visible in the shell state");

    // the DEFAULT policy over the same running exam refuses the same call even when unlocked
    const strict = createApi({ client, machine: m2, operator, capabilities: () => null, emergencyExit: async () => undefined, saveFile: async () => null, now: () => now });
    assert.ok(((await strict.operatorUnlock("2468")) as R).ok);
    const strictTry = (await strict.listIncidents(sid)) as R;
    assert.equal(strictTry.error?.details.shell_code, "exam_mode_active");

    assert.ok(((await live.finishExam(sid)) as R).ok);
    assert.equal(g2.active, false);
    assert.ok(((await live.operatorUnlock("2468")) as R).ok);
    const exportAfter = (await live.exportReport(sid, "json")) as R;
    // the shell no longer blocks it: the request reaches the backend (bootstrap store: report = 501)
    assert.ok(exportAfter.ok || exportAfter.error?.code === "NOT_IMPLEMENTED", JSON.stringify(exportAfter));
  });

  test("arity/size/untrusted values are rejected before reaching the backend", async () => {
    const r1 = (await api.getSession()) as { ok: boolean; error?: { code: string } };
    assert.equal(r1.error?.code, "INVALID_ARGUMENT");
    // a foreign/garbage session id is refused by the access policy first (teacher-only) ...
    const r2 = (await api.getSession("../../health")) as { ok: boolean; error?: { code: string; details: Record<string, unknown> } };
    assert.equal(r2.error?.details.shell_code, "operator_locked");
    // ... and, for the unlocked teacher, by argument validation — never by the backend
    assert.ok(((await api.operatorUnlock("2468")) as { ok: boolean }).ok);
    const r2b = (await api.getSession("../../health")) as { ok: boolean; error?: { code: string } };
    assert.equal(r2b.error?.code, "INVALID_ARGUMENT");
    await api.operatorLock();
    const r3 = (await api.saveAnswer("s", "q", { value: "x".repeat(70_000), client_seq: 1 })) as { ok: boolean; error?: { code: string } };
    assert.equal(r3.error?.code, "INVALID_ARGUMENT");
  });

  test("backend crash: restrictions released at once, supervisor restarts with a NEW token and port", async () => {
    const created = (await api.createSession(structuredClone(fixtures["SessionCreate.synthetic"]))) as { ok: true; data: SessionInfo };
    const sid = created.data.session_id;
    await api.runPreflight(sid);
    await api.operatorUnlock("2468");
    await api.calibrationSkip(sid, { reason: "crash test" });
    await api.startExam(sid);
    assert.equal(guard.active, true);
    const before = sup.connection!;
    const readyCount = ready.length;
    process.kill(sup.pid!, "SIGKILL");
    await until(() => lost.length > 0);
    await machine.settle();
    assert.equal(guard.active, false, "released on backend loss");
    assert.equal(machine.state.mode, "error");
    await until(() => ready.length > readyCount, 60_000);
    const afterConn = sup.connection!;
    assert.notEqual(afterConn.token, before.token);
    assert.ok(states.includes("restarting"));
    const h = await client.json("GET", BackendClient.path("health"));
    assert.ok(h.ok, "client follows the new connection");
    const old = new BackendClient(() => ({ port: afterConn.port, token: before.token }));
    const denied = await old.json("GET", BackendClient.path("health"));
    assert.equal(!denied.ok && denied.error.code, "UNAUTHORIZED", "old token is useless");
  });

  test("graceful stop: stdin shutdown with a RUNNING session; the process exits by itself", async () => {
    const proc = new BackendProcess({ python, cwd: root, env: QUIET_ENV });
    const c2 = await proc.start();
    const c = new BackendClient(() => ({ port: c2.port, token: c2.token }));
    const s = await c.json<SessionInfo>("POST", BackendClient.path("sessions"), fixtures["SessionCreate.synthetic"]);
    assert.ok(s.ok);
    const sid = s.data.session_id;
    assert.ok((await c.json("POST", BackendClient.path("sessions", sid, "preflight"))).ok);
    assert.ok((await c.json("POST", BackendClient.path("sessions", sid, "calibration", "skip"), { reason: "stop test" })).ok);
    const run = await c.json<SessionInfo>("POST", BackendClient.path("sessions", sid, "start"));
    assert.ok(run.ok && run.data.state === "running");
    const t0 = Date.now();
    const res = await proc.stop();
    assert.equal(res.forced, "no", "exited after stdin shutdown without terminate/kill");
    assert.equal(res.code, 0);
    assert.ok(Date.now() - t0 < 7_000);
    assert.ok(proc.stderrTail.some((l) => l.includes("shutdown requested")), proc.stderrTail.join("\n"));
    const gone = await c.json("GET", BackendClient.path("health"));
    assert.equal(!gone.ok && gone.error.details.shell_code, "backend_unavailable");

    await sup.stop();
    assert.equal(sup.state, "stopped");
    assert.equal(states.at(-1), "stopped");
    assert.equal(lost.length, 1, "a requested stop is not reported as a loss");
  });
});

describe("backend process failure paths (fake backends)", { skip: haveBackend ? false : "no python" }, () => {
  const fake = (code: string) => ["-c", code];

  test("stop escalates to kill when the backend ignores shutdown and SIGTERM", { skip: process.platform === "win32" }, async () => {
    const code = [
      "import json,os,signal,sys,time",
      "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
      "sys.stdin.readline()",
      `print('QORGAU_READY '+json.dumps({'contract':'qorgau.v1','contract_version':'1.0.0','backend_version':'0','port':1,'pid':os.getpid()}), flush=True)`,
      "while True: time.sleep(1)",
    ].join("\n");
    const p = new BackendProcess({ python, cwd: root, args: fake(code), shutdownGraceMs: 300, killGraceMs: 300 });
    await p.start();
    const r = await p.stop();
    assert.equal(r.forced, "kill");
    assert.equal(r.signal, "SIGKILL");
  });

  test("READY with a foreign contract is rejected", async () => {
    const code = [
      "import json,os,sys,time",
      "sys.stdin.readline()",
      `print('QORGAU_READY '+json.dumps({'contract':'other.v9','contract_version':'9.0.0','backend_version':'0','port':5,'pid':os.getpid()}), flush=True)`,
      "time.sleep(30)",
    ].join("\n");
    const p = new BackendProcess({ python, cwd: root, args: fake(code), shutdownGraceMs: 300, killGraceMs: 300 });
    await assert.rejects(p.start(), /does not match contract/);
  });

  test("READY timeout -> restarts with backoff -> failed (no endless loop)", async () => {
    const states: string[] = [];
    const sup = new BackendSupervisor(
      { python, cwd: root, args: fake("import time\ntime.sleep(30)"), readyTimeoutMs: 400, shutdownGraceMs: 200, killGraceMs: 200 },
      { onState: (s) => states.push(s), onReady: () => assert.fail("must not be ready"), onLost: () => undefined },
      { maxRestarts: 2, windowMs: 60_000, backoffMs: [50] },
    );
    await sup.start();
    await until(() => states.at(-1) === "failed", 15_000);
    assert.equal(states[0], "starting");
    assert.ok(states.includes("restarting"));
    assert.equal(states.filter((s) => s === "failed").length, 1, "gives up once, no endless loop");
    await sup.stop();
  });

  test("missing python executable -> failed state, no crash", async () => {
    const states: string[] = [];
    const sup = new BackendSupervisor(
      { python: join(root, "does-not-exist", "python"), cwd: root, readyTimeoutMs: 1_000 },
      { onState: (s) => states.push(s), onReady: () => undefined, onLost: () => undefined },
      { maxRestarts: 1, windowMs: 60_000, backoffMs: [20] },
    );
    await sup.start();
    await until(() => states.at(-1) === "failed", 5_000);
    await sup.stop();
    assert.equal(states.at(-1), "stopped");
  });

  test("token is not inherited by the child through dev variables", async () => {
    const code = [
      "import json,os,sys",
      "sys.stdin.readline()",
      "leak = [k for k in os.environ if k in ('QORGAU_DEV_TOKEN','QORGAU_OPERATOR_PIN_HASH','ELECTRON_RUN_AS_NODE','QORGAU_SHELL_NATIVE_ENFORCE')]",
      `print('QORGAU_READY '+json.dumps({'contract':'qorgau.v1','contract_version':'1.0.0','backend_version':','.join(leak) or 'none','port':1,'pid':os.getpid()}), flush=True)`,
      "sys.stdin.read()",
    ].join("\n");
    const env = { ...process.env, QORGAU_DEV_TOKEN: "y".repeat(40), QORGAU_OPERATOR_PIN_HASH: "scrypt:x", ELECTRON_RUN_AS_NODE: "1", QORGAU_SHELL_NATIVE_ENFORCE: "1" };
    const p = new BackendProcess({ python, cwd: root, args: fake(code), env });
    const c = await p.start();
    assert.equal(c.ready.backend_version, "none");
    const r = await p.stop();
    assert.equal(r.forced, "no");
  });
});
