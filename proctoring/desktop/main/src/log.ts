// Shell logger (owner: A06). Writes to stderr only. Registered secrets (the backend token)
// are replaced before anything is written, so a value that slips into a message is still
// never printed. Never log window titles, typed text or clipboard content.

const secrets = new Set<string>();

export function registerSecret(value: string): void {
  if (value.length >= 8) secrets.add(value);
}

export function forgetSecret(value: string): void {
  secrets.delete(value);
}

export function redact(text: string): string {
  let out = text;
  for (const s of secrets) out = out.split(s).join("[REDACTED]");
  return out;
}

type Level = "debug" | "info" | "warn" | "error";
const order: Record<Level, number> = { debug: 10, info: 20, warn: 30, error: 40 };
let threshold: Level = (process.env.QORGAU_SHELL_LOG_LEVEL as Level | undefined) ?? "info";
if (!(threshold in order)) threshold = "info";

export function setLogLevel(level: Level): void {
  threshold = level;
}

function fmt(value: unknown): string {
  if (value instanceof Error) return `${value.name}: ${value.message}`;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function write(level: Level, scope: string, parts: unknown[]): void {
  if (order[level] < order[threshold]) return;
  const line = `${new Date().toISOString()} ${level.toUpperCase()} shell.${scope}: ${parts.map(fmt).join(" ")}`;
  process.stderr.write(redact(line) + "\n");
}

export interface Logger {
  debug(...parts: unknown[]): void;
  info(...parts: unknown[]): void;
  warn(...parts: unknown[]): void;
  error(...parts: unknown[]): void;
}

export function logger(scope: string): Logger {
  return {
    debug: (...p) => write("debug", scope, p),
    info: (...p) => write("info", scope, p),
    warn: (...p) => write("warn", scope, p),
    error: (...p) => write("error", scope, p),
  };
}
