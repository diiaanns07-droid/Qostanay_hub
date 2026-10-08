import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { ShellState } from "@contracts/bridge";
import type { ApiErrorBody, SessionInfo } from "@contracts/qorgau-v1.generated";
import type { BridgeChoice } from "./bridge/selectBridge";
import type { FixtureBridge, FixtureFaults } from "./bridge/fixtureBridge";
import { AppContext, useApp, type AppApi } from "./lib/appContext";
import { LangContext, translate, type Lang, type MsgKey } from "./lib/i18n";
import { LiveStore } from "./lib/liveStore";
import { can } from "./lib/permissions";
import { call } from "./lib/result";
import { describeError, shellCode } from "./lib/errors";
import { Banner, Button, Dialog, ErrorBanner, SourceModeBadge } from "./components/ui";
import { PreflightScreen } from "./screens/Preflight";
import { CalibrationScreen } from "./screens/Calibration";
import { ExamScreen } from "./screens/Exam";
import { OperatorScreen } from "./screens/Operator";
import { StudentDone, SummaryScreen } from "./screens/Summary";
import { LockScreen, MicBanner } from "./components/ClassOverlays";
import { useLive as useLiveVersion } from "./lib/liveStore";

const HEALTH_POLL_MS = 5000;

type Step = 0 | 1 | 2 | 3 | 4;
const STEPS: MsgKey[] = ["step_preflight", "step_calibration", "step_exam", "step_review", "step_summary"];

export function App({ choice }: { choice: BridgeChoice }) {
  if (choice.kind === "missing") return <MissingBridge reason={choice.reason} />;
  return <Main bridge={choice.bridge} fixture={choice.kind === "fixture" ? choice.bridge : null} />;
}

function MissingBridge({ reason }: { reason: string }) {
  return (
    <div className="fatal" role="alert">
      <Brand />
      <h1>Интерфейс не подключён к оболочке</h1>
      <p>{reason}</p>
      <p className="muted small">Данные-заглушки автоматически не подставляются.</p>
    </div>
  );
}

