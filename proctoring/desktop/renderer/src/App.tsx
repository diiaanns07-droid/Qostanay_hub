import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { ShellState } from "@contracts/bridge";
import type { ApiErrorBody, SessionInfo } from "@contracts/qorgau-v1.generated";
import type { BridgeChoice } from "./bridge/selectBridge";
import { FIXTURE_OPERATOR_PIN, type FixtureBridge, type FixtureFaults } from "./bridge/fixtureBridge";
import { AppContext, useApp, type AppApi } from "./lib/appContext";
import { LangContext, translate, type Lang, type MsgKey } from "./lib/i18n";
import { LiveStore } from "./lib/liveStore";
import { call } from "./lib/result";
import { Banner, Button, Dialog, ErrorBanner, SourceModeBadge } from "./components/ui";
import { PreflightScreen } from "./screens/Preflight";
import { CalibrationScreen } from "./screens/Calibration";
import { ExamScreen } from "./screens/Exam";
import { OperatorScreen } from "./screens/Operator";
import { StudentDone, SummaryScreen } from "./screens/Summary";

const HEALTH_POLL_MS = 5000;
const TERMINAL = new Set(["finished", "aborted", "failed"]);

type Step = 0 | 1 | 2 | 3 | 4;
const STEPS: MsgKey[] = ["step_preflight", "step_calibration", "step_exam", "step_review", "step_summary"];

export function App({ choice }: { choice: BridgeChoice }) {
  if (choice.kind === "missing") return <MissingBridge reason={choice.reason} />;
  return <Main bridge={choice.bridge} fixture={choice.kind === "fixture" ? choice.bridge : null} />;
}

function MissingBridge({ reason }: { reason: string }) {
  return (
    <div className="fatal">
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
  const sessionRef = useRef<SessionInfo | null>(null);

  const setSession = useCallback((s: SessionInfo | null) => {
    const prev = sessionRef.current;
    // Ignore stale snapshots of another session (stream may deliver late messages).
    if (s && prev && s.session_id !== prev.session_id && !TERMINAL.has(prev.state)) return;
    sessionRef.current = s;
    setSessionState(s);
  }, []);

  const backendLost = (shell !== null && shell.backend !== "ready") || healthDown;

  const resync = useCallback(async () => {
    const cur = sessionRef.current;
    if (!cur) return;
    const [s, inc] = await Promise.all([call(bridge.getSession(cur.session_id)), call(bridge.listIncidents(cur.session_id))]);
    if (s.ok) setSession(s.data);
    if (inc.ok) live.replaceIncidents(inc.data);
  }, [bridge, live, setSession]);

  // Shell state + restore the bound session after a renderer reload.
  useEffect(() => {
    let alive = true;
    void bridge.getShellState().then(async (st) => {
      if (!alive) return;
      setShell(st);
      if (st.session_id && !sessionRef.current) {
        const r = await call(bridge.getSession(st.session_id));
        if (r.ok && alive) {
          live.reset(r.data.session_id);
          setSession(r.data);
        }
      }
    });
    const unsub = bridge.onShellState((st) => setShell(st));
    return () => {
      alive = false;
      unsub();
    };
  }, [bridge, live, setSession]);

  // Event stream.
  useEffect(() => {
    live.onSession = (s) => {
      const cur = sessionRef.current;
      if (cur && s.session_id === cur.session_id) setSession(s);
    };
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
        live.health = live.health ?? r.data;
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
      sessionRef.current = null;
      setSessionState(null);
      live.reset(null);
      setTeacherTab("review");
    },
  };

  let step: Step = 0;
  let screen: ReactNode;
  const st = session?.state;
  if (!session || st === "created" || st === "preflight") {
    step = 0;
    screen = <PreflightScreen key={session?.session_id ?? "new"} />;
  } else if (st === "calibrating" || st === "ready") {
    step = 1;
    screen = <CalibrationScreen key={session.session_id} />;
  } else if (st === "running" || st === "paused") {
    step = 2;
    screen = teacher ? <OperatorScreen live /> : <ExamScreen key={session.session_id} />;
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
  const t = (k: MsgKey) => translate(lang, k);

  return (
    <LangContext.Provider value={lang}>
      <AppContext.Provider value={api}>
        <div className={`app ${examMode ? "app-exam" : ""} ${teacher ? "app-teacher" : ""}`}>
          {fixture && (
            <div className="fixture-strip" role="note">
              FIXTURE-режим: данные из FixtureBridge (контрактные fixtures и сценарий в памяти) — не backend, не камера, не CV.
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
              <span className={`conn ${backendLost ? "conn-down" : "conn-ok"}`} title={backendLost ? t("backend_lost") : "Локальный сервис на связи"}>
                <span className="conn-dot" aria-hidden="true" />
                <span className="conn-l">{backendLost ? "нет связи" : "на связи"}</span>
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

          {backendLost && (
            <div className="global-banner">
              <Banner tone="danger" title={t("backend_lost")}>
                {shell?.backend === "restarting" ? "Оболочка перезапускает сервис. " : ""}
                Действия временно недоступны; введённые ответы не теряются и будут отправлены после восстановления.
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
              fixture={!!fixture}
              examMode={examMode}
              onClose={() => setPinOpen(false)}
              onUnlocked={(s) => {
                setShell(s);
                setWantTeacher(true);
                setPinOpen(false);
              }}
            />
          )}
          {fixture && <FixturePanel fixture={fixture} />}
        </div>
      </AppContext.Provider>
    </LangContext.Provider>
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
        Qorgau <span>Exam</span>
      </span>
    </div>
  );
}

function PinDialog({
  fixture,
  examMode,
  onClose,
  onUnlocked,
}: {
  fixture: boolean;
  examMode: boolean;
  onClose: () => void;
  onUnlocked: (s: ShellState) => void;
}) {
  const { bridge } = useApp();
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiErrorBody | null>(null);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    const r = await call(bridge.operatorUnlock(pin));
    setBusy(false);
    if (!r.ok) return setErr(r.error);
    onUnlocked(r.data);
  };
  return (
    <Dialog
      title="Режим преподавателя"
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>Отмена</Button>
          <Button variant="primary" busy={busy} disabled={pin.length < 4} onClick={() => void submit()}>
            Открыть
          </Button>
        </>
      }
    >
      <p>
        {examMode
          ? "Идёт экзамен. Режим преподавателя открывается только по PIN, проверка выполняется оболочкой."
          : "Введите PIN преподавателя. Проверка выполняется оболочкой, а не интерфейсом."}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (pin.length >= 4) void submit();
        }}
      >
        <label className="field">
          <span>PIN</span>
          <input type="password" inputMode="numeric" autoComplete="off" value={pin} onChange={(e) => setPin(e.target.value)} maxLength={12} />
        </label>
      </form>
      {fixture && <p className="small muted">FIXTURE: PIN для демонстрации — {FIXTURE_OPERATOR_PIN}.</p>}
      {err && <ErrorBanner context="PIN" error={err} />}
    </Dialog>
  );
}

const FAULT_LABELS: Record<keyof FixtureFaults, string> = {
  backendDown: "Потеря связи с сервисом",
  cameraLost: "Потеря источника кадров",
  answerSaveFails: "Сбой сохранения ответов",
  calibrationFailsOnce: "Сбой точки «вверх» при калибровке (1 раз)",
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
          <p className="small">Только для FixtureBridge. Проверка реальных состояний ошибок интерфейса.</p>
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
          <p className="small muted">PIN преподавателя: {FIXTURE_OPERATOR_PIN}</p>
        </div>
      )}
    </aside>
  );
}
