// Shell configuration from the environment (owner: A06). Pure function so it is unit-tested.
//
//   QORGAU_PROCTORING_ROOT   proctoring/ directory (backend cwd). Default: <desktop>/..
//   QORGAU_PYTHON            Python executable. Default: <root>/.venv/Scripts/python.exe (Windows)
//                            or <root>/.venv/bin/python
//   QORGAU_SHELL_DEV_RENDERER_URL   unpackaged builds only: load the Vite dev server
//                                   (http://127.0.0.1:<port>/ or http://localhost:<port>/)
//   QORGAU_SHELL_ALLOW_DEVTOOLS=1   unpackaged builds only: DevTools outside exam mode
//   QORGAU_SHELL_EMERGENCY_ACCELERATOR  default CommandOrControl+Alt+Shift+F12
//   QORGAU_SHELL_NATIVE_HELPER      path to the Windows helper exe (default <desktop>/native/bin/qorgau-guard.exe)
//   QORGAU_SHELL_NATIVE_ENFORCE=1   helper swallows keys (controlled test only); default = dry-run
//   QORGAU_SHELL_SELFTEST=0         skip the startup self-test (matrix then stays "unverified")
//   QORGAU_SHELL_READY_TIMEOUT_MS   backend READY timeout (default 60000)
import { join, resolve } from "node:path";

export interface ShellConfig {
  appRoot: string;
  proctoringRoot: string;
  python: string;
  preloadPath: string;
  rendererDir: string;
  devRendererUrl: string | null;
  allowDevTools: boolean;
  emergencyAccelerator: string;
  nativeHelperPath: string;
  nativeEnforce: boolean;
  verificationPath: string;
  selfTest: boolean;
  readyTimeoutMs: number;
  warnings: string[];
}

const DEV_URL_RE = /^http:\/\/(127\.0\.0\.1|localhost):\d{2,5}\/?$/;

export function loadConfig(appRoot: string, env: NodeJS.ProcessEnv, isPackaged: boolean, platform: NodeJS.Platform): ShellConfig {
  const warnings: string[] = [];
  const proctoringRoot = resolve(env.QORGAU_PROCTORING_ROOT || join(appRoot, ".."));
  const python =
    env.QORGAU_PYTHON ||
    (platform === "win32" ? join(proctoringRoot, ".venv", "Scripts", "python.exe") : join(proctoringRoot, ".venv", "bin", "python"));
  let devRendererUrl: string | null = null;
  if (env.QORGAU_SHELL_DEV_RENDERER_URL) {
    if (isPackaged) warnings.push("QORGAU_SHELL_DEV_RENDERER_URL ignored in a packaged build");
    else if (!DEV_URL_RE.test(env.QORGAU_SHELL_DEV_RENDERER_URL)) warnings.push("QORGAU_SHELL_DEV_RENDERER_URL must be a loopback http URL; ignored");
    else devRendererUrl = env.QORGAU_SHELL_DEV_RENDERER_URL.replace(/\/?$/, "/");
  }
  const allowDevTools = !isPackaged && env.QORGAU_SHELL_ALLOW_DEVTOOLS === "1";
  if (env.QORGAU_SHELL_ALLOW_DEVTOOLS === "1" && isPackaged) warnings.push("QORGAU_SHELL_ALLOW_DEVTOOLS ignored in a packaged build");
  const timeout = Number(env.QORGAU_SHELL_READY_TIMEOUT_MS ?? "60000");
  return {
    appRoot,
    proctoringRoot,
    python,
    preloadPath: join(appRoot, "dist", "preload", "preload.cjs"),
    rendererDir: join(appRoot, "dist", "renderer"),
    devRendererUrl,
    allowDevTools,
    emergencyAccelerator: env.QORGAU_SHELL_EMERGENCY_ACCELERATOR || "CommandOrControl+Alt+Shift+F12",
    nativeHelperPath: env.QORGAU_SHELL_NATIVE_HELPER || join(appRoot, "native", "bin", "qorgau-guard.exe"),
    nativeEnforce: env.QORGAU_SHELL_NATIVE_ENFORCE === "1",
    verificationPath: join(appRoot, "native", "VERIFICATION.json"),
    selfTest: env.QORGAU_SHELL_SELFTEST !== "0",
    readyTimeoutMs: Number.isFinite(timeout) && timeout >= 1000 ? timeout : 60_000,
    warnings,
  };
}
