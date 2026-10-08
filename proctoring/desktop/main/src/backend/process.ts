// Local backend as a child process (owner: A06). Handshake per CONTRACTS.md §4:
//   spawn: <python> -m proctor serve --token-stdin --port 0   (cwd = proctoring/)
//   stdin line 1: <token>       (generated here per launch; never in argv/env/URL/logs)
//   stdout: QORGAU_READY {...port...}
//   stdin "shutdown" / EOF -> backend aborts the active session and exits.
// Pure Node (no Electron import) so it is tested against the real backend without Electron.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { randomBytes } from "node:crypto";
import { createInterface } from "node:readline";
import type { BackendProcessState } from "@contracts/bridge";
import { CONTRACT_ID } from "@contracts/qorgau-v1.generated";
import { forgetSecret, logger, redact, registerSecret } from "../log";

const log = logger("backend");
export const READY_PREFIX = "QORGAU_READY ";
export const SUPPORTED_CONTRACT_MAJOR = "1.";

export interface ReadyInfo {
  contract: typeof CONTRACT_ID;
  contract_version: string;
  backend_version: string;
  port: number;
  pid: number;
}

export interface BackendConnection {
  port: number;
  /** Bearer token for this launch. Keep inside main; never send to the renderer or a log. */
  token: string;
  ready: ReadyInfo;
  launchId: number;
}

export interface BackendLaunchOptions {
  python: string;
  cwd: string;
  args?: string[];
  env?: NodeJS.ProcessEnv;
  readyTimeoutMs?: number;
  /** After "shutdown" + stdin EOF, wait this long before terminating. */
  shutdownGraceMs?: number;
  /** After terminate, wait this long before SIGKILL. */
  killGraceMs?: number;
}

export const DEFAULT_ARGS = ["-m", "proctor", "serve", "--token-stdin", "--port", "0"];
/** Variables that are shell-only configuration or development secrets: never inherited by the backend. */
const ENV_DENY = [/^ELECTRON_/, /^NODE_OPTIONS$/, /^QORGAU_DEV_TOKEN$/, /^QORGAU_SHELL_/, /^QORGAU_OPERATOR_/];

export function backendEnv(base: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const [k, v] of Object.entries(base)) {
    if (v !== undefined && !ENV_DENY.some((re) => re.test(k))) env[k] = v;
  }
  env.PYTHONUNBUFFERED = "1";
  env.PYTHONIOENCODING = "utf-8";
  return env;
}

export function parseReadyLine(line: string): ReadyInfo | null {
  if (!line.startsWith(READY_PREFIX)) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(line.slice(READY_PREFIX.length));
  } catch {
    return null;
  }
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const port = r.port;
  const pid = r.pid;
  if (r.contract !== CONTRACT_ID) return null;
  if (typeof r.contract_version !== "string" || !r.contract_version.startsWith(SUPPORTED_CONTRACT_MAJOR)) return null;
  if (typeof r.backend_version !== "string" || r.backend_version.length > 32) return null;
  if (typeof port !== "number" || !Number.isInteger(port) || port < 1 || port > 65535) return null;
  if (typeof pid !== "number" || !Number.isInteger(pid) || pid < 1) return null;
  return { contract: CONTRACT_ID, contract_version: r.contract_version, backend_version: r.backend_version, port, pid };
}

export function newToken(): string {
  return randomBytes(32).toString("hex"); // 64 chars >= 32 required by the backend
}

export interface ExitInfo {
  code: number | null;
  signal: NodeJS.Signals | null;
  /** True when stop() asked for it. */
  expected: boolean;
}

let launchCounter = 0;

/** One backend launch. Use BackendSupervisor for restart policy. */
export class BackendProcess {
  private child: ChildProcessWithoutNullStreams | null = null;
  private token: string | null = null;
  private stopping = false;
  private exited: ExitInfo | null = null;
  private exitWaiters: Array<(e: ExitInfo) => void> = [];
  private exitListeners: Array<(e: ExitInfo) => void> = [];
  readonly stderrTail: string[] = [];
  readonly launchId = ++launchCounter;

  constructor(private readonly opts: BackendLaunchOptions) {}

  get pid(): number | null {
    return this.child?.pid ?? null;
  }

  get hasExited(): boolean {
    return this.exited !== null;
  }

  onExit(listener: (e: ExitInfo) => void): void {
    this.exitListeners.push(listener);
  }

