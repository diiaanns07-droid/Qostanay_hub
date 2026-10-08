// Bridge method implementations in main (owner: A06). One method = one fixed backend route or one
// shell action. Arguments arrive UNTRUSTED from the renderer and are validated here; the result is
// always a BridgeResult. Pure logic over injected dependencies (no Electron import) so it is tested
// against the real backend in Node.
import type { BridgeResult, EvidenceBlob, SavedExport, ShellState } from "@contracts/bridge";
import type {
  AnswerRecord,
  CalibrationState,
  EnvironmentCapabilities,
  ExamDefinition,
  HealthReport,
  HumanReview,
  Incident,
  IncidentDetail,
  PreflightReport,
  SessionInfo,
  SessionSummary,
} from "@contracts/qorgau-v1.generated";
import { BackendClient } from "../backend/client";
import { fail, ok, shellError } from "../errors";
import { logger } from "../log";
import { decideAccess, OperatorSession, type AccessPolicy } from "../shell/access";
import type { OperatorAuth } from "../shell/operator";
import type { ShellStateMachine } from "../shell/state";
import type { InvokeName } from "./channels";
import * as v from "./validate";

const log = logger("api");
const P = BackendClient.path;

export interface ApiDeps {
  client: BackendClient;
  machine: ShellStateMachine;
  operator: OperatorAuth;
  capabilities(): EnvironmentCapabilities | null;
  /** Abort the bound session (best effort) and release every restriction. */
  emergencyExit(reason: string): Promise<void>;
  /** Deliver queued environment events before a call that ends/pauses the session (they are rejected after). */
  flushEvents?(): Promise<void>;
  /** Native save dialog; returns null when cancelled. Main writes the file, the renderer never sees a path. */
  saveFile(defaultName: string, bytes: Buffer, filters: { name: string; extensions: string[] }[]): Promise<string | null>;
  /** Teacher access policy (shell/access.ts). Default "review_after_pause". */
  accessPolicy?: AccessPolicy;
  /** Operator unlock lifetime. Defaults: 180 s idle, 30 min total. */
  operatorTimeouts?: { idleMs: number; maxMs: number };
  now?: () => number;
  /** Additive local display metadata; never includes the class join code/token. */
  healthMetadata?(): { computer_name: string; class_configured: boolean };
}

type Handler = (...args: unknown[]) => Promise<unknown>;

/** Arity of every method (exact). */
const ARITY: Record<InvokeName, number> = {
  getShellState: 0,
  getEnvironmentCapabilities: 0,
  operatorUnlock: 1,
  operatorLock: 0,
  requestEmergencyExit: 1,
  health: 0,
  listSessions: 0,
  createSession: 1,
  getSession: 1,
  runPreflight: 1,
  calibrationStart: 1,
  calibrationTarget: 2,
  calibrationState: 1,
  calibrationFinish: 1,
  calibrationCancel: 1,
  calibrationSkip: 2,
  startExam: 1,
  pauseExam: 2,
  resumeExam: 1,
  finishExam: 1,
  abortExam: 2,
  getExam: 1,
  saveAnswer: 3,
  listAnswers: 1,
  listIncidents: 1,
  getIncident: 2,
  addReview: 3,
  getEvidence: 2,
  getSummary: 1,
  exportReport: 2,
  deleteSession: 1,
};

export const DEFAULT_OPERATOR_TIMEOUTS = { idleMs: 180_000, maxMs: 30 * 60_000 };

