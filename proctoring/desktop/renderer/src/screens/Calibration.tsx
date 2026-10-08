import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiErrorBody, CalibrationState, CalibrationTarget } from "@contracts/qorgau-v1.generated";
import { CalibrationTargetValues } from "@contracts/qorgau-v1.generated";
import { useApp } from "../lib/appContext";
import { useLive } from "../lib/liveStore";
import { call } from "../lib/result";
import { CAL_MESSAGE, CAL_PHASE, SOURCE_MODE_RU, TARGET } from "../lib/labels";
import { ratio } from "../lib/format";
import { Badge, Banner, Button, Card, ErrorBanner, Progress, Spinner } from "../components/ui";
import { SkipDialog } from "./Preflight";

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

  // Auto-advance: when no target is collecting, request the next pending one (order center→down).
  useEffect(() => {
    if (!cal || cal.phase !== "collecting" || busy || backendLost) return;
    if (cal.targets.some((t) => t.state === "collecting" || t.state === "failed")) return;
    const next = CalibrationTargetValues.find((t) => cal.targets.find((x) => x.target === t)?.state === "pending");
    if (!next) return;
    const key = `${cal.calibration_id}:${next}`;
    if (autoRequested.current === key) return;
    autoRequested.current = key;
    void selectTarget(next);
  }, [cal, busy, backendLost, selectTarget]);

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
  const allOk = !!cal && cal.targets.every((t) => t.state === "ok");
  const okCount = cal?.targets.filter((t) => t.state === "ok").length ?? 0;

  return (
    <div className="screen calibration">
      <div className="screen-head">
        <div>
          <h1>Калибровка взгляда</h1>
          <p className="lead">
            Смотрите на светящуюся точку, не поворачивая корпус. Сбор завершается, когда сервис наберёт достаточно
            качественных кадров — не по таймеру.
          </p>
        </div>
        {cal && <Badge tone={cal.phase === "failed" ? "danger" : cal.phase === "collecting" ? "accent" : "neutral"}>{CAL_PHASE[cal.phase]}</Badge>}
      </div>

      {error && <ErrorBanner context={error.ctx} error={error.error} onDismiss={() => setError(null)} />}

      <div className="cal-layout">
        <div className={`cal-stage ${curStatus?.state === "failed" ? "cal-stage-failed" : ""}`} aria-hidden="true">
          {CalibrationTargetValues.map((t) => {
            const st = cal?.targets.find((x) => x.target === t);
            const active = t === current && st?.state === "collecting";
            return <span key={t} className={`cal-dot cal-${t} cal-dot-${st?.state ?? "pending"} ${active ? "cal-dot-active" : ""}`} />;
          })}
          {!cal && <Spinner label="Получаем состояние калибровки…" />}
        </div>

        <Card title="Ход калибровки" aside={<span className="muted">{okCount} из {CalibrationTargetValues.length}</span>}>
          <div className="sr-live" aria-live="polite">
            {curStatus && curStatus.state === "collecting" && `Смотрите ${TARGET[curStatus.target]}`}
            {curStatus && curStatus.state === "failed" && `Точка ${TARGET[curStatus.target]} не собрана`}
          </div>
          <ul className="cal-targets">
            {(cal?.targets ?? []).map((t) => (
              <li key={t.target} className={`cal-target state-${t.state}`}>
                <div className="cal-target-head">
                  <span className="cal-target-name">Взгляд {TARGET[t.target]}</span>
                  <span className="small muted">
                    {t.samples}/{t.required_samples} · качество {t.quality === null ? "—" : ratio(t.quality)}
                  </span>
                </div>
                <Progress
                  value={t.samples}
                  max={t.required_samples}
                  tone={t.state === "failed" ? "danger" : t.state === "ok" ? "ok" : "accent"}
                  label={`Точка ${TARGET[t.target]}`}
                />
                {t.state === "failed" && (
                  <div className="cal-fail">
                    <span>{msg(t.message_code) ?? "Сбор не удался"}</span>
                    <Button size="sm" disabled={busy !== null || backendLost} onClick={() => void selectTarget(t.target)}>
                      Повторить точку
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>

          {cal?.phase === "failed" && (
            <Banner tone="danger" title="Калибровка не удалась">
              {msg(cal.message_code) ?? "Сервис не принял результат."} Можно начать заново или пропустить с указанием причины.
            </Banner>
          )}
          {(cal?.phase === "cancelled" || cal?.phase === "not_started") && (
            <Banner tone="info" title="Калибровка не идёт">Нажмите «Начать заново».</Banner>
          )}
          {cal?.message_code && cal.phase === "collecting" && <p className="small muted">{msg(cal.message_code)}</p>}
          {failed.length > 0 && <p className="small">Проверьте освещение и что в кадре только ваше лицо, затем повторите точку.</p>}

          <div className="actions">
            <Button variant="primary" disabled={!allOk || backendLost || cal?.phase !== "collecting"} busy={busy === "finish"} onClick={() => void finish()}>
              Завершить калибровку
            </Button>
            {(cal?.phase === "failed" || cal?.phase === "cancelled" || cal?.phase === "not_started") && (
              <Button busy={busy === "restart"} disabled={backendLost} onClick={() => void restart()}>
                Начать заново
              </Button>
            )}
            <Button
              variant="ghost"
              disabled={backendLost}
              onClick={() => (role === "teacher" ? setSkipOpen(true) : requestTeacher())}
              title="Решение преподавателя: потребуется PIN"
            >
              Пропустить…
            </Button>
            <span className="spacer" />
            <Button variant="danger" busy={busy === "cancel"} disabled={backendLost || session.state !== "calibrating"} onClick={() => void cancel()}>
              Отменить калибровку
            </Button>
          </div>
        </Card>
      </div>
      {skipOpen && <SkipDialog busy={busy === "skip"} onClose={() => setSkipOpen(false)} onSkip={(r) => void skip(r)} />}
    </div>
  );
}
