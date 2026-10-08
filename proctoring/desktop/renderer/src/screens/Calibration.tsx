import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiErrorBody, CalibrationState, CalibrationTarget } from "@contracts/qorgau-v1.generated";
import { CalibrationTargetValues } from "@contracts/qorgau-v1.generated";
import { useApp } from "../lib/appContext";
import { useLive } from "../lib/liveStore";
import { call } from "../lib/result";
import { CAL_MESSAGE, CAL_PHASE, SOURCE_MODE_RU, TARGET } from "../lib/labels";
import { Badge, Banner, Button, ErrorBanner, Spinner } from "../components/ui";
import { SkipDialog } from "./Preflight";
import { setWindowFullscreen } from "../lib/windowControl";

const POLL_MS = 600;

function newer(a: CalibrationState | null, b: CalibrationState | null): CalibrationState | null {
  if (!a) return b;
  if (!b) return a;
  return Date.parse(b.updated_at) >= Date.parse(a.updated_at) ? b : a;
}

const msg = (code: string | null) => (code ? (CAL_MESSAGE[code] ?? code) : null);

export function CalibrationScreen() {
  const { bridge, session, setSession, live, backendLost, role, requestTeacher } = useApp();
  useLive(live);
  const [polled, setPolled] = useState<CalibrationState | null>(session?.calibration ?? null);
  const [busy, setBusy] = useState<null | "target" | "finish" | "cancel" | "restart" | "skip" | "start">(null);
  const [error, setError] = useState<{ ctx: string; error: ApiErrorBody } | null>(null);
  const [skipOpen, setSkipOpen] = useState(false);
  const autoRequested = useRef<string | null>(null);
  /** Full-screen calibration starts only after the student pressed "Начать" (positioned, window full screen). */
  const [armed, setArmed] = useState(false);

  const sid = session?.session_id ?? "";
  const cal = newer(polled, live.calibration);
  const ready = session?.state === "ready";

  const refresh = useCallback(async () => {
    if (!sid) return;
    const r = await call(bridge.calibrationState(sid));
    if (r.ok) setPolled((p) => newer(p, r.data));
  }, [bridge, sid]);

  // Polling is a fallback to the stream; completion is always decided by the backend state.
  useEffect(() => {
    if (ready || backendLost) return;
    void refresh();
    const t = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(t);
  }, [refresh, ready, backendLost]);

  const syncSession = async () => {
    const s = await call(bridge.getSession(sid));
    if (s.ok) setSession(s.data);
  };

  const selectTarget = useCallback(
    async (t: CalibrationTarget) => {
      setBusy("target");
      setError(null);
      const r = await call(bridge.calibrationTarget(sid, t));
      setBusy(null);
      if (!r.ok) return setError({ ctx: `Точка «${TARGET[t]}»`, error: r.error });
      setPolled((p) => newer(p, r.data));
    },
    [bridge, sid],
  );

  // A calibration already in progress (renderer reload) continues without the intro.
  useEffect(() => {
    if (cal && cal.targets.some((t) => t.state !== "pending")) setArmed(true);
  }, [cal]);

  // Leave full screen when calibration is over or the screen is left (exam mode keeps its own full screen).
  useEffect(() => {
    if (ready) setWindowFullscreen(false);
  }, [ready]);
  useEffect(() => () => setWindowFullscreen(false), []);

  // Auto-advance: when no target is collecting, request the next pending one (order center→down).
  useEffect(() => {
    if (!armed || !cal || cal.phase !== "collecting" || busy || backendLost) return;
    // "up" is optional (laptop webcam above the screen): its failure must not stop the walk to "down".
    if (cal.targets.some((t) => t.state === "collecting" || (t.state === "failed" && t.target !== "up"))) return;
    const next = CalibrationTargetValues.find((t) => cal.targets.find((x) => x.target === t)?.state === "pending");
    if (!next) return;
    const key = `${cal.calibration_id}:${next}`;
    if (autoRequested.current === key) return;
    autoRequested.current = key;
    void selectTarget(next);
  }, [armed, cal, busy, backendLost, selectTarget]);

  const finish = async () => {
    setBusy("finish");
    setError(null);
    const r = await call(bridge.calibrationFinish(sid));
    if (!r.ok) {
      setBusy(null);
      return setError({ ctx: "Завершение калибровки", error: r.error });
    }
    setPolled((p) => newer(p, r.data));
    await syncSession();
    setBusy(null);
  };

  const restart = async () => {
    setBusy("restart");
    setError(null);
    autoRequested.current = null;
    const r = await call(bridge.calibrationStart(sid));
    if (!r.ok) {
      setBusy(null);
      return setError({ ctx: "Перезапуск калибровки", error: r.error });
    }
    setPolled(r.data);
    await syncSession();
    setBusy(null);
  };

  const cancel = async () => {
    setBusy("cancel");
    setError(null);
    const r = await call(bridge.calibrationCancel(sid));
    if (!r.ok) {
      setBusy(null);
      return setError({ ctx: "Отмена калибровки", error: r.error });
    }
    await syncSession();
    setBusy(null);
  };

  const skip = async (reason: string) => {
    setBusy("skip");
    const r = await call(bridge.calibrationSkip(sid, { reason }));
    if (!r.ok) {
      setBusy(null);
      return setError({ ctx: "Пропуск калибровки", error: r.error });
    }
    setSkipOpen(false);
    setPolled((p) => newer(p, r.data));
    await syncSession();
    setBusy(null);
  };

  const start = async () => {
    setBusy("start");
    setError(null);
    const r = await call(bridge.startExam(sid));
    setBusy(null);
    if (!r.ok) return setError({ ctx: "Начало экзамена", error: r.error });
    setSession(r.data);
  };

  if (!session) return null;

  if (ready) {
    const c = session.calibration;
    return (
      <div className="screen">
        <div className="ready-panel">
          <Badge tone={c.phase === "completed" ? "ok" : "warn"}>калибровка {CAL_PHASE[c.phase]}</Badge>
          <h1>Всё готово к началу</h1>
          <p className="lead">
            После начала окно экзамена займёт экран, а наблюдение будет идти до завершения. Отвечайте спокойно:
            смотреть в задания, на клавиатуру или в черновик — нормально.
          </p>
          <ul className="ready-list">
            <li>Источник: {SOURCE_MODE_RU[session.source_mode]}</li>
            <li>Ответы сохраняются автоматически.</li>
            <li>Завершить экзамен можно в любой момент кнопкой «Завершить экзамен».</li>
            {c.phase === "skipped" && <li>Калибровка пропущена — оценки взгляда будут помечены как некалиброванные.</li>}
          </ul>
          {error && <ErrorBanner context={error.ctx} error={error.error} onDismiss={() => setError(null)} />}
          <Button variant="primary" size="lg" busy={busy === "start"} disabled={backendLost} onClick={() => void start()}>
            Начать экзамен
          </Button>
        </div>
      </div>
    );
  }

  const current = cal?.current_target ?? null;
  const curStatus = cal?.targets.find((t) => t.target === current) ?? null;
  const failed = cal?.targets.filter((t) => t.state === "failed") ?? [];
  const upSkipped = !!cal && cal.targets.some((t) => t.target === "up" && t.state === "failed");
  const allOk = !!cal && cal.targets.every((t) => t.state === "ok" || (t.target === "up" && t.state === "failed"));
  const okCount = cal?.targets.filter((t) => t.state === "ok").length ?? 0;

  const begin = () => {
    setWindowFullscreen(true);
    setArmed(true);
  };
  const progress = (t: CalibrationTarget) => {
    const st = cal?.targets.find((x) => x.target === t);
    return st && st.required_samples > 0 ? Math.min(1, st.samples / st.required_samples) : 0;
  };

  return (
    <div className="calfs" role="dialog" aria-modal="true" aria-label="Калибровка взгляда на весь экран">
      {CalibrationTargetValues.map((t) => {
        const st = cal?.targets.find((x) => x.target === t);
        const state = st?.state ?? "pending";
        const active = armed && t === current && state === "collecting";
        return (
          <span
            key={t}
            className={`calfs-dot calfs-${t} calfs-dot-${state} ${active ? "calfs-dot-active" : ""}`}
            style={{ ["--p" as string]: String(progress(t)) }}
            data-target={t}
            data-state={state}
            aria-hidden="true"
          >
            {state === "ok" && <span className="calfs-check">✓</span>}
          </span>
        );
      })}

      <div className="calfs-panel">
        <h1>Калибровка взгляда</h1>
        <p className="calfs-hint">Смотрите на точку глазами, голову держите прямо.</p>
        {error && <ErrorBanner context={error.ctx} error={error.error} onDismiss={() => setError(null)} />}
        {!cal && <Spinner label="Получаем состояние калибровки…" />}
        {cal && !armed && cal.phase === "collecting" && (
          <>
            <p className="small">
              Окно развернётся на весь экран, точки появятся у его краёв: в центре, слева, справа, сверху и снизу. Сядьте
              как на экзамене, лицо — в кадре камеры.
            </p>
            <div className="actions calfs-actions">
              <Button variant="primary" size="lg" disabled={backendLost} onClick={begin}>
                Начать калибровку
              </Button>
            </div>
          </>
        )}
        {cal && armed && (
          <>
            <div className="calfs-now" aria-live="polite">
              {curStatus && curStatus.state === "collecting" && (
                <>
                  Смотрите <b>{TARGET[curStatus.target]}</b> — собрано {curStatus.samples} из {curStatus.required_samples}
                </>
              )}
              {curStatus && curStatus.state === "failed" && <>Точка {TARGET[curStatus.target]} не собрана</>}
              {allOk && !upSkipped && <>Все точки собраны — нажмите «Завершить калибровку»</>}
              {allOk && upSkipped && <>Точка «вверх» необязательна: можно повторить её или нажать «Завершить калибровку»</>}
            </div>
            <ul className="calfs-list">
              {(cal.targets ?? []).map((t) => (
                <li key={t.target} className={`state-${t.state}`}>
                  <span className="calfs-list-name">{TARGET[t.target]}</span>
                  <span className="calfs-list-state">{t.state === "ok" ? "собрано ✓" : t.state === "failed" ? "не собрано" : t.state === "collecting" ? `${t.samples}/${t.required_samples}` : "ждёт"}</span>
                </li>
              ))}
            </ul>
          </>
        )}
        {cal?.phase === "failed" && (
          <Banner tone="danger" title="Калибровка не удалась">
            {msg(cal.message_code) ?? "Сервис не принял результат."} Можно начать заново или пропустить с указанием причины.
          </Banner>
        )}
        {(cal?.phase === "cancelled" || cal?.phase === "not_started") && <Banner tone="info" title="Калибровка не идёт">Нажмите «Начать заново».</Banner>}
        {cal?.message_code && cal.phase === "collecting" && <p className="small muted">{msg(cal.message_code)}</p>}
        {failed.map((t) => (
          <p key={t.target} className="calfs-fail">
            Точка «{TARGET[t.target]}» не собрана: {msg(t.message_code) ?? "сбор не удался"}
          </p>
        ))}
        {failed.length > 0 && <p className="small">Проверьте освещение и что в кадре только ваше лицо, затем повторите точку.</p>}
      </div>
      <div className="calfs-bar">
        <div className="actions calfs-actions">
          {failed.map((t) => (
            <Button key={t.target} size="sm" disabled={busy !== null || backendLost} onClick={() => void selectTarget(t.target)}>
              Повторить «{TARGET[t.target]}»
            </Button>
          ))}
          {armed && (
            <Button variant="primary" disabled={!allOk || backendLost || cal?.phase !== "collecting"} busy={busy === "finish"} onClick={() => void finish()}>
              Завершить калибровку
            </Button>
          )}
          {(cal?.phase === "failed" || cal?.phase === "cancelled" || cal?.phase === "not_started") && (
            <Button busy={busy === "restart"} disabled={backendLost} onClick={() => void restart()}>
              Начать заново
            </Button>
          )}
          <Button variant="ghost" disabled={backendLost} onClick={() => (role === "teacher" ? setSkipOpen(true) : requestTeacher())} title="Решение преподавателя: потребуется PIN">
            Пропустить…
          </Button>
          <Button variant="danger" busy={busy === "cancel"} disabled={backendLost || session.state !== "calibrating"} onClick={() => void cancel()}>
            Отменить
          </Button>
        </div>
        <p className="small muted">{okCount} из {CalibrationTargetValues.length} точек · {cal ? CAL_PHASE[cal.phase] : "…"}</p>
      </div>
      {skipOpen && <SkipDialog busy={busy === "skip"} onClose={() => setSkipOpen(false)} onSkip={(r) => void skip(r)} />}
    </div>
  );
}
