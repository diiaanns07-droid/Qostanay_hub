import type { EnvironmentCapabilities, Health, HealthReport, PreflightReport, SourceMode } from "@contracts/qorgau-v1.generated";
import { CAPABILITY, ENV_ACTION, HEALTH_CODE } from "./labels";

export interface ExamCheckRow {
  id: string;
  label: string;
  status: string;
  tone: "ok" | "warn" | "danger" | "neutral";
  detail: string;
}

const modules = [
  ["capture", "Камера", "Источник кадров для проверок телефона, лица и взгляда."],
  ["phone", "Телефон в кадре", "Наличие телефона; эпизоды проверяет преподаватель."],
  ["attention", "Лицо и направление взгляда", "Наличие и число лиц, приблизительное направление взгляда. Перед экзаменом нужна калибровка."],
  ["audio", "Звук", "Локальная проверка звука, если модуль доступен."],
  ["identity", "Сверка лица", "Тот же ли человек за компьютером, что в начале экзамена: лицо запоминается в первые секунды и сверяется раз в секунду. Локально, без базы лиц; это не установление личности."],
] as const;
const keys = ["shortcut_alt_tab", "shortcut_win", "shortcut_print_screen", "shortcut_ctrl_c", "shortcut_ctrl_v", "shortcut_ctrl_x", "shortcut_ctrl_tab", "shortcut_alt_f4"] as const;

function moduleState(h: Health | undefined): Pick<ExamCheckRow, "status" | "tone"> {
  if (!h) return { status: "Не заявлено сервисом", tone: "neutral" };
  if (h.code === "module_not_integrated") return { status: "Модуль не подключён", tone: "neutral" };
  if (h.code.startsWith("fixture_") || h.code === "synthetic_source") return { status: "Имитация", tone: "warn" };
  switch (h.status) {
    case "ok": return { status: "Готово", tone: "ok" };
    case "starting": return { status: "Запускается", tone: "neutral" };
    case "degraded": return { status: "С ограничениями", tone: "warn" };
    case "error": return { status: "Ошибка проверки", tone: "danger" };
    case "unavailable": return { status: "Недоступно", tone: "danger" };
    default: return { status: "Не запущено", tone: "neutral" };
  }
}

/** Display only reported readiness, never infer a module from the existence of a contract label. */
export function examChecks({ health, caps, mode, report, backendLost = false }: {
  health: HealthReport | null;
  caps: EnvironmentCapabilities | null;
  mode: SourceMode;
  report: PreflightReport | null;
  backendLost?: boolean;
}): { modules: ExamCheckRow[]; keys: ExamCheckRow[] } {
  const rows: ExamCheckRow[] = modules.map(([id, label, detail]) => {
    const h = health?.components.find((c) => c.component === id);
    const base = { id, label, detail };
    if (backendLost) return { ...base, status: "Нет связи", tone: "warn" };
    if (!health) return { ...base, status: "Не проверено", tone: "neutral" };
    if (id === "capture" && mode !== "live") return { ...base, status: "Камера не используется", tone: "neutral", detail: mode === "replay" ? "Проверяется запись (REPLAY)." : "Синтетический тест без камеры (SYNTHETIC)." };
    if (mode === "synthetic" && (id === "phone" || id === "attention")) return { ...base, status: "Имитация", tone: "warn", detail: "Сценарные наблюдения SYNTHETIC; распознавание по камере не выполняется." };
    if (mode === "synthetic" && id === "identity") return { ...base, status: "Не используется", tone: "neutral", detail: "В SYNTHETIC нет лиц: сверка лица не выполняется." };
    const state = moduleState(h);
    if (id === "capture" && mode === "live" && h?.status !== "error" && h?.status !== "unavailable" && h?.status !== "degraded" && state.status !== "Имитация") {
      const camera = report?.checks.find((c) => c.check_id === "camera");
      if (!camera || camera.status === "not_run") return { ...base, status: "Ожидает проверки", tone: "neutral", detail: "Доступ к камере будет проверен после согласия и создания сессии." };
      return { ...base, status: camera.status === "pass" ? "Готово" : camera.status === "fail" ? "Недоступно" : "С ограничениями", tone: camera.status === "pass" ? "ok" : camera.status === "fail" ? "danger" : "warn", detail: camera.message_ru };
    }
    return { ...base, ...state, detail: h && h.status !== "ok" ? (HEALTH_CODE[h.code] ?? detail) : detail };
  });
  return { modules: rows, keys: keys.map((action) => {
    const item = caps?.items.find((c) => c.action === action);
    const status = backendLost || !caps?.exam_mode_supported ? "unverified" : item?.status ?? "unverified";
    return { id: action, label: ENV_ACTION[action], status: CAPABILITY[status],
      tone: status === "blocked" ? "ok" : status === "detected_only" ? "warn" : "neutral",
      detail: backendLost ? "Нет связи с сервисом." : !caps ? "Оболочка ещё не сообщила результат." : !caps.exam_mode_supported ? "Режим экзамена не поддерживается." : item?.note_ru ?? "" };
  }) };
}
