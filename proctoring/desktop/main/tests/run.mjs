// A06 test runner (no extra dependency): bundles main/src/__tests__/*.test.ts with the pinned esbuild
// and runs them with Node's built-in test runner. Electron is NOT required: tested modules never
// import "electron" (main.ts/preload.ts glue is covered by typecheck + build + manual checks).
//   node main/tests/run.mjs                 # all tests (integration tests need proctoring/.venv)
//   node main/tests/run.mjs guard state     # only files whose name contains one of the words
import { spawnSync } from "node:child_process";
import { readdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const srcDir = join(desktop, "main", "src", "__tests__");
const outDir = join(desktop, "dist", "test-main");
const filters = process.argv.slice(2);
const files = readdirSync(srcDir)
  .filter((f) => f.endsWith(".test.ts"))
  .filter((f) => filters.length === 0 || filters.some((w) => f.includes(w)));
if (files.length === 0) {
  console.error("no test files selected");
  process.exit(1);
}
rmSync(outDir, { recursive: true, force: true });
await build({
  entryPoints: files.map((f) => join(srcDir, f)),
  outdir: outDir,
  outExtension: { ".js": ".cjs" },
  bundle: true,
  platform: "node",
  format: "cjs",
  target: "node22",
  sourcemap: "inline",
  external: ["electron", "bufferutil", "utf-8-validate"],
  tsconfig: join(desktop, "tsconfig.json"),
  logLevel: "warning",
});
const outputs = files.map((f) => join(outDir, f.replace(/\.ts$/, ".cjs")));
const r = spawnSync(process.execPath, ["--test", "--test-reporter=spec", "--test-concurrency=1", ...outputs], {
  stdio: "inherit",
  cwd: desktop,
  env: { ...process.env, QORGAU_SHELL_LOG_LEVEL: process.env.QORGAU_SHELL_LOG_LEVEL ?? "error" },
});
process.exit(r.status ?? 1);
