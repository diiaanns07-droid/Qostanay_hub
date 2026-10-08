// Component demo: real production ExamSurface, no backend/devices/native helper/kiosk.
import { build } from "esbuild";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { mkdtempSync, rmSync } from "node:fs";
const require = createRequire(import.meta.url);
const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const out = join(desktop, "dist/website-demo.cjs");
await build({ entryPoints: [join(desktop, "main/tools/website-demo.ts")], outfile: out, bundle: true,
  platform: "node", format: "cjs", target: "node22", external: ["electron"], tsconfig: join(desktop, "tsconfig.json") });
const profile = mkdtempSync(join(desktop, "dist/website-demo-profile-"));
const env = { ...process.env, QORGAU_SHELL_NATIVE_ENFORCE: "0" };
delete env.ELECTRON_RUN_AS_NODE;
const show = process.argv.includes("--show");
const child = spawn(require("electron"), [out, `--user-data-dir=${profile}`, ...(show ? ["--show"] : [])],
  { cwd: desktop, env, stdio: "inherit", windowsHide: true });
const watchdog = setTimeout(() => { console.error("website demo watchdog: stopping own child"); child.kill(); }, show ? 310_000 : 90_000);
child.on("error", e => { console.error(e); process.exitCode = 1; });
child.on("exit", code => {
  clearTimeout(watchdog);
  if (!resolve(profile).startsWith(resolve(desktop, "dist") + sep)) throw new Error("unsafe profile path");
  rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  process.exitCode = code ?? 1;
});
