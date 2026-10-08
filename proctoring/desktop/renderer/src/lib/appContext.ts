import { createContext, useContext } from "react";
import type { ShellState } from "@contracts/bridge";
import type { SessionInfo } from "@contracts/qorgau-v1.generated";
import type { FixtureBridge } from "../bridge/fixtureBridge";
import type { LiveStore } from "./liveStore";
import type { AppBridge } from "../../../shared/desk-scan";

export type Role = "student" | "teacher";

export interface AppApi {
  /** QorgauBridge + the A15 desk-scan methods (shared/desk-scan.ts). */
  bridge: AppBridge;
  /** Non-null only for the explicitly labelled FixtureBridge. */
  fixture: FixtureBridge | null;
  live: LiveStore;
  shell: ShellState | null;
  session: SessionInfo | null;
  /** Update the CURRENT session. Snapshots of any other session (late responses) are ignored. */
  setSession: (s: SessionInfo) => void;
  /** Switch the UI to a new session (only after createSession / restore). */
  bindSession: (s: SessionInfo) => void;
  /** True while `sid` is still the session shown by the UI (guard for late async responses). */
  isCurrent: (sid: string) => boolean;
  /** Backend process not ready (shell) or calls failing with a connection error. */
  backendLost: boolean;
  /** Effective role: teacher only when the shell reports operator_unlocked. */
  role: Role;
  requestTeacher: () => void;
  leaveTeacher: () => void;
  /** Drop the current (terminal) session from the UI and go back to preflight. */
  newSession: () => void;
}

export const AppContext = createContext<AppApi | null>(null);

export function useApp(): AppApi {
  const v = useContext(AppContext);
  if (!v) throw new Error("AppContext missing");
  return v;
}
