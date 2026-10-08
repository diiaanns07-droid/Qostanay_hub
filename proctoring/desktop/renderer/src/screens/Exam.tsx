import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ApiErrorBody, ExamDefinition, Question } from "@contracts/qorgau-v1.generated";
import { useApp } from "../lib/appContext";
import { useLive } from "../lib/liveStore";
import { call } from "../lib/result";
import { clock } from "../lib/format";
import { useT } from "../lib/i18n";
import { Banner, Button, Dialog, ErrorBanner, Spinner } from "../components/ui";

type Value = string | string[];
type SaveState = { status: "idle" | "pending" | "saving" | "saved" | "error"; error?: ApiErrorBody };

const RETRY_MS = 3000;
const TEXT_DEBOUNCE_MS = 500;

function isAnswered(v: Value | undefined): boolean {
  if (v === undefined) return false;
  return Array.isArray(v) ? v.length > 0 : v.trim().length > 0;
}

export function ExamScreen() {
  const { bridge, session, setSession, live, backendLost } = useApp();
  useLive(live);
  const t = useT();
  const sid = session?.session_id ?? "";
  const [exam, setExam] = useState<ExamDefinition | null>(null);
  const [loadErr, setLoadErr] = useState<ApiErrorBody | null>(null);
  const [idx, setIdx] = useState(0);
  const [values, setValues] = useState<Record<string, Value>>({});
  const [saves, setSaves] = useState<Record<string, SaveState>>({});
  const [now, setNow] = useState(Date.now());
  const [dialog, setDialog] = useState<null | "finish" | "exit">(null);
  const [finishing, setFinishing] = useState(false);
  const [finishErr, setFinishErr] = useState<ApiErrorBody | null>(null);
  const [exitReason, setExitReason] = useState("");
  const seq = useRef(0);
  const valuesRef = useRef(values);
  valuesRef.current = values;
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const load = useCallback(async () => {
    setLoadErr(null);
    const [e, a] = await Promise.all([call(bridge.getExam(sid)), call(bridge.listAnswers(sid))]);
    if (!e.ok) return setLoadErr(e.error);
    setExam(e.data);
    if (a.ok) {
      const restored: Record<string, Value> = {};
      const st: Record<string, SaveState> = {};
      for (const r of a.data) {
        restored[r.question_id] = r.value;
        st[r.question_id] = { status: "saved" };
        seq.current = Math.max(seq.current, r.client_seq);
      }
      setValues((v) => ({ ...restored, ...v }));
      setSaves((s) => ({ ...st, ...s }));
    }
  }, [bridge, sid]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const i = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(i);
  }, []);

  const save = useCallback(
    async (qid: string) => {
      const value = valuesRef.current[qid];
      if (value === undefined) return;
      seq.current = Math.max(seq.current + 1, Date.now());
      const client_seq = seq.current;
      setSaves((s) => ({ ...s, [qid]: { status: "saving" } }));
      const r = await call(bridge.saveAnswer(sid, qid, { value, client_seq }));
      setSaves((s) => {
        // A newer edit may already be pending; do not overwrite its state.
        if (s[qid]?.status === "pending") return s;
        return { ...s, [qid]: r.ok ? { status: "saved" } : { status: "error", error: r.error } };
      });
    },
    [bridge, sid],
  );

  // Retry failed retryable saves automatically while the backend is reachable.
  useEffect(() => {
    if (backendLost) return;
    const failed = Object.entries(saves).filter(([, s]) => s.status === "error" && s.error?.retryable);
    if (failed.length === 0) return;
    const tm = setTimeout(() => failed.forEach(([q]) => void save(q)), RETRY_MS);
    return () => clearTimeout(tm);
  }, [saves, backendLost, save]);

  const setAnswer = (q: Question, v: Value, debounce: boolean) => {
    setValues((s) => ({ ...s, [q.question_id]: v }));
    setSaves((s) => ({ ...s, [q.question_id]: { status: "pending" } }));
    clearTimeout(timers.current[q.question_id]);
    timers.current[q.question_id] = setTimeout(() => void save(q.question_id), debounce ? TEXT_DEBOUNCE_MS : 0);
  };

  const paused = session?.state === "paused";
  const remainingMs = useMemo(() => {
    if (!exam || !session?.started_at) return null;
    const elapsed = now - Date.parse(session.started_at) - session.paused_total_ms;
    return exam.duration_s * 1000 - elapsed;
  }, [exam, session, now]);
  const timeUp = remainingMs !== null && remainingMs <= 0;

  const health = live.health;
  const capture = health?.components.find((c) => c.component === "capture");
  const monitoringLimited = backendLost || (capture && capture.status !== "ok");

  const unsaved = Object.values(saves).filter((s) => s.status !== "saved" && s.status !== "idle").length;
  const answered = exam ? exam.questions.filter((q) => isAnswered(values[q.question_id])).length : 0;

  const finish = async () => {
    setFinishing(true);
    setFinishErr(null);
    // Flush pending debounced answers first.
    for (const [qid, tm] of Object.entries(timers.current)) {
      clearTimeout(tm);
      if (saves[qid]?.status === "pending") await save(qid);
    }
    const r = await call(bridge.finishExam(sid));
    setFinishing(false);
    if (!r.ok) return setFinishErr(r.error);
    setDialog(null);
    setSession(r.data);
  };

  const emergencyExit = async () => {
    setFinishing(true);
    const r = await bridge.requestEmergencyExit(exitReason.trim() || "student_emergency_exit").catch(() => null);
    setFinishing(false);
    if (!r || !r.ok) {
      setFinishErr(r?.error ?? { code: "INTERNAL", message: "Оболочка не ответила", retryable: true, details: {} });
      return;
    }
    setDialog(null);
    const s = await call(bridge.getSession(sid));
    if (s.ok) setSession(s.data);
  };

  if (!session) return null;
  if (loadErr)
    return (
      <div className="screen">
        <ErrorBanner context="Загрузка заданий" error={loadErr} onRetry={() => void load()} />
        <Button onClick={() => void load()}>Повторить загрузку</Button>
      </div>
    );
  if (!exam) return <Spinner label="Загружаем задания…" />;

  const q = exam.questions[Math.min(idx, exam.questions.length - 1)];
  const disabled = paused || timeUp || session.state !== "running";
  const st = q ? saves[q.question_id] : undefined;

  return (
    <div className="screen exam">
      <div className="exam-head">
        <div>
          <div className="eyebrow">{exam.is_demo ? "Демонстрационный тест" : "Экзамен"}</div>
          <h1 className="exam-title">{exam.title}</h1>
        </div>
        <div className={`timer ${remainingMs !== null && remainingMs < 60_000 ? "timer-low" : ""}`} aria-live="off">
          <span className="timer-label">{t("time_left")}</span>
          <span className="timer-value" role="timer">
            {remainingMs === null ? "—" : clock(remainingMs)}
          </span>
        </div>
      </div>

      <div className="exam-progress" aria-label={`Отвечено ${answered} из ${exam.questions.length}`}>
        {exam.questions.map((qq, i) => {
          const a = isAnswered(values[qq.question_id]);
          return (
            <button
              key={qq.question_id}
              type="button"
              className={`qpill ${i === idx ? "qpill-on" : ""} ${a ? "qpill-done" : ""}`}
              onClick={() => setIdx(i)}
              aria-current={i === idx ? "step" : undefined}
              aria-label={`${t("question")} ${i + 1}${a ? ", есть ответ" : ""}`}
            >
              {i + 1}
            </button>
          );
        })}
        <span className="muted small">
          отвечено {answered} из {exam.questions.length}
        </span>
      </div>

      {paused && <Banner tone="warn" title={t("monitoring_paused")}>Преподаватель приостановил экзамен. Дождитесь продолжения.</Banner>}
      {timeUp && !paused && (
        <Banner tone="warn" title="Время вышло" actions={<Button variant="primary" onClick={() => setDialog("finish")}>{t("finish_exam")}</Button>}>
          Ответы больше не изменяются. Завершите экзамен.
        </Banner>
      )}

      {q && (
        <article className="question" aria-labelledby={`q-${q.question_id}`}>
          <div className="q-meta">
            {t("question")} {idx + 1} {t("of")} {exam.questions.length}
            {q.kind === "multi_choice" && <span className="muted"> · можно выбрать несколько</span>}
          </div>
          <h2 id={`q-${q.question_id}`} className="q-prompt">
            {q.prompt}
          </h2>
          <QuestionInput q={q} value={values[q.question_id]} disabled={disabled} onChange={(v, d) => setAnswer(q, v, d)} />
          <div className="save-state" aria-live="polite">
            {st?.status === "saving" || st?.status === "pending" ? (
              <span className="muted">{t("saving")}</span>
            ) : st?.status === "saved" ? (
              <span className="ok-text">✓ {t("saved")}</span>
            ) : st?.status === "error" ? (
              <span className="danger-text">
                {t("not_saved")}: {st.error?.message}
                {st.error?.retryable ? " — повторим автоматически" : ""}{" "}
                <button type="button" className="link" onClick={() => void save(q.question_id)}>
                  {t("retry")}
                </button>
              </span>
            ) : null}
          </div>
        </article>
      )}

      <div className="exam-nav">
        <Button disabled={idx === 0} onClick={() => setIdx((i) => Math.max(0, i - 1))}>
          ← Назад
        </Button>
        {idx < exam.questions.length - 1 ? (
          <Button variant="primary" onClick={() => setIdx((i) => Math.min(exam.questions.length - 1, i + 1))}>
            Далее →
          </Button>
        ) : (
          <Button variant="primary" onClick={() => setDialog("finish")}>
            {t("finish_exam")}
          </Button>
        )}
      </div>

      <footer className="exam-foot">
        <span className={`monitor-pill ${monitoringLimited ? "monitor-limited" : ""}`}>
          <span className="pulse" aria-hidden="true" />
          {monitoringLimited ? "Наблюдение ограничено (техническая причина)" : t("monitoring_on")} · {session.source_mode.toUpperCase()}
        </span>
        <span className="spacer" />
        <button type="button" className="link small" onClick={() => setDialog("exit")}>
          {t("emergency_exit")}
        </button>
        <Button variant="secondary" onClick={() => setDialog("finish")}>
          {t("finish_exam")}
        </Button>
      </footer>

      {dialog === "finish" && (
        <Dialog
          title="Завершить экзамен?"
          onClose={() => setDialog(null)}
          actions={
            <>
              <Button onClick={() => setDialog(null)}>Вернуться к заданиям</Button>
              <Button variant="primary" busy={finishing} disabled={backendLost} onClick={() => void finish()}>
                Завершить
              </Button>
            </>
          }
        >
          <p>
            Отвечено {answered} из {exam.questions.length}.
            {answered < exam.questions.length && " Неотвеченные вопросы останутся без ответа."}
          </p>
          {unsaved > 0 && <Banner tone="warn" title={`Не сохранено ответов: ${unsaved}`}>Перед завершением попробуем сохранить их ещё раз.</Banner>}
          {finishErr && <ErrorBanner context="Завершение" error={finishErr} />}
          {backendLost && <Banner tone="danger" title="Нет связи с локальным сервисом">Завершение станет доступно после восстановления связи.</Banner>}
        </Dialog>
      )}
      {dialog === "exit" && (
        <Dialog
          title="Аварийный выход"
          tone="danger"
          onClose={() => setDialog(null)}
          actions={
            <>
              <Button onClick={() => setDialog(null)}>Отмена</Button>
              <Button variant="danger" busy={finishing} onClick={() => void emergencyExit()}>
                Прервать и выйти
              </Button>
            </>
          }
        >
          <p>Сессия будет прервана, все ограничения сняты. Используйте при технической проблеме или плохом самочувствии.</p>
          <label className="field">
            <span>Причина (необязательно)</span>
            <input value={exitReason} onChange={(e) => setExitReason(e.target.value)} maxLength={200} />
          </label>
          {finishErr && <ErrorBanner context="Аварийный выход" error={finishErr} />}
        </Dialog>
      )}
    </div>
  );
}

