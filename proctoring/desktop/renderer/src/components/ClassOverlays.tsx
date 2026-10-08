// Class mode (A07-student): teacher lock screen, microphone banner, class connection block.
// Data: class_state from the C2 uplink on /v1/stream (LiveStore.classState). The student cannot close the lock
// screen or hide the microphone banner; camera and monitoring keep running underneath (nothing is stopped here).
import { useEffect, useRef } from "react";
import type { ClassState } from "../lib/classState";
import { CONNECTION_RU, parseClassHealth } from "../lib/classState";
import { Badge, Card } from "./ui";

export function LockScreen({ state }: { state: ClassState }) {
  const ref = useRef<HTMLDivElement>(null);
  const request = state.lock_request;
  const requested = request?.locked && Date.parse(request.expires_at) > Date.now();
  const reason = requested ? request.reason_ru : state.lock_reason_ru ?? state.lock_requested_reason_ru;
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    ref.current?.focus();
    // keep keyboard focus inside the lock screen (the app underneath is also inert)
    const keep = (e: FocusEvent) => {
      if (ref.current && e.target instanceof Node && !ref.current.contains(e.target)) ref.current.focus();
    };
    document.addEventListener("focusin", keep);
    return () => {
      document.removeEventListener("focusin", keep);
      if (previous?.isConnected && !previous.closest("[inert]")) previous.focus();
    };
  }, []);
  return (
    <div className="lockscreen" data-adal-lock data-lock-token={requested ? request.request_token : undefined} role="alertdialog" aria-modal="true" aria-labelledby="lock-title" aria-describedby="lock-reason" tabIndex={-1} ref={ref}>
      <div className="lockscreen-card">
        <div className="lockscreen-icon" aria-hidden="true">⏸</div>
        <h1 id="lock-title">Преподаватель приостановил ваш экзамен</h1>
        <p id="lock-reason" className="lockscreen-reason">
          {reason ? (
            <>
              Причина: <b data-lock-reason>{reason}</b>
            </>
          ) : (
            "Причина не указана."
          )}
        </p>
        <p className="lockscreen-note">
          Работа в Adal приостановлена до команды преподавателя. Эта команда ограничивает экран приложения;
          состояние камеры и наблюдения не меняется.
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
export function ClassBlock({ state, health }: { state: ClassState | null; health?: unknown }) {
  const local = parseClassHealth(health);
  if (!state) {
    return (
      <Card title="Класс" className="classblock">
        <p className="small">
          <Badge tone="neutral">{local.configured === false ? "нет сервера" : "нет данных"}</Badge>{" "}
          {local.configured === false
            ? "Сервер класса не настроен. Экзамен проходит локально. Для подключения обратитесь к преподавателю."
            : "Состояние подключения к классу ещё не получено. Ожидаем сведения от локального сервиса."}
        </p>
        <p className="small">Компьютер: <b data-testid="class-computer">{local.computerName ?? "имя пока недоступно"}</b></p>
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
        <dd data-testid="class-computer">{state.computer_name ?? local.computerName ?? "имя пока недоступно"}</dd>
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
