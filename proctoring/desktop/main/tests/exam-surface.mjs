// Builds/runs only the bounded hidden-window fixture. Never launches the real backend or guard.
import { build } from "esbuild";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { mkdtempSync, rmSync } from "node:fs";
const require = createRequire(import.meta.url);
const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const out = join(desktop, "dist/exam-fixture.cjs");
await build({ entryPoints: [join(desktop, "main/tests/exam-surface.fixture.ts")], outfile: out, bundle: true, platform: "node", format: "cjs", target: "node22", external: ["electron"], tsconfig: join(desktop, "tsconfig.json") });
const profile = mkdtempSync(join(desktop, "dist/exam-fixture-profile-"));
const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(require("electron"), [out, `--user-data-dir=${profile}`], { cwd: desktop, env, stdio: "inherit", windowsHide: true });
const timeout = setTimeout(() => { console.error("exam fixture timeout"); child.kill(); }, 45000);
child.on("exit", (code) => {
  clearTimeout(timeout);
  // Generated profile is verified within this checkout's dist directory.
  if (!resolve(profile).startsWith(resolve(desktop, "dist") + (process.platform === "win32" ? "\\" : "/"))) throw new Error("unsafe test profile path");
  rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  process.exit(code ?? 1);
});
