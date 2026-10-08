// @ts-check
// Teacher side of qorgau.class.audio.v1 (T05): one controller per teacher panel = at most one audio session.
// The UI never decides "listening" by itself: `listening` is true only when the peer connection is connected,
// inbound RTP packets keep arriving and the student reported a live microphone.
import { mapMediaError, micPrecheck, reasonRu } from "../shared/media-errors.js";

/**
 * @typedef {"idle"|"requesting"|"connecting"|"connected"|"reconnecting"|"ended"|"rejected"|"error"} Phase
 * @typedef {{code: string, message_ru: string, hint_ru: string}} Problem
 * @typedef {object} TeacherAudioState
 * @property {Phase} phase
 * @property {string|null} studentId
 * @property {string|null} sessionId
 * @property {boolean} listen        requested
 * @property {boolean} talk          requested
 * @property {boolean} listening     ACTUAL: connected + inbound packets + student mic live
 * @property {boolean} speaking      ACTUAL: connected + outbound packets
 * @property {number} inLevel        0..1 incoming audio level
 * @property {string} connection     RTCPeerConnection.connectionState
 * @property {{mic_live: boolean, indicator_shown: boolean, teacher_audio_playing: boolean, connection: string}|null} studentMedia
 * @property {string|null} reason    reason code of the end
 * @property {string} reasonText
 * @property {Problem|null} problem  local problem (teacher mic, signaling)
 * @property {boolean} needsPlayClick browser blocked autoplay of the student audio
 * @property {boolean} signalingUp
 */
/**
 * @typedef {object} Signaling
 * @property {(msg: Record<string, unknown>) => boolean} send
 * @property {(fn: (msg: any) => void) => () => void} subscribe
 * @property {(fn: (up: boolean, closeCode?: number) => void) => () => void} onStatus
 * @property {boolean} up
 */

const STATS_MS = 500;
const INBOUND_FRESH_MS = 1500;
const RESTART_AFTER_DISCONNECT_MS = 3000;
const GIVE_UP_MS = 10_000;

export class TeacherAudio {
  /**
   * @param {{signaling: Signaling, audioElement: HTMLAudioElement,
   *   getUserMedia?: (c: MediaStreamConstraints) => Promise<MediaStream>}} o
   */
  constructor(o) {
    this.sig = o.signaling;
    this.audioEl = o.audioElement;
    this.getUserMedia = o.getUserMedia ?? ((c) => navigator.mediaDevices.getUserMedia(c));
    /** @type {Set<(s: TeacherAudioState) => void>} */
    this.listeners = new Set();
    /** @type {TeacherAudioState} */
    this.s = this._fresh();
    /** @type {RTCPeerConnection|null} */
    this.pc = null;
    /** @type {MediaStreamTrack|null} */
    this.micTrack = null;
    /** @type {RTCIceCandidateInit[]} */
    this.pendingIce = [];
    this.inBytes = 0;
    this.inAt = 0;
    this.outPackets = 0;
    this.outAt = 0;
    /** @type {ReturnType<typeof setInterval>|null} */
    this.statsTimer = null;
    /** @type {ReturnType<typeof setTimeout>|null} */
    this.restartTimer = null;
    /** @type {ReturnType<typeof setTimeout>|null} */
    this.giveUpTimer = null;
    this.restarted = false;
    this.generation = 0;
    this.unsub = this.sig.subscribe((m) => this._onMessage(m));
    this.unsubStatus = this.sig.onStatus((up, code) => this._onSignaling(up, code));
    this.s.signalingUp = this.sig.up;
  }

  /** @returns {TeacherAudioState} */
  _fresh() {
    return {
      phase: "idle",
      studentId: null,
      sessionId: null,
      listen: false,
      talk: false,
      listening: false,
      speaking: false,
      inLevel: 0,
      connection: "new",
      studentMedia: null,
      reason: null,
      reasonText: "",
      problem: null,
      needsPlayClick: false,
      signalingUp: this.sig?.up ?? false,
    };
  }

  get state() {
    return this.s;
  }

  /** Re-notify listeners (e.g. the student's online flag changed outside the controller). */
  nudge() {
    this._set({});
  }

