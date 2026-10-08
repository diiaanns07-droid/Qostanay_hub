// Readable Russian explanations for bridge errors. The shell (A06) keeps a contract ErrorCode and adds
// details.shell_code; the backend (A01/A08) may add details.reason. Unknown codes are shown verbatim.
import type { ApiErrorBody } from "@contracts/qorgau-v1.generated";
import { ERROR_HINT } from "./labels";

export const SHELL_HINT: Record<string, string> = {
  backend_unavailable: "Нет связи с локальным сервисом. Оболочка перезапускает его; действие можно повторить.",
  backend_bad_response: "Оболочка получила от сервиса некорректный ответ.",
  operator_locked: "Действие доступно только в режиме преподавателя (нужен PIN).",
  operator_pin_wrong: "Неверный PIN.",
  operator_pin_rate_limited: "Слишком много неверных попыток. Подождите и повторите позже.",
  operator_pin_not_configured:
    "PIN преподавателя не настроен на этом компьютере. Администратор задаёт QORGAU_OPERATOR_PIN_HASH (node main/tools/hash-pin.mjs).",
  session_not_bound: "Сессия создана не в этом запуске приложения — управлять ею здесь нельзя.",
  exam_mode_active: "Недоступно во время экзамена: журнал, решения и материалы открываются после паузы или завершения.",
  enforcement_error: "Защита среды не включилась или ещё не измерена оболочкой.",
  invalid_argument: "Оболочка отклонила данные запроса.",
  untrusted_sender: "Запрос отклонён оболочкой (недоверенный источник).",
  save_cancelled: "Сохранение отменено.",
};

export const REASON_HINT: Record<string, string> = {
  media_missing: "Файл материала отсутствует на диске.",
  ttl_expired: "Материал удалён по сроку хранения; метаданные сохранены.",
  invalid_path: "Материал недоступен (некорректная ссылка на файл).",
  hash_mismatch: "Файл материала изменён или повреждён (не совпадает хэш) — не показан.",
};

export function shellCode(e: ApiErrorBody | null | undefined): string | null {
  const c = e?.details?.shell_code;
  return typeof c === "string" ? c : null;
}

export function errorReason(e: ApiErrorBody | null | undefined): string | null {
  const r = e?.details?.reason;
  return typeof r === "string" ? r : null;
}

/** One-line Russian title for an error (shell code > backend reason > contract code). */
export function describeError(e: ApiErrorBody): string {
  const sc = shellCode(e);
  if (sc && SHELL_HINT[sc]) return SHELL_HINT[sc]!;
  const rs = errorReason(e);
  if (rs && REASON_HINT[rs]) return REASON_HINT[rs]!;
  return ERROR_HINT[e.code] ?? "Ошибка";
}

export const isConnectionError = (e: ApiErrorBody) => shellCode(e) === "backend_unavailable";
