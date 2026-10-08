// Renderer unit checks using the existing esbuild + Node runner; no new dependencies.
import { build } from "esbuild";
import { spawnSync } from "node:child_process";
await build({ entryPoints: ["renderer/tests/class-state.test.ts"], outfile: "dist/test-renderer/class-state.test.cjs", bundle: true, platform: "node", format: "cjs", logLevel: "warning" });
const run = spawnSync(process.execPath, ["--test", "dist/test-renderer/class-state.test.cjs"], { stdio: "inherit" });
process.exit(run.status ?? 1);