  /** True while a session holds the line (one active session per teacher). */
  get busy() {
    return ["requesting", "connecting", "connected", "reconnecting"].includes(this.s.phase);
  }

  /** @param {(s: TeacherAudioState) => void} fn */
  subscribe(fn) {
    this.listeners.add(fn);
    fn(this.s);
    return () => this.listeners.delete(fn);
  }

  /**
   * Invariant enforced on EVERY state change (not only on the next stats tick): "listening" requires a connected
   * peer, listen requested and the student's live microphone; "speaking" requires a connected peer and talk.
   * @param {Partial<TeacherAudioState>} patch
   */
  _set(patch) {
    const n = { ...this.s, ...patch };
    if (n.listening && !(n.connection === "connected" && n.listen && n.studentMedia?.mic_live === true && !this.audioEl.paused && !n.needsPlayClick)) {
      n.listening = false;
      n.inLevel = 0;
    }
    if (n.speaking && !(n.connection === "connected" && n.talk)) n.speaking = false;
    this.s = n;
    this.listeners.forEach((l) => l(this.s));
  }

  // ------------------------------------------------------------------ actions
  /** @param {string} studentId @param {{listen: boolean, talk: boolean}} want */
  async start(studentId, want) {
    if (this.busy) {
      this._set({ problem: { code: "teacher_busy", message_ru: "Уже идёт аудиосвязь с другим студентом.", hint_ru: "Сначала завершите текущую связь." } });
      return;
    }
    if (!want.listen && !want.talk) return;
    if (!this.sig.up) {
      this._set({ problem: { code: "signaling_down", message_ru: "Нет связи с сервером класса.", hint_ru: "Дождитесь восстановления соединения." } });
      return;
    }
    this._reset();
    const generation = this.generation;
    this._set({ ...this._fresh(), phase: "requesting", studentId, listen: want.listen, talk: want.talk, signalingUp: true });
    if (want.talk && !(await this._ensureMic())) {
      if (generation !== this.generation) return;
      this._set({ phase: "error" });
      return;
    }
    if (generation !== this.generation || !this.sig.up) return;
    this.sig.send({ type: "audio_request", student_id: studentId, listen: want.listen, talk: want.talk });
  }

  /** @param {boolean} listen */
  async setListen(listen) {
    await this._change(listen, this.s.talk);
  }

  /** @param {boolean} talk */
  async setTalk(talk) {
    await this._change(this.s.listen, talk);
  }

  /** @param {boolean} listen @param {boolean} talk */
  async _change(listen, talk) {
    const generation = this.generation;
    if (!this.busy || !this.s.sessionId) return;
    if (!listen && !talk) return this.stop();
    if (talk && !(await this._ensureMic())) return; // keep the session, report the mic problem
    if (generation !== this.generation) return;
    if (!talk) this._releaseMic();
    this._set({ listen, talk });
    this.sig.send({ type: "audio_update", audio_session_id: this.s.sessionId, listen, talk });
    if (this.pc) {
      this._applyDirection();
      await this._negotiate(false);
    }
  }

  stop(reason = "teacher_stop") {
    if (this.s.sessionId && this.busy) this.sig.send({ type: "audio_stop", audio_session_id: this.s.sessionId, reason });
    this._teardown();
    if (this.s.phase !== "idle") this._set({ phase: "ended", reason, reasonText: reasonRu(reason), listening: false, speaking: false });
  }

  /** The browser blocked autoplay: call from a click handler. */
  async resumePlayback() {
    try {
      await this.audioEl.play();
      this._set({ needsPlayClick: false });
    } catch {
      this._set({ needsPlayClick: true });
    }
  }

  dispose() {
    this.stop();
    this.unsub();
    this.unsubStatus();
  }

