// Class mode (A07-student): teacher lock screen, microphone banner, class connection block.
// Data: class_state from the C2 uplink on /v1/stream (LiveStore.classState). The student cannot close the lock
// screen or hide the microphone banner; camera and monitoring keep running underneath (nothing is stopped here).
import { useEffect, useRef } from "react";
import type { ClassState } from "../lib/classState";
import { CONNECTION_RU } from "../lib/classState";
import { Badge, Card } from "./ui";

export function LockScreen({ state }: { state: ClassState }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    // keep keyboard focus inside the lock screen (the app underneath is also inert)
    const keep = (e: FocusEvent) => {
      if (ref.current && e.target instanceof Node && !ref.current.contains(e.target)) ref.current.focus();
    };
    document.addEventListener("focusin", keep);
    return () => document.removeEventListener("focusin", keep);
  }, []);
  return (
    <div className="lockscreen" role="alertdialog" aria-modal="true" aria-labelledby="lock-title" aria-describedby="lock-reason" tabIndex={-1} ref={ref}>
      <div className="lockscreen-card">
        <div className="lockscreen-icon" aria-hidden="true">⏸</div>
        <h1 id="lock-title">Преподаватель приостановил ваш экзамен</h1>
        <p id="lock-reason" className="lockscreen-reason">
          {state.lock_reason_ru ? (
            <>
              Причина: <b>{state.lock_reason_ru}</b>
            </>
          ) : (
            "Причина не указана."
          )}
        </p>
        <p className="lockscreen-note">
          Оставайтесь на месте. Камера и наблюдение продолжают работать. Экран снимет преподаватель — закрыть его
          самостоятельно нельзя.
        </p>
      </div>
    </div>
  );
}

export function MicBanner({ state }: { state: ClassState }) {
  const dir = state.audio_direction === "talk" ? " (преподаватель говорит с вами)" : state.audio_direction === "both" ? " (двусторонняя связь)" : "";
  return (
    <div className="micbanner" role="status" aria-live="assertive">
      <span className="micbanner-icon" aria-hidden="true">🎙</span>
      <b>Микрофон включён преподавателем для проверки</b>
      <span className="micbanner-dir">{dir}</span>
    </div>
  );
}

/** "Класс" block for the preparation screen. Status only: the uplink is configured by QORGAU_CLASS_SERVER/CODE. */
export function ClassBlock({ state }: { state: ClassState | null }) {
  if (!state) {
    return (
      <Card title="Класс" className="classblock">
        <p className="small">
          <Badge tone="neutral">нет данных</Badge> Состояние подключения к классу ещё не получено. Если компьютер не
          подключён к классу, экзамен проходит локально — это нормально.
        </p>
      </Card>
    );
  }
  const c = CONNECTION_RU[state.connection];
  return (
    <Card title="Класс" className="classblock" aside={<Badge tone={c.tone}>{c.text}</Badge>}>
      <dl className="classblock-list small">
        <dt>Связь</dt>
        <dd data-testid="class-connection">{c.text}</dd>
        <dt>Сервер класса</dt>
        <dd className="mono">{state.server || "—"}</dd>
        <dt>Компьютер</dt>
        <dd>{state.computer_name ?? "—"}</dd>
        {state.student_id && (
          <>
            <dt>Номер у преподавателя</dt>
            <dd className="mono">{state.student_id}</dd>
          </>
        )}
        {state.exam?.title && (
          <>
            <dt>Экзамен</dt>
            <dd>{state.exam.title}</dd>
          </>
        )}
      </dl>
      {state.message_ru && <p className="small">{state.message_ru}</p>}
      {state.connection === "rejected" && <p className="small">Попросите преподавателя проверить код подключения и перезапустите приложение.</p>}
    </Card>
  );
}
