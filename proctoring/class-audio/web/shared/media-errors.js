// @ts-check
// Microphone / WebRTC failure → protocol error_code + readable Russian text and a concrete fix (T05).
// Shared by the teacher module and the student endpoint reference.

/** @typedef {{code: string, message_ru: string, hint_ru: string}} MediaProblem */

/** Preconditions before calling getUserMedia (secure context, API present). @returns {MediaProblem|null} */
export function micPrecheck() {
  if (typeof window !== "undefined" && window.isSecureContext === false) {
    return {
      code: "insecure_context",
      message_ru: "Микрофон недоступен: страница открыта не в защищённом контексте.",
      hint_ru: "Откройте по https:// (с доверенным сертификатом) или на этом же компьютере через http://127.0.0.1. Отключать защиту браузера нельзя.",
    };
  }
  if (typeof navigator === "undefined" || !navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
    return { code: "not_supported", message_ru: "Браузер не поддерживает доступ к микрофону.", hint_ru: "Используйте актуальный Chrome, Edge или Firefox." };
  }
  if (typeof RTCPeerConnection === "undefined") {
    return { code: "not_supported", message_ru: "Браузер не поддерживает WebRTC.", hint_ru: "Используйте актуальный Chrome, Edge или Firefox." };
  }
  return null;
}

/** @param {unknown} err @returns {MediaProblem} */
export function mapMediaError(err) {
  const name = err && typeof err === "object" && "name" in err ? String(/** @type {any} */ (err).name) : "";
  switch (name) {
    case "NotAllowedError":
    case "PermissionDeniedError":
      return {
        code: "mic_denied",
        message_ru: "Доступ к микрофону запрещён.",
        hint_ru: "Разрешите микрофон для этой страницы: значок слева от адреса → «Микрофон» → «Разрешить», затем повторите. Проверьте также параметры конфиденциальности Windows → Микрофон.",
      };
    case "NotFoundError":
    case "DevicesNotFoundError":
    case "OverconstrainedError":
      return { code: "mic_not_found", message_ru: "Микрофон не найден.", hint_ru: "Подключите микрофон или гарнитуру и повторите." };
    case "NotReadableError":
    case "TrackStartError":
    case "AbortError":
      return {
        code: "mic_busy",
        message_ru: "Микрофон занят или недоступен системе.",
        hint_ru: "Закройте другие программы, использующие микрофон, и повторите.",
      };
    case "NotSupportedError":
      return {
        code: "not_supported",
        message_ru: "Браузер не смог запросить доступ к микрофону.",
        hint_ru: "Откройте страницу в обычном окне браузера (не во встроенном/безоконном режиме) и разрешите микрофон.",
      };
    case "SecurityError":
      return { code: "insecure_context", message_ru: "Браузер запретил доступ к микрофону по соображениям безопасности.", hint_ru: "Нужен https:// или localhost." };
    default:
      return { code: "internal", message_ru: "Не удалось включить микрофон.", hint_ru: name ? `Техническая причина: ${name}.` : "Повторите попытку." };
  }
}

/** Reasons of `audio_state.reason` / student `ack.error_code` for the teacher UI. */
export const REASON_RU = {
  teacher_stop: "связь завершена преподавателем",
  student_stop: "связь завершена на стороне студента",
  teacher_disconnected: "пульт преподавателя потерял связь с сервером",
  teacher_auth_lost: "вход преподавателя завершён — потоки закрыты",
  student_disconnected: "студент потерял связь с сервером",
  network_lost: "медиасоединение оборвалось и не восстановилось",
  server_shutdown: "сервер класса остановлен",
  accept_timeout: "студент не ответил за 15 с",
  connect_timeout: "соединение не установилось за 20 с",
  mic_denied: "у студента запрещён доступ к микрофону",
  mic_not_found: "у студента не найден микрофон",
  mic_busy: "микрофон студента занят другой программой",
  insecure_context: "приложение студента открыто не в защищённом контексте",
  not_supported: "приложение студента не поддерживает аудиосвязь",
  busy: "у студента уже идёт другая аудиосвязь",
  exam_state: "аудиосвязь сейчас недоступна на стороне студента",
  declined: "студент не подтвердил аудиосвязь",
  update_failed: "не удалось изменить режим на стороне студента",
  internal: "внутренняя ошибка на стороне студента",
};

/** @param {string|null|undefined} code */
export function reasonRu(code) {
  if (!code) return "";
  return /** @type {Record<string,string>} */ (REASON_RU)[code] ?? code;
}
