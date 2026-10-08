// Full-screen window for calibration (A07-student). Electron: window.qorgauWindow (preload, one fixed IPC
// channel; the shell denies the HTML Fullscreen permission). Browser/FIXTURE: the HTML Fullscreen API.
type WindowApi = { setFullscreen(on: boolean): void };

function shellApi(): WindowApi | null {
  const w = (globalThis as { qorgauWindow?: WindowApi }).qorgauWindow;
  return w && typeof w.setFullscreen === "function" ? w : null;
}

export function setWindowFullscreen(on: boolean): void {
  const api = shellApi();
  if (api) {
    api.setFullscreen(on);
    return;
  }
  try {
    if (on && !document.fullscreenElement && document.fullscreenEnabled) void document.documentElement.requestFullscreen().catch(() => {});
    if (!on && document.fullscreenElement) void document.exitFullscreen().catch(() => {});
  } catch {
    /* not available: the overlay still covers the whole window */
  }
}
