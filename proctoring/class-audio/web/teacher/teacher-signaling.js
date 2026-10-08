// @ts-check
// /ws/teacher client for the audio module (T05). Same origin as the page (the class server serves the panel on
// loopback); the teacher cookie (HttpOnly, SameSite=Strict) authenticates the socket — no token in the URL.
// Reconnects with backoff 1→15 s, except after 4401/4403 (authorization lost / not allowed): then it waits for login.

function uuid() {
  return crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
}

export class TeacherSignaling {
  /** @param {{url?: string}} [o] */
  constructor(o = {}) {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    this.url = o.url ?? `${proto}//${location.host}/ws/teacher`;
    /** @type {WebSocket|null} */
    this.ws = null;
    this.up = false;
    this.stopped = false;
    this.attempt = 0;
    /** @type {number|null} */
    this.lastClose = null;
    /** @type {Set<(m: any) => void>} */
    this.msgListeners = new Set();
    /** @type {Set<(up: boolean, code?: number) => void>} */
    this.statusListeners = new Set();
    /** @type {ReturnType<typeof setTimeout>|null} */
    this.timer = null;
  }

  connect() {
    this.stopped = false;
    if (this.ws) return;
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.up = true;
      this.statusListeners.forEach((l) => l(true));
    };
    ws.onmessage = (e) => {
      let m;
      try {
        m = JSON.parse(String(e.data));
      } catch {
        return;
      }
      this.msgListeners.forEach((l) => l(m));
    };
    ws.onclose = (e) => {
      const wasUp = this.up;
      this.ws = null;
      this.up = false;
      this.lastClose = e.code;
      if (wasUp || e.code === 4401 || e.code === 4403) this.statusListeners.forEach((l) => l(false, e.code));
      if (this.stopped || e.code === 4401 || e.code === 4403) return; // needs a new login, do not hammer
      const delay = Math.min(15_000, 1000 * 2 ** this.attempt++);
      this.timer = setTimeout(() => this.connect(), delay);
    };
  }

  close() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close(1000);
  }

  /** @param {Record<string, unknown>} msg */
  send(msg) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify({ v: 1, msg_id: uuid(), sent_at: new Date().toISOString(), ...msg }));
    return true;
  }

  /** @param {(m: any) => void} fn */
  subscribe(fn) {
    this.msgListeners.add(fn);
    return () => this.msgListeners.delete(fn);
  }

  /** @param {(up: boolean, code?: number) => void} fn */
  onStatus(fn) {
    this.statusListeners.add(fn);
    return () => this.statusListeners.delete(fn);
  }
}
