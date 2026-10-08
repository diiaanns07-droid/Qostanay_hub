// Episode card: explanation from backend facts, timing, technical provenance, evidence, append-only human review.
// The incident body comes from the merged live store (stream + storage); reviews and materials only from
// storage (A08) via getIncident/getEvidence, and only when the shell allows it (not during exam mode).
import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiErrorBody, Incident, IncidentDetail, ReviewDecision } from "@contracts/qorgau-v1.generated";
import { ReviewDecisionValues } from "@contracts/qorgau-v1.generated";
import { useApp } from "../lib/appContext";
import { can } from "../lib/permissions";
import { call } from "../lib/result";
import { describeError } from "../lib/errors";
import { CATEGORY, DECISION, END_REASON, PRIORITY, REVIEW_STATUS, RULE, RULE_HINT, SOURCE_MODE } from "../lib/labels";
import { bytes, duration, fact, num, ratio, sessionT, wall, wallDate } from "../lib/format";
import { Badge, Banner, Button, ErrorBanner, KV, Spinner, type Tone } from "./ui";

const prioTone = { low: "neutral", medium: "warn", high: "warn" } as const;
const reviewTone: Record<string, Tone> = { pending: "warn", confirmed: "danger", dismissed: "ok", inconclusive: "neutral" };

let rememberedOperator = "";

type Media = { url: string } | { error: string };

