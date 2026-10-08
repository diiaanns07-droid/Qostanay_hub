// Teacher console. live=true during running/paused (preview, sources, controls); live=false for post-exam review.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ApiErrorBody, IncidentCategory, ReviewStatus, SessionSummary } from "@contracts/qorgau-v1.generated";
import { IncidentCategoryValues } from "@contracts/qorgau-v1.generated";
import { useApp } from "../lib/appContext";
import { useLive } from "../lib/liveStore";
import { can } from "../lib/permissions";
import { call } from "../lib/result";
import { CATEGORY, COMPONENT, ENFORCEMENT, ENV_ACTION, HEALTH, PRIORITY_SHORT, REVIEW_STATUS, RULE, SESSION_STATE } from "../lib/labels";
import { clock, duration, num, sessionT } from "../lib/format";
import { Badge, Banner, Button, Card, Dialog, Dot, ErrorBanner, Spinner, type Tone } from "../components/ui";
import { PreviewPanel } from "../components/PreviewPanel";
import { Timeline } from "../components/Timeline";
import { IncidentCard } from "../components/IncidentCard";

type ReviewFilter = "all" | "pending" | "reviewed";

export function OperatorScreen({ live: isLive, onSummary }: { live: boolean; onSummary?: () => void }) {
  const { bridge, session, setSession, isCurrent, live, backendLost, shell } = useApp();
  const ver = useLive(live);
  const history = can(shell, "history", session?.state);
  const reviewPerm = can(shell, "review", session?.state);
  const restAllowed = history.ok && !backendLost;
  const sid = session?.session_id ?? "";
  const [loadErr, setLoadErr] = useState<ApiErrorBody | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [cats, setCats] = useState<Set<IncidentCategory>>(new Set(IncidentCategoryValues));
  const [rf, setRf] = useState<ReviewFilter>("all");
  const [dialog, setDialog] = useState<null | "pause" | "finish" | "abort">(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<null | "pause" | "resume" | "finish" | "abort">(null);
  const [actErr, setActErr] = useState<ApiErrorBody | null>(null);
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [now, setNow] = useState(Date.now());

  // REST (A08) is the source of truth for reviews/materials. The shell closes it during the exam (exam mode):
  // then only the stream (A05) is shown and nothing is requested that the bridge would reject.
  const [restSeq, setRestSeq] = useState(0);
  const loadSeq = useRef(0);
  const load = useCallback(async () => {
    if (!restAllowed) {
      setLoaded(true);
      return;
    }
    const mySeq = ++loadSeq.current;
    setLoadErr(null);
    const r = await call(bridge.listIncidents(sid));
    if (mySeq !== loadSeq.current || !isCurrent(sid)) return; // a newer refresh or another session won
    setLoaded(true);
    if (r.ok) {
      live.applyRest(r.data);
      setRestSeq((n) => n + 1);
    } else setLoadErr(r.error);
    if (!isLive || session?.state === "paused") {
      const s = await call(bridge.getSummary(sid));
      if (s.ok && mySeq === loadSeq.current && isCurrent(sid)) setSummary(s.data);
    }
  }, [bridge, sid, live, isLive, restAllowed, isCurrent, session?.state]);

  useEffect(() => {
    void load();
  }, [load]);

  // Stream changed an incident → re-read storage (debounced) when the shell allows it.
  useEffect(() => {
    if (!restAllowed || live.restStaleSeq === 0) return;
    const t = setTimeout(() => void load(), 600);
    return () => clearTimeout(t);
  }, [live.restStaleSeq, restAllowed, load]);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const all = useMemo(() => live.incidents(), [live, ver]);
  const filtered = all.filter(
    (i) => cats.has(i.category) && (rf === "all" || (rf === "pending" ? i.review_status === "pending" : i.review_status !== "pending")),
  );
  const sel = selected ? live.get(selected) : undefined;

  // Review mode: open the first episode that still waits for a decision.
  useEffect(() => {
    if (isLive || selected || all.length === 0) return;
    setSelected((all.find((i) => i.review_status === "pending") ?? all[0])!.incident_id);
  }, [isLive, selected, all]);

  if (!session) return null;
  const startMs = session.exam_started_t_ms ?? 0;
  const lastIncEnd = all.reduce((m, i) => Math.max(m, i.t_end_ms ?? i.t_start_ms + i.duration_ms), startMs);
  const elapsedWall = session.started_at
    ? (session.finished_at ? Date.parse(session.finished_at) : now) - Date.parse(session.started_at)
    : 0;
  const endMs = Math.max(lastIncEnd, startMs + elapsedWall, live.metrics?.t_session_ms ?? 0);

  const control = async (kind: "pause" | "resume" | "finish" | "abort") => {
    setBusy(kind);
    setActErr(null);
    const r =
      kind === "pause"
        ? await call(bridge.pauseExam(sid, { reason: reason.trim() || "operator_pause" }))
        : kind === "resume"
          ? await call(bridge.resumeExam(sid))
          : kind === "finish"
            ? await call(bridge.finishExam(sid))
            : await call(bridge.abortExam(sid, { reason: reason.trim() || "operator_abort" }));
    setBusy(null);
    if (!r.ok) return setActErr(r.error);
    // Close only the dialog this action came from (another dialog may have been opened meanwhile).
    setDialog((d) => (d === kind ? null : d));
    if (kind !== "resume") setReason("");
    setSession(r.data);
  };

  const pending = all.filter((i) => i.review_status === "pending").length;
  const counts = IncidentCategoryValues.map((c) => [c, all.filter((i) => i.category === c).length] as const);

  return (
    <div className={`screen operator ${isLive ? "operator-live" : "operator-review"}`}>
      <div className="screen-head">
        <div>
          <h1>{isLive ? "Наблюдение за сессией" : "Проверка эпизодов"}</h1>
          <p className="lead">
            Эпизоды — это наблюдения для проверки, а не обвинения. Приоритет показывает очередь проверки, решение
            принимает преподаватель.
          </p>
        </div>
        <div className="head-actions">
          <Badge tone={session.state === "running" ? "accent" : session.state === "paused" ? "warn" : "neutral"}>{SESSION_STATE[session.state]}</Badge>
          {isLive && session.state === "running" && (
            <Button disabled={backendLost} onClick={() => setDialog("pause")}>
              Пауза
            </Button>
          )}
          {isLive && session.state === "paused" && (
            <Button variant="primary" busy={busy === "resume"} disabled={backendLost || busy !== null} onClick={() => void control("resume")}>
              Продолжить
            </Button>
          )}
          {isLive && (
            <>
              <Button variant="primary" disabled={backendLost} onClick={() => setDialog("finish")}>
                Завершить экзамен
              </Button>
              <Button variant="danger" disabled={backendLost} onClick={() => setDialog("abort")}>
                Прервать
              </Button>
            </>
          )}
          {!isLive && onSummary && (
            <Button variant="primary" onClick={onSummary}>
              К итогу →
            </Button>
          )}
        </div>
      </div>

      {actErr && <ErrorBanner context="Управление сессией" error={actErr} onDismiss={() => setActErr(null)} />}
      {loadErr && <ErrorBanner context="Список эпизодов" error={loadErr} onRetry={() => void load()} />}
      {session.state === "paused" && (
        <Banner tone="warn" title="Пауза">Наблюдение не учитывается, ответы не принимаются, ограничения среды сняты до продолжения. Период войдёт в отчёт как пробел.</Banner>
      )}
      {!history.ok && (
        <Banner tone="info" title="Идёт экзамен: показан только поток событий">
          Оболочка закрывает журнал, решения и материалы, пока действуют ограничения экзамена. Эпизоды ниже пришли из потока
          и ещё не сверены с хранилищем; принять решение можно на паузе или после завершения.
        </Banner>
      )}
      {live.seqGaps > 0 && isLive && (
        <Banner tone="info">
          Поток событий терял сообщения ({live.seqGaps}).{" "}
          {history.ok ? "Список эпизодов перечитан из хранилища." : "Список будет сверен с хранилищем после паузы или завершения."}
        </Banner>
      )}

      <div className="op-grid">
        {isLive && (
          <div className="op-left stack">
            <Card title="Источник" className="card-tight">
              <PreviewPanel sessionId={sid} />
            </Card>
            <SourcesCard />
          </div>
        )}

        <div className="op-main stack">
          <Card
            title="Таймлайн"
            className="card-tight"
            aside={
              <span className="small muted">
                {all.length} эп. · ожидают проверки: {pending} · {clock(endMs - startMs)}
              </span>
            }
          >
            <div className="filters" role="group" aria-label="Фильтры">
              {counts.map(([c, n]) => (
                <button
                  key={c}
                  type="button"
                  className={`chip chip-${c} ${cats.has(c) ? "chip-on" : ""}`}
                  aria-pressed={cats.has(c)}
                  onClick={() =>
                    setCats((s) => {
                      const next = new Set(s);
                      if (next.has(c)) next.delete(c);
                      else next.add(c);
                      return next;
                    })
                  }
                >
                  {CATEGORY[c]} <span className="chip-n">{n}</span>
                </button>
              ))}
              <span className="spacer" />
              <label className="select small">
                <span className="sr-only">Статус проверки</span>
                <select value={rf} onChange={(e) => setRf(e.target.value as ReviewFilter)}>
                  <option value="all">все решения</option>
                  <option value="pending">ожидают проверки</option>
                  <option value="reviewed">проверенные</option>
                </select>
              </label>
            </div>
            <Timeline
              incidents={filtered}
              startMs={startMs}
              endMs={endMs}
              gaps={summary?.gaps ?? []}
              selected={selected}
              onSelect={setSelected}
              categories={cats}
            />
          </Card>

          <div className="op-split">
            <Card title="Эпизоды" className="card-tight list-card">
              {!loaded && <Spinner label="Загружаем эпизоды…" />}
              {loaded && filtered.length === 0 && (
                <p className="empty">
                  {all.length === 0
                    ? isLive
                      ? "Эпизодов пока нет. Это не значит «нарушений нет» — смотрите состояние источников."
                      : "Эпизодов за сессию не зафиксировано. Учитывайте покрытие наблюдения в итоге."
                    : "Нет эпизодов под выбранные фильтры."}
                </p>
              )}
              <ul className="inc-list">
                {filtered.map((i) => (
                  <li key={i.incident_id}>
                    <button
                      type="button"
                      className={`inc-item ${selected === i.incident_id ? "inc-sel" : ""}`}
                      onClick={() => setSelected(i.incident_id)}
                      aria-current={selected === i.incident_id || undefined}
                    >
                      <span className={`cat-mark cat-${i.category}`} aria-hidden="true" />
                      <span className="inc-main">
                        <span className="inc-title">
                          {RULE[i.rule_id]}
                          {i.state === "open" && <span className="live-dot" title="идёт сейчас" />}
                        </span>
                        <span className="inc-sub small muted">
                          {sessionT(i.t_start_ms - startMs)} · {duration(i.duration_ms)} · приоритет {PRIORITY_SHORT[i.priority]}
                        </span>
                      </span>
                      {live.hasRest(i.incident_id) ? (
                        <span className={`rs rs-${i.review_status}`}>{REVIEW_STATUS[i.review_status]}</span>
                      ) : (
                        <span className="rs rs-stream" title="Статус решения известен только хранилищу">из потока</span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
            <Card className="card-tight detail-card">
              {sel ? (
                <IncidentCard
                  sessionId={sid}
                  incident={sel}
                  fromStorage={live.hasRest(sel.incident_id)}
                  restAllowed={restAllowed}
                  restReason={history.reason}
                  reviewReason={reviewPerm.reason}
                  refreshKey={restSeq}
                  examStartMs={startMs}
                  retainMedia={session.retain_media}
                  onReviewed={() => void load()}
                />
              ) : (
                <div className="empty-detail">
                  <strong>Выберите эпизод</strong>
                  <span>в списке или на таймлайне, чтобы увидеть причины, длительность, кадры и записать решение.</span>
                </div>
              )}
            </Card>
          </div>
        </div>
      </div>

      {dialog && (
        <Dialog
          title={dialog === "pause" ? "Приостановить экзамен" : dialog === "finish" ? "Завершить экзамен для студента?" : "Прервать сессию?"}
          tone={dialog === "abort" ? "danger" : "neutral"}
          onClose={() => setDialog(null)}
          actions={
            <>
              <Button onClick={() => setDialog(null)}>Отмена</Button>
              <Button
                variant={dialog === "abort" ? "danger" : "primary"}
                busy={busy === dialog}
                disabled={backendLost || (busy !== null && busy !== dialog)}
                onClick={() => void control(dialog)}
              >
                {dialog === "pause" ? "Пауза" : dialog === "finish" ? "Завершить" : "Прервать"}
              </Button>
            </>
          }
        >
          {dialog === "pause" && <p>На паузе наблюдение не учитывается, ответы не принимаются, ограничения среды снимаются.</p>}
          {dialog === "finish" && <p>Открытые эпизоды будут закрыты, захват кадров остановлен. Ответы сохранены.</p>}
          {dialog === "abort" && <p>Сессия завершится как прерванная. Используйте при технической проблеме.</p>}
          {dialog !== "finish" && (
            <label className="field">
              <span>Причина (в отчёт)</span>
              <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={200} />
            </label>
          )}
          {actErr && <ErrorBanner context="Управление" error={actErr} />}
        </Dialog>
      )}
    </div>
  );
}

function SourcesCard() {
  const { live, backendLost } = useApp();
  useLive(live);
  const h = live.health;
  const m = live.metrics;
  const toneOf = (s: string): Tone => (s === "ok" ? "ok" : s === "degraded" || s === "starting" ? "warn" : "danger");
  return (
    <Card title="Состояние источников" className="card-tight">
      {backendLost && <Banner tone="danger" title="Связь с сервисом потеряна">Данные ниже могут быть устаревшими.</Banner>}
      {!h ? (
        <p className="muted small">Сервис ещё не прислал состояние компонентов.</p>
      ) : (
        <ul className="comp-list compact">
          {h.components.map((c) => (
            <li key={c.component} title={c.message}>
              <Dot tone={toneOf(c.status)} />
              <span className="comp-name">{COMPONENT[c.component]}</span>
              <span className="comp-status">{HEALTH[c.status]}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="metrics">
        <div>
          <span className="metric-v">{m ? num(m.capture_fps) : "—"}</span>
          <span className="metric-k">кадр/с захват</span>
        </div>
        <div>
          <span className="metric-v">{m ? String(m.frames_dropped) : "—"}</span>
          <span className="metric-k">потеряно кадров</span>
        </div>
        <div>
          <span className="metric-v">{m?.e2e_latency_ms_p95 != null ? `${Math.round(m.e2e_latency_ms_p95)} мс` : "—"}</span>
          <span className="metric-k">задержка p95</span>
        </div>
      </div>
      {m && m.e2e_latency_ms_p95 === null && <p className="small muted">Задержка не измеряется этим источником.</p>}
      {live.envEvents.length > 0 && (
        <>
          <h3 className="subhead">События среды</h3>
          <ul className="env-list">
            {live.envEvents
              .slice(-5)
              .reverse()
              .map((e) => (
                <li key={e.observation_id} className="small">
                  <span className="mono">{sessionT(e.t_session_ms)}</span> {ENV_ACTION[e.action]} — {ENFORCEMENT[e.enforcement]}
                </li>
              ))}
          </ul>
        </>
      )}
    </Card>
  );
}