  // ------------------------------------------------------------------ server messages
  /** @param {any} m */
  async _onMessage(m) {
    if (!m || typeof m !== "object") return;
    if (m.type === "audio_state") return this._onState(m);
    if (m.type === "audio_error") {
      if (m.audio_session_id && this.s.sessionId && m.audio_session_id !== this.s.sessionId) return;
      if (this.s.phase === "requesting" && !this.s.sessionId) {
        this._teardown();
        this._set({ phase: "error", problem: { code: m.code, message_ru: m.message_ru ?? m.code, hint_ru: "" } });
      } else {
        this._set({ problem: { code: m.code, message_ru: m.message_ru ?? m.code, hint_ru: "" } });
      }
      return;
    }
    if (m.type === "audio_signal" && m.audio_session_id === this.s.sessionId && this.pc) {
      if (m.kind === "answer") {
        await this.pc.setRemoteDescription({ type: "answer", sdp: m.sdp });
        for (const c of this.pendingIce.splice(0)) await this.pc.addIceCandidate(c).catch(() => {});
      } else if (m.kind === "ice") {
        if (!this.pc.remoteDescription) {
          if (m.ice) this.pendingIce.push(m.ice);
        } else await this.pc.addIceCandidate(m.ice ?? undefined).catch(() => {});
      }
    }
  }

  /** @param {any} m */
  async _onState(m) {
    // adopt the server-issued id only for the student we asked for
    if (!this.s.sessionId) {
      if (this.s.phase !== "requesting" || m.student_id !== this.s.studentId) return;
      this._set({ sessionId: m.audio_session_id });
    } else if (m.audio_session_id !== this.s.sessionId) return;
    this._set({ studentMedia: m.student_media ?? this.s.studentMedia });
    if (m.state === "accepted" && !this.pc) {
      this._set({ phase: "connecting" });
      this._createPeer();
      await this._negotiate(false);
    } else if (m.state === "ended" || m.state === "rejected") {
      this._teardown();
      this._set({ phase: m.state, reason: m.reason, reasonText: reasonRu(m.reason), listening: false, speaking: false });
    }
  }

  /** @param {boolean} up @param {number} [code] */
  _onSignaling(up, code) {
    this._set({ signalingUp: up });
    if (!up && (this.busy || this.pc)) {
      // the server ends the session too (teacher_disconnected / auth lost) — never keep media without signaling
      const reason = code === 4401 ? "teacher_auth_lost" : "teacher_disconnected";
      this._teardown();
      this._set({ phase: "ended", reason, reasonText: reasonRu(reason), listening: false, speaking: false });
    }
  }

  // ------------------------------------------------------------------ WebRTC
  _createPeer() {
    const pc = new RTCPeerConnection({ iceServers: [] }); // LAN host candidates only
    this.pc = pc;
    pc.addTransceiver("audio", { direction: this._direction() });
    if (this.s.talk && this.micTrack) void pc.getTransceivers()[0]?.sender.replaceTrack(this.micTrack);
    pc.onicecandidate = (e) => {
      if (this.s.sessionId) this.sig.send({ type: "audio_signal", audio_session_id: this.s.sessionId, kind: "ice", ice: e.candidate ? e.candidate.toJSON() : null });
    };
    pc.ontrack = (e) => {
      this.audioEl.srcObject = new MediaStream([e.track]);
      void this.audioEl.play().then(
        () => this._set({ needsPlayClick: false }),
        () => this._set({ needsPlayClick: true }),
      );
    };
    pc.onconnectionstatechange = () => this._onConnection();
    this.statsTimer = setInterval(() => void this._stats(), STATS_MS);
  }

  _direction() {
    const { listen, talk } = this.s;
    return listen && talk ? "sendrecv" : listen ? "recvonly" : "sendonly";
  }

  _applyDirection() {
    const tr = this.pc?.getTransceivers()[0];
    if (!tr) return;
    tr.direction = this._direction();
    void tr.sender.replaceTrack(this.s.talk ? this.micTrack : null);
  }

  /** @param {boolean} iceRestart */
  async _negotiate(iceRestart) {
    const pc = this.pc;
    if (!pc || !this.s.sessionId) return;
    const offer = await pc.createOffer({ iceRestart });
    await pc.setLocalDescription(offer);
    this.sig.send({ type: "audio_signal", audio_session_id: this.s.sessionId, kind: "offer", sdp: pc.localDescription?.sdp ?? offer.sdp, ice_restart: iceRestart });
  }

