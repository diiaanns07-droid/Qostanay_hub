// @ts-check
// Student side of qorgau.class.audio.v1 — REFERENCE implementation (T05) for the student app (Electron renderer).
// Transport-agnostic: feed it every message the class server sends (command audio_*, audio_signal) and give it
// `send(msg)` towards the server. Used as-is by the labelled TEST PEER.
//
// Normative behaviour (PROTOCOL_AUDIO.md §5):
//  * the microphone is acquired only when the teacher asked to LISTEN and released (track.stop) when not;
//  * `ack ok:true` for audio_start is sent only after the indicator is on screen (onIndicators resolved);
//  * FAIL CLOSED: audio_stop, transport loss, peer failure, any error → every track stopped, peer closed,
//    indicators off.
import { mapMediaError, micPrecheck } from "./media-errors.js";

/**
 * @typedef {object} Indicators
 * @property {boolean} micLive        microphone track is live (must show "Микрофон включён преподавателем")
 * @property {boolean} teacherSpeaking teacher audio is being received now (must show "Говорит преподаватель")
 * @property {boolean} sessionActive  any audio session (talk-only: "Связь с преподавателем")
 * @property {number} teacherLevel    0..1 for an optional meter
 */
/**
 * @typedef {object} EndpointOptions
 * @property {(msg: Record<string, unknown>) => void} send                   towards the class server
 * @property {(ind: Indicators) => (void|Promise<void>)} onIndicators         must render; resolve when visible
 * @property {HTMLAudioElement} audioElement                                   plays teacher audio
 * @property {(c: MediaStreamConstraints) => Promise<MediaStream>} [getUserMedia]
 * @property {(e: {type: string, detail?: unknown}) => void} [onLog]
 */

const DISCONNECT_GRACE_MS = 10_000;
const STATS_MS = 500;

export class StudentAudioEndpoint {
  /** @param {EndpointOptions} o */
  constructor(o) {
    this.o = o;
    this.getUserMedia = o.getUserMedia ?? ((c) => navigator.mediaDevices.getUserMedia(c));
    /** @type {{id: string, commandId: string, listen: boolean, talk: boolean} | null} */
    this.session = null;
    /** @type {RTCPeerConnection|null} */
    this.pc = null;
    /** @type {MediaStreamTrack|null} */
    this.mic = null;
    /** @type {MediaStreamTrack|null} */
    this.remote = null;
    this.queue = Promise.resolve();
    this.ind = { micLive: false, teacherSpeaking: false, sessionActive: false, teacherLevel: 0 };
    this.connection = "new";
    /** @type {ReturnType<typeof setInterval>|null} */
    this.statsTimer = null;
    /** @type {ReturnType<typeof setTimeout>|null} */
    this.failTimer = null;
    this.lastBytes = 0;
    this.lastBytesAt = 0;
    /** @type {RTCIceCandidateInit[]} */
    this.pendingIce = [];
  }

  /** Everything is serialized: an offer can never overtake the microphone request of an update. */
  handleMessage(/** @type {any} */ msg) {
    this.queue = this.queue.then(() => this._handle(msg)).catch((e) => this._log("error", String(e)));
    return this.queue;
  }

  /** Link to the class server is gone (closed, ping timeout, exam finished): stop everything now. */
  transportLost() {
    this._teardown("transport_lost", false);
  }

  /** Student-side stop (e.g. app closing / exam finish). */
  stop(reason = "student_stop") {
    const s = this.session;
    this._teardown(reason, true);
    if (s) this.o.send({ type: "audio_media", audio_session_id: s.id, mic_live: false, indicator_shown: false, teacher_audio_playing: false, connection: "closed", stopped: true });
  }

  get micActive() {
    return !!this.mic && this.mic.readyState === "live";
  }

