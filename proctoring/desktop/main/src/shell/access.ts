// Teacher/operator access policy for bridge methods (owner: A06). Pure logic, unit-tested.
//
// Two policies; the DEFAULT is the strict one:
//   "review_after_pause" (default)  During the exam (restrictions engaged) history, review, evidence,
//                                   summary, export and delete are closed for everyone. The teacher
//                                   reviews after pause (restrictions released) or finish.
//   "operator_live_review"          Opt-in (QORGAU_SHELL_OPERATOR_LIVE_REVIEW=1), pending A01 approval.
//                                   During the exam an operator-UNLOCKED console may read incidents,
//                                   evidence and the summary of the BOUND session and add reviews.
//                                   History (other sessions), export and delete stay closed.
// Neither policy has a bypass: every operator method needs the PIN unlock (scrypt, rate-limited), and
// the unlock expires after inactivity and after an absolute limit (OperatorSession below).
import type { ErrorCode } from "@contracts/qorgau-v1.generated";
import type { ShellCode } from "../errors";
import type { InvokeName } from "../ipc/channels";

export type AccessPolicy = "review_after_pause" | "operator_live_review";

export interface AccessContext {
  examActive: boolean;
  operatorUnlocked: boolean;
  boundSessionId: string | null;
}

export type AccessDecision = { allow: true } | { allow: false; code: ErrorCode; shellCode: ShellCode; message: string };

/** Need operator_unlocked in every mode. */
export const OPERATOR_REQUIRED: ReadonlySet<InvokeName> = new Set<InvokeName>([
  "pauseExam",
  "resumeExam",
  "calibrationSkip",
  "addReview",
  "getEvidence",
  "exportReport",
  "deleteSession",
]);

/** Closed while restrictions are engaged, whatever the policy. */
export const CLOSED_DURING_EXAM: ReadonlySet<InvokeName> = new Set<InvokeName>(["listSessions", "exportReport", "deleteSession"]);

/** Closed during the exam unless policy=operator_live_review AND unlocked AND the bound session. */
export const LIVE_REVIEW: ReadonlySet<InvokeName> = new Set<InvokeName>(["listIncidents", "getIncident", "addReview", "getEvidence", "getSummary"]);

const deny = (code: ErrorCode, shellCode: ShellCode, message: string): AccessDecision => ({ allow: false, code, shellCode, message });

/**
 * @param firstArg the raw first argument (session id for session-scoped methods); compared by
 *                 string equality only, validation happens afterwards in the handler.
 */
export function decideAccess(method: InvokeName, firstArg: unknown, ctx: AccessContext, policy: AccessPolicy): AccessDecision {
  if (ctx.examActive) {
    if (CLOSED_DURING_EXAM.has(method)) return deny("INVALID_STATE", "exam_mode_active", `${method} is not available during the exam`);
    if (LIVE_REVIEW.has(method)) {
      if (policy !== "operator_live_review") {
        return deny("INVALID_STATE", "exam_mode_active", `${method} is available after pause or finish`);
      }
      if (!ctx.operatorUnlocked) return deny("INVALID_STATE", "operator_locked", `${method} requires the operator (teacher) unlock`);
      if (typeof firstArg !== "string" || firstArg !== ctx.boundSessionId) {
        return deny("SESSION_MISMATCH", "session_not_bound", "During the exam only the current session can be reviewed");
      }
    }
  }
  if (OPERATOR_REQUIRED.has(method) && !ctx.operatorUnlocked) {
    return deny("INVALID_STATE", "operator_locked", `${method} requires the operator (teacher) unlock`);
  }
  return { allow: true };
}

/** Human-readable availability table for A07 (generated from the same sets; used in tests/docs). */
export function accessTable(methods: readonly InvokeName[], policy: AccessPolicy): Array<{ method: InvokeName; exam_locked: boolean; exam_unlocked: boolean; idle_locked: boolean; idle_unlocked: boolean }> {
  const bound = "bound";
  const ctx = (examActive: boolean, operatorUnlocked: boolean): AccessContext => ({ examActive, operatorUnlocked, boundSessionId: bound });
  return methods.map((m) => ({
    method: m,
    exam_locked: decideAccess(m, bound, ctx(true, false), policy).allow,
    exam_unlocked: decideAccess(m, bound, ctx(true, true), policy).allow,
    idle_locked: decideAccess(m, bound, ctx(false, false), policy).allow,
    idle_unlocked: decideAccess(m, bound, ctx(false, true), policy).allow,
  }));
}

/**
 * Operator unlock lifetime: expires after `idleMs` without an operator-gated call and after `maxMs`
 * in total. Expiry calls `onExpire` (which locks the shell state, so the UI updates immediately).
 */
export class OperatorSession {
  private unlockedAt: number | null = null;
  private lastUse = 0;
  private timer: NodeJS.Timeout | null = null;

  constructor(
    private readonly opts: { idleMs: number; maxMs: number; onExpire(reason: "idle" | "max"): void; now?: () => number },
  ) {}

  private now(): number {
    return (this.opts.now ?? Date.now)();
  }

  get active(): boolean {
    return this.unlockedAt !== null;
  }

  unlocked(): void {
    const t = this.now();
    this.unlockedAt = t;
    this.lastUse = t;
    this.arm();
  }

  /** Call on every operator-gated call that was allowed. */
  touch(): void {
    if (this.unlockedAt === null) return;
    this.lastUse = this.now();
    this.arm();
  }

  /** True when the unlock is still valid; expires (and reports) otherwise. */
  check(): boolean {
    if (this.unlockedAt === null) return false;
    const reason = this.expiredReason();
    if (reason) {
      this.expire(reason);
      return false;
    }
    return true;
  }

  locked(): void {
    this.unlockedAt = null;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private expiredReason(): "idle" | "max" | null {
    if (this.unlockedAt === null) return null;
    const t = this.now();
    if (t - this.unlockedAt >= this.opts.maxMs) return "max";
    if (t - this.lastUse >= this.opts.idleMs) return "idle";
    return null;
  }

  private expire(reason: "idle" | "max"): void {
    this.locked();
    this.opts.onExpire(reason);
  }

  private arm(): void {
    if (this.timer) clearTimeout(this.timer);
    if (this.unlockedAt === null) return;
    const t = this.now();
    const until = Math.max(0, Math.min(this.lastUse + this.opts.idleMs, this.unlockedAt + this.opts.maxMs) - t);
    this.timer = setTimeout(() => {
      this.timer = null;
      const reason = this.expiredReason();
      if (reason) this.expire(reason);
      else this.arm();
    }, until + 5);
    this.timer.unref?.();
  }
}
