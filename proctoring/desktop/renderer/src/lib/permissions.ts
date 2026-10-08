// What the shell will allow right now. Mirrors A06 ipc/api.ts (BLOCKED_IN_EXAM, OPERATOR_ONLY) so the UI
// never offers an action the bridge rejects. The bridge stays the authority: a rejection is still shown.
import type { ShellState } from "@contracts/bridge";
import type { SessionState } from "@contracts/qorgau-v1.generated";

export type GuardedAction =
  | "history" // listSessions / listIncidents / getIncident / getSummary
  | "review" // addReview
  | "evidence" // getEvidence
  | "export" // exportReport
  | "delete" // deleteSession
  | "pause"; // pauseExam / resumeExam

const BLOCKED_IN_EXAM = new Set<GuardedAction>(["history", "review", "evidence", "export", "delete"]);
const OPERATOR_ONLY = new Set<GuardedAction>(["review", "evidence", "export", "delete", "pause"]);

export interface Permission {
  ok: boolean;
  reason: string | null;
}

/**
 * `sessionState` closes the race "session already running, exam mode not engaged yet": a running session is
 * treated as exam mode unless the shell reports an engage failure (mode "error").
 */
export function can(shell: ShellState | null, action: GuardedAction, sessionState?: SessionState | null): Permission {
  if (!shell) return { ok: false, reason: "Состояние оболочки ещё не получено." };
  const examLike = shell.exam_mode_active || (sessionState === "running" && shell.mode !== "error");
  if (examLike && BLOCKED_IN_EXAM.has(action)) {
    return {
      ok: false,
      reason: "Во время экзамена оболочка закрывает журнал, решения и материалы. Доступно на паузе или после завершения.",
    };
  }
  if (OPERATOR_ONLY.has(action) && !shell.operator_unlocked) {
    return { ok: false, reason: "Нужен режим преподавателя (PIN)." };
  }
  return { ok: true, reason: null };
}
