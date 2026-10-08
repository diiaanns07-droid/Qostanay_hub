// A15 «Осмотр рабочего места»: pure logic of the desk-scan step (hints, student texts, mode mapping).
// The backend message_ru is shown too, but the student-facing text is built here from state + objects
// so the UI does not depend on its exact wording. The scan helps the teacher; it is not evidence.
import type { DeskScanObject, DeskScanResult, DeskScanState } from "@contracts/qorgau-v1.generated";
import type { DeskScanMode } from "../../../shared/desk-scan";

export type DeskScanVariant = "laptop" | "usb" | "fixed";

export const DESK_SCAN_VARIANTS: ReadonlyArray<{ id: DeskScanVariant; n: number; label: string }> = [
  { id: "laptop", n: 1, label: "Ноутбук" },
  { id: "usb", n: 2, label: "USB-камера" },
  { id: "fixed", n: 3, label: "Камера не двигается" },
];

/** Scan length requested from the backend (DeskScanRequest.duration_s, 5..30). */
export const DESK_SCAN_SECONDS = 12;
export const DESK_SCAN_POLL_MS = 500;
/** The client gives up polling after duration + this grace. */
export const DESK_SCAN_GRACE_S = 20;
/** skip_reason meaning "variant 3: the camera cannot move, the teacher checks the desk in the room". */
export const FIXED_CAMERA_REASON = "fixed_camera_teacher_check";

export const DESK_SCAN_HONESTY =
  "Осмотр помогает преподавателю, это не доказательство нарушения. Видео осмотра сохраняется только если включено хранение медиа.";
export const FIXED_CAMERA_TEXT = "Осмотр места проведёт преподаватель в аудитории";
export const DESK_SCAN_TIMEOUT_TEXT = "Осмотр не завершился вовремя. Повторите осмотр или обратитесь к преподавателю";

/** Query-string mode for a variant; null = no camera scan (teacher check, PIN). */
export function modeForVariant(v: DeskScanVariant): DeskScanMode | null {
  return v === "laptop" ? "laptop" : v === "usb" ? "usb" : null;
}

export function pollDeadlineMs(durationS: number): number {
  return (durationS + DESK_SCAN_GRACE_S) * 1000;
}

/** Big hint shown during the countdown (elapsed seconds since «Начать осмотр»). */
export function deskScanHint(v: DeskScanVariant, elapsedS: number): string {
  if (v === "fixed") return FIXED_CAMERA_TEXT;
  if (v === "usb") return "Снимите камеру с монитора и медленно проведите ею над столом слева направо, затем верните на место";
  if (elapsedS < 4) return "Медленно наклоните экран назад, чтобы камера увидела стол";
  if (elapsedS < 8) return "Плавно поверните ноутбук влево";
  return "…и вправо, затем верните на место";
}

export function isDeskScanFinal(state: DeskScanState): boolean {
  return state === "clear" || state === "objects_found" || state === "failed" || state === "skipped";
}

/** "телефон, книга" — unique Russian labels in the order the backend reports them. */
export function objectsList(objects: readonly DeskScanObject[]): string {
  const seen: string[] = [];
  for (const o of objects) {
    const l = (o.label_ru || o.class_name).trim();
    if (l && !seen.includes(l)) seen.push(l);
  }
  return seen.join(", ");
}

export type DeskScanTone = "ok" | "warn" | "danger" | "info";

export interface DeskScanView {
  tone: DeskScanTone;
  /** Student-facing text built from state + objects (not from message_ru wording). */
  text: string;
}

/** «уберите его / её / их» — same wording as the backend (книга — женский род). */
export function pronoun(objects: readonly DeskScanObject[]): string {
  if (objects.length !== 1) return "их";
  return objects[0]?.class_name === "book" ? "её" : "его";
}

const trimDot = (s: string) => s.trim().replace(/[.。]+$/, "");

/** null while there is no final result (not_started / recording). */
export function deskScanView(r: Pick<DeskScanResult, "state" | "objects" | "message_ru" | "skip_reason">): DeskScanView | null {
  switch (r.state) {
    case "clear":
      return { tone: "ok", text: "Стол осмотрен: посторонних предметов не замечено" };
    case "objects_found": {
      const list = objectsList(r.objects);
      return {
        tone: "warn",
        text: list
          ? `Замечено: ${list} — уберите ${pronoun(r.objects)} и повторите осмотр`
          : "Замечены посторонние предметы — уберите их и повторите осмотр",
      };
    }
    case "failed": {
      const why = r.message_ru ? trimDot(r.message_ru) : "причина не сообщена";
      return { tone: "danger", text: `Осмотр не удался: ${why}. Повторите осмотр или обратитесь к преподавателю` };
    }
    case "skipped": {
      if (r.message_ru) return { tone: "info", text: r.message_ru };
      if (r.skip_reason === FIXED_CAMERA_REASON) return { tone: "info", text: `${FIXED_CAMERA_TEXT} (подтверждено преподавателем)` };
      return { tone: "info", text: `Осмотр пропущен: ${r.skip_reason ?? "причина не указана"}` };
    }
    default:
      return null;
  }
}