function Main({ bridge, fixture }: { bridge: AppApi["bridge"]; fixture: FixtureBridge | null }) {
  const live = useMemo(() => new LiveStore(), []);
  const [shell, setShell] = useState<ShellState | null>(null);
  const [session, setSessionState] = useState<SessionInfo | null>(null);
  const [healthDown, setHealthDown] = useState(false);
  const [wantTeacher, setWantTeacher] = useState(false);
  const [pinOpen, setPinOpen] = useState(false);
  const [teacherTab, setTeacherTab] = useState<"review" | "summary">("review");
  const [lang, setLang] = useState<Lang>("ru");
  /** The bound session disappeared or failed because the backend restarted (crash recovery). */
  const [lostSession, setLostSession] = useState<{ sid: string; why: string } | null>(null);
  const sessionRef = useRef<SessionInfo | null>(null);
  const shellRef = useRef<ShellState | null>(null);
  shellRef.current = shell;

  /** Updates of the current session only: a late response for a previous/other session is dropped. */
  const setSession = useCallback((s: SessionInfo) => {
    const cur = sessionRef.current;
    if (!cur || s.session_id !== cur.session_id) return;
    sessionRef.current = s;
    setSessionState(s);
  }, []);

  const bindSession = useCallback(
    (s: SessionInfo) => {
      if (sessionRef.current?.session_id !== s.session_id) live.reset(s.session_id);
      sessionRef.current = s;
      setSessionState(s);
    },
    [live],
  );

  const isCurrent = useCallback((sid: string) => sessionRef.current?.session_id === sid, []);

  const backendState = shell?.backend ?? null;
  const backendLost = (backendState !== null && backendState !== "ready") || healthDown;

  const resync = useCallback(async () => {
    const cur = sessionRef.current;
    if (!cur) return;
    const sid = cur.session_id;
    const s = await call(bridge.getSession(sid));
    if (!isCurrent(sid)) return;
    if (s.ok) {
      setSession(s.data);
      if (s.data.state === "failed" && s.data.last_error?.details?.recovered) {
        setLostSession({ sid, why: "Сервис был перезапущен после сбоя; сессия остановлена, сохранённые до сбоя данные доступны в итоге." });
      }
    } else if (s.error.code === "SESSION_NOT_FOUND") {
      setLostSession({ sid, why: "После перезапуска сервиса эта сессия ему неизвестна. Начните новую сессию." });
    }
    if (can(shellRef.current, "history", sessionRef.current?.state).ok && isCurrent(sid)) {
      const inc = await call(bridge.listIncidents(sid));
      if (inc.ok && isCurrent(sid)) live.applyRest(inc.data);
    }
  }, [bridge, live, setSession, isCurrent]);

  // Shell state + restore the bound session after a renderer reload.
  useEffect(() => {
    let alive = true;
    void bridge.getShellState().then(async (st) => {
      if (!alive) return;
      setShell(st);
      if (st.session_id && !sessionRef.current) {
        const r = await call(bridge.getSession(st.session_id));
        if (r.ok && alive && !sessionRef.current) bindSession(r.data);
      }
    });
    const unsub = bridge.onShellState((st) => setShell(st));
    return () => {
      alive = false;
      unsub();
    };
  }, [bridge, bindSession]);

  // Event stream.
  useEffect(() => {
    live.onSession = (s) => setSession(s);
    live.onResync = () => void resync();
    const unsub = bridge.subscribeEvents((env) => live.ingest(env));
    return () => {
      unsub();
      live.onSession = null;
      live.onResync = null;
    };
  }, [bridge, live, resync, setSession]);

  // Health ping: detects a lost backend even if the shell has not reported it yet; resyncs on recovery.
  useEffect(() => {
    let wasDown = false;
    const ping = async () => {
      const r = await call(bridge.health());
      if (r.ok) {
        if (!live.health) live.health = r.data;
        if (wasDown) void resync();
        wasDown = false;
        setHealthDown(false);
      } else {
        wasDown = true;
        setHealthDown(true);
      }
    };
    void ping();
    const t = setInterval(() => void ping(), HEALTH_POLL_MS);
    return () => clearInterval(t);
  }, [bridge, live, resync]);

  // Backend became ready again (restart after a crash) → re-read the session; it may be gone or failed.
  const prevBackend = useRef(backendState);
  useEffect(() => {
    if (prevBackend.current && prevBackend.current !== "ready" && backendState === "ready") void resync();
    prevBackend.current = backendState;
  }, [backendState, resync]);

  // Unlock is cleared by the shell when an exam starts → fall back to the student view.
  useEffect(() => {
    if (shell && !shell.operator_unlocked) setWantTeacher(false);
  }, [shell?.operator_unlocked]);

  const teacher = !!shell?.operator_unlocked && wantTeacher;

  const api: AppApi = {
    bridge,
    fixture,
    live,
    shell,
    session,
    setSession,
    bindSession,
    isCurrent,
    backendLost,
    role: teacher ? "teacher" : "student",
    requestTeacher: () => {
      if (shell?.operator_unlocked) setWantTeacher(true);
      else setPinOpen(true);
    },
    leaveTeacher: () => {
      setWantTeacher(false);
      void bridge.operatorLock().then(setShell);
    },
    newSession: () => {
      setLostSession(null);
      sessionRef.current = null;
      setSessionState(null);
      live.reset(null);
      setTeacherTab("review");
    },
  };

  let step: Step = 0;
  let screen: ReactNode;
  const st = session?.state;
  const lostHere = !!lostSession && session?.session_id === lostSession.sid && st !== "failed";
  if (lostHere && lostSession) {
    // The backend no longer knows this session: nothing on it can be continued; offer a clean restart.
    screen = (
      <div className="screen">
        <div className="ready-panel">
          <h1>Сессия недоступна</h1>
          <p className="lead">{lostSession.why}</p>
          <p className="small muted mono">{lostSession.sid}</p>
          <Button variant="primary" onClick={api.newSession}>
            Новая сессия
          </Button>
        </div>
      </div>
    );
  } else if (!session || st === "created" || st === "preflight") {
    step = 0;
    screen = <PreflightScreen key={session?.session_id ?? "new"} />;
  } else if (st === "calibrating" || st === "ready") {
    step = 1;
    screen = <CalibrationScreen key={session.session_id} />;
  } else if (st === "running" || st === "paused") {
    step = 2;
    screen = teacher ? <OperatorScreen key={`live-${session.session_id}`} live /> : <ExamScreen key={session.session_id} />;
  } else if (teacher) {
    step = teacherTab === "review" ? 3 : 4;
    screen =
      teacherTab === "review" ? (
        <OperatorScreen key={`rev-${session.session_id}`} live={false} onSummary={() => setTeacherTab("summary")} />
      ) : (
        <SummaryScreen key={`sum-${session.session_id}`} onReview={() => setTeacherTab("review")} />
      );
  } else {
    step = 4;
    screen = <StudentDone />;
  }

  const examMode = !!shell?.exam_mode_active;
  useLiveVersion(live);
  const cls = live.classState;
  const locked = !!cls?.locked;
  const mic = !!cls?.mic_active;
  const t = (k: MsgKey) => translate(lang, k);
  const shellErr = shell?.last_error ?? null;

  return (
    <LangContext.Provider value={lang}>
      <AppContext.Provider value={api}>
        {mic && cls && <MicBanner state={cls} />}
        {locked && cls && <LockScreen state={cls} />}
        <div className={`app ${examMode ? "app-exam" : ""} ${teacher ? "app-teacher" : ""} ${mic ? "app-mic" : ""}`} inert={locked || undefined} aria-hidden={locked || undefined}>
          {fixture && (
            <div className="fixture-strip" role="note">
              FIXTURE-режим: данные из FixtureBridge (контрактные fixtures и сценарий в памяти) — не backend, не камера, не CV.
              Это не интеграционная проверка.
            </div>
          )}
          <header className="topbar">
            <Brand />
            <nav className="stepper" aria-label="Этапы">
              <ol>
                {STEPS.map((k, i) => (
                  <li key={k} className={i === step ? "on" : i < step ? "done" : ""} aria-current={i === step ? "step" : undefined}>
                    <span className="step-n">{i + 1}</span>
                    <span className="step-l">{t(k)}</span>
                  </li>
                ))}
              </ol>
            </nav>
            <div className="topbar-right">
              <SourceModeBadge mode={session?.source_mode ?? null} fixture={!!fixture} />
              <span
                className={`conn ${backendLost ? (backendState === "starting" ? "conn-wait" : "conn-down") : "conn-ok"}`}
                title={backendLost ? t("backend_lost") : "Локальный сервис на связи"}
              >
                <span className="conn-dot" aria-hidden="true" />
                <span className="conn-l">{backendLost ? (backendState === "starting" ? "запуск…" : "нет связи") : "на связи"}</span>
              </span>
              {teacher ? (
                <Button size="sm" variant="ghost" onClick={api.leaveTeacher} title={t("lock_teacher")}>
                  {t("role_teacher")} ✕
                </Button>
              ) : (
                <Button size="sm" variant="ghost" onClick={api.requestTeacher}>
                  {t("teacher_mode")}
                </Button>
              )}
              <label className="lang">
                <span className="sr-only">Язык интерфейса</span>
                <select value={lang} onChange={(e) => setLang(e.target.value as Lang)}>
                  <option value="ru">RU</option>
                  <option value="kk" title={t("lang_draft")}>KK*</option>
                </select>
              </label>
            </div>
          </header>

          {backendLost && <BackendBanner state={backendState} healthDown={healthDown} />}
          {lostSession && session?.session_id === lostSession.sid && !lostHere && (
            <div className="global-banner">
              <Banner tone="warn" title="Сессия прервана перезапуском сервиса" actions={<Button size="sm" onClick={api.newSession}>Новая сессия</Button>}>
                {lostSession.why}
              </Banner>
            </div>
          )}
          {/* Backend outages have their own banner; this one is for shell failures (e.g. exam mode not engaged). */}
          {shellErr && shell?.mode === "error" && shellCode(shellErr) !== "backend_unavailable" && (
            <div className="global-banner">
              <Banner tone="danger" title={shellCode(shellErr) === "enforcement_error" ? "Режим экзамена не включился" : "Ошибка оболочки"}>
                {describeError(shellErr)} <span className="small">({shellErr.message})</span> Ограничения сняты.
              </Banner>
            </div>
          )}
          {session?.last_error && (
            <div className="global-banner">
              <ErrorBanner context="Сессия" error={session.last_error} />
            </div>
          )}

          <main className="main" id="main">
            {screen}
          </main>

          {pinOpen && (
            <PinDialog
              fixturePin={fixture?.operatorPin ?? null}
              examMode={examMode}
              onClose={() => setPinOpen(false)}
              onUnlocked={(s) => {
                setShell(s);
                setWantTeacher(true);
                setPinOpen(false);
              }}
            />
          )}
        </div>
        {fixture && <FixturePanel fixture={fixture} />}
      </AppContext.Provider>
    </LangContext.Provider>
  );
}

