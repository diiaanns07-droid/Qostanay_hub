// Russian labels for contract enums. Wording follows the case rules: observations are not accusations,
// priority is review priority, gaze is approximate.
import type {
  CalibrationPhase,
  CalibrationTarget,
  CapabilityStatus,
  CheckStatus,
  Component,
  EnforcementResult,
  EnvironmentAction,
  ErrorCode,
  HealthStatus,
  IncidentCategory,
  IncidentEndReason,
  IncidentRule,
  PreflightCheckId,
  ReviewDecision,
  ReviewPriority,
  ReviewStatus,
  SessionState,
  SourceMode,
} from "@contracts/qorgau-v1.generated";

export const RULE: Record<IncidentRule, string> = {
  phone_visible: "Телефон в кадре",
  phone_raised: "Телефон поднят",
  possible_screen_capture: "Возможная съёмка экрана",
  gaze_prolonged_down: "Долгий взгляд вниз",
  gaze_prolonged_side: "Долгий взгляд в сторону",
  face_missing: "Лицо не видно",
  multiple_faces: "Второе лицо в кадре",
  environment_blocked_action: "Заблокированное действие",
  environment_escape: "Выход из окна экзамена",
  monitoring_degraded: "Наблюдение ухудшено",
};

export const RULE_HINT: Partial<Record<IncidentRule, string>> = {
  gaze_prolonged_down: "Направление взгляда — приблизительная оценка. Чтение нижней части страницы или черновик — обычное поведение.",
  gaze_prolonged_side: "Направление взгляда — приблизительная оценка, не доказательство.",
  phone_visible: "Детектор телефона не доказывает фотографирование экрана.",
  possible_screen_capture: "Направление камеры телефона может быть неразличимо.",
  monitoring_degraded: "Технический эпизод: качество или наличие данных ухудшилось.",
};

export const CATEGORY: Record<IncidentCategory, string> = {
  phone: "Телефон",
  attention: "Внимание",
  presence: "Присутствие",
  environment: "Среда",
  technical: "Техника",
};

export const PRIORITY: Record<ReviewPriority, string> = {
  low: "низкий приоритет проверки",
  medium: "средний приоритет проверки",
  high: "высокий приоритет проверки",
};

export const PRIORITY_SHORT: Record<ReviewPriority, string> = { low: "низкий", medium: "средний", high: "высокий" };

export const REVIEW_STATUS: Record<ReviewStatus, string> = {
  pending: "ожидает проверки",
  confirmed: "подтверждено",
  dismissed: "отклонено",
  inconclusive: "недостаточно данных",
};

export const DECISION: Record<ReviewDecision, string> = {
  confirmed: "Подтвердить",
  dismissed: "Отклонить",
  inconclusive: "Недостаточно данных",
};

export const END_REASON: Record<IncidentEndReason, string> = {
  condition_cleared: "условие прекратилось",
  session_finished: "сессия завершена",
  session_paused: "сессия на паузе",
  session_aborted: "сессия прервана",
  source_lost: "источник потерян",
  merged: "объединён с другим эпизодом",
};

export const SOURCE_MODE: Record<SourceMode, string> = { live: "LIVE", replay: "REPLAY", synthetic: "SYNTHETIC" };
export const SOURCE_MODE_RU: Record<SourceMode, string> = {
  live: "камера (live)",
  replay: "запись (replay)",
  synthetic: "синтетический тест",
};

export const SESSION_STATE: Record<SessionState, string> = {
  created: "создана",
  preflight: "проверка",
  calibrating: "калибровка",
  ready: "готова к началу",
  running: "экзамен идёт",
  paused: "пауза",
  finished: "завершена",
  aborted: "прервана",
  failed: "сбой",
};

export const CHECK: Record<PreflightCheckId, string> = {
  backend: "Локальный сервис",
  camera: "Камера / источник кадров",
  lighting: "Освещение",
  phone_model: "Модель телефона",
  face_model: "Модель лица",
  fusion: "Объединение эпизодов",
  storage: "Локальное хранилище",
  environment_protection: "Защита среды",
  offline_assets: "Офлайн-ресурсы",
};

export const CHECK_STATUS: Record<CheckStatus, string> = {
  pass: "в норме",
  warn: "предупреждение",
  fail: "не пройдено",
  not_run: "не проверено",
};

export const COMPONENT: Record<Component, string> = {
  backend: "Сервис",
  capture: "Захват кадров",
  phone: "Телефон",
  attention: "Лицо и внимание",
  fusion: "Эпизоды",
  evidence: "Хранилище",
  environment: "Среда",
};

export const HEALTH: Record<HealthStatus, string> = {
  starting: "запуск",
  ok: "в норме",
  degraded: "ухудшено",
  unavailable: "недоступно",
  error: "ошибка",
  stopped: "остановлено",
};

export const TARGET: Record<CalibrationTarget, string> = {
  center: "в центр",
  left: "влево",
  right: "вправо",
  up: "вверх",
  down: "вниз",
};

export const CAL_PHASE: Record<CalibrationPhase, string> = {
  not_started: "не начата",
  collecting: "идёт сбор",
  completed: "завершена",
  failed: "не удалась",
  skipped: "пропущена",
  cancelled: "отменена",
};