function QuestionInput({
  q,
  value,
  disabled,
  onChange,
}: {
  q: Question;
  value: Value | undefined;
  disabled: boolean;
  onChange: (v: Value, debounce: boolean) => void;
}) {
  if (q.kind === "short_text") {
    const v = typeof value === "string" ? value : "";
    return (
      <div className="field">
        <textarea
          className="answer-text"
          value={v}
          disabled={disabled}
          maxLength={q.max_length ?? undefined}
          rows={3}
          aria-label="Ваш ответ"
          onChange={(e) => onChange(e.target.value, true)}
        />
        {q.max_length !== null && (
          <span className="small muted counter">
            {v.length}/{q.max_length}
          </span>
        )}
      </div>
    );
  }
  const multi = q.kind === "multi_choice";
  const selected = Array.isArray(value) ? value : typeof value === "string" && value ? [value] : [];
  return (
    <div className="options" role={multi ? "group" : "radiogroup"} aria-label="Варианты ответа">
      {q.options.map((o, i) => {
        const on = selected.includes(o.option_id);
        return (
          <label key={o.option_id} className={`option ${on ? "option-on" : ""} ${disabled ? "option-disabled" : ""}`}>
            <input
              type={multi ? "checkbox" : "radio"}
              name={`q-${q.question_id}`}
              checked={on}
              disabled={disabled}
              onChange={() => {
                const next = multi ? (on ? selected.filter((x) => x !== o.option_id) : [...selected, o.option_id]) : [o.option_id];
                onChange(next, false);
              }}
            />
            <span className="option-key">{String.fromCharCode(65 + i)}</span>
            <span className="option-text">{o.text}</span>
          </label>
        );
      })}
    </div>
  );
}