function BackendBanner({ state, healthDown }: { state: ShellState["backend"] | null; healthDown: boolean }) {
  const keep = "Введённые ответы не теряются: они хранятся в окне и будут отправлены после восстановления.";
  let tone: "info" | "warn" | "danger" = "danger";
  let title = "Нет связи с локальным сервисом";
  let text = `Действия временно недоступны. ${keep}`;
  if (state === "starting") {
    tone = "info";
    title = "Запуск локального сервиса…";
    text = "Проверки и экзамен станут доступны, когда сервис ответит.";
  } else if (state === "restarting") {
    tone = "warn";
    title = "Сервис перезапускается";
    text = `Оболочка перезапускает локальный сервис; ограничения экзамена на это время сняты. ${keep}`;
  } else if (state === "failed") {
    title = "Локальный сервис не запускается";
    text = "Оболочка исчерпала попытки перезапуска. Закройте и снова откройте приложение; сообщите преподавателю.";
  } else if (state === "stopped") {
    title = "Локальный сервис остановлен";
  } else if (healthDown) {
    text = `Сервис не отвечает на проверку состояния. ${keep}`;
  }
  return (
    <div className="global-banner">
      <Banner tone={tone} title={title} role={tone === "danger" ? "alert" : "status"}>
        {text}
      </Banner>
    </div>
  );
}

