// IPC channel names shared by preload and main (owner: A06). Each invoke channel = one bridge
// method = one fixed backend route (or one shell action). There is no generic channel.

export const INVOKE = {
  getShellState: "qorgau:shell:get-state",
  getEnvironmentCapabilities: "qorgau:shell:get-capabilities",
  operatorUnlock: "qorgau:shell:operator-unlock",
  operatorLock: "qorgau:shell:operator-lock",
  requestEmergencyExit: "qorgau:shell:emergency-exit",

  health: "qorgau:api:health",
  listSessions: "qorgau:api:list-sessions",
  createSession: "qorgau:api:create-session",
  getSession: "qorgau:api:get-session",
  runPreflight: "qorgau:api:preflight",
  calibrationStart: "qorgau:api:calibration-start",
  calibrationTarget: "qorgau:api:calibration-target",
  calibrationState: "qorgau:api:calibration-state",
  calibrationFinish: "qorgau:api:calibration-finish",
  calibrationCancel: "qorgau:api:calibration-cancel",
  calibrationSkip: "qorgau:api:calibration-skip",
  getDeskScan: "qorgau:api:desk-scan-get",
  startDeskScan: "qorgau:api:desk-scan-start",
  skipDeskScan: "qorgau:api:desk-scan-skip",
  startExam: "qorgau:api:start",
  pauseExam: "qorgau:api:pause",
  resumeExam: "qorgau:api:resume",
  finishExam: "qorgau:api:finish",
  abortExam: "qorgau:api:abort",
  getExam: "qorgau:api:exam",

  saveAnswer: "qorgau:api:save-answer",
  listAnswers: "qorgau:api:list-answers",
  listIncidents: "qorgau:api:list-incidents",
  getIncident: "qorgau:api:get-incident",
  addReview: "qorgau:api:add-review",
  getEvidence: "qorgau:api:get-evidence",
  getSummary: "qorgau:api:summary",
  exportReport: "qorgau:api:export-report",
  deleteSession: "qorgau:api:delete-session",
} as const;

export type InvokeName = keyof typeof INVOKE;

/** main -> renderer pushes */
export const PUSH = {
  shellState: "qorgau:push:shell-state",
  streamEvent: "qorgau:push:stream-event",
  previewFrame: "qorgau:push:preview-frame",
} as const;

/** renderer -> main one-way notifications (subscription counts only, no data) */
export const SEND = {
  eventsSubscribed: "qorgau:send:events-subscribed",
  previewSubscribed: "qorgau:send:preview-subscribed",
  /** A07-student: full-screen calibration (boolean). Leaving full screen is ignored while exam mode is on. */
  windowFullscreen: "qorgau:send:window-fullscreen",
} as const;
