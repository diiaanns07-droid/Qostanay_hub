// @ts-check
// Production C1 state. Receipt history and the current screen are deliberately separate.
export const ACTIONS = Object.freeze({
  start_exam: "Начать экзамен", lock: "Закрыть экран…", unlock: "Открыть экран", finish_exam: "Завершить экзамен…",
});
export const REASONS = Object.freeze([
  "Уберите телефон и дождитесь преподавателя",
  "Уберите посторонние материалы и дождитесь преподавателя",
  "Дождитесь проверки преподавателя",
]);

/** @param {any} card */
export function currentLock(card) {
  if (!card?.connected || card.stale) return { state: "unknown", tone: "muted", text: "Текущее состояние экрана неизвестно: нет свежего статуса." };
  if (card.lock_scope !== "app_overlay") return { state: "unknown", tone: "muted", text: "Приложение ещё не подтвердило состояние экрана." };
  if (card.lock_state === "requested") return { state: "pending", tone: "info", text: card.lock_requested ? "Ожидаем подтверждения закрытия экрана." : "Ожидаем подтверждения открытия экрана." };
  if (card.lock_state === "failed") return { state: "failed", tone: "bad", text: "Приложение не подтвердило последний запрос экрана." };
  if (card.lock_state === "applied" && card.lock_confirmed === true && typeof card.locked === "boolean") {
    return { state: card.locked ? "locked" : "unlocked", tone: card.locked ? "warn" : "ok", text: card.locked ? "Экран Adal закрыт — подтверждено приложением." : "Экран Adal открыт — подтверждено приложением." };
  }
  return { state: "unknown", tone: "muted", text: "Приложение ещё не подтвердило состояние экрана." };
}

/** @param {any} cmd @param {any} card */
export function commandState(cmd, card) {
  if (!cmd) return { state: "none", tone: "muted", text: "Команд этому студенту ещё не было." };
  if (["queued", "sent", "received"].includes(cmd.status)) return { state: "pending", tone: "info", text: cmd.unconfirmed ? "Команда отправлена, подтверждение не получено." : "Команда принята. Ожидаем ответа приложения." };
  if (cmd.status === "succeeded") {
    if (["lock", "unlock"].includes(cmd.kind)) {
      const receipt = cmd.ack?.result;
      const confirmed = cmd.ack?.ok === true && !cmd.ack?.late && receipt?.lock_scope === "app_overlay" &&
        receipt.lock_state === "applied" && receipt.locked === (cmd.kind === "lock") &&
        receipt.class_session_id === card?.session_id && typeof receipt.backend_instance_id === "string" && !!receipt.backend_instance_id;
      return confirmed ? { state: "confirmed", tone: "ok", text: "Команда подтверждена экраном приложения при выполнении." }
        : { state: "unconfirmed", tone: "warn", text: "Клиент ответил, но подтверждения экрана для этой команды нет." };
    }
    return { state: "confirmed", tone: "ok", text: "Выполнение подтверждено приложением." };
  }
  const labels = /** @type {Record<string,string>} */ ({ failed: "Команда не выполнена", expired: "Срок команды истёк без подтверждения", cancelled: "Команда отменена" });
  const text = labels[cmd.status];
  return { state: text ? "failed" : "unknown", tone: text ? "bad" : "muted", text: text ? `${text}.${cmd.ack?.error_ru ? ` ${cmd.ack.error_ru}` : ""}` : "Состояние команды неизвестно." };
}

/** @param {any} session @param {any} card @param {string} kind */
export function unavailable(session, card, kind) {
  if (!session || session.state !== "open" || session.session_id !== card?.session_id) return "Студент не относится к текущему классу.";
  if (!card?.connected || card.stale) return "Для команды нужен свежий статус подключённого приложения.";
  if (!Array.isArray(card.capabilities) || !card.capabilities.includes(kind)) return "Приложение не сообщило о поддержке этой команды.";
  if (kind === "start_exam" && card.exam_state !== "preflight") return "Начало доступно после подготовки студента.";
  if (kind === "finish_exam" && [null, "idle", "finished"].includes(card.exam_state)) return "У студента нет активного экзамена.";
  return null;
}
