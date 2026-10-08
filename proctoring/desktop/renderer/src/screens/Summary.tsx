import { useCallback, useEffect, useState } from "react";
import type { ApiErrorBody, SessionSummary } from "@contracts/qorgau-v1.generated";
import { useApp } from "../lib/appContext";
import { call } from "../lib/result";
import { COMPONENT, GAP_REASON, REVIEW_STATUS, RULE, SESSION_STATE, SOURCE_MODE_RU, CAL_PHASE, human } from "../lib/labels";
import { can } from "../lib/permissions";
import { ReviewZone } from "../components/ReviewZone";
import { duration, sessionT, wallDate } from "../lib/format";
import { Badge, Banner, Button, Card, Dialog, ErrorBanner, KV, Spinner } from "../components/ui";
import type { IncidentRule, ReviewStatus } from "@contracts/qorgau-v1.generated";

/** Student view after the exam: no episodes, no monitoring details. */
export function StudentDone() {
  const { session, requestTeacher } = useApp();
  if (!session) return null;
  return (
    <div className="screen">
      <div className="ready-panel">
        <Badge tone={session.state === "finished" ? "ok" : "warn"}>{SESSION_STATE[session.state]}</Badge>
        <h1>{session.state === "finished" ? "Экзамен завершён" : "Сессия остановлена"}</h1>
        <p className="lead">
          {session.state === "finished"
            ? "Ответы сохранены на этом компьютере. Можно сообщить преподавателю, что вы закончили."
            : "Ограничения сняты. Обратитесь к преподавателю."}
        </p>
        <Button onClick={requestTeacher}>Режим преподавателя…</Button>
      </div>
    </div>
  );
}

