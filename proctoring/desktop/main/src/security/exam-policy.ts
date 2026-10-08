// URL-mode policy only. This is NOT a Windows application/firewall allowlist.
export interface ExamRule { origin: string; path: string; subtree: boolean; query: string | null }
export interface ExamPolicy { examId: string; title: string; entry: string; rules: readonly ExamRule[]; key: string }
export type ExamPolicyResult = { kind: "none" | "unsupported" | "invalid"; message: string } | { kind: "url"; policy: ExamPolicy };

/** Reject ambiguous URL spellings before WHATWG normalization. Never accept credentials or patterns in hosts. */
function parseUrl(raw: string): URL | null {
  if (raw.length > 4096 || /[\s\\\u0000-\u001f\u007f]/u.test(raw) || !/^https?:\/\//i.test(raw)) return null;
  try {
    const u = new URL(raw);
    if (u.username || u.password || !u.hostname || u.hostname.endsWith(".") || u.hostname.includes("*")) return null;
    // Encoded separators / second decode / NUL can mean a different server path.
    if (/%(?:2f|5c|25|00)/i.test(u.pathname)) return null;
    return u;
  } catch { return null; }
}

export function compileExamPolicy(value: unknown): ExamPolicyResult {
  if (value == null) return { kind: "none", message: "Сайт экзамена не назначен" };
  if (typeof value !== "object") return { kind: "invalid", message: "Некорректные настройки сайта экзамена" };
  const o = value as Record<string, unknown>;
  if (o.mode === "app") return { kind: "unsupported", message: "Режим разрешённых программ Windows пока не поддерживается" };
  const bad = (): ExamPolicyResult => ({ kind: "invalid", message: "Разрешены полные http(s)-адреса и шаблоны с /* только в конце пути" });
  if (o.mode !== "url" || typeof o.exam_id !== "string" || !o.exam_id || !Array.isArray(o.allowed_urls) || o.allowed_urls.length < 1 || o.allowed_urls.length > 100) return bad();
  const rules: ExamRule[] = [];
  let entry = "";
  for (const raw of o.allowed_urls) {
    if (typeof raw !== "string" || raw.includes("#")) return bad();
    const subtree = raw.endsWith("/*");
    const address = subtree ? raw.slice(0, -1) : raw;
    if (address.includes("*")) return bad();
    const u = parseUrl(address);
    if (!u || (subtree && u.search)) return bad();
    rules.push({ origin: u.origin, path: u.pathname, subtree, query: u.search || null });
    if (!entry) entry = u.href;
  }
  const title = typeof o.title === "string" ? o.title.slice(0, 200) : "Сайт экзамена";
  return { kind: "url", policy: { examId: o.exam_id, title, entry, rules, key: JSON.stringify([o.exam_id, rules]) } };
}

/** No implicit host/subdomain/port expansion; resources and redirects use the SAME path rules. */
export function examUrlAllowed(policy: ExamPolicy, raw: string, resourceType = "mainFrame"): boolean {
  if (resourceType === "webSocket") raw = raw.replace(/^wss:/i, "https:").replace(/^ws:/i, "http:");
  const u = parseUrl(raw);
  return !!u && policy.rules.some((r) => u.origin === r.origin &&
    (r.subtree ? u.pathname.startsWith(r.path) : u.pathname === r.path) && (r.query === null || u.search === r.query));
}

export interface ExamBounds { x: number; y: number; width: number; height: number }
/** Keep trusted shell strips visible even if its renderer supplies a bad rectangle. */
export function examBounds(raw: unknown, width: number, height: number, zoom = 1): ExamBounds | null {
  if (!raw || typeof raw !== "object" || !Number.isFinite(zoom) || zoom <= 0) return null;
  const o = raw as Record<string, unknown>;
  if (![o.x, o.y, o.width, o.height].every((v) => typeof v === "number" && Number.isFinite(v))) return null;
  const x = Math.max(0, Math.ceil((o.x as number) * zoom));
  const y = Math.max(112, Math.ceil((o.y as number) * zoom));
  const right = Math.min(width, Math.floor(((o.x as number) + (o.width as number)) * zoom));
  const bottom = Math.min(height - 64, Math.floor(((o.y as number) + (o.height as number)) * zoom));
  return right - x >= 100 && bottom - y >= 80 ? { x, y, width: right - x, height: bottom - y } : null;
}