  start(): Promise<BackendConnection> {
    if (this.child) return Promise.reject(new Error("already started"));
    const token = newToken();
    this.token = token;
    registerSecret(token);
    const args = this.opts.args ?? DEFAULT_ARGS;
    const child = spawn(this.opts.python, args, {
      cwd: this.opts.cwd,
      env: backendEnv(this.opts.env ?? process.env),
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
      shell: false,
      detached: false,
    });
    this.child = child;
    log.info(`launch #${this.launchId}: ${this.opts.python} ${args.join(" ")} (cwd ${this.opts.cwd})`);

    const stderr = createInterface({ input: child.stderr });
    stderr.on("line", (line) => {
      const clean = redact(line).slice(0, 500);
      this.stderrTail.push(clean);
      if (this.stderrTail.length > 40) this.stderrTail.shift();
      if (/\b(ERROR|CRITICAL|Traceback)\b/.test(clean)) log.warn(`backend: ${clean}`);
      else log.debug(`backend: ${clean}`);
    });

    return new Promise<BackendConnection>((resolve, reject) => {
      let settled = false;
      const timeoutMs = this.opts.readyTimeoutMs ?? 60_000;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        reject(new Error(`backend did not print ${READY_PREFIX.trim()} within ${timeoutMs} ms`));
        void this.stop();
      }, timeoutMs);

      const stdout = createInterface({ input: child.stdout });
      stdout.on("line", (line) => {
        const ready = parseReadyLine(line);
        if (ready === null) {
          log.debug(`backend stdout (ignored): ${redact(line).slice(0, 200)}`);
          if (line.startsWith(READY_PREFIX) && !settled) {
            settled = true;
            clearTimeout(timer);
            reject(new Error("backend READY line does not match contract qorgau.v1 1.x"));
            void this.stop();
          }
          return;
        }
        if (settled) return;
        if (ready.pid !== child.pid) log.warn(`READY pid ${ready.pid} differs from child pid ${child.pid}`);
        settled = true;
        clearTimeout(timer);
        log.info(`launch #${this.launchId} ready: port ${ready.port}, backend ${ready.backend_version}`);
        resolve({ port: ready.port, token, ready, launchId: this.launchId });
      });

      child.on("error", (err) => {
        log.error(`spawn failed: ${err.message}`);
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          reject(err);
        }
        this.markExited({ code: null, signal: null, expected: this.stopping });
      });
      child.on("exit", (code, signal) => {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          const tail = this.stderrTail.slice(-5).join(" | ");
          reject(new Error(`backend exited before READY (code ${code}, signal ${signal}) ${tail}`));
        }
        this.markExited({ code, signal, expected: this.stopping });
      });
      // stdin errors (EPIPE after the child died) must not crash main
      child.stdin.on("error", (err) => log.debug(`stdin: ${err.message}`));
      child.stdin.write(token + "\n");
    });
  }

  private markExited(info: ExitInfo): void {
    if (this.exited) return;
    this.exited = info;
    if (this.token) forgetSecretLater(this.token);
    const level = info.expected ? "info" : "warn";
    log[level](`launch #${this.launchId} exited: code ${info.code}, signal ${info.signal}, expected ${info.expected}`);
    for (const w of this.exitWaiters.splice(0)) w(info);
    for (const l of this.exitListeners) l(info);
  }

  private waitExit(ms: number): Promise<ExitInfo | null> {
    if (this.exited) return Promise.resolve(this.exited);
    return new Promise((resolve) => {
      const timer = setTimeout(() => resolve(null), ms);
      this.exitWaiters.push((e) => {
        clearTimeout(timer);
        resolve(e);
      });
    });
  }

  /** Graceful: "shutdown" + EOF, then terminate, then kill. Resolves when the process is gone. */
  async stop(): Promise<ExitInfo & { forced: "no" | "terminate" | "kill" }> {
    this.stopping = true;
    const child = this.child;
    if (!child || this.exited) return { ...(this.exited ?? { code: null, signal: null, expected: true }), forced: "no" };
    try {
      if (child.stdin.writable) {
        child.stdin.write("shutdown\n");
        child.stdin.end();
      }
    } catch {
      /* already closed */
    }
    let e = await this.waitExit(this.opts.shutdownGraceMs ?? 7_000);
    if (e) return { ...e, forced: "no" };
    log.warn(`launch #${this.launchId}: graceful shutdown timed out, terminating`);
    child.kill("SIGTERM");
    e = await this.waitExit(this.opts.killGraceMs ?? 3_000);
    if (e) return { ...e, forced: "terminate" };
    child.kill("SIGKILL");
    e = await this.waitExit(5_000);
    return { ...(e ?? { code: null, signal: "SIGKILL", expected: true }), forced: "kill" };
  }
}