function Brand() {
  return (
    <div className="brand">
      <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
        <path d="M16 2 4 6.5v8.7C4 23 9.2 28.3 16 30c6.8-1.7 12-7 12-14.8V6.5L16 2Z" fill="var(--accent)" />
        <path d="m10.5 16.2 3.8 3.8 7.4-7.6" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="brand-name">
        ADAL
      </span>
    </div>
  );
}

/** PIN format as validated by the shell: 4–64 letters/digits. The check itself (and its rate limit) is in main. */
const PIN_RE = /^[0-9A-Za-z]{4,64}$/;

function PinDialog({
  fixturePin,
  examMode,
  onClose,
  onUnlocked,
}: {
  fixturePin: string | null;
  examMode: boolean;
  onClose: () => void;
  onUnlocked: (s: ShellState) => void;
}) {
  const { bridge } = useApp();
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiErrorBody | null>(null);
  const valid = PIN_RE.test(pin);
  const notConfigured = shellCode(err) === "operator_pin_not_configured";
  const submit = async () => {
    if (!valid) return;
    setBusy(true);
    setErr(null);
    const r = await call(bridge.operatorUnlock(pin));
    setBusy(false);
    if (!r.ok) {
      setPin("");
      return setErr(r.error);
    }
    onUnlocked(r.data);
  };
  return (
    <Dialog
      title="Режим преподавателя"
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>Отмена</Button>
          <Button variant="primary" busy={busy} disabled={!valid || notConfigured} onClick={() => void submit()}>
            Открыть
          </Button>
        </>
      }
    >
      <p>
        {examMode
          ? "Идёт экзамен. Режим преподавателя открывается только по PIN; журнал и решения во время экзамена закрыты — доступны пауза и завершение."
          : "Введите PIN преподавателя. Проверка и ограничение числа попыток выполняются оболочкой, а не интерфейсом."}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label className="field">
          <span>PIN</span>
          <input
            type="password"
            autoComplete="off"
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/\s/g, ""))}
            maxLength={64}
            aria-invalid={pin.length > 0 && !valid}
            aria-describedby="pin-hint"
          />
        </label>
        <p id="pin-hint" className="hint">
          4–64 символа: цифры или латинские буквы.
        </p>
      </form>
      {fixturePin && <p className="small muted">FIXTURE: одноразовый PIN этой вкладки — {fixturePin}.</p>}
      {err && <ErrorBanner context="PIN" error={err} />}
    </Dialog>
  );
}

