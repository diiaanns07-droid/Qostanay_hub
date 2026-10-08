// Shell state machine normal/preflight/exam/releasing/error (owner: A06). Pure logic, no Electron.
//
//   normal    — no bound session, or the bound session is terminal (finished/aborted/failed).
//   preflight — bound session in created/preflight/calibrating/ready, or paused by the operator.
//   exam      — bound session RUNNING and every restriction engaged by the guard.
//   releasing — restrictions are being released (finish/abort/pause/backend loss/emergency).
//   error     — backend failed/lost or the guard failed to engage; restrictions are released.
//
// Exam mode is entered ONLY when the backend reports the bound session as `running` (after an
// explicit startExam/resumeExam). Startup, install and preflight never engage restrictions.
import type { ApiErrorBody, SessionInfo, SessionState } from "@contracts/qorgau-v1.generated";
import type { BackendProcessState, ShellMode, ShellState } from "@contracts/bridge";
import { logger } from "../log";

const log = logger("state");

export const PREFLIGHT_STATES: readonly SessionState[] = ["created", "preflight", "calibrating", "ready"];
export const TERMINAL_STATES: readonly SessionState[] = ["finished", "aborted", "failed"];

export interface Guard {
  /** Engage every restriction for this session. Throws if exam mode cannot be entered. */
  engage(sessionId: string): Promise<void>;
  /** Release everything. Must never throw and must be idempotent. */
  release(reason: string): Promise<void>;
  readonly active: boolean;
}

export type ReleaseReason =
  | "session_paused"
  | "session_finished"
  | "session_aborted"
  | "session_failed"
  | "backend_lost"
  | "emergency_exit"
  | "renderer_gone"
  | "renderer_unresponsive"
  | "window_closed"
  | "app_quit"
  | "engage_failed"
  | "rebind";

export class ShellStateMachine {
  private s: ShellState;
  private listeners = new Set<(s: ShellState) => void>();
  private sessionState: SessionState | null = null;
  private chain: Promise<void> = Promise.resolve();
  /** Sessions that must never (re-)engage exam mode in this shell run (emergency exit latch). */
  private noEngage = new Set<string>();

  constructor(
    private readonly guard: Guard,
    init: { shell_version: string; platform: string },
  ) {
    this.s = {
      mode: "normal",
      backend: "stopped",
      exam_mode_active: false,
      operator_unlocked: false,
      session_id: null,
      last_error: null,
      shell_version: init.shell_version,
      platform: init.platform,
    };
  }

  get state(): ShellState {
    return { ...this.s };
  }

  get boundSessionState(): SessionState | null {
    return this.sessionState;
  }

  onChange(listener: (s: ShellState) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private patch(p: Partial<ShellState>): void {
    const before = JSON.stringify(this.s);
    this.s = { ...this.s, ...p };
    if (JSON.stringify(this.s) === before) return;
    log.info(`mode=${this.s.mode} exam=${this.s.exam_mode_active} backend=${this.s.backend} session=${this.s.session_id ?? "-"}`);
    for (const l of this.listeners) {
      try {
        l(this.state);
      } catch (err) {
        log.error("listener failed", err);
      }
    }
  }

  /** Serialize engage/release so they never interleave. */
  private run(task: () => Promise<void>): Promise<void> {
    const next = this.chain.then(task, task);
    this.chain = next.catch(() => undefined);
    return next;
  }

  /** Wait until every queued transition has finished (tests, shutdown). */
  settle(): Promise<void> {
    return this.chain;
  }

  setBackend(state: BackendProcessState, detail = ""): void {
    this.patch({ backend: state });
    if (state === "failed") {
      void this.releaseTo("error", "backend_lost", {
        code: "INTERNAL",
        message: `Local backend failed: ${detail}`.slice(0, 1000),
        retryable: true,
        details: { shell_code: "backend_unavailable" },
      });
    }
  }

  /** Backend crashed/was killed while a session might be active: release immediately. */
  backendLost(reason: string): Promise<void> {
    this.sessionState = null;
    return this.releaseTo("error", "backend_lost", {
      code: "INTERNAL",
      message: `Local backend connection lost: ${reason}`.slice(0, 1000),
      retryable: true,
      details: { shell_code: "backend_unavailable" },
    });
  }

  /** A new session was created through the bridge: bind it (releases anything left over). */
  bind(info: SessionInfo): Promise<void> {
    return this.run(async () => {
      if (this.guard.active) await this.guard.release("rebind");
      this.sessionState = info.state;
      this.patch({
        session_id: info.session_id,
        mode: this.modeFor(info.state),
        exam_mode_active: false,
        last_error: null,
      });
    }).then(() => this.observe(info));
  }

  /** Any SessionInfo seen from the backend (responses or stream). Unbound sessions are ignored. */
  observe(info: SessionInfo): Promise<void> {
    if (info.session_id !== this.s.session_id) return Promise.resolve();
    const prev = this.sessionState;
    this.sessionState = info.state;
    if (info.state === "running") {
      if (this.guard.active && this.s.mode === "exam") return Promise.resolve();
      return this.run(async () => {
        if (this.sessionState !== "running" || this.s.session_id !== info.session_id) return;
        if (this.guard.active) return;
        if (this.noEngage.has(info.session_id)) {
          this.patch({ mode: "error", exam_mode_active: false });
          return;
        }
        if (prev !== "running" && prev !== "paused") this.patch({ operator_unlocked: false }); // new exam
        try {
          await this.guard.engage(info.session_id);
          this.patch({ mode: "exam", exam_mode_active: true, last_error: null });
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          log.error(`engage failed: ${message}`);
          await this.guard.release("engage_failed");
          this.patch({
            mode: "error",
            exam_mode_active: false,
            last_error: { code: "INTERNAL", message: `Exam mode could not be engaged: ${message}`.slice(0, 1000), retryable: true, details: { shell_code: "enforcement_error" } },
          });
        }
      });
    }
    if (info.state === "paused") return this.releaseTo("preflight", "session_paused", null);
    if (TERMINAL_STATES.includes(info.state)) {
      const reason: ReleaseReason =
        info.state === "finished" ? "session_finished" : info.state === "aborted" ? "session_aborted" : "session_failed";
      return this.releaseTo("normal", reason, info.state === "failed" ? info.last_error : null);
    }
    // created/preflight/calibrating/ready
    if (this.guard.active) return this.releaseTo("preflight", "session_paused", null);
    this.patch({ mode: "preflight" });
    return Promise.resolve();
  }

  /** Release every restriction and move to `target`. Safe to call any time, any number of times. */
  releaseTo(target: ShellMode, reason: ReleaseReason, error: ApiErrorBody | null): Promise<void> {
    return this.run(async () => {
      if (this.guard.active || this.s.exam_mode_active) {
        this.patch({ mode: "releasing" });
        await this.guard.release(reason);
      }
      this.patch({ mode: target, exam_mode_active: false, ...(error ? { last_error: error } : {}) });
    });
  }

  /** After an emergency exit the session can never re-enter exam mode, even if it still runs. */
  forbidEngage(sessionId: string): void {
    this.noEngage.add(sessionId);
  }

  setOperator(unlocked: boolean): void {
    this.patch({ operator_unlocked: unlocked });
  }

  setError(error: ApiErrorBody): void {
    this.patch({ last_error: error });
  }

  private modeFor(state: SessionState): ShellMode {
    if (PREFLIGHT_STATES.includes(state) || state === "paused") return "preflight";
    if (state === "running") return "preflight"; // becomes "exam" only after the guard engaged
    return "normal";
  }
}
