// QA-only adaptation of the existing real-bridge scenario to session-bound teacher authentication.
// No product files/assertions change. After preflight creates/binds a session, enter the same
// per-run teacher PIN through the UI again before requesting the calibration skip.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const desktop = join(root, "desktop");
const original = join(desktop, "renderer/tests/real-bridge/run-replay.mjs");
const source = readFileSync(original, "utf8");
const anchor = '    const uiDelayMs = Number(process.env.QORGAU_REPLAY_UI_DELAY_MS || 0);';
if (source.split(anchor).length !== 2) throw new Error("Replay scenario changed; review QA adaptation");
const adapted = source
  .replace('const here = dirname(fileURLToPath(import.meta.url));', `const here = ${JSON.stringify(dirname(original))};`)
  .replace(anchor, `    if (!shell.shellState().operator_unlocked) await unlock(PIN);
    await until(() => shell.shellState().operator_unlocked);
    check(replayId + ": teacher authenticated for this bound session", true);
${anchor}`);
const out = resolve(process.argv[2] || join(desktop, "dist/final-replay-current-auth"));
mkdirSync(out, { recursive: true });
mkdirSync(join(desktop, "dist"), { recursive: true });
const generated = join(desktop, "dist/final-replay-current-auth.mjs");
writeFileSync(generated, adapted);
writeFileSync(join(out, "qa-adapter.json"), JSON.stringify({
  original: "desktop/renderer/tests/real-bridge/run-replay.mjs",
  original_sha256: createHash("sha256").update(source).digest("hex"),
  change: "Re-enter existing per-run teacher PIN via UI after preflight binds session; all original checks retained",
  product_modified: false,
}, null, 2) + "\n");
const child = spawn(process.execPath, [generated, out], { cwd: desktop, env: process.env, stdio: "inherit", windowsHide: true });
child.on("error", error => { console.error(error); process.exitCode = 1; });
child.on("exit", code => { process.exitCode = code ?? 1; });
