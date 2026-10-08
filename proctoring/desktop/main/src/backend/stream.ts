// WebSocket clients for /v1/stream and /v1/preview (owner: A06). Main keeps one socket each and
// fans out to the renderer. Token goes in the Authorization header (never the URL); no Origin.
import WebSocket from "ws";
import type { PreviewFrameMeta, StreamEnvelope } from "@contracts/qorgau-v1.generated";
import { logger } from "../log";
import type { ClientTarget } from "./client";

const log = logger("stream");
const MAX_PREVIEW_HEADER = 64 * 1024;
const MAX_MESSAGE = 16 * 1024 * 1024;

export function parsePreviewFrame(data: Buffer): { meta: PreviewFrameMeta; jpeg: Buffer } | null {
  if (data.length < 4) return null;
  const headerLen = data.readUInt32BE(0);
  if (headerLen === 0 || headerLen > MAX_PREVIEW_HEADER || 4 + headerLen > data.length) return null;
  let meta: unknown;
  try {
    meta = JSON.parse(data.subarray(4, 4 + headerLen).toString("utf8"));
  } catch {
    return null;
  }
  if (typeof meta !== "object" || meta === null || typeof (meta as PreviewFrameMeta).frame_id !== "number") return null;
  return { meta: meta as PreviewFrameMeta, jpeg: data.subarray(4 + headerLen) };
}

export function parseEnvelope(text: string): StreamEnvelope | null {
  let env: unknown;
  try {
    env = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof env !== "object" || env === null) return null;
  const e = env as StreamEnvelope;
  if (e.contract !== "qorgau.v1" || typeof e.seq !== "number" || typeof e.message !== "object" || e.message === null) return null;
  return e;
}

export interface StreamHandlers {
  onEnvelope?(env: StreamEnvelope): void;
  onPreview?(meta: PreviewFrameMeta, jpeg: Buffer): void;
  onOpen?(): void;
  onClose?(code: number): void;
}

/** Reconnecting socket bound to the current backend launch. */
export class BackendSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private attempt = 0;
  private timer: NodeJS.Timeout | null = null;

  constructor(
    private readonly kind: "stream" | "preview",
    private readonly target: () => ClientTarget | null,
    private readonly handlers: StreamHandlers,
  ) {}

  open(): void {
    this.closed = false;
    this.connect();
  }

  private connect(): void {
    if (this.closed) return;
    const t = this.target();
    if (!t) return;
    const ws = new WebSocket(`ws://127.0.0.1:${t.port}/v1/${this.kind}`, ["qorgau.v1"], {
      headers: { Authorization: `Bearer ${t.token}` },
      maxPayload: MAX_MESSAGE,
      handshakeTimeout: 5_000,
      perMessageDeflate: false,
    });
    this.ws = ws;
    ws.on("open", () => {
      this.attempt = 0;
      log.info(`${this.kind} connected`);
      this.handlers.onOpen?.();
    });
    ws.on("message", (data, isBinary) => {
      const buf = Array.isArray(data) ? Buffer.concat(data) : Buffer.from(data as ArrayBuffer);
      if (this.kind === "stream") {
        if (isBinary) return;
        const env = parseEnvelope(buf.toString("utf8"));
        if (env) this.handlers.onEnvelope?.(env);
        else log.warn("stream: dropped malformed envelope");
      } else if (isBinary) {
        const frame = parsePreviewFrame(buf);
        if (frame) this.handlers.onPreview?.(frame.meta, frame.jpeg);
      }
    });
    ws.on("error", (err) => log.debug(`${this.kind} error: ${err.message}`));
    ws.on("close", (code) => {
      if (this.ws === ws) this.ws = null;
      this.handlers.onClose?.(code);
      if (this.closed) return;
      if (code === 4401 || code === 4403) {
        log.error(`${this.kind} rejected by backend (${code}); not retrying with this token`);
        return;
      }
      const delay = Math.min(5_000, 250 * 2 ** this.attempt++);
      this.timer = setTimeout(() => this.connect(), delay);
    });
  }

  close(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      try {
        ws.terminate();
      } catch {
        /* ignore */
      }
    }
  }
}
