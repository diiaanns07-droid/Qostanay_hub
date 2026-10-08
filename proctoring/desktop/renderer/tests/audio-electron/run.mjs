import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve, delimiter } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const require = createRequire(import.meta.url);
const { chromium, _electron } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");
const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const proctoring = resolve(desktop, "..");
const out = join(desktop, "out", `audio-electron-${Date.now()}`);
mkdirSync(out, { recursive: true });
for (const [name, platform] of [["main", "node"], ["preload", "node"], ["renderer", "browser"]]) {
  await build({ absWorkingDir: desktop, entryPoints: [`renderer/tests/audio-electron/${name}.ts`], outfile: join(out, `${name}.${platform === "node" ? "cjs" : "js"}`),
    platform, bundle: true, format: platform === "node" ? "cjs" : "iife", external: platform === "node" ? ["electron"] : [] });
}
writeFileSync(join(out, "index.html"), '<!doctype html><meta charset="utf-8"><h1>Adal synthetic audio fixture</h1><script src="renderer.js"></script>');
const samples = 48000 * 3;
const wav = Buffer.alloc(44 + samples * 2);
wav.write("RIFF"); wav.writeUInt32LE(wav.length - 8, 4); wav.write("WAVEfmt ", 8); wav.writeUInt32LE(16, 16);
wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(48000, 24); wav.writeUInt32LE(96000, 28);
wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write("data", 36); wav.writeUInt32LE(samples * 2, 40);
for (let i = 0; i < samples; i++) wav.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 440 * i / 48000)), 44 + 2 * i);
writeFileSync(join(out, "tone.wav"), wav);
const env = { ...process.env, PYTHONPATH: [proctoring, join(proctoring, "backend"), join(proctoring, "contracts/python")].join(delimiter), PYTHONIOENCODING: "utf-8" };
delete env.ELECTRON_RUN_AS_NODE;
const python = spawn(process.env.QORGAU_PYTHON ?? "python", [join(proctoring, "class-audio/tests/e2e/bridge_server.py"), join(out, "server")], { cwd: proctoring, env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
let fixture, processLog = "";
python.stderr.on("data", (chunk) => { processLog += String(chunk); });
const ready = new Promise((resolveReady, reject) => {
  let buffer = "";
  python.stdout.on("data", (chunk) => { buffer += chunk; const line = buffer.split("\n").find((value) => value.startsWith('{"port"')); if (line) resolveReady(JSON.parse(line)); });
  python.on("exit", (code) => reject(new Error(`bridge fixture exited ${code}: ${processLog.slice(-2000)}`)));
  setTimeout(() => reject(new Error("bridge fixture startup timed out")), 30000).unref();
});
let electron, chrome;
const results = [];
const sleep = (ms) => new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
async function until(check, timeout = 12000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const result = await check(); if (result) return result; await sleep(80); }
  throw new Error("condition timed out");
}
try {
  fixture = await ready;
  const api = async (path, body) => (await fetch(`http://127.0.0.1:${fixture.port}/v1/${path}`, { method: body ? "POST" : "GET",
    headers: { Authorization: `Bearer ${fixture.token}`, "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) })).json();
  const studentId = await until(async () => { try { return (await api("test/events")).student_id; } catch { return null; } });
  electron = await _electron.launch({ executablePath: require("electron"), args: [join(out, "main.cjs")], env: { ...env, AUDIO_FIXTURE_PORT: String(fixture.port), AUDIO_FIXTURE_TOKEN: fixture.token }, timeout: 30000 });
  const student = await electron.firstWindow();
  student.on("pageerror", (error) => console.log("student error", error.message));
  await student.waitForFunction(() => !!window.audioTest);
  chrome = await chromium.launch({ channel: "chrome", headless: true, args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", `--use-file-for-fake-audio-capture=${join(out, "tone.wav")}`, "--autoplay-policy=no-user-gesture-required", "--mute-audio"] });
  const context = await chrome.newContext();
  const base = `http://127.0.0.1:${fixture.class_port}`;
  await context.request.post(`${base}/api/teacher/login`, { data: { pin: fixture.pin } });
  const teacher = await context.newPage();
  teacher.on("pageerror", (error) => console.log("teacher error", error.message));
  await teacher.goto(base);
  await teacher.evaluate(async () => {
    const { TeacherAudio } = await import("/api/teacher/audio/assets/teacher/teacher-audio.js");
    const { TeacherSignaling } = await import("/api/teacher/audio/assets/teacher/teacher-signaling.js");
    const audio = document.createElement("audio"); document.body.append(audio);
    const signaling = new TeacherSignaling({ url: `ws://${location.host}/api/teacher/audio/ws` });
    const controller = new TeacherAudio({ signaling, audioElement: audio });
    window.audioTeacher = { controller, signaling, audio, violations: [] };
    controller.subscribe((state) => { if (state.listening && (state.connection !== "connected" || !state.studentMedia?.mic_live)) window.audioTeacher.violations.push(state.phase); });
    signaling.connect();
  });
  await teacher.waitForFunction(() => window.audioTeacher.signaling.up);
  await sleep(300);
  const start = (listen, talk) => teacher.evaluate(({ studentId, listen, talk }) => window.audioTeacher.controller.start(studentId, { listen, talk }), { studentId, listen, talk });
  const stop = async () => {
    await teacher.evaluate(() => window.audioTeacher.controller.stop());
    await student.waitForFunction(() => window.audioTest.tracks.every((track) => track.readyState === "ended") && document.querySelector("[data-testid=class-audio-notice]").hidden);
    await sleep(200);
  };
  await start(true, false);
  try { await teacher.waitForFunction(() => window.audioTeacher.controller.state.listening && window.audioTeacher.controller.state.inLevel > 0.001, { timeout: 20000 }); }
  catch (error) { console.log("audio debug", await teacher.evaluate(() => window.audioTeacher.controller.state), await student.evaluate(() => ({ calls: window.audioTest.calls, banner: document.querySelector("aside")?.textContent, visibility: document.visibilityState }))); throw error; }
  const stats = await teacher.evaluate(async () => {
    const list = []; (await window.audioTeacher.controller.pc.getStats()).forEach((r) => { if (r.type === "inbound-rtp" && r.kind === "audio") list.push({ bytes: r.bytesReceived, packets: r.packetsReceived, level: r.audioLevel }); }); return list;
  });
  assert(stats[0].bytes > 0 && stats[0].packets > 0);
  assert.equal(await student.evaluate(() => window.audioTest.calls[0].visible && window.audioTest.calls[0].text.includes("запросил микрофон")), true);
  console.log("PASS real C1/C2/Electron audio received; pre-capture notice verified");
  const recorded = await teacher.evaluate(async () => {
    const chunks = [], recorder = new MediaRecorder(window.audioTeacher.audio.srcObject, { mimeType: "audio/webm;codecs=opus" });
    const done = new Promise((resolveDone) => { recorder.ondataavailable = (event) => chunks.push(event.data); recorder.onstop = async () => resolveDone(Array.from(new Uint8Array(await new Blob(chunks).arrayBuffer()))); });
    recorder.start(); setTimeout(() => recorder.stop(), 700); return done;
  });
  assert(recorded.length > 1000); writeFileSync(join(out, "received-synthetic-tone.webm"), Buffer.from(recorded));
  results.push({ name: "C1 → C2 → Electron actual IPC/WebRTC synthetic tone, banner before capture, received playable Opus", stats, recordedBytes: recorded.length });
  await stop();
  const captures = await student.evaluate(() => window.audioTest.calls.length);
  await start(false, true);
  await teacher.waitForFunction(() => window.audioTeacher.controller.state.speaking && window.audioTeacher.controller.state.studentMedia?.teacher_audio_playing);
  assert.equal(await student.evaluate(() => window.audioTest.calls.length), captures);
  results.push({ name: "talk-only receives teacher RTP and plays without student capture" });
  await stop();
  for (const [failure, reason] of [["NotAllowedError", "mic_denied"], ["NotFoundError", "mic_not_found"]]) {
    await student.evaluate((value) => { window.audioTest.failure = value; }, failure);
    await start(true, false);
    await teacher.waitForFunction((expected) => window.audioTeacher.controller.state.reason === expected, reason);
    assert.equal(await student.evaluate(() => window.audioTest.tracks.every((track) => track.readyState === "ended")), true);
    results.push({ name: `${failure} gives ${reason}, no success or live tracks` });
    await sleep(250);
  }
  await student.evaluate(() => { window.audioTest.failure = ""; });
  await start(true, true);
  await teacher.waitForFunction(() => window.audioTeacher.controller.state.listening && window.audioTeacher.controller.state.speaking);
  await api("test/action", { action: "finish" });
  await student.waitForFunction(() => window.audioTest.tracks.every((track) => track.readyState === "ended") && window.audioTest.pcs.every((pc) => pc.connectionState === "closed"));
  await teacher.waitForFunction(() => window.audioTeacher.controller.state.phase === "ended");
  results.push({ name: "local exam finish closes all student tracks/peers and teacher session" });
  await api("test/action", { action: "resume" }); await sleep(250);
  await start(true, false);
  await teacher.waitForFunction(() => window.audioTeacher.controller.state.listening);
  await api("test/action", { action: "disconnect" });
  await student.waitForFunction(() => window.audioTest.tracks.every((track) => track.readyState === "ended") && window.audioTest.pcs.every((pc) => pc.connectionState === "closed"));
  assert.deepEqual(await teacher.evaluate(() => window.audioTeacher.violations), []);
  results.push({ name: "classroom disconnect ends capture and peers; zero false listening states" });
  writeFileSync(join(out, "results.json"), JSON.stringify({ ok: true, results, realCapture: false, guards: false }, null, 2));
  console.log(JSON.stringify({ ok: true, results, out }, null, 2));
} finally {
  if (electron) await electron.close();
  if (chrome) await chrome.close();
  python.stdin.end();
  await Promise.race([new Promise((resolveExit) => python.once("exit", resolveExit)), sleep(5000)]);
  if (python.exitCode === null) python.kill();
}