  _onConnection() {
    const pc = this.pc;
    if (!pc || !this.s.sessionId) return;
    const c = pc.connectionState;
    this._set({ connection: c });
    this._report();
    if (c === "connected") {
      this._clearRecovery();
      this.restarted = false;
      this._set({ phase: "connected" });
    } else if (c === "disconnected" || c === "failed") {
      this._set({ phase: "reconnecting", listening: false, speaking: false });
      if (!this.restartTimer && !this.restarted) {
        this.restartTimer = setTimeout(
          () => {
            this.restartTimer = null;
            this.restarted = true;
            void this._negotiate(true); // ICE restart within the same session
          },
          c === "failed" ? 0 : RESTART_AFTER_DISCONNECT_MS,
        );
      }
      if (!this.giveUpTimer) this.giveUpTimer = setTimeout(() => this.stop("network_lost"), GIVE_UP_MS);
    }
  }

  _report() {
    if (!this.s.sessionId || !this.pc) return;
    this.sig.send({
      type: "audio_media",
      audio_session_id: this.s.sessionId,
      connection: this.pc.connectionState,
      receiving: this.s.listening,
      sending: this.s.speaking,
    });
  }

  async _stats() {
    const pc = this.pc;
    if (!pc) return;
    let inBytes = 0;
    let level = 0;
    let outPackets = 0;
    try {
      (await pc.getStats()).forEach((r) => {
        if (r.type === "inbound-rtp" && r.kind === "audio") {
          inBytes = r.bytesReceived ?? 0;
          if (typeof r.audioLevel === "number") level = r.audioLevel;
        }
        if (r.type === "outbound-rtp" && r.kind === "audio") outPackets = r.packetsSent ?? 0;
      });
    } catch {
      return;
    }
    const now = Date.now();
    if (inBytes > this.inBytes) this.inAt = now;
    if (outPackets > this.outPackets) this.outAt = now;
    this.inBytes = inBytes;
    this.outPackets = outPackets;
    const connected = pc.connectionState === "connected";
    const listening = connected && this.s.listen && now - this.inAt < INBOUND_FRESH_MS && this.s.studentMedia?.mic_live === true;
    const speaking = connected && this.s.talk && !!this.micTrack && now - this.outAt < INBOUND_FRESH_MS;
    const changed = listening !== this.s.listening || speaking !== this.s.speaking;
    if (changed || Math.abs(level - this.s.inLevel) > 0.01) this._set({ listening, speaking, inLevel: listening ? level : 0 });
    if (changed) this._report();
  }

  // ------------------------------------------------------------------ mic + cleanup
  async _ensureMic() {
    const generation = this.generation;
    if (this.micTrack && this.micTrack.readyState === "live") return true;
    const pre = micPrecheck();
    if (pre) {
      this._set({ problem: pre });
      return false;
    }
    try {
      const stream = await this.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false });
      if (generation !== this.generation || !this.sig.up) {
        stream.getTracks().forEach((track) => track.stop());
        return false;
      }
      this.micTrack = stream.getAudioTracks()[0] ?? null;
      stream.getTracks().forEach((t) => t !== this.micTrack && t.stop());
      if (!this.micTrack) throw Object.assign(new Error("no track"), { name: "NotFoundError" });
      this.micTrack.addEventListener("ended", () => {
        if (this.s.talk) void this.setTalk(false);
      });
      this._set({ problem: null });
      return true;
    } catch (e) {
      this._set({ problem: mapMediaError(e) });
      return false;
    }
  }

  _releaseMic() {
    if (this.micTrack) this.micTrack.stop();
    this.micTrack = null;
  }

  _clearRecovery() {
    if (this.restartTimer) clearTimeout(this.restartTimer);
    if (this.giveUpTimer) clearTimeout(this.giveUpTimer);
    this.restartTimer = null;
    this.giveUpTimer = null;
  }

  _teardown() {
    this.generation += 1;
    this._clearRecovery();
    if (this.statsTimer) clearInterval(this.statsTimer);
    this.statsTimer = null;
    if (this.pc) {
      this.pc.getReceivers().forEach((r) => r.track && r.track.stop());
      this.pc.getSenders().forEach((s) => s.track && s.track.stop());
      this.pc.close();
    }
    this.pc = null;
    this._releaseMic();
    this.pendingIce = [];
    this.audioEl.pause();
    this.audioEl.srcObject = null;
    this.inBytes = 0;
    this.inAt = 0;
    this.outPackets = 0;
    this.outAt = 0;
    this.restarted = false;
  }

  _reset() {
    this._teardown();
  }
}
