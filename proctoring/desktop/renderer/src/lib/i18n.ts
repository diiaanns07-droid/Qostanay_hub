// UI chrome dictionary. Russian is the reference language.
// Kazakh entries are a DRAFT that has NOT been reviewed by a native speaker (see handoffs/A07/STATUS.md);
// missing keys fall back to Russian. Domain labels (rules, checks) stay Russian until reviewed.
import { createContext, useContext } from "react";

export type Lang = "ru" | "kk";

const ru = {
  app_name: "Qorgau Exam",
  step_preflight: "Подготовка",
  step_calibration: "Калибровка",
  step_exam: "Экзамен",
  step_review: "Проверка",
  step_summary: "Итог",
  role_student: "Студент",
  role_teacher: "Преподаватель",
  teacher_mode: "Режим преподавателя",
  lock_teacher: "Закрыть режим преподавателя",
  finish_exam: "Завершить экзамен",
  emergency_exit: "Аварийный выход",
  retry: "Повторить",
  cancel: "Отмена",
  close: "Закрыть",
  saved: "Сохранено",
  saving: "Сохранение…",
  not_saved: "Не сохранено",
  time_left: "Осталось",
  question: "Вопрос",
  of: "из",
  monitoring_on: "Наблюдение идёт",
  monitoring_paused: "Пауза — наблюдение и ответы приостановлены",
  backend_lost: "Нет связи с локальным сервисом",
  lang_draft: "черновик, не проверен",
} as const;

export type MsgKey = keyof typeof ru;

const kk: Partial<Record<MsgKey, string>> = {
  step_preflight: "Дайындық",
  step_calibration: "Калибрлеу",
  step_exam: "Емтихан",
  step_review: "Тексеру",
  step_summary: "Қорытынды",
  role_student: "Студент",
  role_teacher: "Оқытушы",
  teacher_mode: "Оқытушы режимі",
  finish_exam: "Емтиханды аяқтау",
  emergency_exit: "Апаттық шығу",
  retry: "Қайталау",
  cancel: "Бас тарту",
  close: "Жабу",
  saved: "Сақталды",
  saving: "Сақталуда…",
  not_saved: "Сақталмады",
  time_left: "Қалған уақыт",
  question: "Сұрақ",
  of: "/",
  monitoring_on: "Бақылау жүріп жатыр",
  backend_lost: "Жергілікті қызметпен байланыс жоқ",
  lang_draft: "жоба, тексерілмеген",
};

const dicts: Record<Lang, Partial<Record<MsgKey, string>>> = { ru, kk };

export function translate(lang: Lang, key: MsgKey): string {
  return dicts[lang][key] ?? ru[key];
}

export const LangContext = createContext<Lang>("ru");

export function useT(): (key: MsgKey) => string {
  const lang = useContext(LangContext);
  return (key) => translate(lang, key);
}
