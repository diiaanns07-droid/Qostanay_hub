// Controller for the optional Windows helper desktop/native (owner: A06). Pure Node, tested with a
// protocol-compatible fake helper; the real helper (C, Win32) is NOT RUN without Windows.
//
// Protocol (one JSON object per line):
//   helper -> main:  {"type":"selfcheck","version","os","elevated"}            (--self-check, then exit 0)
//                    {"type":"ready","version","mode":"dry_run"|"enforce","hook":bool,"foreground_watch":bool}
//                    {"type":"key","key":"win"|"alt_tab"|"alt_esc"|"ctrl_esc"|"print_screen","swallowed":bool}
//                    {"type":"foreground","foreign":bool,"process":"<basename>"|null}
//                    {"type":"error","code":"<snake>"}   {"type":"bye","reason":"<snake>"}
//   main -> helper:  "hb" every second; "stop". stdin EOF, a missed heartbeat (5 s), parent exit or
//                    the max duration make the helper unhook and exit by itself.
// Never transmitted: other keys, typed text, window titles, clipboard.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { createInterface } from "node:readline";
import type { EnforcementResult, EnvironmentAction } from "@contracts/qorgau-v1.generated";
import { logger } from "../log";
import type { NativeHelperInfo } from "./capabilities";
import type { NativeHelperHandle } from "./guard";

const log = logger("native");
const PROCESS_RE = /^[A-Za-z0-9 ._()-]{1,64}$/;
export const HEARTBEAT_MS = 1_000;

export interface NativeObservation {
  action: EnvironmentAction;
  enforcement: EnforcementResult;
  mechanism: string;
  detail: { shortcut?: string; process_name?: string };
}

const KEY_MAP: Record<string, { action: EnvironmentAction; shortcut: string }> = {
  win: { action: "shortcut_win", shortcut: "Win" },
  ctrl_esc: { action: "shortcut_win", shortcut: "Ctrl+Esc" },
  alt_tab: { action: "shortcut_alt_tab", shortcut: "Alt+Tab" },
  alt_esc: { action: "shortcut_alt_tab", shortcut: "Alt+Esc" },
  print_screen: { action: "shortcut_print_screen", shortcut: "PrintScreen" },
};

/** Map one helper line to an observation (or null). Unknown/malformed input is ignored. */
export function parseHelperLine(line: string): { kind: string; obs: NativeObservation | null; raw: Record<string, unknown> } | null {
  if (line.length > 1024) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(line);
  } catch {
    return null;
  }
  if (typeof raw !== "object" || raw === null || typeof (raw as { type?: unknown }).type !== "string") return null;
  const r = raw as Record<string, unknown>;
  switch (r.type) {
    case "key": {
      const m = typeof r.key === "string" ? KEY_MAP[r.key] : undefined;
      if (!m) return { kind: "key", obs: null, raw: r };
      return {
        kind: "key",
        raw: r,
        obs: { action: m.action, enforcement: r.swallowed === true ? "blocked" : "detected_only", mechanism: "native.ll_keyboard_hook", detail: { shortcut: m.shortcut } },
      };
    }
    case "foreground": {
      if (r.foreign !== true) return { kind: "foreground", obs: null, raw: r };
      const name = typeof r.process === "string" && PROCESS_RE.test(r.process) ? r.process : undefined;
      return {
        kind: "foreground",
        raw: r,
        obs: { action: "foreign_window_foreground", enforcement: "detected_only", mechanism: "native.foreground_watch", detail: name ? { process_name: name } : {} },
      };
    }
    case "error":
      return {
        kind: "error",
        raw: r,
        obs: { action: "enforcement_error", enforcement: "failed", mechanism: "native.helper", detail: { shortcut: String(r.code ?? "error").slice(0, 32) } },
      };
    default:
      return { kind: String(r.type), obs: null, raw: r };
  }
}

export interface HelperCommand {
  command: string;
  /** prepended arguments (tests run a script through node) */
  prefixArgs?: string[];
}

/** Startup availability check: binary present + --self-check answered. Windows only. */
export async function checkHelper(cmd: HelperCommand, platform: NodeJS.Platform, enforce: boolean, timeoutMs = 3_000): Promise<NativeHelperInfo> {
  if (platform !== "win32" && !cmd.prefixArgs) return { available: false, enforce: false, detail: "не Windows" };
  if (!cmd.prefixArgs && !existsSync(cmd.command)) return { available: false, enforce: false, detail: "не собран (desktop/native/bin/qorgau-guard.exe отсутствует)" };
  return new Promise((resolve) => {
    const child = spawn(cmd.command, [...(cmd.prefixArgs ?? []), "--self-check"], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    let done = false;
    const finish = (info: NativeHelperInfo) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(info);
    };
    const timer = setTimeout(() => {
      child.kill();
      finish({ available: false, enforce: false, detail: "self-check timeout" });
    }, timeoutMs);
    createInterface({ input: child.stdout }).on("line", (line) => {
      const p = parseHelperLine(line);
      if (p?.kind === "selfcheck") {
        const os = typeof p.raw.os === "string" ? p.raw.os.slice(0, 40) : "?";
        const elevated = p.raw.elevated === true ? "admin" : "standard user";
        finish({ available: true, enforce, detail: `self-check ok: Windows ${os}, ${elevated}, ${enforce ? "enforce" : "dry-run"}` });
      }
    });
    child.on("error", (err) => finish({ available: false, enforce: false, detail: `self-check failed: ${err.message}` }));
    child.on("exit", (code) => finish({ available: false, enforce: false, detail: `self-check exited ${code}` }));
  });
}