  // ------------------------------------------------------------------ dispatch
  /** @param {any} msg */
  async _handle(msg) {
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "command") {
      const p = msg.payload ?? {};
      if (msg.kind === "audio_start") return this._start(msg.command_id, p);
      if (msg.kind === "audio_update") return this._update(msg.command_id, p);
      if (msg.kind === "audio_stop") {
        if (!this.session || p.audio_session_id === this.session.id) this._teardown(p.reason ?? "audio_stop", false);
        this._ack(msg.command_id, true);
        return;
      }
      return;
    }
    if (msg.type === "audio_signal") {
      if (!this.session || msg.audio_session_id !== this.session.id) return; // never another session's media
      if (msg.kind === "offer") return this._offer(msg.sdp);
      if (msg.kind === "ice") return this._ice(msg.ice);
    }
  }

  /** @param {string} cid @param {any} p */
  async _start(cid, p) {
    if (this.session && this.session.id !== p.audio_session_id) return this._ack(cid, false, "busy", "Уже идёт другая аудиосвязь.");
    const listen = p.listen === true || p.direction === "listen" || p.direction === "both";
    const talk = p.talk === true || p.direction === "talk" || p.direction === "both";
    const pre = micPrecheck();
    if (pre && (listen || pre.code === "not_supported")) return this._ack(cid, false, pre.code, pre.message_ru);
    this.session = { id: String(p.audio_session_id), commandId: cid, listen, talk };
    try {
      if (listen) await this._acquireMic();
      await this._indicate({ sessionActive: true });
      this._ack(cid, true);
      this._media();
    } catch (e) {
      const prob = mapMediaError(e);
      this._log("mic_error", prob.code);
      this._teardown(prob.code, false);
      this._ack(cid, false, prob.code, prob.message_ru);
    }
  }

  /** @param {string} cid @param {any} p */
  async _update(cid, p) {
    const s = this.session;
    if (!s || p.audio_session_id !== s.id) return this._ack(cid, false, "busy", "Нет такой аудиосвязи.");
    s.listen = p.listen === true;
    s.talk = p.talk === true;
    try {
      if (s.listen && !this.micActive) await this._acquireMic();
      if (!s.listen) this._releaseMic();
      await this._indicate({});
      this._ack(cid, true);
      this._media();
    } catch (e) {
      const prob = mapMediaError(e);
      this._teardown(prob.code, false);
      this._ack(cid, false, prob.code, prob.message_ru);
    }
  }

  // ------------------------------------------------------------------ media
  async _acquireMic() {
    const stream = await this.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
    const track = stream.getAudioTracks()[0];
    if (!track) throw Object.assign(new Error("no audio track"), { name: "NotFoundError" });
    stream.getTracks().forEach((t) => t !== track && t.stop());
    this.mic = track;
    track.addEventListener("ended", () => {
      // device unplugged / revoked by the OS: show it and stop the session (fail closed)
      if (this.session && this.mic === track) this.stop("mic_ended");
    });
    if (this.pc) {
      const tr = this.pc.getTransceivers()[0];
      if (tr) await tr.sender.replaceTrack(track);
    }
    await this._indicate({ micLive: true });
  }

  _releaseMic() {
    if (this.mic) {
      this.mic.stop();
      this.mic = null;
    }
    if (this.pc) {
      const tr = this.pc.getTransceivers()[0];
      if (tr) void tr.sender.replaceTrack(null).catch(() => {});
    }
    void this._indicate({ micLive: false });
  }

  _ensurePc() {
    if (this.pc) return this.pc;
    const pc = new RTCPeerConnection({ iceServers: [] }); // LAN host candidates only, no STUN/TURN
    this.pc = pc;
    pc.onicecandidate = (e) => {
      if (!this.session) return;
      this.o.send({ type: "audio_signal", audio_session_id: this.session.id, command_id: this.session.commandId, kind: "ice", ice: e.candidate ? e.candidate.toJSON() : null });
    };
    pc.ontrack = (e) => {
      this.remote = e.track;
      const el = this.o.audioElement;
      el.srcObject = new MediaStream([e.track]);
      void el.play().catch(() => this._log("autoplay_blocked"));
    };
    pc.onconnectionstatechange = () => {
      this.connection = pc.connectionState;
      if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
        // the teacher may ICE-restart; if nothing recovers in time, close (fail closed)
        if (!this.failTimer) this.failTimer = setTimeout(() => this.stop("network_lost"), DISCONNECT_GRACE_MS);
      } else if (pc.connectionState === "connected" && this.failTimer) {
        clearTimeout(this.failTimer);
        this.failTimer = null;
      }
      if (pc.connectionState === "closed") return;
      this._media();
    };
    this.statsTimer = setInterval(() => void this._stats(), STATS_MS);
    return pc;
  }

  /** @param {string} sdp */
  async _offer(sdp) {
    const s = this.session;
    if (!s) return;
    const pc = this._ensurePc();
    await pc.setRemoteDescription({ type: "offer", sdp });
    const tr = pc.getTransceivers()[0];
    if (tr) {
      // student sends only when the teacher listens; receives only when the teacher talks
      tr.direction = s.listen && s.talk ? "sendrecv" : s.listen ? "sendonly" : "recvonly";
      await tr.sender.replaceTrack(s.listen ? this.mic : null);
    }
    for (const c of this.pendingIce.splice(0)) await pc.addIceCandidate(c).catch(() => {});
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    this.o.send({ type: "audio_signal", audio_session_id: s.id, command_id: s.commandId, kind: "answer", sdp: pc.localDescription?.sdp ?? answer.sdp });
  }

  /** @param {RTCIceCandidateInit|null} ice */
  async _ice(ice) {
    if (!this.pc || !this.pc.remoteDescription) {
      if (ice) this.pendingIce.push(ice);
      return;
    }
    await this.pc.addIceCandidate(ice ?? undefined).catch(() => {});
  }

  async _stats() {
    const pc = this.pc;
    if (!pc || !this.session) return;
    let bytes = 0;
    let level = 0;
    try {
      const report = await pc.getStats();
      report.forEach((r) => {
        if (r.type === "inbound-rtp" && r.kind === "audio") {
          bytes = r.bytesReceived ?? 0;
          level = typeof r.audioLevel === "number" ? r.audioLevel : level;
        }
      });
    } catch {
      return;
    }
    const now = Date.now();
    if (bytes > this.lastBytes) this.lastBytesAt = now;
    this.lastBytes = bytes;
    const speaking = !!this.session?.talk && pc.connectionState === "connected" && now - this.lastBytesAt < 1500;
    if (speaking !== this.ind.teacherSpeaking || Math.abs(level - this.ind.teacherLevel) > 0.02) {
      const changed = speaking !== this.ind.teacherSpeaking;
      await this._indicate({ teacherSpeaking: speaking, teacherLevel: level });
      if (changed) this._media();
    }
  }

  // ------------------------------------------------------------------ helpers
  /** @param {Partial<Indicators>} patch */
  async _indicate(patch) {
    this.ind = { ...this.ind, ...patch, micLive: patch.micLive ?? this.micActive, sessionActive: patch.sessionActive ?? !!this.session };
    await this.o.onIndicators({ ...this.ind });
  }

  _media() {
    const s = this.session;
    if (!s) return;
    this.o.send({
      type: "audio_media",
      audio_session_id: s.id,
      mic_live: this.micActive,
      indicator_shown: this.ind.sessionActive && (this.ind.micLive || !this.micActive),
      teacher_audio_playing: this.ind.teacherSpeaking,
      connection: this.pc ? this.pc.connectionState : "new",
    });
  }

  /** @param {string} cid @param {boolean} ok @param {string} [code] @param {string} [text] */
  _ack(cid, ok, code, text) {
    /** @type {Record<string, unknown>} */
    const m = { type: "ack", command_id: cid, ok };
    if (!ok) {
      m.error_code = code ?? "internal";
      m.error_ru = text ?? "";
    }
    this.o.send(m);
  }

  /** @param {string} reason @param {boolean} _initiated */
  _teardown(reason, _initiated) {
    this._log("teardown", reason);
    if (this.statsTimer) clearInterval(this.statsTimer);
    if (this.failTimer) clearTimeout(this.failTimer);
    this.statsTimer = null;
    this.failTimer = null;
    if (this.mic) this.mic.stop();
    this.mic = null;
    if (this.remote) this.remote.stop();
    this.remote = null;
    if (this.pc) {
      this.pc.getSenders().forEach((s) => s.track && s.track.stop());
      this.pc.close();
    }
    this.pc = null;
    this.pendingIce = [];
    const el = this.o.audioElement;
    el.pause();
    el.srcObject = null;
    this.session = null;
    this.lastBytes = 0;
    this.lastBytesAt = 0;
    this.connection = "closed";
    this.ind = { micLive: false, teacherSpeaking: false, sessionActive: false, teacherLevel: 0 };
    void this.o.onIndicators({ ...this.ind });
  }

  /** @param {string} type @param {unknown} [detail] */
  _log(type, detail) {
    this.o.onLog?.({ type, detail });
  }
}
