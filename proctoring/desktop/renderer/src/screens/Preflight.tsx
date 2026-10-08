import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ApiErrorBody,
  EnvironmentCapabilities,
  HealthReport,
  PreflightCheck,
  PreflightReport,
  SourceMode,
} from "@contracts/qorgau-v1.generated";
import { useApp } from "../lib/appContext";
import { useLive } from "../lib/liveStore";
import { ClassBlock } from "../components/ClassOverlays";
import { ExamChecks } from "../components/ExamChecks";
import { call } from "../lib/result";
import { CAPABILITY, CHECK, CHECK_STATUS, COMPONENT, ENV_ACTION, HEALTH, HEALTH_CODE, SOURCE_MODE_RU } from "../lib/labels";
import { Badge, Banner, Button, Card, Dialog, Dot, ErrorBanner, SourceModeBadge, Spinner, type Tone } from "../components/ui";

const CONSENT_VERSION = "consent-ru-1";
const DEFAULT_EXAM_ID = "demo-exam-1";

const checkTone = (c: PreflightCheck): Tone =>
  c.status === "pass" ? "ok" : c.status === "fail" ? (c.required ? "danger" : "warn") : c.status === "warn" ? "warn" : "neutral";

/** `onDeskScan`: A15 — after the checks the student goes to «Осмотр рабочего места», then to calibration. */
export function PreflightScreen({ onDeskScan }: { onDeskScan: (sessionId: string) => void }) {
  const { bridge, shell, session, setSession, bindSession, isCurrent, backendLost, role, requestTeacher, live } = useApp();
  useLive(live);
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [caps, setCaps] = useState<EnvironmentCapabilities | null | { error: ApiErrorBody }>(null);
  const capsTries = useRef(0);
  const [loadErr, setLoadErr] = useState<ApiErrorBody | null>(null);
  const [report, setReport] = useState<PreflightReport | null>(null);
  const [busy, setBusy] = useState<null | "create" | "preflight" | "skip" | "abort">(null);
  const [actionErr, setActionErr] = useState<{ ctx: string; error: ApiErrorBody } | null>(null);
  const [skipOpen, setSkipOpen] = useState(false);

  // form
  const [mode, setMode] = useState<SourceMode>(bridge.transport === "fixture" ? "synthetic" : "live");
  const [replayId, setReplayId] = useState("");
  const [label, setLabel] = useState("");
  const [examId, setExamId] = useState(DEFAULT_EXAM_ID);
  const [retainMedia, setRetainMedia] = useState(false);
  const [consent, setConsent] = useState(false);

  const loadEnv = useCallback(async () => {
    setLoadErr(null);
    const [h, c] = await Promise.all([call(bridge.health()), call(bridge.getEnvironmentCapabilities())]);
    if (h.ok) setHealth(h.data);
    else setLoadErr(h.error);
    setCaps(c.ok ? c.data : { error: c.error });
  }, [bridge]);

  useEffect(() => {
    // The renderer can appear before the child service is ready. Refresh after recovery too,
    // so a startup/restart error never requires a manual retry once the service is available.
    if (shell?.backend === "ready" && !backendLost) void loadEnv();
  }, [loadEnv, shell?.backend, backendLost]);

  // The shell measures capabilities at startup (self-test); "not measured yet" is retryable → poll briefly.
  useEffect(() => {
    if (!caps || !("error" in caps) || !caps.error.retryable || capsTries.current >= 20) return;
    const t = setTimeout(async () => {
      capsTries.current += 1;
      const c = await call(bridge.getEnvironmentCapabilities());
      setCaps(c.ok ? c.data : { error: c.error });
    }, 3000);
    return () => clearTimeout(t);
  }, [caps, bridge]);

  const runPreflight = useCallback(
    async (sid: string) => {
      setBusy("preflight");
      setActionErr(null);
      const r = await call(bridge.runPreflight(sid));
      if (!isCurrent(sid)) return; // cancelled / another session meanwhile
      setBusy(null);
      if (!r.ok) return setActionErr({ ctx: "Проверка", error: r.error });
      setReport(r.data);
      const s = await call(bridge.getSession(sid));
      if (s.ok) setSession(s.data);
      await loadEnv();
    },
    [bridge, setSession, isCurrent, loadEnv],
  );

  // A session restored after reload/reconnect in created/preflight: re-run checks so the report is current.
  useEffect(() => {
    if (session && (session.state === "created" || session.state === "preflight") && !report && busy === null) {
      void runPreflight(session.session_id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.session_id]);

  const create = async () => {
    setBusy("create");
    setActionErr(null);
    const r = await call(
      bridge.createSession({
        source: {
          mode,
          camera_index: 0,
          replay_id: mode === "replay" ? replayId.trim() : null,
          width: 640,
          height: 480,
          fps: 30,
        },
        exam_id: examId.trim(),
        student_label: label.trim() || null,
        consent: { accepted: true, text_version: CONSENT_VERSION, accepted_at: new Date().toISOString() },
        retain_media: retainMedia,
      }),
    );
    setBusy(null);
    if (!r.ok) return setActionErr({ ctx: "Создание сессии", error: r.error });
    bindSession(r.data);
    await runPreflight(r.data.session_id);
  };

  const skip = async (reason: string) => {
    if (!session) return;
    setBusy("skip");
    const r = await call(bridge.calibrationSkip(session.session_id, { reason }));
    if (!r.ok) {
      setBusy(null);
      return setActionErr({ ctx: "Пропуск калибровки", error: r.error });
    }
    const s = await call(bridge.getSession(session.session_id));
    setBusy(null);
    setSkipOpen(false);
    if (s.ok) setSession(s.data);
  };

  const abort = async () => {
    if (!session) return;
    setBusy("abort");
    const r = await call(bridge.abortExam(session.session_id, { reason: "cancelled_at_preflight" }));
    setBusy(null);
    if (!r.ok) return setActionErr({ ctx: "Отмена сессии", error: r.error });
    setSession(r.data);
  };

  const sessionActive = session && (session.state === "created" || session.state === "preflight");
  const shownMode = sessionActive ? session.source_mode : mode;
  const requiredFailed = report?.checks.filter((c) => c.required && c.status !== "pass") ?? [];
  const formValid = consent && examId.trim().length > 0 && (mode !== "replay" || replayId.trim().length > 0);
  const latestHealth = live.health && (!health || Date.parse(live.health.server_time) > Date.parse(health.server_time)) ? live.health : health;

  return (
    <div className="screen preflight">
      <div className="screen-head">
        <div>
          <h1>Подготовка к экзамену</h1>
          <p className="lead">
            Укажите имя, ознакомьтесь с условиями и проверьте готовность компьютера к экзамену.
          </p>
        </div>
        <SourceModeBadge mode={shownMode} fixture={bridge.transport === "fixture"} />
      </div>

      {shownMode !== "live" && (
        <Banner tone="warn" title={shownMode === "synthetic" ? "SYNTHETIC · тест без камеры" : "REPLAY · записанное видео"}>
          {shownMode === "synthetic" ? "Наблюдения сгенерированы для проверки приложения. Это не результаты работы камеры." : "Используется выбранная запись, а не камера в реальном времени."}
        </Banner>
      )}
      {loadErr && <ErrorBanner context="Состояние сервиса" error={loadErr} onRetry={() => void loadEnv()} />}
      {actionErr && <ErrorBanner context={actionErr.ctx} error={actionErr.error} onDismiss={() => setActionErr(null)} />}

      <div className="preflight-layout">
        <div className="stack">
          {!sessionActive ? (
            <Card title="Перед началом">
              <label className="field preflight-name">
                <span>Как вас назвать? <span className="muted">Необязательно</span></span>
                <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Имя или псевдоним" maxLength={64} autoComplete="off" />
                <span className="hint">Используйте имя или метку, согласованную с преподавателем.</span>
              </label>

              <div className="preflight-privacy">
                <h3>Что нужно знать</h3>
                <p>Приложение анализирует кадры камеры и события окна экзамена. Наблюдения, эпизоды и ответы сохраняются на этом компьютере. Результаты проверяет преподаватель.</p>
                <p>При подключении к классу преподавателю передаются статусы, эпизоды и превью. Короткие клипы эпизодов передаются по его запросу.</p>
              </div>
              <label className="check">
                <input type="checkbox" checked={retainMedia} onChange={(e) => setRetainMedia(e.target.checked)} />
                <span>Сохранять отдельные кадры-доказательства для эпизодов <span className="muted">(необязательно)</span></span>
              </label>
              <label className="check check-strong">
                <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                <span>Студент ознакомлен с тем, что обрабатывается и сохраняется, и согласен начать.</span>
              </label>
              <div className="actions preflight-primary">
                <Button variant="primary" size="lg" busy={busy === "create" || busy === "preflight"} disabled={!formValid || backendLost} onClick={() => void create()}>
                  Проверить устройства
                </Button>
                <span className="hint">{!consent ? "Для проверки нужно согласие участника." : "После проверки экзамен ещё не начнётся."}</span>
              </div>

              <details className="preflight-details preflight-settings">
                <summary>Дополнительные настройки</summary>
                <p className="hint">Для обычного экзамена оставьте камеру. Запись и синтетический режим нужны для проверки приложения.</p>
                <fieldset className="field">
                  <legend>Источник кадров</legend>
                  <div className="segmented" role="radiogroup">
                    {(["live", "replay", "synthetic"] as const).map((m) => (
                      <label key={m} className={`seg ${mode === m ? "seg-on" : ""}`}>
                        <input type="radio" name="mode" value={m} checked={mode === m} onChange={() => setMode(m)} />
                        {SOURCE_MODE_RU[m]}
                      </label>
                    ))}
                  </div>
                  <p className="hint">
                    {mode === "live" && "Камера этого компьютера. Требует все модели и защиту среды; без них начать нельзя."}
                    {mode === "replay" && "Заранее записанное видео проходит через тот же конвейер. Везде помечается как REPLAY."}
                    {mode === "synthetic" && "Тестовый режим без камеры и без CV. Везде помечается как SYNTHETIC."}
                  </p>
                </fieldset>

                {mode === "replay" && (
                  <label className="field">
                    <span>Идентификатор записи</span>
                    <input value={replayId} onChange={(e) => setReplayId(e.target.value)} placeholder="например, demo-phone-01" />
                  </label>
                )}
                <label className="field">
                  <span>Идентификатор экзамена</span>
                  <input value={examId} onChange={(e) => setExamId(e.target.value)} />
                </label>
                <p className="hint">Версия условий: {CONSENT_VERSION}</p>
              </details>
              {consent && !formValid && (
                <Banner tone="warn" title="Заполните дополнительные настройки">
                  {!examId.trim() ? "Не указан идентификатор экзамена. " : ""}
                  {mode === "replay" && !replayId.trim() ? "Для REPLAY нужен идентификатор записи." : ""}
                </Banner>
              )}
            </Card>
          ) : (
            <Card
              title="Проверка готовности"
            >
              {busy === "preflight" && !report && <Spinner label="Выполняем проверки…" />}
              {report && (
                <>
                  {report.ready ? (
                    <Banner tone="ok" title="Обязательные проверки пройдены">
                      Предупреждения ниже не мешают начать, но отражаются в отчёте.
                    </Banner>
                  ) : (
                    <Banner tone="danger" title="Начать нельзя: не пройдены обязательные проверки">
                      {requiredFailed.map((c) => CHECK[c.check_id]).join(", ") || "см. список ниже"}
                    </Banner>
                  )}
                  <ul className="checks">
                    {report.checks.map((c) => (
                      <li key={c.check_id} className={`check-row tone-${checkTone(c)}`}>
                        <Dot tone={checkTone(c)} />
                        <div className="check-main">
                          <div className="check-name">
                            {CHECK[c.check_id]}
                            {c.required ? <Badge tone="neutral">обязательно</Badge> : <span className="muted small">необязательно</span>}
                          </div>
                          <div className="check-msg">{c.message_ru}</div>
                        </div>
                        <div className={`check-status status-${c.status}`}>{CHECK_STATUS[c.status]}</div>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              <div className="actions">
                <Button
                  variant="primary"
                  size="lg"
                  disabled={!report?.ready || backendLost}
                  onClick={() => onDeskScan(session.session_id)}
                  title={!report?.ready ? "Сначала должны пройти обязательные проверки" : undefined}
                >
                  К осмотру рабочего места
                </Button>
                <Button busy={busy === "preflight"} disabled={backendLost} onClick={() => void runPreflight(session.session_id)}>
                  Проверить снова
                </Button>
                <Button
                  variant="ghost"
                  disabled={!report?.ready || backendLost}
                  onClick={() => (role === "teacher" ? setSkipOpen(true) : requestTeacher())}
                  title="Решение преподавателя: потребуется PIN"
                >
                  Без калибровки…
                </Button>
                <span className="spacer" />
                <Button variant="danger" busy={busy === "abort"} disabled={backendLost} onClick={() => void abort()}>
                  Отменить сессию
                </Button>
              </div>
            </Card>
          )}
        </div>

        <div className="stack preflight-sidebar">
          <ExamChecks health={loadErr ? null : latestHealth} caps={caps && !("error" in caps) ? caps : null}
            mode={sessionActive ? session.source_mode : mode} report={sessionActive ? report : null} backendLost={backendLost} />
          <ClassBlock state={live.classState} health={health} />
          <details className="preflight-details preflight-diagnostics">
            <summary>Диагностика компьютера {health && <Badge tone={health.overall === "ok" ? "ok" : health.overall === "degraded" ? "warn" : "danger"}>{HEALTH[health.overall]}</Badge>}</summary>
            <Card title="Компоненты" aside={health && <Badge tone={health.overall === "ok" ? "ok" : health.overall === "degraded" ? "warn" : "danger"}>{HEALTH[health.overall]}</Badge>}>
              {!health && !loadErr && <Spinner label="Запрашиваем состояние…" />}
              {!health && loadErr && <p className="muted">Нет данных о компонентах.</p>}
              {health && (
                <ul className="comp-list">
                  {health.components.map((c) => {
                    const tone: Tone = c.status === "ok" ? "ok" : c.status === "degraded" || c.status === "starting" ? "warn" : "danger";
                    return (
                      <li key={c.component}>
                        <Dot tone={tone} />
                        <span className="comp-name">{COMPONENT[c.component]}</span>
                        <span className="comp-status">{HEALTH[c.status]}</span>
                        <span className="comp-msg" title={c.message || undefined}>
                          {HEALTH_CODE[c.code] ?? c.message}
                          {typeof c.details.errors === "number" ? ` · ошибок: ${c.details.errors}` : ""}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
              {health && <p className="small muted">Версия сервиса {health.backend_version} · контракт {health.contract_version}</p>}
            </Card>
          </details>

          <Card title="Готовность компьютера">
            {caps === null && <Spinner label="Запрашиваем возможности оболочки…" />}
            {caps && "error" in caps && (
              <Banner tone={caps.error.retryable ? "info" : "warn"} title={caps.error.retryable ? "Оболочка ещё измеряет возможности защиты" : "Оболочка не сообщила возможности защиты"}>
                {caps.error.retryable && capsTries.current < 20 ? "Повторим запрос автоматически. " : ""}
                Для LIVE-сессии это обязательная проверка.
              </Banner>
            )}
            {caps && !("error" in caps) && (
              <>
                <ProtectionSummary caps={caps} />
                <details className="preflight-details">
                  <summary>Возможности защиты</summary>
                  <p className="small muted">
                    {caps.platform} · оболочка {caps.shell_version} · режим экзамена {caps.exam_mode_supported ? "поддерживается" : "не поддерживается"}
                  </p>
                  <table className="table">
                    <thead>
                      <tr>
                        <th scope="col">Действие</th>
                        <th scope="col">Статус</th>
                        <th scope="col">Примечание</th>
                      </tr>
                    </thead>
                    <tbody>
                      {caps.items.map((it) => (
                        <tr key={it.action}>
                          <td>{ENV_ACTION[it.action]}</td>
                          <td>
                            <Badge tone={it.status === "blocked" ? "ok" : it.status === "detected_only" ? "info" : it.status === "unverified" ? "warn" : "neutral"}>
                              {CAPABILITY[it.status]}
                            </Badge>
                          </td>
                          <td className="small">{it.note_ru ?? "—"}{it.verified_on ? ` · проверено: ${it.verified_on}` : ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="small muted">Перечислено только то, что сообщила оболочка. Остальные действия не заявляются как защищённые.</p>
                </details>
              </>
            )}
          </Card>
        </div>
      </div>

      {skipOpen && <SkipDialog busy={busy === "skip"} onClose={() => setSkipOpen(false)} onSkip={(r) => void skip(r)} />}
    </div>
  );
}

export function SkipDialog({ busy, onClose, onSkip }: { busy: boolean; onClose: () => void; onSkip: (reason: string) => void }) {
  const [reason, setReason] = useState("");
  return (
    <Dialog
      title="Начать без калибровки"
      tone="warn"
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>Отмена</Button>
          <Button variant="warn" busy={busy} disabled={reason.trim().length < 3} onClick={() => onSkip(reason.trim())}>
            Пропустить калибровку
          </Button>
        </>
      }
    >
      <p>Без калибровки оценки направления взгляда будут помечены как некалиброванные. Причина попадёт в отчёт.</p>
      <label className="field">
        <span>Причина</span>
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} maxLength={200} placeholder="например, студент в очках с бликами" />
      </label>
    </Dialog>
  );
}

/** Honest summary of the capability matrix: partial protection stays visibly partial. */
function ProtectionSummary({ caps }: { caps: EnvironmentCapabilities }) {
  const n = (st: string) => caps.items.filter((i) => i.status === st).length;
  const blocked = n("blocked");
  const detected = n("detected_only");
  const open = n("unverified") + n("unsupported");
  if (!caps.exam_mode_supported) {
    return (
      <Banner tone="danger" title="Режим экзамена не поддерживается оболочкой на этом компьютере">
        LIVE-сессия не пройдёт проверку защиты среды.
      </Banner>
    );
  }
  if (open === 0 && detected === 0) {
    return <Banner tone="ok" title={`Все ${blocked} заявленных действий блокируются`}>Проверено оболочкой на этом компьютере.</Banner>;
  }
  return (
    <Banner tone="warn" title="Защита частичная">
      Блокируется: {blocked} · только фиксируется: {detected} · не проверено или не поддерживается: {open}. Непроверенные действия не
      считаются защищёнными.
    </Banner>
  );
}
