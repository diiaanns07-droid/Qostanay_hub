// T05 end-to-end: REAL dev signaling server (python) + REAL WebRTC in headless Chromium, teacher page + labelled
// TEST PEER pages. Not the student application. Media: Chromium fake capture device (a test tone) where noted;
// permission denial and "no device" use the browser's real behaviour (no permission granted / no audio device).
//
// Usage: node proctoring/class-audio/tests/e2e/audio.e2e.mjs <outDir>
// Env: QORGAU_PYTHON (python 3.12 with fastapi/uvicorn/websockets), PLAYWRIGHT_MODULE (playwright install).
import { spawn, execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, existsSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { networkInterfaces, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { createServer, connect as tcpConnect } from "node:net";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const serverDir = resolve(here, "..", "..", "server");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");
const PY = process.env.QORGAU_PYTHON ?? "python3";
const OUT = resolve(process.argv[2] ?? "audio-e2e-out");
mkdirSync(OUT, { recursive: true });

const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 15000, step = 100) {
  const end = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) throw new Error("timeout waiting for condition");
    await sleep(step);
  }
}
const freePort = () =>
  new Promise((r) => {
    const s = createServer();
    s.listen(0, "127.0.0.1", () => {
      const p = s.address().port;
      s.close(() => r(p));
    });
  });
const lanIp = () =>
  Object.values(networkInterfaces())
    .flat()
    .find((i) => i && i.family === "IPv4" && !i.internal)?.address ?? null;

async function startServer({ tls = null } = {}) {
  const port = await freePort();
  const pinFile = join(mkdtempSync(join(tmpdir(), "qa-pin-")), "pin.json");
  const args = ["-m", "qorgau_class_audio.devserver", "--host", "0.0.0.0", "--port", String(port), "--pin-file", pinFile];
  if (tls) args.push("--tls-cert", tls.cert, "--tls-key", tls.key);
  const proc = spawn(PY, args, { cwd: serverDir, env: { ...process.env, PYTHONPATH: serverDir, QORGAU_AUDIO_LOG: "WARNING" }, stdio: ["ignore", "pipe", "pipe"] });
  let out = "";
  proc.stdout.on("data", (d) => (out += d));
  proc.stderr.on("data", (d) => (out += d));
  await until(() => existsSync(pinFile) && readFileSync(pinFile, "utf8").length > 5, 20000);
  await until(
    () =>
      new Promise((r) => {
        const sock = tcpConnect(port, "127.0.0.1", () => {
          sock.end();
          r(true);
        });
        sock.on("error", () => r(false));
      }),
    20000,
  );
  const { pin, join_code } = JSON.parse(readFileSync(pinFile, "utf8"));
  return { proc, port, pin, join: join_code, log: () => out };
}

// The cloud sandbox routes browser traffic to non-loopback addresses through an egress proxy (it answered the
// ws:// upgrade with 403). Real classroom PCs have no such proxy: talk to the LAN address directly.
const DIRECT = ["--proxy-server=direct://"];
const FAKE = [...DIRECT, "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"];

async function teacherPage(browser, base, pin) {
  const ctx = await browser.newContext({ viewport: { width: 1100, height: 760 }, locale: "ru-RU" });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("teacher pageerror", String(e)));
  await page.goto(`${base}/?debug=1`);
  await page.getByLabel("PIN преподавателя").fill(pin);
  await page.getByRole("button", { name: "Войти" }).click();
  await page.getByText("Код подключения для студентов").waitFor();
  await page.waitForFunction(() => window.__qa?.sig?.up === true);
  // record any moment where the UI claims "listening" without a connected peer (must never happen)
  await page.evaluate(() => {
    window.__violations = [];
    window.__qa.controller.subscribe((s) => {
      if (s.listening && s.connection !== "connected") window.__violations.push(`listening while ${s.connection}`);
      if (s.listening && !(s.studentMedia && s.studentMedia.mic_live)) window.__violations.push("listening without student mic_live");
    });
  });
  return { ctx, page };
}

async function peerPage(browser, base, join, label) {
  const ctx = await browser.newContext({ viewport: { width: 800, height: 700 }, locale: "ru-RU" });
  const page = await ctx.newPage();
  await page.goto(`${base}/test-peer/`);
  await page.locator("#code").fill(join);
  await page.locator("#label").fill(label);
  await page.getByRole("button", { name: "Подключиться к серверу" }).click();
  await page.getByText(/Подключён к серверу как/).waitFor();
  const sid = await page.evaluate(() => window.__peer.studentId);
  return { ctx, page, sid };
}