export function IncidentCard({
  sessionId,
  incident,
  fromStorage,
  restAllowed,
  restReason,
  reviewReason,
  refreshKey,
  examStartMs,
  retainMedia,
  onReviewed,
}: {
  sessionId: string;
  incident: Incident;
  /** review_status/evidence_ids already confirmed by storage (A08). */
  fromStorage: boolean;
  restAllowed: boolean;
  restReason: string | null;
  reviewReason: string | null;
  refreshKey: number;
  examStartMs: number;
  retainMedia: boolean;
  onReviewed: () => void;
}) {
  const { bridge, backendLost, shell, session } = useApp();
  const incidentId = incident.incident_id;
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<ApiErrorBody | null>(null);
  const [decision, setDecision] = useState<ReviewDecision | null>(null);
  const [comment, setComment] = useState("");
  const [operator, setOperator] = useState(rememberedOperator);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<ApiErrorBody | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [media, setMedia] = useState<Record<string, Media>>({});
  const current = useRef(incidentId);
  current.current = incidentId;
  const evidencePerm = can(shell, "evidence", session?.state);

  const load = useCallback(async () => {
    if (!restAllowed) return;
    const asked = incidentId;
    const r = await call(bridge.getIncident(sessionId, asked));
    if (current.current !== asked) return; // user selected another episode meanwhile
    if (r.ok) {
      setDetail(r.data);
      setError(null);
    } else setError(r.error);
  }, [bridge, sessionId, incidentId, restAllowed]);

  useEffect(() => {
    setDetail(null);
    setError(null);
    setDecision(null);
    setComment("");
    setSaveErr(null);
    setSaved(null);
  }, [incidentId]);

  // Re-read storage when the episode changes in the stream, after a list refresh, or when access opens.
  useEffect(() => {
    void load();
  }, [load, incident.update_seq, refreshKey]);

  // Evidence media via the bridge (opaque ids, session ownership checked by backend). Operator only.
  const evidenceKey = detail ? detail.evidence.map((e) => e.evidence_id).join(",") : "";
  useEffect(() => {
    if (!detail || !evidencePerm.ok) return;
    let cancelled = false;
    for (const ev of detail.evidence) {
      if (media[ev.evidence_id] || ev.media_type !== "image/jpeg") continue;
      void call(bridge.getEvidence(sessionId, ev.evidence_id)).then((r) => {
        if (cancelled) return;
        if (r.ok) {
          const url = URL.createObjectURL(new Blob([r.data.bytes as BlobPart], { type: r.data.media_type }));
          setMedia((m) => ({ ...m, [ev.evidence_id]: { url } }));
        } else setMedia((m) => ({ ...m, [ev.evidence_id]: { error: describeError(r.error) } }));
      });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [evidenceKey, evidencePerm.ok]);

  const mediaRef = useRef(media);
  mediaRef.current = media;
  useEffect(
    () => () => {
      for (const m of Object.values(mediaRef.current)) if ("url" in m) URL.revokeObjectURL(m.url);
    },
    [],
  );

  const canReview = reviewReason === null && restAllowed && !backendLost;

  const submit = async () => {
    if (!decision || !canReview) return;
    const asked = incidentId;
    setSaving(true);
    setSaveErr(null);
    setSaved(null);
    rememberedOperator = operator.trim();
    const r = await call(bridge.addReview(sessionId, asked, { decision, comment: comment.trim(), operator: operator.trim() }));
    if (current.current !== asked) return;
    setSaving(false);
    if (!r.ok) return setSaveErr(r.error);
    // Only now (confirmed by storage) the decision is shown as recorded.
    setSaved(`Решение «${REVIEW_STATUS[r.data.decision]}» записано в хранилище (${wall(r.data.created_at)}).`);
    setDecision(null);
    setComment("");
    await load();
    onReviewed();
  };

  const i = incident;
  const hint = i.explanation.caveats_ru.length === 0 ? RULE_HINT[i.rule_id] : undefined;
  const reviews = detail?.reviews ?? [];

  return (
    <article className="incident" aria-labelledby="inc-title">
      <header className="incident-head">
        <div>
          <div className="eyebrow">
            {CATEGORY[i.category]} · {i.state === "open" ? "идёт сейчас" : "завершён"}
          </div>
          <h2 id="inc-title">{RULE[i.rule_id]}</h2>
        </div>
        <div className="incident-badges">
          <Badge tone={prioTone[i.priority]}>{PRIORITY[i.priority]}</Badge>
          {fromStorage ? (
            <Badge tone={reviewTone[i.review_status] ?? "neutral"}>{REVIEW_STATUS[i.review_status]}</Badge>
          ) : (
            <Badge tone="neutral" title="Статус проверки известен только хранилищу">решение: не сверено</Badge>
          )}
          <Badge tone={i.source_mode === "live" ? "accent" : "warn"}>{SOURCE_MODE[i.source_mode]}</Badge>
        </div>
      </header>

      <p className="incident-summary">{i.explanation.summary_ru}</p>

      <div className="incident-grid">
        <section>
          <h3>Факты</h3>
          {i.explanation.facts.length === 0 ? (
            <p className="muted small">Сервис не передал фактов для этого эпизода.</p>
          ) : (
            <KV items={i.explanation.facts.map((f) => [f.label_ru, fact(f)])} />
          )}
        </section>
        <section>
          <h3>Время</h3>
          <KV
            items={[
              ["Начало", `${sessionT(i.t_start_ms - examStartMs)} от начала экзамена · ${wall(i.wall_start)}`],
              ["Длительность", i.state === "open" ? `${duration(i.duration_ms)} (продолжается)` : duration(i.duration_ms)],
              ["Окончание", i.end_reason ? END_REASON[i.end_reason] : "—"],
              ["Наблюдений", String(i.observation_count)],
            ]}
          />
        </section>
      </div>

      {(i.explanation.caveats_ru.length > 0 || hint) && (
        <div className="caveats">
          <h3>Ограничения</h3>
          <ul>
            {hint && <li>{hint}</li>}
            {i.explanation.caveats_ru.map((c, k) => (
              <li key={k}>{c}</li>
            ))}
          </ul>
        </div>
      )}

      {error && <ErrorBanner context="Эпизод в хранилище" error={error} onRetry={() => void load()} />}

      <section>
        <h3>Кадры</h3>
        {!restAllowed ? (
          <p className="muted small">{restReason ?? "Материалы недоступны."}</p>
        ) : !detail && !error ? (
          <Spinner label="Загружаем материалы…" />
        ) : !detail ? null : detail.evidence.length === 0 ? (
          <p className="muted small">
            {retainMedia ? "Кадры для этого эпизода не сохранены." : "Сохранение кадров выключено для этой сессии (только метаданные)."}
            {i.trigger_frame_id !== null && ` Ключевой кадр: №${i.trigger_frame_id}.`}
          </p>
        ) : !evidencePerm.ok ? (
          <p className="muted small">Сохранено кадров: {detail.evidence.length}. {evidencePerm.reason}</p>
        ) : (
          <div className="evidence">
            {detail.evidence.map((ev) => {
              const m = media[ev.evidence_id];
              return (
                <figure key={ev.evidence_id}>
                  {m && "url" in m ? (
                    <img src={m.url} alt={`Кадр ${ev.frame_id ?? ""} эпизода`} />
                  ) : m ? (
                    <div className="evidence-err">{m.error}</div>
                  ) : ev.media_type === "image/jpeg" ? (
                    <Spinner label="Загрузка кадра" />
                  ) : (
                    <div className="evidence-err">Видео: просмотр в отчёте</div>
                  )}
                  <figcaption className="small muted">
                    кадр {ev.frame_id ?? "—"} · {sessionT(ev.t_session_ms - examStartMs)} · {bytes(ev.size_bytes)}
                  </figcaption>
                </figure>
              );
            })}
          </div>
        )}
      </section>

      <details className="provenance">
        <summary>Происхождение и версии</summary>
        <KV
          items={[
            ["Оценка детектора (макс.)", i.max_confidence === null ? "нет данных" : `${num(i.max_confidence, 2)} — не вероятность нарушения`],
            ["Качество входа (среднее)", ratio(i.mean_quality)],
            ["Правило", `${i.rule_id} · ${i.rule_version}`],
            ["Конфигурация", i.config_version],
            ["Эпизод", `${i.incident_id} · ревизия ${i.update_seq}`],
            ["Начало (wall-clock)", wallDate(i.wall_start)],
            ["Статус решения", fromStorage ? "из хранилища" : "из потока, не сверен с хранилищем"],
          ]}
        />
      </details>

      <section className="review">
        <h3>Решение преподавателя</h3>
        {reviews.length > 0 && (
          <ol className="review-history">
            {reviews.map((r) => (
              <li key={r.review_id}>
                <Badge tone={reviewTone[r.decision] ?? "neutral"}>{REVIEW_STATUS[r.decision]}</Badge>
                <span className="small">
                  {r.operator} · {wall(r.created_at)}
                  {r.supersedes_review_id && " · заменяет предыдущее"}
                </span>
                {r.comment && <p className="review-comment">{r.comment}</p>}
              </li>
            ))}
          </ol>
        )}
        {!canReview ? (
          <Banner tone="info" title="Решение сейчас записать нельзя">
            {reviewReason ?? restReason ?? (backendLost ? "Нет связи с локальным сервисом." : "Нет доступа к хранилищу.")}
          </Banner>
        ) : (
          <>
            <div className="decision-row" role="radiogroup" aria-label="Решение">
              {ReviewDecisionValues.map((d) => (
                <button
                  key={d}
                  type="button"
                  role="radio"
                  aria-checked={decision === d}
                  className={`decision decision-${d} ${decision === d ? "decision-on" : ""}`}
                  onClick={() => setDecision(d)}
                >
                  {DECISION[d]}
                </button>
              ))}
            </div>
            <div className="row-2">
              <label className="field">
                <span>Комментарий</span>
                <textarea rows={2} value={comment} maxLength={2000} onChange={(e) => setComment(e.target.value)} placeholder="что видно на кадрах, контекст" />
              </label>
              <label className="field">
                <span>Проверяющий</span>
                <input value={operator} maxLength={64} onChange={(e) => setOperator(e.target.value)} placeholder="инициалы или роль" />
              </label>
            </div>
            {i.state === "open" && <Banner tone="info">Эпизод ещё идёт — решение можно принять сейчас или после его завершения.</Banner>}
          </>
        )}
        {saveErr && <ErrorBanner context="Решение не записано" error={saveErr} onRetry={() => void submit()} />}
        {saved && (
          <Banner tone="ok" role="status">
            {saved}
          </Banner>
        )}
        {canReview && (
          <div className="actions">
            <Button variant="primary" busy={saving} disabled={!decision || !operator.trim()} onClick={() => void submit()}>
              {reviews.length ? "Записать новое решение" : "Записать решение"}
            </Button>
            {!operator.trim() && decision && <span className="hint">Укажите проверяющего.</span>}
          </div>
        )}
      </section>
    </article>
  );
}