const FAULT_LABELS: Record<keyof FixtureFaults, string> = {
  backendDown: "Потеря связи с сервисом",
  cameraLost: "Потеря источника кадров",
  answerSaveFails: "Сбой сохранения ответов (диск)",
  calibrationFailsOnce: "Сбой точки «вверх» при калибровке (1 раз)",
  slowSaves: "Медленное сохранение (гонка запросов)",
};

function FixturePanel({ fixture }: { fixture: FixtureBridge }) {
  const [open, setOpen] = useState(false);
  const [, force] = useState(0);
  return (
    <aside className={`fx-panel ${open ? "fx-open" : ""}`} aria-label="Имитация сбоев FIXTURE">
      <button type="button" className="fx-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        FIXTURE · сбои
      </button>
      {open && (
        <div className="fx-body">
          <p className="small">Только для FixtureBridge. Проверка состояний ошибок интерфейса, не интеграция.</p>
          {(Object.keys(FAULT_LABELS) as Array<keyof FixtureFaults>).map((k) => (
            <label key={k} className="check small">
              <input
                type="checkbox"
                checked={fixture.faults[k]}
                onChange={(e) => {
                  fixture.setFault(k, e.target.checked);
                  force((n) => n + 1);
                }}
              />
              {FAULT_LABELS[k]}
            </label>
          ))}
          <p className="small"><b>Класс (FIXTURE, не сервер класса)</b></p>
          <label className="check small">
            <input
              type="checkbox"
              checked={fixture.classState.locked === true}
              onChange={(e) => {
                fixture.emitClassState({ locked: e.target.checked, lock_reason_ru: e.target.checked ? "Телефон на столе (FIXTURE)" : null });
                force((n) => n + 1);
              }}
            />
            Заблокировать экзамен
          </label>
          <label className="check small">
            <input
              type="checkbox"
              checked={fixture.classState.mic_active === true}
              onChange={(e) => {
                fixture.emitClassState({ mic_active: e.target.checked, audio_direction: e.target.checked ? "listen" : null });
                force((n) => n + 1);
              }}
            />
            Микрофон включён преподавателем
          </label>
          <label className="small">
            Связь с классом{" "}
            <select
              aria-label="Связь с классом (FIXTURE)"
              value={String(fixture.classState.connection)}
              onChange={(e) => {
                fixture.emitClassState({ connection: e.target.value, message_ru: e.target.value === "rejected" ? "Сервер класса отклонил код подключения. Экзамен продолжается локально." : null });
                force((n) => n + 1);
              }}
            >
              {["connected", "connecting", "reconnecting", "rejected", "stopped"].map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
          <Button size="sm" onClick={() => fixture.emitContract11Incidents()}>Эпизоды 1.1 (FIXTURE)</Button>
          <p className="small muted">PIN преподавателя (только эта вкладка): {fixture.operatorPin}</p>
        </div>
      )}
    </aside>
  );
}
