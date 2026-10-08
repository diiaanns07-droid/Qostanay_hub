// @ts-check
// TEST PEER (T05) — a minimal qorgau.class.v1 student link + the reference StudentAudioEndpoint.
// Purpose: verify teacher↔student audio independently of the student application. Clearly NOT the student app.
import { StudentAudioEndpoint } from "/shared/student-endpoint.js";

const $ = (/** @type {string} */ id) => /** @type {HTMLElement} */ (document.getElementById(id));
const stateEl = $("state");
const logEl = $("log");
const audio = /** @type {HTMLAudioElement} */ ($("teacher-audio"));
const simDeny = /** @type {HTMLInputElement} */ ($("sim-deny"));
const simNoDev = /** @type {HTMLInputElement} */ ($("sim-nodev"));

/** @type {WebSocket|null} */
let ws = null;
let resumeToken = /** @type {string|null} */ (null);
let studentId = /** @type {string|null} */ (null);
/** @type {ReturnType<typeof setInterval>|null} */
let statusTimer = null;

$("secure").textContent = `Защищённый контекст: ${window.isSecureContext ? "да" : "НЕТ — микрофон браузером недоступен (нужен https:// или localhost)"} · origin ${location.origin}`;

/** crypto.randomUUID exists only in secure contexts; the peer must still work (and report) over plain http. */
function uuid() {
  if (window.isSecureContext && crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

/** @param {string} t */
function log(t) {
  const li = document.createElement("li");
  li.textContent = `${new Date().toISOString().slice(11, 23)} ${t}`;
  logEl.prepend(li);
  while (logEl.children.length > 200) logEl.lastChild?.remove();
}

/** @param {Record<string, unknown>} msg */
function send(msg) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ v: 1, msg_id: uuid(), sent_at: new Date().toISOString(), ...msg }));
  if (msg.type !== "pong" && msg.type !== "status") log(`→ ${msg.type}${msg.kind ? ":" + msg.kind : ""}${msg.ok === false ? " FAIL " + msg.error_code : ""}`);
}

const endpoint = new StudentAudioEndpoint({
  send,
  audioElement: audio,
  getUserMedia: async (c) => {
    if (simDeny.checked) throw Object.assign(new Error("simulated"), { name: "NotAllowedError" });
    if (simNoDev.checked) throw Object.assign(new Error("simulated"), { name: "NotFoundError" });
    return navigator.mediaDevices.getUserMedia(c);
  },
  onIndicators: (ind) =>
    new Promise((resolve) => {
      $("ind-mic").hidden = !ind.micLive;
      $("ind-teacher").hidden = !ind.teacherSpeaking;
      $("ind-session").hidden = !ind.sessionActive;
      sendStatus();
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())); // painted
    }),
  onLog: (e) => log(`· ${e.type} ${e.detail ?? ""}`),
});

function sendStatus() {
  send({
    type: "status",
    exam_state: "running",
    camera: "unknown",
    monitoring: "degraded",
    zone: null,
    zone_reasons_ru: [],
    incidents_total: 0,
    incidents_by_priority: { low: 0, medium: 0, high: 0 },
    locked: false,
    mic_active: endpoint.micActive,
  });
}

/** @param {{join_code?: string, resume_token?: string}} cred */
function connect(cred) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const sock = new WebSocket(`${proto}//${location.host}/ws/student`);
  ws = sock;
  stateEl.textContent = "Подключение…";
  sock.onopen = () => {
    sock.send(
      JSON.stringify({
        type: "hello",
        v: 1,
        msg_id: uuid(),
        sent_at: new Date().toISOString(),
        protocol: "qorgau.class.v1",
        computer_name: "TEST-PEER",
        student_label: /** @type {HTMLInputElement} */ ($("label")).value || "test-peer",
        app_version: "test-peer-t05",
        ...cred,
      }),
    );
  };
  sock.onmessage = (e) => {
    const m = JSON.parse(String(e.data));
    if (m.type === "ping") return send({ type: "pong" });
    if (m.type !== "audio_signal") log(`← ${m.type}${m.kind ? ":" + m.kind : ""}${m.code ? " " + m.code : ""}`);
    if (m.type === "welcome") {
      studentId = m.student_id;
      resumeToken = m.resume_token;
      stateEl.textContent = `Подключён к серверу как ${studentId}.`;
      sendStatus();
      if (statusTimer) clearInterval(statusTimer);
      statusTimer = setInterval(sendStatus, 2000);
      return;
    }
    if (m.type === "error") {
      stateEl.textContent = `Сервер отклонил: ${m.message_ru ?? m.code}`;
      return;
    }
    void endpoint.handleMessage(m);
  };
  sock.onclose = (e) => {
    if (ws === sock) ws = null;
    if (statusTimer) clearInterval(statusTimer);
    statusTimer = null;
    endpoint.transportLost(); // FAIL CLOSED: no server link → no microphone
    stateEl.textContent = `Нет связи с сервером (код ${e.code}). Микрофон выключен.`;
    log(`connection closed ${e.code}`);
  };
}

$("join").addEventListener("submit", (e) => {
  e.preventDefault();
  connect({ join_code: /** @type {HTMLInputElement} */ ($("code")).value.trim() });
});
$("drop").addEventListener("click", () => {
  // simulate a network cut: the socket dies without any audio_stop from our side
  ws?.close(4000, "simulated network drop");
});
$("reconnect").addEventListener("click", () => {
  if (resumeToken) connect({ resume_token: resumeToken });
});

// test hooks (this page is a test tool)
Object.assign(window, {
  __peer: {
    endpoint,
    get studentId() {
      return studentId;
    },
    tracks: () => ({
      mic: endpoint.mic ? endpoint.mic.readyState : null,
      remote: endpoint.remote ? endpoint.remote.readyState : null,
      pc: endpoint.pc ? endpoint.pc.connectionState : null,
      lastMic: lastMic ? lastMic.readyState : null,
    }),
  },
});

// remember the last microphone track to prove it was stopped (readyState "ended") after teardown
/** @type {MediaStreamTrack|null} */
let lastMic = null;
const origAcquire = endpoint._acquireMic.bind(endpoint);
endpoint._acquireMic = async () => {
  await origAcquire();
  lastMic = endpoint.mic;
};
