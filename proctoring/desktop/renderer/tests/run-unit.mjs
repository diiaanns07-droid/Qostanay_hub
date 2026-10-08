// Renderer unit checks using the existing esbuild + Node runner; no new dependencies.
import { build } from "esbuild";
import { spawnSync } from "node:child_process";
const tests = ["class-state", "exam-checks", "review-zone", "desk-scan"];
await build({ entryPoints: tests.map((t) => `renderer/tests/${t}.test.ts`), outdir: "dist/test-renderer", outExtension: { ".js": ".cjs" }, bundle: true, platform: "node", format: "cjs", logLevel: "warning" });
const run = spawnSync(process.execPath, ["--test", ...tests.map((t) => `dist/test-renderer/${t}.test.cjs`)], { stdio: "inherit" });
process.exit(run.status ?? 1);
