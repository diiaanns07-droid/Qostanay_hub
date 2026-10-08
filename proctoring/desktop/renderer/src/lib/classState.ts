// class_state stream message from the C2 uplink (handoffs/C2/STATUS.md). Additive, not in qorgau.v1 StreamPayload,
// so it is parsed defensively here; anything malformed is ignored (never shown half-valid).
export type ClassConnection = "connecting" | "connected" | "reconnecting" | "rejected" | "stopped";

export interface ClassState {
  connection: ClassConnection;
  server: string;
  student_id: string | null;
  locked: boolean;
  lock_reason_ru: string | null;
  mic_active: boolean;
  audio_direction: "listen" | "talk" | "both" | null;
  exam: { title?: string } | null;
  message_ru: string | null;
  computer_name: string | null; // not sent by C2 yet (request in handoffs/A07/STUDENT.md)
}

const CONNECTIONS: readonly string[] = ["connecting", "connected", "reconnecting", "rejected", "stopped"];
const str = (v: unknown, max = 300): string | null => (typeof v === "string" && v.length > 0 ? v.slice(0, max) : null);

export function parseClassState(m: unknown): ClassState | null {
  if (typeof m !== "object" || m === null) return null;
  const o = m as Record<string, unknown>;
  if (o.type !== "class_state" || typeof o.connection !== "string" || !CONNECTIONS.includes(o.connection)) return null;
  const dir = o.audio_direction;
  return {
    connection: o.connection as ClassConnection,
    server: str(o.server, 260) ?? "",
    student_id: str(o.student_id, 64),
    locked: o.locked === true,
    lock_reason_ru: str(o.lock_reason_ru, 200),
    mic_active: o.mic_active === true,
    audio_direction: dir === "listen" || dir === "talk" || dir === "both" ? dir : null,
    exam: typeof o.exam === "object" && o.exam !== null ? { title: str((o.exam as Record<string, unknown>).title, 200) ?? undefined } : null,
    message_ru: str(o.message_ru),
    computer_name: str(o.computer_name, 64),
  };
}

export const CONNECTION_RU: Record<ClassConnection, { text: string; tone: "ok" | "warn" | "danger" | "neutral" }> = {
  connected: { text: "подключено", tone: "ok" },
  connecting: { text: "подключение…", tone: "neutral" },
  reconnecting: { text: "переподключение: сервер класса не отвечает", tone: "warn" },
  rejected: { text: "неверный код подключения", tone: "danger" },
  stopped: { text: "связь с классом остановлена", tone: "neutral" },
};