/** Known message codes from A04/bootstrap; unknown codes are shown verbatim, never hidden. */
export const CAL_MESSAGE: Record<string, string> = {
  low_quality: "Качество кадров недостаточно: проверьте освещение и положение лица",
  face_not_found: "Лицо не найдено в кадре",
  multiple_faces: "В кадре больше одного лица",
  targets_incomplete: "Не все точки собраны",
  synthetic_calibration: "Синтетическая калибровка (не CV)",
  fixture_calibration: "FIXTURE-калибровка (не CV)",
  skipped_by_operator: "Пропущена оператором",
  collecting: "Идёт сбор кадров",
  select_target: "Выберите точку калибровки",
  interrupted: "Сбор прерван (нет кадров или сессия изменилась)",
  no_face: "Лицо не найдено в кадре",
  no_face_detected: "Лицо не найдено в кадре",
  no_face_low_light: "Лицо не найдено: слишком темно",
  no_face_poor_image: "Лицо не найдено: плохое качество изображения",
  low_light: "Слишком темно: добавьте света спереди",
  low_face_quality: "Лицо видно нечётко: сядьте ближе и ровнее",
  small_face: "Лицо слишком маленькое в кадре: сядьте ближе к камере",
  partial_face: "Лицо видно не полностью",
  extreme_pose: "Голова сильно повёрнута: смотрите на точку, не поворачивая корпус",
  timeout: "Не удалось набрать качественные кадры",
};

export const ENV_ACTION: Record<EnvironmentAction, string> = {
  shortcut_alt_tab: "Alt+Tab",
  shortcut_ctrl_c: "Ctrl+C",
  shortcut_ctrl_v: "Ctrl+V",
  shortcut_ctrl_x: "Ctrl+X",
  shortcut_ctrl_tab: "Ctrl+Tab",
  shortcut_win: "Win",
  shortcut_print_screen: "PrtScn",
  shortcut_alt_f4: "Alt+F4",
  focus_lost: "Потеря фокуса окна",
  focus_regained: "Возврат фокуса",
  foreign_window_foreground: "Постороннее окно поверх",
  new_window_blocked: "Новое окно",
  navigation_blocked: "Переход по ссылке",
  devtools_blocked: "Инструменты разработчика",
  clipboard_blocked: "Буфер обмена",
  display_changed: "Смена монитора",
  exam_mode_engaged: "Режим экзамена включён",
  exam_mode_released: "Режим экзамена снят",
  enforcement_error: "Ошибка защиты",
};

export const CAPABILITY: Record<CapabilityStatus, string> = {
  blocked: "блокируется",
  detected_only: "только фиксируется",
  unsupported: "не поддерживается",
  unverified: "не проверено",
};

export const ENFORCEMENT: Record<EnforcementResult, string> = {
  blocked: "заблокировано",
  detected_only: "зафиксировано",
  allowed: "разрешено",
  failed: "сбой блокировки",
  unsupported: "не поддерживается",
};

export const ERROR_HINT: Partial<Record<ErrorCode, string>> = {
  UNAUTHORIZED: "Нет доступа. Проверьте PIN или перезапустите приложение.",
  INVALID_STATE: "Действие недоступно в текущем состоянии сессии.",
  SESSION_ACTIVE: "Уже есть незавершённая сессия. Завершите или прервите её.",
  PREFLIGHT_FAILED: "Обязательные проверки не пройдены.",
  CAMERA_UNAVAILABLE: "Камера недоступна.",
  CAMERA_BUSY: "Камера занята другим приложением.",
  CAMERA_DENIED: "Доступ к камере запрещён системой.",
  MODEL_MISSING: "Локальная модель не найдена — подготовьте веса заранее.",
  MODEL_INVALID: "Файл модели повреждён или не совпадает с manifest.",
  MODULE_NOT_INTEGRATED: "Модуль ещё не интегрирован в эту сборку.",
  CALIBRATION_FAILED: "Калибровка не удалась.",
  STORAGE_ERROR: "Ошибка локального хранилища.",
  NOT_IMPLEMENTED: "Функция ещё не реализована в этой сборке.",
  INTERNAL: "Внутренняя ошибка или нет связи с локальным сервисом.",
};

/** Health/pipeline codes (A01 r2, A02, A08) → readable Russian; unknown codes are shown as-is. */
export const HEALTH_CODE: Record<string, string> = {
  ok: "в норме",
  module_not_integrated: "модуль не входит в эту сборку",
  model_missing: "нет файла модели (подготовьте веса заранее)",
  model_invalid: "файл модели не совпадает с manifest",
  analyzer_error: "ошибки анализатора",
  analyzer_recovered: "анализатор восстановился",
  fusion_error: "ошибка объединения эпизодов",
  fusion_recovered: "объединение эпизодов восстановилось",
  fusion_queue_overflow: "очередь эпизодов переполнена — часть наблюдений пропущена",
  store_write_failed: "ошибка записи в хранилище",
  store_recovered: "запись в хранилище восстановилась",
  storage_unavailable: "хранилище недоступно",
  storage_write_failed: "ошибка записи в хранилище",
  store_in_use: "хранилище занято другим процессом",
  schema_too_new: "база данных новее программы",
  camera_disconnected: "камера отключилась",
  camera_unavailable: "камера недоступна",
  camera_busy: "камера занята другим приложением",
  camera_denied: "доступ к камере запрещён системой",
  synthetic_source: "синтетический источник (не камера)",
  shell_not_reported: "оболочка не сообщила возможности",
  capabilities_unverified: "возможности защиты не проверены",
};

export const GAP_REASON: Record<string, string> = {
  camera_disconnected: "камера отключилась",
  session_paused: "пауза",
  paused: "пауза",
  no_observations: "нет наблюдений",
  undetermined: "анализ не дал определённого результата",
  analyzer_error: "ошибка анализатора",
  fusion_error: "ошибка объединения эпизодов",
  fusion_queue_overflow: "переполнение очереди",
  store_write_failed: "ошибка записи",
  source_lost: "источник потерян",
};

export const human = (map: Record<string, string>, code: string | null | undefined): string =>
  code ? (map[code] ?? code) : "—";
