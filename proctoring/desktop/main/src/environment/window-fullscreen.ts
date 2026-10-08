/** A trusted calibration request cannot release fullscreen owned by the exam guard. */
export function applyCalibrationFullscreen(
  window: { setFullScreen(flag: boolean): void },
  flag: boolean,
  guardActive: boolean,
  examModeActive: boolean,
): void {
  // RUNNING reaches React before native startup finishes and exam_mode_active is published.
  // Calibration unmounts then; the guard already owns fullscreen during this interval.
  if (!flag && (guardActive || examModeActive)) return;
  window.setFullScreen(flag);
}
