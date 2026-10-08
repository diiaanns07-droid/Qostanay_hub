// Preload (owner: A06): exposes window.qorgau: QorgauBridge (contracts/ts/bridge.ts) and, for the A07-student
// full-screen calibration, window.qorgauWindow.setFullscreen(boolean) (one fixed one-way channel).
// Runs with contextIsolation + sandbox. Every method is one fixed IPC channel; no generic invoke,
// no ipcRenderer, no Node API and no IPC event objects ever reach the page.
import { contextBridge, ipcRenderer } from "electron";
import "./exam";
import "./class-audio";
import "../../main/src/class-lock-preload";
import { BRIDGE_VERSION, type ShellState, type Unsubscribe } from "@contracts/bridge";
import type { PreviewFrameMeta, StreamEnvelope } from "@contracts/qorgau-v1.generated";
import { INVOKE, PUSH, SEND, type InvokeName } from "../../main/src/ipc/channels";
import type { AppBridge } from "../../shared/desk-scan";

const call =
  (name: InvokeName) =>
  (...args: unknown[]): Promise<never> =>
    ipcRenderer.invoke(INVOKE[name], ...args) as Promise<never>;

function fanout<A extends unknown[]>(channel: string, onCountChange?: (count: number) => void) {
  const listeners = new Set<(...a: A) => void>();
  ipcRenderer.on(channel, (_event, ...payload) => {
    for (const l of listeners) {
      try {
        l(...(payload as A));
      } catch (err) {
        console.error("qorgau listener failed", err);
      }
    }
  });
  return (listener: (...a: A) => void): Unsubscribe => {
    if (typeof listener !== "function") throw new TypeError("listener must be a function");
    const wrapped = (...a: A) => listener(...a);
    listeners.add(wrapped);
    onCountChange?.(listeners.size);
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      listeners.delete(wrapped);
      onCountChange?.(listeners.size);
    };
  };
}

const onShellState = fanout<[ShellState]>(PUSH.shellState);
// Notify only after the listener was inserted. Audio and React subscribe separately;
// each later subscriber needs current class state, without replaying audio commands.
const subscribeEvents = fanout<[StreamEnvelope]>(PUSH.streamEvent, (count) =>
  ipcRenderer.send(SEND.eventsSubscribed, count > 0),
);
const subscribePreview = fanout<[PreviewFrameMeta, Uint8Array]>(PUSH.previewFrame, (count) =>
  ipcRenderer.send(SEND.previewSubscribed, count > 0),
);

const bridge: AppBridge = {
  bridgeVersion: BRIDGE_VERSION,
  transport: "electron",

  getShellState: call("getShellState"),
  onShellState,
  getEnvironmentCapabilities: call("getEnvironmentCapabilities"),
  operatorUnlock: call("operatorUnlock"),
  operatorLock: call("operatorLock"),
  requestEmergencyExit: call("requestEmergencyExit"),

  health: call("health"),
  listSessions: call("listSessions"),
  createSession: call("createSession"),
  getSession: call("getSession"),
  runPreflight: call("runPreflight"),
  calibrationStart: call("calibrationStart"),
  calibrationTarget: call("calibrationTarget"),
  calibrationState: call("calibrationState"),
  calibrationFinish: call("calibrationFinish"),
  calibrationCancel: call("calibrationCancel"),
  calibrationSkip: call("calibrationSkip"),
  getDeskScan: call("getDeskScan"),
  startDeskScan: call("startDeskScan"),
  skipDeskScan: call("skipDeskScan"),
  startExam: call("startExam"),
  pauseExam: call("pauseExam"),
  resumeExam: call("resumeExam"),
  finishExam: call("finishExam"),
  abortExam: call("abortExam"),
  getExam: call("getExam"),

  saveAnswer: call("saveAnswer"),
  listAnswers: call("listAnswers"),
  listIncidents: call("listIncidents"),
  getIncident: call("getIncident"),
  addReview: call("addReview"),
  getEvidence: call("getEvidence"),
  getSummary: call("getSummary"),
  exportReport: call("exportReport"),
  deleteSession: call("deleteSession"),

  subscribeEvents,
  subscribePreview,
};

contextBridge.exposeInMainWorld("qorgau", bridge);
contextBridge.exposeInMainWorld("qorgauWindow", {
  setFullscreen: (on: boolean): void => ipcRenderer.send(SEND.windowFullscreen, on === true),
});
