import { createContext, useContext } from "react";
import type { QorgauBridge, ShellState } from "@contracts/bridge";
import type { SessionInfo } from "@contracts/qorgau-v1.generated";
import type { FixtureBridge } from "../bridge/fixtureBridge";
import type { LiveStore } from "./liveStore";

export type Role = "student" | "teacher";

export interface AppApi {
  bridge: QorgauBridge;
  /** Non-null only for the explicitly labelled FixtureBridge. */
  fixture: FixtureBridge | null;
  live: LiveStore;
  shell: ShellState | null;
  session: SessionInfo | null;
  setSession: (s: SessionInfo | null) => void;
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
