// Bundles Electron main and preload (owner: A01; sources: A06).
//   main/src/main.ts       -> dist/main/main.cjs
//   preload/src/preload.ts -> dist/preload/preload.cjs  (single file: required by sandbox: true)
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const entries = [
  { in: "main/src/main.ts", out: "dist/main/main.cjs" },
  { in: "preload/src/preload.ts", out: "dist/preload/preload.cjs" },
];
const missing = entries.filter((e) => !existsSync(resolve(root, e.in)));
if (missing.length) {
  console.error(`build-electron: missing entry point(s) ${missing.map((e) => e.in).join(", ")} (owner: A06)`);
  process.exit(1);
}
for (const e of entries) {
  await build({
    entryPoints: [resolve(root, e.in)],
    outfile: resolve(root, e.out),
    bundle: true,
    platform: "node",
    format: "cjs",
    target: "node22",
    sourcemap: true,
    external: ["electron", "bufferutil", "utf-8-validate"],
    tsconfig: resolve(root, "tsconfig.json"),
    logLevel: "info",
  });
}