export class NativeHelper implements NativeHelperHandle {
  private child: ChildProcessWithoutNullStreams | null = null;
  private hb: NodeJS.Timeout | null = null;
  private stopping = false;
  lastBye: string | null = null;

  constructor(
    private readonly cmd: HelperCommand,
    private readonly opts: { enforce: boolean; maxMinutes: number; onObservation(o: NativeObservation): void; readyTimeoutMs?: number },
  ) {}

  get running(): boolean {
    const c = this.child;
    return !this.stopping && c !== null && !c.killed && c.exitCode === null && c.signalCode === null;
  }

  start(_sessionId: string): Promise<{ mode: "dry_run" | "enforce" }> {
    if (this.running) return Promise.resolve({ mode: this.opts.enforce ? "enforce" : "dry_run" });
    this.stopping = false;
    this.lastBye = null;
    const args = [
      ...(this.cmd.prefixArgs ?? []),
      "--parent-pid",
      String(process.pid),
      "--mode",
      this.opts.enforce ? "enforce" : "dry-run",
      "--max-minutes",
      String(this.opts.maxMinutes),
    ];
    const child = spawn(this.cmd.command, args, { stdio: ["pipe", "pipe", "pipe"], windowsHide: true, shell: false });
    this.child = child;
    child.stdin.on("error", () => undefined);
    createInterface({ input: child.stderr }).on("line", (l) => log.debug(`helper: ${l.slice(0, 300)}`));
    return new Promise((resolve, reject) => {
      let ready = false;
      const timer = setTimeout(() => {
        if (ready) return;
        this.stopSync();
        reject(new Error("helper did not report ready"));
      }, this.opts.readyTimeoutMs ?? 3_000);
      createInterface({ input: child.stdout }).on("line", (line) => {
        const p = parseHelperLine(line);
        if (!p) return;
        if (p.kind === "ready" && !ready) {
          ready = true;
          clearTimeout(timer);
          if (p.raw.hook !== true) log.warn("helper ready WITHOUT keyboard hook");
          const mode = p.raw.mode === "enforce" ? "enforce" : "dry_run";
          if (mode === "enforce" && !this.opts.enforce) {
            log.error("helper claims enforce mode without opt-in; stopping it");
            this.stopSync();
            reject(new Error("unexpected helper mode"));
            return;
          }
          this.hb = setInterval(() => {
            if (child.stdin.writable) child.stdin.write("hb\n");
          }, HEARTBEAT_MS);
          resolve({ mode });
          return;
        }
        if (p.kind === "bye") this.lastBye = String(p.raw.reason ?? "");
        if (p.obs) this.opts.onObservation(p.obs);
      });
      child.on("error", (err) => {
        clearTimeout(timer);
        if (!ready) reject(err);
      });
      child.on("exit", (code, signal) => {
        clearTimeout(timer);
        if (this.hb) clearInterval(this.hb);
        this.hb = null;
        if (this.child === child) this.child = null;
        if (!ready) {
          reject(new Error(`helper exited before ready (code ${code})`));
          return;
        }
        if (!this.stopping) {
          log.warn(`helper exited unexpectedly (code ${code}, signal ${signal}, bye ${this.lastBye})`);
          this.opts.onObservation({ action: "enforcement_error", enforcement: "failed", mechanism: "native.helper", detail: { shortcut: "helper_exit" } });
        }
      });
    });
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    this.stopping = true;
    if (this.hb) clearInterval(this.hb);
    this.hb = null;
    await new Promise<void>((resolve) => {
      const t = setTimeout(() => {
        child.kill();
        resolve();
      }, 1_500);
      child.once("exit", () => {
        clearTimeout(t);
        resolve();
      });
      if (child.killed) return; // stopSync already signalled it; just wait for the exit
      try {
        child.stdin.write("stop\n");
        child.stdin.end();
      } catch {
        child.kill();
      }
    });
  }

  /** Synchronous: kill now (the OS removes the hook with the process). */
  stopSync(): void {
    const child = this.child;
    this.stopping = true;
    if (this.hb) clearInterval(this.hb);
    this.hb = null;
    if (child && child.exitCode === null) {
      try {
        child.kill();
      } catch {
        /* already gone */
      }
    }
  }
}