function forgetSecretLater(token: string): void {
  // Late stderr lines of a dead process may still be flushed; keep redacting for a while.
  setTimeout(() => forgetSecret(token), 30_000).unref();
}

export interface SupervisorHooks {
  onState(state: BackendProcessState, detail: string): void;
  onReady(conn: BackendConnection): void;
  /** Connection lost unexpectedly (crash/kill). Restrictions must be released by the caller. */
  onLost(reason: string): void;
}

export interface RestartPolicy {
  maxRestarts: number;
  windowMs: number;
  backoffMs: number[];
}

export const DEFAULT_RESTART: RestartPolicy = { maxRestarts: 3, windowMs: 120_000, backoffMs: [500, 2_000, 5_000] };

/** Owns at most one BackendProcess; restarts after crashes with backoff; never after stop(). */
export class BackendSupervisor {
  private current: BackendProcess | null = null;
  private conn: BackendConnection | null = null;
  private stateValue: BackendProcessState = "stopped";
  private stopped = false;
  private restarts: number[] = [];
  private restartTimer: NodeJS.Timeout | null = null;

  constructor(
    private readonly opts: BackendLaunchOptions,
    private readonly hooks: SupervisorHooks,
    private readonly policy: RestartPolicy = DEFAULT_RESTART,
  ) {}

  get state(): BackendProcessState {
    return this.stateValue;
  }

  get connection(): BackendConnection | null {
    return this.conn;
  }

  get pid(): number | null {
    return this.current?.pid ?? null;
  }

  private setState(state: BackendProcessState, detail: string): void {
    this.stateValue = state;
    this.hooks.onState(state, detail);
  }

  async start(): Promise<BackendConnection | null> {
    this.stopped = false;
    return this.launch("starting");
  }

  private async launch(state: "starting" | "restarting"): Promise<BackendConnection | null> {
    this.setState(state, state === "starting" ? "launch" : `restart ${this.restarts.length}`);
    const proc = new BackendProcess(this.opts);
    this.current = proc;
    proc.onExit((e) => this.handleExit(proc, e));
    try {
      const conn = await proc.start();
      if (this.stopped || this.current !== proc) {
        await proc.stop();
        return null;
      }
      this.conn = conn;
      this.setState("ready", `port ${conn.port}`);
      this.hooks.onReady(conn);
      return conn;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.error(`launch failed: ${msg}`);
      if (!this.stopped && this.current === proc) this.scheduleRestart(`launch failed: ${msg}`);
      return null;
    }
  }

  private handleExit(proc: BackendProcess, e: ExitInfo): void {
    if (proc !== this.current) return;
    const wasReady = this.conn !== null && this.conn.launchId === proc.launchId;
    this.conn = null;
    if (this.stopped || e.expected) return;
    const reason = `backend exited unexpectedly (code ${e.code}, signal ${e.signal})`;
    if (wasReady) this.hooks.onLost(reason);
    this.scheduleRestart(reason);
  }

  private scheduleRestart(reason: string): void {
    if (this.restartTimer) return;
    const now = Date.now();
    this.restarts = this.restarts.filter((t) => now - t < this.policy.windowMs);
    if (this.restarts.length >= this.policy.maxRestarts) {
      this.setState("failed", `${reason}; ${this.restarts.length} restarts within ${this.policy.windowMs} ms`);
      return;
    }
    const delay = this.policy.backoffMs[Math.min(this.restarts.length, this.policy.backoffMs.length - 1)] ?? 1_000;
    this.restarts.push(now);
    this.setState("restarting", `${reason}; retry in ${delay} ms`);
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null;
      if (!this.stopped) void this.launch("restarting");
    }, delay);
  }

  /** Manual retry after "failed" (operator action). */
  async retry(): Promise<BackendConnection | null> {
    if (this.stateValue !== "failed") return this.conn;
    this.restarts = [];
    return this.launch("starting");
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.restartTimer) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }
    const proc = this.current;
    this.conn = null;
    if (proc) await proc.stop();
    this.setState("stopped", "stopped");
  }
}
