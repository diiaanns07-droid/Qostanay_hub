import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ApiErrorBody, ExamDefinition, Question } from "@contracts/qorgau-v1.generated";
import { useApp } from "../lib/appContext";
import { useLive } from "../lib/liveStore";
import { call } from "../lib/result";
import { clock } from "../lib/format";
import { describeError } from "../lib/errors";
import { useT } from "../lib/i18n";
import { AnswerSync, type AnswerValue } from "../lib/autosave";
import { Banner, Button, Dialog, ErrorBanner, Spinner } from "../components/ui";

const TEXT_DEBOUNCE_MS = 500;
const CHOICE_DEBOUNCE_MS = 0;
const FINISH_FLUSH_TIMEOUT_MS = 8000;

function isAnswered(v: AnswerValue | undefined): boolean {
  if (v === undefined) return false;
  return Array.isArray(v) ? v.length > 0 : v.trim().length > 0;
}

export function ExamScreen() {
  const { bridge, session, setSession, isCurrent, live, backendLost } = useApp();
  useLive(live);
  const t = useT();
  const sid = session?.session_id ?? "";
  const [exam, setExam] = useState<ExamDefinition | null>(null);
  const [loadErr, setLoadErr] = useState<ApiErrorBody | null>(null);
  const [idx, setIdx] = useState(0);
  const [values, setValues] = useState<Record<string, AnswerValue>>({});
  const [, setSyncVersion] = useState(0);
  const [tick, setTick] = useState(0);
  const [dialog, setDialog] = useState<null | "finish" | "exit">(null);
  const [finishing, setFinishing] = useState(false);
  const [finishErr, setFinishErr] = useState<ApiErrorBody | null>(null);
  const [unsavedAtFinish, setUnsavedAtFinish] = useState<string[] | null>(null);
  const [exitReason, setExitReason] = useState("");
  const frozenElapsed = useRef<number | null>(null);

  const sync = useMemo(
    () => new AnswerSync(sid, (qid, body) => call(bridge.saveAnswer(sid, qid, body)), () => setSyncVersion((n) => n + 1)),
    [bridge, sid],
  );
  useEffect(() => () => sync.dispose(), [sync]);

  const load = useCallback(async () => {
    setLoadErr(null);
    const [e, a] = await Promise.all([call(bridge.getExam(sid)), call(bridge.listAnswers(sid))]);
    if (!isCurrent(sid)) return;
    if (!e.ok) return setLoadErr(e.error);
    setExam(e.data);
    // Restore = stored answers merged with unsent local edits by client_seq (no duplicates).
    setValues(sync.restore(a.ok ? a.data : []));
    if (!a.ok) setLoadErr(a.error);
  }, [bridge, sid, sync, isCurrent]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const i = setInterval(() => setTick((n) => n + 1), 500);
    return () => clearInterval(i);
  }, []);

  // Answers are accepted only while running and the backend is reachable; otherwise edits wait locally.
  const accepting = session?.state === "running" && !backendLost;
  useEffect(() => {
    sync.setOnline(accepting);
  }, [sync, accepting]);

  const setAnswer = (q: Question, v: AnswerValue, debounce: boolean) => {
    setValues((s) => ({ ...s, [q.question_id]: v }));
    sync.edit(q.question_id, v, debounce ? TEXT_DEBOUNCE_MS : CHOICE_DEBOUNCE_MS);
  };

  // Timer from server-side time: session clock samples (metrics/observations) or the backend's wall clock.
  // Re-anchored on every sample, frozen while paused; paused_total_ms comes from SessionInfo.
  const paused = session?.state === "paused";
  const elapsedMs = (() => {
    void tick;
    if (!session) return null;
    if (paused) return frozenElapsed.current;
    const tNow = live.sessionNow();
    if (tNow !== null && session.exam_started_t_ms !== null) return tNow - session.exam_started_t_ms - session.paused_total_ms;
    if (session.started_at) return live.serverNow() - Date.parse(session.started_at) - session.paused_total_ms;
    return null;
  })();
  if (!paused) frozenElapsed.current = elapsedMs;
  const remainingMs = exam && elapsedMs !== null ? exam.duration_s * 1000 - elapsedMs : null;
  const timeUp = remainingMs !== null && remainingMs <= 0;

  const health = live.health;
  const capture = health?.components.find((c) => c.component === "capture");
  const monitoringLimited = backendLost || (capture && capture.status !== "ok");

  const unsaved = sync.unsaved();
  const answered = exam ? exam.questions.filter((q) => isAnswered(values[q.question_id])).length : 0;

  const doFinish = async (force: boolean) => {
    setFinishing(true);
    setFinishErr(null);
    if (!force) {
      // Flush every pending answer first; finish only after the backend acknowledged them (or the user decides).
      await Promise.race([sync.flushAll(), new Promise((r) => setTimeout(r, FINISH_FLUSH_TIMEOUT_MS))]);
      const left = sync.unsaved();
      if (left.length > 0) {
        setFinishing(false);
        setUnsavedAtFinish(left);
        return;
      }
    }
    const r = await call(bridge.finishExam(sid));
    setFinishing(false);
    if (!r.ok) return setFinishErr(r.error);
    setDialog(null);
    setSession(r.data);
  };

  const emergencyExit = async () => {
    setFinishing(true);
    const r = await call(bridge.requestEmergencyExit(exitReason.trim() || "student_emergency_exit"));
    setFinishing(false);
    if (!r.ok) return setFinishErr(r.error);
    setDialog(null);
    const s = await call(bridge.getSession(sid));
    if (s.ok) setSession(s.data);
  };

  if (!session) return null;
  if (loadErr && !exam)
    return (
      <div className="screen">
        <ErrorBanner context="Загрузка заданий" error={loadErr} onRetry={() => void load()} />
        <Button onClick={() => void load()}>Повторить загрузку</Button>
      </div>
    );
  if (!exam) return <Spinner label="Загружаем задания…" />;

  const q = exam.questions[Math.min(idx, exam.questions.length - 1)];
  const disabled = paused || timeUp || session.state !== "running";
  const st = q ? sync.status(q.question_id) : null;
  const qTitle = (qid: string) => {
    const k = exam.questions.findIndex((x) => x.question_id === qid);
    return k >= 0 ? `№${k + 1}` : qid;
  };

  return (
    <div className="screen exam">
      <div className="exam-head">
        <div>
          <div className="eyebrow">{exam.is_demo ? "Демонстрационный тест" : "Экзамен"}</div>
          <h1 className="exam-title">{exam.title}</h1>
        </div>
        <div className={`timer ${remainingMs !== null && remainingMs < 60_000 ? "timer-low" : ""} ${paused ? "timer-paused" : ""}`}>
          <span className="timer-label">{paused ? "Пауза" : t("time_left")}</span>
          <span className="timer-value" role="timer" aria-live="off">
            {remainingMs === null ? "—" : clock(remainingMs)}
          </span>
        </div>
      </div>

      <div className="exam-progress" aria-label={`Отвечено ${answered} из ${exam.questions.length}`}>
        {exam.questions.map((qq, i) => {
          const a = isAnswered(values[qq.question_id]);
          const s = sync.status(qq.question_id)?.status;
          return (
            <button
              key={qq.question_id}
              type="button"
              className={`qpill ${i === idx ? "qpill-on" : ""} ${a ? "qpill-done" : ""} ${s === "error" ? "qpill-err" : ""}`}
              onClick={() => setIdx(i)}
              aria-current={i === idx ? "step" : undefined}
              aria-label={`${t("question")} ${i + 1}${a ? ", есть ответ" : ""}${s === "error" ? ", не сохранён" : ""}`}
            >
              {i + 1}
            </button>
          );
        })}
        <span className="muted small">
          отвечено {answered} из {exam.questions.length}
          {unsaved.length > 0 && ` · не сохранено: ${unsaved.length}`}
        </span>
      </div>

      {paused && <Banner tone="warn" title={t("monitoring_paused")}>Преподаватель приостановил экзамен. Время остановлено; ответы будут отправлены после продолжения.</Banner>}
      {session.source_mode === "replay" && live.captureHealth?.code === "replay_ended" && (
        <Banner tone="warn" title="REPLAY — запись закончилась" actions={<Button variant="primary" disabled={backendLost} onClick={() => setDialog("finish")}>Завершить экзамен</Button>}>
          Новые кадры больше не поступают. Завершите экзамен, чтобы открыть итог и отчёт в режиме преподавателя.
        </Banner>
      )}
      {timeUp && !paused && (
        <Banner tone="warn" title="Время вышло" actions={<Button variant="primary" onClick={() => setDialog("finish")}>{t("finish_exam")}</Button>}>
          Ответы больше не изменяются. Завершите экзамен.
        </Banner>
      )}
      {loadErr && exam && <ErrorBanner context="Сохранённые ответы не загружены" error={loadErr} onRetry={() => void load()} />}

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
            ) : st?.status === "waiting" ? (
              <span className="warn-text">Ответ сохранён в окне и будет отправлен, когда {paused ? "экзамен продолжится" : "связь восстановится"}.</span>
            ) : st?.status === "error" ? (
              <span className="danger-text">
                {t("not_saved")}: {st.error ? describeError(st.error) : ""}
                {st.error?.retryable ? " Повторим автоматически." : ""}{" "}
                <button type="button" className="link" onClick={() => void sync.flush(q.question_id)}>
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
          onClose={() => {
            setDialog(null);
            setUnsavedAtFinish(null);
          }}
          actions={
            unsavedAtFinish ? (
              <>
                <Button busy={finishing} disabled={backendLost} onClick={() => void doFinish(false)}>
                  Повторить сохранение
                </Button>
                <Button variant="danger" busy={finishing} disabled={backendLost} onClick={() => void doFinish(true)}>
                  Завершить без них
                </Button>
              </>
            ) : (
              <>
                <Button onClick={() => setDialog(null)}>Вернуться к заданиям</Button>
                <Button variant="primary" busy={finishing} disabled={backendLost} onClick={() => void doFinish(false)}>
                  Завершить
                </Button>
              </>
            )
          }
        >
          <p>
            Отвечено {answered} из {exam.questions.length}.
            {answered < exam.questions.length && " Неотвеченные вопросы останутся без ответа."}
          </p>
          {unsavedAtFinish ? (
            <Banner tone="danger" title={`Не сохранены ответы: ${unsavedAtFinish.map(qTitle).join(", ")}`}>
              Сервис не подтвердил запись. Повторите сохранение или завершите экзамен без этих ответов — тогда они не попадут в
              результат.
            </Banner>
          ) : (
            unsaved.length > 0 && (
              <Banner tone="warn" title={`Ещё не подтверждено ответов: ${unsaved.length}`}>
                Перед завершением отправим их и дождёмся подтверждения.
              </Banner>
            )
          )}
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
  value: AnswerValue | undefined;
  disabled: boolean;
  onChange: (v: AnswerValue, debounce: boolean) => void;
}) {
  if (q.kind === "short_text") {
    const v = typeof value === "string" ? value : "";
    const max = Math.min(q.max_length ?? 4000, 4000);
    return (
      <div className="field">
        <textarea
          className="answer-text"
          value={v}
          disabled={disabled}
          maxLength={max}
          rows={3}
          aria-label="Ваш ответ"
          onChange={(e) => onChange(e.target.value, true)}
        />
        <span className="small muted counter">
          {v.length}/{max}
        </span>
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
