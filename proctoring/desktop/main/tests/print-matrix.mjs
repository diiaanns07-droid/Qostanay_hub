// Prints the capability matrix the shell would report on THIS machine (owner: A06).
//   node main/tests/print-matrix.mjs            # uses real os + VERIFICATION.json; probe = not run
//   node main/tests/print-matrix.mjs --json
// On the target Windows machine this reflects os.release() and any matching VERIFICATION.json record.
// It does NOT run the Electron self-test (that needs the running app); probe checks show as not_run,
// so in-app items print "unverified" here. Run `npm start` on Windows for the live (self-tested) matrix.
import { release as osRelease, version as osVersion } from "node:os";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const bundle = join(desktop, "dist", "test-main", "_capabilities.mjs");
await build({
  entryPoints: [join(desktop, "main", "src", "environment", "capabilities.ts")],
  outfile: bundle,
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  tsconfig: join(desktop, "tsconfig.json"),
  logLevel: "warning",
});
const { buildCapabilities, parseVerificationRecords, summarize } = await import(bundle);

let records = [];
try {
  const parsed = parseVerificationRecords(readFileSync(join(desktop, "native", "VERIFICATION.json"), "utf8"));
  records = parsed.records;
} catch {
  /* none */
}
const platform = { platform: process.platform, release: osRelease(), arch: process.arch, label: `${process.platform} ${osRelease()} ${process.arch} (${osVersion()})`.slice(0, 128) };
const caps = buildCapabilities({ platform, shellVersion: "0.1.0", probe: {}, probeRanAt: null, records, helper: { available: false, enforce: false, detail: process.platform === "win32" ? "not built" : "non-Windows" } });

if (process.argv.includes("--json")) {
  console.log(JSON.stringify(caps, null, 2));
} else {
  console.log(`platform: ${caps.platform}`);
  console.log(`exam_mode_supported: ${caps.exam_mode_supported}  summary: ${JSON.stringify(summarize(caps))}`);
  console.log("action".padEnd(28) + "status".padEnd(14) + "mechanism");
  for (const i of caps.items) console.log(i.action.padEnd(28) + i.status.padEnd(14) + i.mechanism);
}
