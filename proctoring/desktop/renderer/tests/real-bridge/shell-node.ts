// A07 integration harness: the REAL shell main-process logic of A06 (ipc/api.ts, state machine, backend
// supervisor/client/sockets, operator PIN) wired exactly like main/src/main.ts, but without Electron:
//   * the IPC transport is replaced by Playwright exposeBinding (run-real.mjs);
//   * the exam guard is a no-op stand-in (no OS effect) — kiosk/keyboard enforcement is NOT tested here;
//   * the save dialog writes to a given directory (the files are real exports from the backend).
// Needs a checkout that contains A06 main/src (A01 integration candidate). Not part of the renderer bundle.
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { ShellState } from "@contracts/bridge";
import type { PreviewFrameMeta, SessionInfo, StreamEnvelope } from "@contracts/qorgau-v1.generated";
import { BackendClient } from "../../../main/src/backend/client";
import { BackendSupervisor } from "../../../main/src/backend/process";
import { BackendSocket } from "../../../main/src/backend/stream";
import { buildCapabilities } from "../../../main/src/environment/capabilities";
import { EnvironmentEventQueue } from "../../../main/src/environment/events";
import { createApi } from "../../../main/src/ipc/api";
import { OperatorAuth } from "../../../main/src/shell/operator";
import { ShellStateMachine, TERMINAL_STATES, type Guard } from "../../../main/src/shell/state";

class NoOsGuard implements Guard {
  active = false;
  log: string[] = [];
  async engage(sessionId: string): Promise<void> {
    this.log.push(`engage:${sessionId}`);
    this.active = true;
  }
  async release(reason: string): Promise<void> {
    this.log.push(`release:${reason}`);
    this.active = false;
  }
}

export interface HarnessOptions {
  python: string;
  proctoringRoot: string;
  env: NodeJS.ProcessEnv;
  operatorPinHash: string;
  exportDir: string;
}

export async function startShell(o: HarnessOptions) {
  mkdirSync(o.exportDir, { recursive: true });
  const guard = new NoOsGuard();
  const machine = new ShellStateMachine(guard, { shell_version: "harness-a07", platform: "linux (A07 harness, no Electron)" });
  let supervisor: BackendSupervisor | null = null;
  const target = () => (supervisor?.connection ? { port: supervisor.connection.port, token: supervisor.connection.token } : null);
  const client = new BackendClient(target);
  const events = new EnvironmentEventQueue(client, () => machine.state.session_id, { flushDelayMs: 50 });
  const operator = new OperatorAuth({ QORGAU_OPERATOR_PIN_HASH: o.operatorPinHash });
  const capabilities = buildCapabilities({
    platform: { platform: "linux", release: "harness", arch: "x64", label: "linux (A07 harness, no Electron)" },
    shellVersion: "harness-a07",
    probe: {},
    probeRanAt: null,
    records: [],
    helper: { available: false, enforce: false, detail: "harness" },
  });

  const envelopeListeners = new Set<(e: StreamEnvelope) => void>();
  const previewListeners = new Set<(m: PreviewFrameMeta, jpeg: Buffer) => void>();
  const shellListeners = new Set<(s: ShellState) => void>();
  machine.onChange((s) => shellListeners.forEach((l) => l(s)));

  const stream = new BackendSocket("stream", target, {
    onEnvelope: (env) => {
      if (env.message.type === "session_state") void machine.observe(env.message.session);
      envelopeListeners.forEach((l) => l(env));
    },
  });
  const preview = new BackendSocket("preview", target, {
    onPreview: (meta, jpeg) => previewListeners.forEach((l) => l(meta, jpeg)),
  });

  supervisor = new BackendSupervisor(
    { python: o.python, cwd: o.proctoringRoot, env: o.env, readyTimeoutMs: 90_000 },
    {
      onState: (state, detail) => machine.setBackend(state, detail),
      onReady: async () => {
        await client.json("PUT", BackendClient.path("environment", "capabilities"), capabilities);
        stream.open();
        preview.open();
      },
      onLost: (reason) => {
        stream.close();
        preview.close();
        void machine.backendLost(reason);
      },
    },
    { maxRestarts: 3, windowMs: 120_000, backoffMs: [300, 1000] },
  );
  const conn = await supervisor.start();
  if (!conn) throw new Error("backend did not become ready");

  async function emergencyExit(reason: string): Promise<void> {
    const sid = machine.state.session_id;
    const st = machine.boundSessionState;
    if (sid) machine.forbidEngage(sid);
    await machine.releaseTo("normal", "emergency_exit", null);
    await Promise.race([events.flush(), new Promise((r) => setTimeout(r, 2000))]);
    if (sid && st && !TERMINAL_STATES.includes(st) && supervisor?.connection) {
      const r = await client.json<SessionInfo>("POST", BackendClient.path("sessions", sid, "abort"), { reason: `emergency_exit: ${reason}`.slice(0, 200) });
      if (r.ok) await machine.observe(r.data);
    }
  }

  const saved: string[] = [];
  const api = createApi({
    client,
    machine,
    operator,
    capabilities: () => capabilities,
    emergencyExit,
    flushEvents: () => events.flush(),
    saveFile: async (defaultName, bytes) => {
      const path = join(o.exportDir, defaultName);
      writeFileSync(path, bytes);
      saved.push(path);
      return path;
    },
  });

  return {
    api,
    guard,
    saved,
    shellState: () => machine.state,
    onShell: (l: (s: ShellState) => void) => shellListeners.add(l),
    onEnvelope: (l: (e: StreamEnvelope) => void) => envelopeListeners.add(l),
    onPreview: (l: (m: PreviewFrameMeta, jpeg: Buffer) => void) => previewListeners.add(l),
    backendPid: () => supervisor?.pid ?? null,
    stop: async () => {
      stream.close();
      preview.close();
      await supervisor?.stop();
    },
  };
}

export { hashPin } from "../../../main/src/shell/operator";
export { CSP } from "../../../main/src/security/web";