export function SummaryScreen({ onReview }: { onReview: () => void }) {
  const { bridge, session, newSession, backendLost, shell } = useApp();
  const exportPerm = can(shell, "export");
  const deletePerm = can(shell, "delete");
  const sid = session?.session_id ?? "";
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [err, setErr] = useState<ApiErrorBody | null>(null);
  const [busy, setBusy] = useState<null | "html" | "json" | "delete">(null);
  const [exportMsg, setExportMsg] = useState<{ ok: boolean; text: string; error?: ApiErrorBody } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    const r = await call(bridge.getSummary(sid));
    if (r.ok) setSummary(r.data);
    else setErr(r.error);
  }, [bridge, sid]);

  useEffect(() => {
    void load();
  }, [load]);

  const doExport = async (format: "html" | "json") => {
    setBusy(format);
    setExportMsg(null);
    const r = await call(bridge.exportReport(sid, format));
    setBusy(null);
    if (!r.ok) return setExportMsg({ ok: false, text: `Экспорт ${format.toUpperCase()} не выполнен`, error: r.error });
    // Success is shown only when the shell confirms the file was written (saved: true).
    setExportMsg(
      r.data.saved
        ? { ok: true, text: `Файл записан: ${r.data.file_name} (папка выбрана в системном диалоге).` }
        : { ok: false, text: "Сохранение отменено в диалоге — файл не записан." },
    );
  };

  const doDelete = async () => {
    setBusy("delete");
    const r = await call(bridge.deleteSession(sid));
    setBusy(null);
    if (!r.ok) {
      setConfirmDelete(false);
      return setErr(r.error);
    }
    setConfirmDelete(false);
    newSession();
  };

  if (!session) return null;
  if (err && !summary)
    return (
      <div className="screen">
        <ErrorBanner context="Итог сессии" error={err} onRetry={() => void load()} />
        <Button onClick={() => void load()}>Повторить</Button>
      </div>
    );
  if (!summary) return <Spinner label="Собираем итог…" />;

  const s = summary.session;
  const examMs =
    s.started_at && s.finished_at ? Math.max(0, Date.parse(s.finished_at) - Date.parse(s.started_at)) : summary.observed_ms + summary.paused_ms;
  const gapMs = Math.max(0, examMs - summary.observed_ms - summary.paused_ms);
  const pct = (v: number) => (examMs > 0 ? `${Math.max(0, Math.min(100, (v / examMs) * 100))}%` : "0%");
  // A08 reports "pending" inside reviews_by_decision; older/fixture data may omit it → derive it.
  const rbd = summary.reviews_by_decision;
  const decided = (["confirmed", "dismissed", "inconclusive"] as const).reduce((a, d) => a + (rbd[d] ?? 0), 0);
  const pending = typeof rbd.pending === "number" ? rbd.pending : Math.max(0, summary.incidents_total - decided);

  return (
    <div className="screen summary">
      <div className="screen-head">
        <div>
          <h1>Итог сессии</h1>
          <p className="lead">
            Сводка из локального хранилища. Эпизоды — наблюдения для проверки; окончательные решения — в колонке
            «Решения преподавателя».
          </p>
        </div>
        <div className="head-actions">
          <Button onClick={onReview}>← К проверке эпизодов</Button>
          <Button variant="primary" onClick={newSession}>
            Новая сессия
          </Button>
        </div>
      </div>
      {err && <ErrorBanner context="Действие" error={err} onDismiss={() => setErr(null)} />}
      {s.source_mode === "replay" && <Banner tone="warn" title="REPLAY — воспроизведение записи">Результат анализа выбранного видео. Это не наблюдение с камеры в реальном времени.</Banner>}
      <ReviewZone summary={summary} />
      {pending > 0 && (
        <Banner tone="warn" title={`Не проверено эпизодов: ${pending}`} actions={<Button size="sm" onClick={onReview}>Проверить</Button>}>
          Отчёт можно выгрузить и сейчас — непроверенные эпизоды будут помечены как ожидающие.
        </Banner>
      )}

      <div className="summary-grid">
        <Card title="Сессия">
          <KV
            items={[
              ["Идентификатор", <span className="mono">{s.session_id}</span>],
              ["Состояние", SESSION_STATE[s.state]],
              ["Источник", SOURCE_MODE_RU[s.source_mode]],
              ["Студент", s.student_label ?? "без метки"],
              ["Начало", wallDate(s.started_at)],
              ["Окончание", wallDate(s.finished_at)],
              ["Калибровка", CAL_PHASE[s.calibration.phase]],
              ["Кадры-доказательства", s.retain_media ? "сохранялись" : "не сохранялись"],
              ["Версии", `сервис ${s.backend_version} · контракт ${s.contract_version}`],
            ]}
          />
        </Card>

        <Card title="Покрытие наблюдением">
          <div className="coverage" aria-hidden="true">
            <span className="cov-obs" style={{ width: pct(summary.observed_ms) }} />
            <span className="cov-pause" style={{ width: pct(summary.paused_ms) }} />
            <span className="cov-gap" style={{ width: pct(gapMs) }} />
          </div>
          <ul className="legend">
            <li>
              <span className="lg lg-obs" /> наблюдение {duration(summary.observed_ms)}
            </li>
            <li>
              <span className="lg lg-pause" /> пауза {duration(summary.paused_ms)}
            </li>
            <li>
              <span className="lg lg-gap" /> пробелы {duration(gapMs)}
            </li>
          </ul>
          <p className="small muted">Длительность экзамена: {duration(examMs)}. В пробелах и на паузе выводов о поведении нет.</p>
          {summary.gaps.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Компонент</th>
                  <th scope="col">Начало</th>
                  <th scope="col">Длительность</th>
                  <th scope="col">Причина</th>
                </tr>
              </thead>
              <tbody>
                {summary.gaps.map((g, i) => (
                  <tr key={i}>
                    <td>{COMPONENT[g.component]}</td>
                    <td className="mono">{sessionT(g.t_start_ms - (s.exam_started_t_ms ?? 0))}</td>
                    <td>{g.t_end_ms === null ? "до конца" : duration(g.t_end_ms - g.t_start_ms)}</td>
                    <td className="small" title={g.reason}>
                      {human(GAP_REASON, g.reason)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Эпизоды" aside={<span className="big-num">{summary.incidents_total}</span>}>
          {summary.incidents_total === 0 ? (
            <p className="muted">Эпизодов нет. Это верно только для периода наблюдения.</p>
          ) : (
            <table className="table">
              <tbody>
                {Object.entries(summary.incidents_by_rule).map(([rule, n]) => (
                  <tr key={rule}>
                    <td>{RULE[rule as IncidentRule] ?? rule}</td>
                    <td className="num">{n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Решения преподавателя">
          <table className="table">
            <tbody>
              {(["confirmed", "dismissed", "inconclusive"] as ReviewStatus[]).map((d) => (
                <tr key={d}>
                  <td>{REVIEW_STATUS[d]}</td>
                  <td className="num">{summary.reviews_by_decision[d] ?? 0}</td>
                </tr>
              ))}
              <tr>
                <td>{REVIEW_STATUS.pending}</td>
                <td className="num">{pending}</td>
              </tr>
            </tbody>
          </table>
        </Card>

        <Card title="Ограничения" className="span-2">
          <ul className="limits">
            {summary.limitations_ru.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </Card>

        <Card title="Отчёт и данные" className="span-2">
          <p className="small muted">
            Отчёт формируется локально; путь сохранения выбирается в системном диалоге. Хэши в отчёте подтверждают
            целостность файлов, но не защищают от владельца компьютера.
          </p>
          <div className="actions">
            <Button variant="primary" busy={busy === "html"} disabled={backendLost || !exportPerm.ok || busy !== null} onClick={() => void doExport("html")}>
              Сохранить отчёт HTML
            </Button>
            <Button busy={busy === "json"} disabled={backendLost || !exportPerm.ok || busy !== null} onClick={() => void doExport("json")}>
              Экспорт JSON
            </Button>
            <span className="spacer" />
            <Button variant="danger" disabled={backendLost || !deletePerm.ok} onClick={() => setConfirmDelete(true)}>
              Удалить данные сессии…
            </Button>
          </div>
          {!exportPerm.ok && <p className="hint">{exportPerm.reason}</p>}
          {exportMsg &&
            (exportMsg.error ? (
              <ErrorBanner context={exportMsg.text} error={exportMsg.error} onDismiss={() => setExportMsg(null)} />
            ) : (
              <Banner tone={exportMsg.ok ? "ok" : "info"}>{exportMsg.text}</Banner>
            ))}
        </Card>
      </div>

      {confirmDelete && (
        <Dialog
          title="Удалить данные сессии?"
          tone="danger"
          onClose={() => setConfirmDelete(false)}
          actions={
            <>
              <Button onClick={() => setConfirmDelete(false)}>Отмена</Button>
              <Button variant="danger" busy={busy === "delete"} onClick={() => void doDelete()}>
                Удалить безвозвратно
              </Button>
            </>
          }
        >
          <p>
            Будут удалены ответы, эпизоды, решения и кадры сессии <span className="mono">{s.session_id}</span> с этого
            компьютера. Уже выгруженные отчёты не затрагиваются.
          </p>
        </Dialog>
      )}
    </div>
  );
}
