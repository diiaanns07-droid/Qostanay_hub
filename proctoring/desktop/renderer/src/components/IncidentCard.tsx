// Episode card: explanation from backend facts, timing, technical provenance, evidence, append-only human review.
import { useCallback, useEffect, useState } from "react";
import type { ApiErrorBody, IncidentDetail, ReviewDecision } from "@contracts/qorgau-v1.generated";
import { ReviewDecisionValues } from "@contracts/qorgau-v1.generated";
import { useApp } from "../lib/appContext";
import { call } from "../lib/result";
import { CATEGORY, DECISION, END_REASON, PRIORITY, REVIEW_STATUS, RULE, RULE_HINT, SOURCE_MODE } from "../lib/labels";
import { bytes, duration, fact, num, ratio, sessionT, wall, wallDate } from "../lib/format";
import { Badge, Banner, Button, ErrorBanner, KV, Spinner, type Tone } from "./ui";

const prioTone = { low: "neutral", medium: "warn", high: "warn" } as const;
const reviewTone: Record<string, Tone> = { pending: "warn", confirmed: "danger", dismissed: "ok", inconclusive: "neutral" };

let rememberedOperator = "";

export function IncidentCard({
  sessionId,
  incidentId,
  updateSeq,
  examStartMs,
  retainMedia,
}: {
  sessionId: string;
  incidentId: string;
  updateSeq: number;
  examStartMs: number;
  retainMedia: boolean;
}) {
  const { bridge, backendLost } = useApp();
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<ApiErrorBody | null>(null);
  const [decision, setDecision] = useState<ReviewDecision | null>(null);
  const [comment, setComment] = useState("");
  const [operator, setOperator] = useState(rememberedOperator);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<ApiErrorBody | null>(null);
  const [media, setMedia] = useState<Record<string, string | { error: string }>>({});

  const load = useCallback(async () => {
    setError(null);
    const r = await call(bridge.getIncident(sessionId, incidentId));
    if (r.ok) setDetail(r.data);
    else setError(r.error);
  }, [bridge, sessionId, incidentId]);

  useEffect(() => {
    setDetail(null);
    setDecision(null);
    setComment("");
    setSaveErr(null);
  }, [incidentId]);

  useEffect(() => {
    void load();
  }, [load, updateSeq]);

  // Evidence media via the bridge (opaque ids, session ownership checked by backend).
  useEffect(() => {
    if (!detail) return;
    let cancelled = false;
    const urls: string[] = [];
    for (const ev of detail.evidence) {
      if (media[ev.evidence_id] || ev.media_type !== "image/jpeg") continue;
      void call(bridge.getEvidence(sessionId, ev.evidence_id)).then((r) => {
        if (cancelled) return;
        if (r.ok) {
          const u = URL.createObjectURL(new Blob([r.data.bytes as BlobPart], { type: r.data.media_type }));
          urls.push(u);
          setMedia((m) => ({ ...m, [ev.evidence_id]: u }));
        } else setMedia((m) => ({ ...m, [ev.evidence_id]: { error: r.error.message } }));
      });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.incident.incident_id, detail?.evidence.length]);

  useEffect(
    () => () => {
      Object.values(media).forEach((m) => typeof m === "string" && URL.revokeObjectURL(m));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [incidentId],
  );

  const submit = async () => {
    if (!decision) return;
    setSaving(true);
    setSaveErr(null);
    rememberedOperator = operator.trim();
    const r = await call(bridge.addReview(sessionId, incidentId, { decision, comment: comment.trim(), operator: operator.trim() }));
    setSaving(false);
    if (!r.ok) return setSaveErr(r.error);
    setDecision(null);
    setComment("");
    await load();
  };

  if (error) return <ErrorBanner context="Эпизод" error={error} onRetry={() => void load()} />;
  if (!detail) return <Spinner label="Загружаем эпизод…" />;
  const i = detail.incident;
  // Generic rule hint only when the backend sent no caveats of its own (avoids repeating the same warning).
  const hint = i.explanation.caveats_ru.length === 0 ? RULE_HINT[i.rule_id] : undefined;

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
          <Badge tone={reviewTone[i.review_status] ?? "neutral"}>{REVIEW_STATUS[i.review_status]}</Badge>
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

      <section>
        <h3>Кадры</h3>
        {detail.evidence.length === 0 ? (
          <p className="muted small">
            {retainMedia ? "Кадры для этого эпизода не сохранены." : "Сохранение кадров выключено для этой сессии (только метаданные)."}
            {i.trigger_frame_id !== null && ` Ключевой кадр: №${i.trigger_frame_id}.`}
          </p>
        ) : (
          <div className="evidence">
            {detail.evidence.map((ev) => {
              const m = media[ev.evidence_id];
              return (
                <figure key={ev.evidence_id}>
                  {typeof m === "string" ? (
                    <img src={m} alt={`Кадр ${ev.frame_id ?? ""} эпизода`} />
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
          ]}
        />
      </details>

      <section className="review">
        <h3>Решение преподавателя</h3>
        {detail.reviews.length > 0 && (
          <ol className="review-history">
            {detail.reviews.map((r) => (
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
            <textarea rows={2} value={comment} maxLength={1000} onChange={(e) => setComment(e.target.value)} placeholder="что видно на кадрах, контекст" />
          </label>
          <label className="field">
            <span>Проверяющий</span>
            <input value={operator} maxLength={64} onChange={(e) => setOperator(e.target.value)} placeholder="инициалы или роль" />
          </label>
        </div>
        {saveErr && <ErrorBanner context="Сохранение решения" error={saveErr} onRetry={() => void submit()} />}
        {i.state === "open" && <Banner tone="info">Эпизод ещё идёт — решение можно принять сейчас или после его завершения.</Banner>}
        <div className="actions">
          <Button variant="primary" busy={saving} disabled={!decision || !operator.trim() || backendLost} onClick={() => void submit()}>
            {detail.reviews.length ? "Записать новое решение" : "Записать решение"}
          </Button>
          {!operator.trim() && decision && <span className="hint">Укажите проверяющего.</span>}
        </div>
      </section>
    </article>
  );
}