const tstate = (page) => page.evaluate(() => JSON.parse(JSON.stringify(window.__qa.controller.state)));
const ptracks = (page) => page.evaluate(() => window.__peer.tracks());
const indicators = (page) =>
  page.evaluate(() => ({ mic: !document.getElementById("ind-mic").hidden, teacher: !document.getElementById("ind-teacher").hidden, session: !document.getElementById("ind-session").hidden }));

async function selectStudent(teacher, sid) {
  await teacher.locator(`[data-student-id="${sid}"]`).click();
  await teacher.locator(".qa-audio").waitFor();
}

let srv = null;
let browser = null;
const extra = [];
try {
  srv = await startServer();
  const base = `http://127.0.0.1:${srv.port}`;
  browser = await chromium.launch({ args: FAKE });
  const T = await teacherPage(browser, base, srv.pin);
  const A = await peerPage(browser, base, srv.join, "A");
  await T.page.locator(`[data-student-id="${A.sid}"]`).waitFor();
  check("teacher logged in by one-time PIN, student A listed", true, A.sid);

  // ---------------------------------------------------------------- S1 listen
  await selectStudent(T.page, A.sid);
  await T.page.getByRole("button", { name: "Слушать" }).click();
  const seen = new Set();
  await until(async () => {
    seen.add(await T.page.locator(".qa-status").textContent());
    return (await tstate(T.page)).listening;
  }, 20000, 50);
  await T.page.getByText("Слушаю студента.").waitFor();
  const st1 = await tstate(T.page);
  check("listening shown only after real connection + inbound audio", st1.connection === "connected" && st1.studentMedia?.mic_live === true, [...seen].join(" → "));
  await until(async () => (await tstate(T.page)).inLevel > 0, 10000).then(
    () => check("teacher receives the student's audio (inbound audioLevel > 0, fake tone)", true),
    () => check("teacher receives the student's audio (inbound audioLevel > 0, fake tone)", false),
  );
  const indA = await indicators(A.page);
  check("student sees 'Микрофон включён преподавателем'", indA.mic === true);
  check("student mic track live only while listening", (await ptracks(A.page)).mic === "live");
  await T.page.screenshot({ path: join(OUT, "01-teacher-listening.png") });
  await A.page.screenshot({ path: join(OUT, "02-peer-mic-indicator.png") });

  // ---------------------------------------------------------------- S2 talk
  await T.page.getByRole("button", { name: "Говорить" }).click();
  await until(async () => (await indicators(A.page)).teacher, 20000).then(
    () => check("student sees 'Говорит преподаватель' (teacher audio actually arrives)", true),
    () => check("student sees 'Говорит преподаватель' (teacher audio actually arrives)", false),
  );
  await until(async () => (await tstate(T.page)).speaking && (await tstate(T.page)).studentMedia?.teacher_audio_playing, 15000).then(
    () => check("teacher 'Говорю' after outbound packets + student report", true),
    () => check("teacher 'Говорю' after outbound packets + student report", false),
  );
  await T.page.getByText("Связь в обе стороны: слушаю и говорю.").waitFor();
  await T.page.screenshot({ path: join(OUT, "03-teacher-both.png") });
  await A.page.screenshot({ path: join(OUT, "04-peer-both-indicators.png") });
  // talk only → the student's microphone must be released
  await T.page.getByRole("button", { name: "Не слушать" }).click();
  await until(async () => (await ptracks(A.page)).mic === null && !(await indicators(A.page)).mic, 15000).then(
    () => check("listen off → student mic track stopped, indicator gone, talk continues", true),
    () => check("listen off → student mic track stopped, indicator gone, talk continues", false),
  );
  await T.page.getByRole("button", { name: "Слушать" }).click();
  await until(async () => (await tstate(T.page)).listening, 20000).then(
    () => check("listen on again (renegotiation)", true),
    () => check("listen on again (renegotiation)", false),
  );

  // ---------------------------------------------------------------- S3 another student cannot be connected by accident
  const B = await peerPage(browser, base, srv.join, "B");
  await T.page.locator(`[data-student-id="${B.sid}"]`).waitFor();
  await selectStudent(T.page, B.sid);
  await T.page.getByText(/Сейчас идёт аудиосвязь с/).waitFor();
  check("panel of another student shows the busy line, no connect buttons", (await T.page.locator(".qa-actions").isHidden()));
  const raw = await T.page.evaluate(
    (sid) =>
      new Promise((resolve) => {
        const un = window.__qa.sig.subscribe((m) => {
          if (m.type === "audio_error") {
            un();
            resolve(m.code);
          }
        });
        window.__qa.sig.send({ type: "audio_request", student_id: sid, listen: true, talk: false });
      }),
    B.sid,
  );
  check("raw second audio_request rejected by the server", raw === "teacher_busy", raw);
  const asid = (await tstate(T.page)).sessionId;
  const forged = await B.page.evaluate(
    (asid) =>
      new Promise((resolve) => {
        // student B uses ITS OWN authenticated socket to inject messages for A's session
        const orig = window.__peer.endpoint.o.send;
        orig({ type: "audio_signal", audio_session_id: asid, kind: "answer", sdp: "v=0\r\n" });
        orig({ type: "audio_media", audio_session_id: asid, mic_live: false, indicator_shown: false, stopped: true });
        setTimeout(() => resolve(true), 1500);
      }),
    asid,
  );
  void forged;
  const stA = await tstate(T.page);
  check("forged signal/stop from student B for A's session dropped; A still connected", stA.phase === "connected" && stA.sessionId === asid);
  check("student B never got a microphone request", (await ptracks(B.page)).mic === null && !(await indicators(B.page)).mic);
  await T.page.screenshot({ path: join(OUT, "05-teacher-busy-other-student.png") });

  // ---------------------------------------------------------------- S4 explicit stop closes every track
  await selectStudent(T.page, A.sid);
  await T.page.getByRole("button", { name: "Завершить связь" }).click();
  await T.page.getByText(/Связь завершена: связь завершена преподавателем/).waitFor();
  await until(async () => {
    const t = await ptracks(A.page);
    const i = await indicators(A.page);
    return t.mic === null && t.pc === null && t.lastMic === "ended" && !i.mic && !i.teacher && !i.session;
  }, 10000).then(
    () => check("stop → student mic track 'ended', peer closed, all indicators off", true),
    async () => check("stop → student mic track 'ended', peer closed, all indicators off", false, JSON.stringify(await ptracks(A.page))),
  );
  const tAfter = await T.page.evaluate(() => ({ pc: window.__qa.controller.pc, mic: window.__qa.controller.micTrack, src: document.getElementById("student-audio").srcObject }));
  check("stop → teacher peer closed, teacher mic released, audio element detached", tAfter.pc === null && tAfter.mic === null && tAfter.src === null);
  await until(async () => (await T.page.locator(`[data-student-id="${A.sid}"]`).textContent()).includes("микрофон включён") === false, 6000).then(
    () => check("student status mic_active=false reaches the teacher list", true),
    () => check("student status mic_active=false reaches the teacher list", false),
  );

  // ---------------------------------------------------------------- S8 network drop + recovery (student link cut)
  await T.page.getByRole("button", { name: "Слушать" }).click();
  await until(async () => (await tstate(T.page)).listening, 20000);
  await A.page.getByRole("button", { name: "Оборвать связь (без завершения)" }).click();
  await until(async () => (await ptracks(A.page)).mic === null && !(await indicators(A.page)).mic, 5000).then(
    () => check("student link lost → student mic stopped immediately (fail closed)", true),
    () => check("student link lost → student mic stopped immediately (fail closed)", false),
  );
  await T.page.getByText(/Связь завершена: студент потерял связь с сервером/).waitFor({ timeout: 10000 });
  check("teacher sees 'студент потерял связь', listening off", !(await tstate(T.page)).listening);
  await T.page.screenshot({ path: join(OUT, "06-teacher-student-dropped.png") });
  await A.page.getByRole("button", { name: "Переподключиться (resume_token)" }).click();
  await A.page.getByText(new RegExp(`Подключён к серверу как ${A.sid}`)).waitFor();
  await until(async () => !(await T.page.getByRole("button", { name: /Подключиться снова/ }).isDisabled()), 8000);
  await T.page.getByRole("button", { name: "Подключиться снова" }).click();
  await until(async () => (await tstate(T.page)).listening, 20000).then(
    () => check("recovery: same student (resume_token) → teacher reconnects → listening again", true),
    () => check("recovery: same student (resume_token) → teacher reconnects → listening again", false),
  );

  // ---------------------------------------------------------------- S9 abrupt crash of the student page
  await A.page.close();
  await T.page.getByText(/Связь завершена: студент потерял связь с сервером/).waitFor({ timeout: 20000 });
  check("student page crash/close → teacher session ended, peer torn down", (await T.page.evaluate(() => window.__qa.controller.pc)) === null);

  // ---------------------------------------------------------------- S10 authorization loss closes streams
  const A2 = await peerPage(browser, base, srv.join, "A2");
  await T.page.locator(`[data-student-id="${A2.sid}"]`).waitFor();
  await selectStudent(T.page, A2.sid);
  await T.page.getByRole("button", { name: "Слушать" }).click();
  await until(async () => (await tstate(T.page)).listening, 20000);
  const viol = await T.page.evaluate(() => window.__violations);
  check("UI never claimed 'listening' without a connected peer and live student mic", viol.length === 0, viol.slice(0, 3).join("; "));
  // revoke from ANOTHER client (cookie of this page): logout endpoint
  await T.page.evaluate(() => fetch("/api/teacher/logout", { method: "POST", credentials: "same-origin" }));
  await until(async () => {
    const t = await ptracks(A2.page);
    return t.mic === null && t.lastMic === "ended" && !(await indicators(A2.page)).mic;
  }, 10000).then(
    () => check("teacher logout (auth lost) → student mic track ended, indicator off", true),
    () => check("teacher logout (auth lost) → student mic track ended, indicator off", false),
  );
  await T.page.getByText("Вход завершён. Аудиосвязь закрыта.").waitFor({ timeout: 10000 });
  check("teacher page: auth lost shown, teacher peer closed", (await T.page.evaluate(() => window.__qa.controller.pc)) === null);
  await T.page.screenshot({ path: join(OUT, "07-teacher-auth-lost.png") });
  await A2.page.screenshot({ path: join(OUT, "08-peer-after-auth-lost.png") });

  // ---------------------------------------------------------------- S5/S6/S7 real browser permission + device errors
  // full Chromium (not the headless shell): "--deny-permission-prompts" answers every permission prompt with
  // "Block" → the page gets the real NotAllowedError, exactly as when a user denies the microphone
  const deny = await chromium.launch({ channel: "chromium", args: [...DIRECT, "--use-fake-device-for-media-stream", "--deny-permission-prompts"] });
  extra.push(deny);
  const nodev = await chromium.launch({ args: [...DIRECT, "--use-fake-ui-for-media-stream"] }); // permission yes, no device
  extra.push(nodev);
  const T2 = await teacherPage(browser, base, srv.pin);
  const C = await peerPage(deny, base, srv.join, "C-denied");
  const D = await peerPage(nodev, base, srv.join, "D-nodevice");
  for (const [p, label, code, text] of [
    [C, "student mic permission denied (real NotAllowedError)", "mic_denied", "у студента запрещён доступ к микрофону"],
    [D, "no microphone device (real NotFoundError)", "mic_not_found", "у студента не найден микрофон"],
  ]) {
    await T2.page.locator(`[data-student-id="${p.sid}"]`).waitFor();
    await selectStudent(T2.page, p.sid);
    await T2.page.getByRole("button", { name: "Слушать" }).click();
    await until(async () => (await tstate(T2.page)).phase === "rejected", 15000).catch(() => {});
    const s = await tstate(T2.page);
    const ui = await T2.page.locator(".qa-status").textContent();
    check(label, s.reason === code && ui.includes(text) && !(await indicators(p.page)).mic, `${s.phase}/${s.reason}`);
  }
  await T2.page.screenshot({ path: join(OUT, "09-teacher-student-mic-denied.png") });
  // teacher's own microphone denied (teacher page in the no-permission browser)
  const T3 = await teacherPage(deny, base, srv.pin);
  const E = await peerPage(browser, base, srv.join, "E");
  await T3.page.locator(`[data-student-id="${E.sid}"]`).waitFor();
  await selectStudent(T3.page, E.sid);
  await T3.page.getByRole("button", { name: "Говорить" }).click();
  await T3.page.getByText(/Доступ к микрофону запрещён/).waitFor({ timeout: 10000 });
  check("teacher mic denied → clear message + fix hint, no request sent to the student", !(await indicators(E.page)).session && (await tstate(T3.page)).sessionId === null);
  await T3.page.screenshot({ path: join(OUT, "10-teacher-mic-denied.png") });

  // ---------------------------------------------------------------- S12 insecure context (plain http from another address)
  const ip = lanIp();
  if (ip) {
    const r = await fetch(`http://${ip}:${srv.port}/`);
    check("teacher page refused from a non-loopback address (403)", r.status === 403, `http://${ip}`);
    const ins = await browser.newContext();
    const ip1 = await ins.newPage();
    await ip1.goto(`http://${ip}:${srv.port}/test-peer/`);
    await ip1.locator("#code").fill(srv.join);
    await ip1.getByRole("button", { name: "Подключиться к серверу" }).click();
    await ip1.getByText(/Подключён к серверу как/).waitFor();
    const isid = await ip1.evaluate(() => window.__peer.studentId);
    await T2.page.locator(`[data-student-id="${isid}"]`).waitFor();
    await selectStudent(T2.page, isid);
    await T2.page.getByRole("button", { name: "Слушать" }).click();
    await until(async () => (await tstate(T2.page)).phase === "rejected", 15000).catch(() => {});
    const s = await tstate(T2.page);
    check("student page over plain http from another address: not a secure context → rejected insecure_context", s.reason === "insecure_context", s.reason ?? "");
    await ip1.screenshot({ path: join(OUT, "11-peer-insecure-context.png") });
  } else check("non-loopback interface available", false, "skipped insecure-context checks");

  // ---------------------------------------------------------------- S11 server shutdown → fail closed
  const F = await peerPage(browser, base, srv.join, "F");
  await T2.page.locator(`[data-student-id="${F.sid}"]`).waitFor();
  await selectStudent(T2.page, F.sid);
  await T2.page.getByRole("button", { name: "Слушать" }).click();
  await until(async () => (await tstate(T2.page)).listening, 20000);
  srv.proc.kill("SIGTERM");
  await until(async () => (await ptracks(F.page)).mic === null && (await ptracks(F.page)).lastMic === "ended", 15000).then(
    () => check("server stopped → student mic ended (audio_stop or link loss)", true),
    () => check("server stopped → student mic ended (audio_stop or link loss)", false),
  );
  await until(async () => (await tstate(T2.page)).phase === "ended", 10000).then(
    () => check("server stopped → teacher session ended, nothing left open", true),
    () => check("server stopped → teacher session ended, nothing left open", false),
  );
  srv = null;

  // ---------------------------------------------------------------- S13 HTTPS/WSS on the LAN address (trusted test CA)
  if (ip) {
    const dir = mkdtempSync(join(tmpdir(), "qa-tls-"));
    const ext = join(dir, "ext.cnf");
    writeFileSync(ext, `subjectAltName=IP:${ip},IP:127.0.0.1,DNS:localhost\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n`);
    const ossl = (args) => execFileSync("openssl", args, { cwd: dir, stdio: "pipe" });
    ossl(["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=Qorgau T05 test CA", "-keyout", "ca.key", "-out", "ca.pem", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign"]);
    ossl(["req", "-newkey", "rsa:2048", "-nodes", "-subj", `/CN=${ip}`, "-keyout", "server.key", "-out", "server.csr"]);
    ossl(["x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-days", "2", "-extfile", ext, "-out", "server.pem"]);
    // trust the test CA ONLY for browsers started with this HOME (own NSS db) — no certificate checks disabled
    const home = join(dir, "home");
    mkdirSync(join(home, ".pki", "nssdb"), { recursive: true });
    execFileSync("certutil", ["-d", `sql:${join(home, ".pki", "nssdb")}`, "-N", "--empty-password"]);
    execFileSync("certutil", ["-d", `sql:${join(home, ".pki", "nssdb")}`, "-A", "-t", "C,,", "-n", "qorgau-t05-test-ca", "-i", join(dir, "ca.pem")]);
    const tls = await startServer({ tls: { cert: join(dir, "server.pem"), key: join(dir, "server.key") } }).catch((e) => ({ error: e }));
    if (tls.error) check("TLS dev server started", false, String(tls.error));
    else {
      srv = tls;
      const tb = await chromium.launch({ args: FAKE, env: { ...process.env, HOME: home } });
      extra.push(tb);
      const TT = await teacherPage(tb, `https://127.0.0.1:${tls.port}`, tls.pin);
      const P = await peerPage(tb, `https://${ip}:${tls.port}`, tls.join, "LAN-TLS");
      const secure = await P.page.evaluate(() => window.isSecureContext && location.protocol === "https:");
      await TT.page.locator(`[data-student-id="${P.sid}"]`).waitFor();
      await selectStudent(TT.page, P.sid);
      await TT.page.getByRole("button", { name: "Слушать" }).click();
      await until(async () => (await tstate(TT.page)).listening, 20000).then(
        () => check(`HTTPS/WSS: peer on https://${ip} (secure context, trusted test CA) → listening`, secure, "same machine, NOT a second computer"),
        () => check(`HTTPS/WSS: peer on https://${ip} (secure context, trusted test CA) → listening`, false),
      );
      await P.page.screenshot({ path: join(OUT, "12-peer-https-lan-address.png") });
    }
  }
} catch (e) {
  check("e2e completed", false, String(e?.stack ?? e).slice(0, 600));
  if (srv) console.log(srv.log().slice(-2000));
} finally {
  for (const b of extra) await b.close().catch(() => {});
  await browser?.close().catch(() => {});
  srv?.proc.kill("SIGTERM");
}
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} checks passed · screenshots in ${OUT}`);
process.exit(failed ? 1 : 0);
