// Launch only the dedicated LIVE window; watchdog can terminate only this child.
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const root = resolve(desktop, "..");
const env = { ...process.env, QORGAU_PROCTORING_ROOT: root, QORGAU_PYTHON: resolve(root, ".venv/Scripts/python.exe") };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(resolve(desktop, "node_modules/electron/dist/electron.exe"),
  [resolve(desktop, "dist/live-env.cjs"), "--root", root, ...process.argv.slice(2)],
  { cwd: desktop, env, windowsHide: true, stdio: "inherit" });
const watchdog = setTimeout(() => { console.error("LIVE watchdog: stopping own Electron child"); child.kill(); }, 130_000);
child.on("error", error => { clearTimeout(watchdog); console.error(error.message); process.exitCode = 1; });
child.on("exit", code => { clearTimeout(watchdog); process.exitCode = code ?? 1; });