export function createApi(d: ApiDeps): Record<InvokeName, Handler> {
  const { client, machine } = d;
  const policy: AccessPolicy = d.accessPolicy ?? "review_after_pause";
  const opSession = new OperatorSession({
    ...(d.operatorTimeouts ?? DEFAULT_OPERATOR_TIMEOUTS),
    ...(d.now ? { now: d.now } : {}),
    onExpire: (reason) => {
      log.info(`operator unlock expired (${reason})`);
      machine.setOperator(false);
    },
  });

  /** Session-changing calls feed the state machine (this is what engages/releases exam mode). */
  const sessionCall = async (method: "POST", path: string, body?: unknown, flushFirst = false): Promise<BridgeResult<SessionInfo>> => {
    if (flushFirst && d.flushEvents) {
      await Promise.race([d.flushEvents(), new Promise<void>((r) => setTimeout(r, 2_000).unref())]);
    }
    const r = await client.json<SessionInfo>(method, path, body, 60_000);
    if (r.ok) await machine.observe(r.data);
    return r;
  };

  /** Exam = restrictions engaged OR the bound session still RUNNING (e.g. after a release path). */
  const examInProgress = (): boolean => machine.state.exam_mode_active || machine.boundSessionState === "running";

  const decide = (name: InvokeName, firstArg: unknown) => {
    const st = machine.state;
    if (!st.operator_unlocked) opSession.locked(); // e.g. cleared by the state machine when the exam engages
    const unlocked = st.operator_unlocked && opSession.check(); // expiry locks the shell state too
    return decideAccess(name, firstArg, { examActive: examInProgress(), operatorUnlocked: unlocked, boundSessionId: st.session_id }, policy);
  };

  const latched = (sid: string): BridgeResult<never> | null =>
    machine.engageForbidden(sid)
      ? fail(shellError("INVALID_STATE", "enforcement_error", "Exam restrictions cannot be re-engaged for this session; finish or abort it"))
      : null;

  const bound = (sid: string): BridgeResult<never> | null => {
    const s = machine.state.session_id;
    if (s !== null && s !== sid && machine.state.exam_mode_active) {
      return fail(shellError("SESSION_MISMATCH", "session_not_bound", "Another session holds exam mode"));
    }
    return null;
  };

  const api: Record<InvokeName, Handler> = {
    getShellState: async () => machine.state,
    getEnvironmentCapabilities: async () => {
      const caps = d.capabilities();
      return caps
        ? ok(caps)
        : fail(shellError("NOT_FOUND", "enforcement_error", "Environment capabilities are not measured yet", true));
    },
    operatorUnlock: async (pinRaw) => {
      const pin = v.pin(pinRaw);
      const outcome = d.operator.check(pin);
      if (outcome === "ok") {
        machine.setOperator(true);
        opSession.unlocked();
        return ok<ShellState>(machine.state);
      }
      const map = {
        wrong: shellError("UNAUTHORIZED", "operator_pin_wrong", "Wrong operator PIN"),
        rate_limited: shellError("UNAUTHORIZED", "operator_pin_rate_limited", "Too many attempts, wait and retry", true),
        not_configured: shellError("NOT_IMPLEMENTED", "operator_pin_not_configured", "Operator PIN is not configured on this computer"),
      } as const;
      return fail(map[outcome]);
    },
    operatorLock: async () => {
      opSession.locked();
      machine.setOperator(false);
      return machine.state;
    },
    requestEmergencyExit: async (reasonRaw) => {
      const reason = v.emergencyReason(reasonRaw);
      await d.emergencyExit(reason);
      return ok<ShellState>(machine.state);
    },

    health: async () => {
      const r = await client.json<HealthReport>("GET", P("health"));
      return r.ok && d.healthMetadata ? ok({ ...r.data, ...d.healthMetadata() }) : r;
    },
    listSessions: async () => client.json<SessionInfo[]>("GET", P("sessions")),
    createSession: async (bodyRaw) => {
      const body = v.sessionCreate(bodyRaw);
      if (machine.state.exam_mode_active) {
        return fail(shellError("SESSION_ACTIVE", "exam_mode_active", "An exam is running"));
      }
      const r = await client.json<SessionInfo>("POST", P("sessions"), body);
      if (r.ok) await machine.bind(r.data);
      return r;
    },
    getSession: async (sid) => {
      const r = await client.json<SessionInfo>("GET", P("sessions", v.id(sid, "session_id")));
      if (r.ok) await machine.observe(r.data);
      return r;
    },
    runPreflight: async (sid) => client.json<PreflightReport>("POST", P("sessions", v.id(sid, "session_id"), "preflight"), undefined, 60_000),
    calibrationStart: async (sid) => client.json<CalibrationState>("POST", P("sessions", v.id(sid, "session_id"), "calibration", "start")),
    calibrationTarget: async (sid, target) =>
      client.json<CalibrationState>("POST", P("sessions", v.id(sid, "session_id"), "calibration", "target"), {
        target: v.calibrationTarget(target),
      }),
    calibrationState: async (sid) => client.json<CalibrationState>("GET", P("sessions", v.id(sid, "session_id"), "calibration")),
    calibrationFinish: async (sid) => client.json<CalibrationState>("POST", P("sessions", v.id(sid, "session_id"), "calibration", "finish")),
    calibrationCancel: async (sid) => client.json<CalibrationState>("POST", P("sessions", v.id(sid, "session_id"), "calibration", "cancel")),
    calibrationSkip: async (sid, body) =>
      client.json<CalibrationState>("POST", P("sessions", v.id(sid, "session_id"), "calibration", "skip"), v.calibrationSkip(body)),
    startExam: async (sidRaw) => {
      const sid = v.id(sidRaw, "session_id");
      if (machine.state.session_id !== sid) {
        return fail(shellError("SESSION_MISMATCH", "session_not_bound", "Start only the session created in this shell run"));
      }
      return latched(sid) ?? sessionCall("POST", P("sessions", sid, "start"));
    },
    pauseExam: async (sid, body) => {
      const s = v.id(sid, "session_id");
      return bound(s) ?? sessionCall("POST", P("sessions", s, "pause"), v.pauseRequest(body), true);
    },
    resumeExam: async (sid) => {
      const s = v.id(sid, "session_id");
      if (machine.state.session_id !== s) return fail(shellError("SESSION_MISMATCH", "session_not_bound", "Resume only the bound session"));
      return latched(s) ?? sessionCall("POST", P("sessions", s, "resume"));
    },
    finishExam: async (sid) => {
      const s = v.id(sid, "session_id");
      return bound(s) ?? sessionCall("POST", P("sessions", s, "finish"), undefined, true);
    },
    abortExam: async (sid, body) => {
      const s = v.id(sid, "session_id");
      return bound(s) ?? sessionCall("POST", P("sessions", s, "abort"), v.abortRequest(body), true);
    },
    getExam: async (sid) => client.json<ExamDefinition>("GET", P("sessions", v.id(sid, "session_id"), "exam")),

    saveAnswer: async (sid, qid, body) =>
      client.json<AnswerRecord>("PUT", P("sessions", v.id(sid, "session_id"), "answers", v.id(qid, "question_id")), v.answerUpsert(body)),
    listAnswers: async (sid) => client.json<AnswerRecord[]>("GET", P("sessions", v.id(sid, "session_id"), "answers")),
    listIncidents: async (sid) => client.json<Incident[]>("GET", P("sessions", v.id(sid, "session_id"), "incidents")),
    getIncident: async (sid, iid) =>
      client.json<IncidentDetail>("GET", P("sessions", v.id(sid, "session_id"), "incidents", v.id(iid, "incident_id"))),
    addReview: async (sid, iid, body) =>
      client.json<HumanReview>(
        "POST",
        P("sessions", v.id(sid, "session_id"), "incidents", v.id(iid, "incident_id"), "reviews"),
        v.humanReviewCreate(body),
      ),
    getEvidence: async (sid, eid) => {
      const r = await client.binary(P("sessions", v.id(sid, "session_id"), "evidence", v.id(eid, "evidence_id")));
      if (!r.ok) return r;
      const mt = r.data.contentType;
      if (mt !== "image/jpeg" && mt !== "video/mp4" && mt !== "video/webm") {
        return fail(shellError("INTERNAL", "backend_bad_response", `unexpected evidence media type ${mt}`));
      }
      return ok<EvidenceBlob>({ media_type: mt, bytes: new Uint8Array(r.data.bytes) });
    },
    getSummary: async (sid) => client.json<SessionSummary>("GET", P("sessions", v.id(sid, "session_id"), "summary")),
    exportReport: async (sidRaw, fmtRaw) => {
      const sid = v.id(sidRaw, "session_id");
      const fmt = v.exportFormat(fmtRaw);
      const r = await client.binary(P("sessions", sid, `report.${fmt}`), 60_000);
      if (!r.ok) return r;
      // the fetch can take long: never open a native dialog if an exam started meanwhile
      const again = decide("exportReport", sid);
      if (!again.allow) return fail(shellError(again.code, again.shellCode, again.message));
      const fileName = `qorgau-report-${sid}.${fmt}`;
      const saved = await d.saveFile(fileName, r.data.bytes, [{ name: fmt.toUpperCase(), extensions: [fmt] }]);
      return ok<SavedExport>({ file_name: fileName, saved: saved !== null });
    },
    deleteSession: async (sid) => client.json<{ deleted: true }>("DELETE", P("sessions", v.id(sid, "session_id"))),
  };

  // Wrap: arity, size, exam/operator gating, validation errors -> INVALID_ARGUMENT.
  const wrapped = {} as Record<InvokeName, Handler>;
  for (const name of Object.keys(api) as InvokeName[]) {
    const fn = api[name];
    wrapped[name] = async (...args: unknown[]) => {
      try {
        if (args.length !== ARITY[name]) throw new v.ValidationError("args", `expected ${ARITY[name]} argument(s), got ${args.length}`);
        v.checkSize(args);
        const decision = decide(name, args[0]);
        if (!decision.allow) {
          return fail(shellError(decision.code, decision.shellCode, decision.message));
        }
        const result = await fn(...args);
        // only a successful call that actually needed the unlock keeps it alive (no refresh by polling
        // PIN-free reads or by rejected arguments)
        if (decision.operator && (result as { ok?: boolean } | null)?.ok === true) opSession.touch();
        return result;
      } catch (err) {
        if (err instanceof v.ValidationError) {
          log.warn(`${name}: rejected argument ${err.field}: ${err.problem}`);
          return fail(shellError("INVALID_ARGUMENT", "invalid_argument", err.message.slice(0, 300), false, { field: err.field }));
        }
        log.error(`${name} failed`, err);
        return fail(shellError("INTERNAL", "backend_bad_response", `${name} failed in the shell`));
      }
    };
  }
  // getShellState / operatorLock return plain ShellState (not BridgeResult) per bridge.ts
  wrapped.getShellState = async () => machine.state;
  wrapped.operatorLock = async () => {
    opSession.locked();
    machine.setOperator(false);
    return machine.state;
  };
  return wrapped;
}

export const API_ARITY = ARITY;
