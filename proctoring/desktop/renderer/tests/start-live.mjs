// Supervised camera check: actual Electron/backend, no native helper and no external class connection.
// Reuses existing dependencies. No SDK/package installation, settings changes or foreign-process control.
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { spawn } from "node:child_process";

const here = dirname(fileURLToPath(import.meta.url));
const desktop = resolve(here, "../..");
const root = resolve(desktop, "..");
const python = process.env.QORGAU_PYTHON || join(root, ".venv", "Scripts", "python.exe");
const models = process.env.QORGAU_MODELS_DIR || join(process.env.LOCALAPPDATA || "", "QorgauExam", "models");
if (process.platform !== "win32") throw new Error("This supervised launcher is for the Windows demo laptop.");
for (const path of [python, join(desktop, "dist/main/main.cjs"), join(desktop, "dist/renderer/index.html"), join(models, "attention/face_landmarker.task"), join(models, "phone/yolo11n.onnx")]) {
  if (!existsSync(path)) throw new Error(`Required existing file is missing: ${path}`);
}
const base = join(process.env.LOCALAPPDATA, "QorgauExam", "A07-live");
const out = join(base, new Date().toISOString().replace(/[:.]/g, "-"));
mkdirSync(out, { recursive: true });
writeFileSync(join(base, "latest.json"), JSON.stringify({ out, desktop }, null, 2));
const env = { ...process.env, QORGAU_PYTHON: python, QORGAU_PROCTORING_ROOT: root,
  PYTHONPATH: [join(root, "backend"), join(root, "contracts/python")].join(";"),
  QORGAU_MODELS_DIR: models, QORGAU_DATA_DIR: join(out, "data"), QORGAU_QA_OUTPUT: out,
  QORGAU_SHELL_NATIVE_HELPER: join(out, "native-helper-disabled.exe"), QORGAU_SHELL_NATIVE_ENFORCE: "0",
  QORGAU_CLASS_SERVER: "", QORGAU_CLASS_CODE: "",
};
delete env.ELECTRON_RUN_AS_NODE;
delete env.NODE_OPTIONS;
delete env.QORGAU_SHELL_DEV_RENDERER_URL;
console.log("LIVE A07: real camera/backend. Exit: Ctrl+Alt+Shift+F12 during exam, or close before exam.");
console.log("Follow the dots, finish calibration, start exam, look down for 4-5 seconds, then finish exam.");
console.log("Evidence (no automatic PASS decision): " + out);
const electron = createRequire(join(desktop, "package.json"))("electron");
const child = spawn(electron, [join(here, "live-bootstrap.cjs")], { cwd: desktop, env, stdio: "inherit", windowsHide: false });
child.on("error", (error) => { console.error(error.message); process.exitCode = 1; });
child.on("exit", (code) => { process.exitCode = code ?? 1; });
