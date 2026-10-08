// Type-checks every TS project whose sources exist (owner: A01). Missing module = SKIP, not PASS.
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const tsc = resolve(root, "node_modules", "typescript", "bin", "tsc");
const projects = [
  { config: "tsconfig.contracts.json", dir: "../contracts/ts", owner: "A01" },
  { config: "tsconfig.main.json", dir: "main/src", owner: "A06" },
  { config: "tsconfig.renderer.json", dir: "renderer/src", owner: "A07" },
];
let failed = false;
for (const p of projects) {
  const dir = resolve(root, p.dir);
  if (!existsSync(dir) || readdirSync(dir).length === 0) {
    console.log(`SKIP  ${p.config} (no sources in ${p.dir}; owner ${p.owner})`);
    continue;
  }
  const r = spawnSync(process.execPath, [tsc, "-p", resolve(root, p.config)], { stdio: "inherit" });
  console.log(`${r.status === 0 ? "PASS" : "FAIL"}  ${p.config}`);
  failed ||= r.status !== 0;
}
process.exit(failed ? 1 : 0);
