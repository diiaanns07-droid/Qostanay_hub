// HTTP client for the loopback API v1 (owner: A06). Lives in main only: adds the bearer token,
// never sends an Origin header, refuses non-loopback targets, caps response sizes.
// Pure Node (node:http), no Electron import.
import { request as httpRequest } from "node:http";
import type { BridgeResult } from "@contracts/bridge";
import type { ApiErrorBody } from "@contracts/qorgau-v1.generated";
import { backendUnavailable, shellError } from "../errors";
import { logger } from "../log";

const log = logger("client");

export interface ClientTarget {
  port: number;
  token: string;
}

export interface RawResponse {
  status: number;
  headers: Record<string, string | string[] | undefined>;
  body: Buffer;
}

export type Method = "GET" | "POST" | "PUT" | "DELETE";

const MAX_JSON_BYTES = 8 * 1024 * 1024;
const MAX_BINARY_BYTES = 64 * 1024 * 1024;

export class BackendClient {
  constructor(
    private readonly target: () => ClientTarget | null,
    private readonly defaultTimeoutMs = 15_000,
  ) {}

  /** Path segments are validated ids; still encoded so nothing can alter the route. */
  static path(...segments: string[]): string {
    return "/v1/" + segments.map((s) => encodeURIComponent(s)).join("/");
  }

  raw(method: Method, path: string, body?: unknown, opts: { timeoutMs?: number; maxBytes?: number } = {}): Promise<RawResponse> {
    const t = this.target();
    if (!t) return Promise.reject(new Error("backend not ready"));
    if (!path.startsWith("/v1/")) return Promise.reject(new Error("only /v1 routes"));
    const payload = body === undefined ? undefined : Buffer.from(JSON.stringify(body), "utf8");
    const maxBytes = opts.maxBytes ?? MAX_JSON_BYTES;
    return new Promise<RawResponse>((resolve, reject) => {
      const req = httpRequest(
        {
          host: "127.0.0.1",
          port: t.port,
          method,
          path,
          headers: {
            Host: `127.0.0.1:${t.port}`,
            Authorization: `Bearer ${t.token}`,
            Accept: "application/json",
            ...(payload ? { "Content-Type": "application/json", "Content-Length": String(payload.length) } : {}),
          },
          timeout: opts.timeoutMs ?? this.defaultTimeoutMs,
          agent: false,
        },
        (res) => {
          const chunks: Buffer[] = [];
          let size = 0;
          res.on("data", (c: Buffer) => {
            size += c.length;
            if (size > maxBytes) {
              req.destroy(new Error(`response larger than ${maxBytes} bytes`));
              return;
            }
            chunks.push(c);
          });
          res.on("end", () => resolve({ status: res.statusCode ?? 0, headers: res.headers, body: Buffer.concat(chunks) }));
          res.on("error", reject);
        },
      );
      req.on("timeout", () => req.destroy(new Error("request timed out")));
      req.on("error", reject);
      if (payload) req.write(payload);
      req.end();
    });
  }

  async json<T>(method: Method, path: string, body?: unknown, timeoutMs?: number): Promise<BridgeResult<T>> {
    let res: RawResponse;
    try {
      res = await this.raw(method, path, body, timeoutMs === undefined ? {} : { timeoutMs });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg === "backend not ready") log.debug(`${method} ${path}: ${msg}`);
      else log.warn(`${method} ${path}: ${msg}`);
      return { ok: false, error: backendUnavailable(msg) };
    }
    return parseJsonResponse<T>(res, `${method} ${path}`);
  }

  async binary(path: string, timeoutMs?: number): Promise<BridgeResult<{ contentType: string; bytes: Buffer }>> {
    let res: RawResponse;
    try {
      res = await this.raw("GET", path, undefined, { maxBytes: MAX_BINARY_BYTES, ...(timeoutMs ? { timeoutMs } : {}) });
    } catch (err) {
      return { ok: false, error: backendUnavailable(err instanceof Error ? err.message : String(err)) };
    }
    if (res.status < 200 || res.status >= 300) return parseJsonResponse(res, `GET ${path}`) as never;
    const ct = String(res.headers["content-type"] ?? "application/octet-stream").split(";")[0]!.trim();
    return { ok: true, data: { contentType: ct, bytes: res.body } };
  }
}

export function parseJsonResponse<T>(res: RawResponse, what: string): BridgeResult<T> {
  let parsed: unknown = undefined;
  if (res.body.length > 0) {
    try {
      parsed = JSON.parse(res.body.toString("utf8"));
    } catch {
      parsed = undefined;
    }
  }
  if (res.status >= 200 && res.status < 300) {
    if (parsed === undefined && res.body.length > 0) {
      return { ok: false, error: shellError("INTERNAL", "backend_bad_response", `${what}: response is not JSON`) };
    }
    return { ok: true, data: (parsed ?? null) as T };
  }
  const err = (parsed as { error?: ApiErrorBody } | undefined)?.error;
  if (err && typeof err.code === "string" && typeof err.message === "string") {
    return { ok: false, error: { code: err.code, message: err.message, retryable: !!err.retryable, details: err.details ?? {} } };
  }
  return {
    ok: false,
    error: shellError("INTERNAL", "backend_bad_response", `${what}: HTTP ${res.status} without ApiError body`, false, {
      http_status: res.status,
    }),
  };
}
