// Protocol-compatible FAKE of the Windows helper, for testing main/src/environment/native.ts on any
// OS. It does NOT hook any key or watch any window — it only speaks the line protocol so the
// controller (heartbeat, ready, observations, stop, parent-death) can be tested. Never a real guard.
//   node fake-helper.mjs --self-check
//   node fake-helper.mjs --parent-pid N --mode dry-run|enforce --max-minutes M [scenario flags]
import { createInterface } from "node:readline";

const argv = process.argv.slice(2);
const has = (f) => argv.includes(f);
const val = (f, d) => {
  const i = argv.indexOf(f);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : d;
};
const say = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");

if (has("--self-check")) {
  say({ type: "selfcheck", version: "0.0.1-fake", os: "fake", elevated: false });
  process.exit(0);
}

const mode = val("--mode", "dry-run") === "enforce" ? "enforce" : "dry_run";
if (has("--no-ready")) {
  setTimeout(() => process.exit(0), 10_000); // never reports ready -> controller times out
} else if (has("--claim-enforce")) {
  say({ type: "ready", version: "0.0.1-fake", mode: "enforce", hook: true, foreground_watch: true });
} else {
  say({ type: "ready", version: "0.0.1-fake", mode, hook: !has("--no-hook"), foreground_watch: true });
}

let lastHb = Date.now();
const rl = createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const cmd = line.trim();
  if (cmd === "hb") lastHb = Date.now();
  else if (cmd === "stop") {
    say({ type: "bye", reason: "stop_requested" });
    process.exit(0);
  }
});
rl.on("close", () => {
  say({ type: "bye", reason: "stdin_eof" });
  process.exit(0);
});

// scenario emissions
if (has("--emit-keys")) {
  setTimeout(() => say({ type: "key", key: "win", swallowed: mode === "enforce" }), 50);
  setTimeout(() => say({ type: "key", key: "alt_tab", swallowed: mode === "enforce" }), 80);
  setTimeout(() => say({ type: "key", key: "unknown_key", swallowed: true }), 100);
  setTimeout(() => say({ type: "foreground", foreign: true, process: "chrome.exe" }), 120);
  setTimeout(() => say({ type: "foreground", foreign: true, process: "bad title!" }), 140); // invalid name -> no name
  setTimeout(() => say({ type: "garbage not json" }), 160);
}
if (has("--crash")) setTimeout(() => process.exit(3), 100);

// watchdog: a missed heartbeat (parent gone) makes the real helper unhook; the fake just exits
setInterval(() => {
  if (Date.now() - lastHb > 5_000) {
    say({ type: "bye", reason: "heartbeat_lost" });
    process.exit(0);
  }
}, 1_000).unref();
