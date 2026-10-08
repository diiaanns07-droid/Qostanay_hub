// A15 «Осмотр рабочего места»: optional step between preparation (Preflight) and calibration.
// The backend records ~12 s of camera video and looks for objects; the client polls GET until the state
// leaves "recording". Variant 3 (camera cannot move) and the operator skip go through the teacher PIN,
// exactly like the calibration skip. The scan helps the teacher; it is not evidence of a violation.
import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiErrorBody, DeskScanResult } from "@contracts/qorgau-v1.generated";
import { useApp } from "../lib/appContext";
import { call } from "../lib/result";
import {
  DESK_SCAN_HONESTY,
  DESK_SCAN_POLL_MS,
  DESK_SCAN_SECONDS,
  DESK_SCAN_TIMEOUT_TEXT,
  DESK_SCAN_VARIANTS,
  FIXED_CAMERA_REASON,
  FIXED_CAMERA_TEXT,
  deskScanHint,
  deskScanView,
  isDeskScanFinal,
  modeForVariant,
  pollDeadlineMs,
  type DeskScanVariant,
  type DeskScanView,
} from "../lib/deskScan";
import { PreviewPanel } from "../components/PreviewPanel";
import { Banner, Button, Card, Dialog, ErrorBanner, SourceModeBadge, Spinner } from "../components/ui";

/** An action that waits for the teacher PIN; it runs once the unlock arrives (within this window). */
const PENDING_MS = 60_000;

export function DeskScanScreen({ onBack }: { onBack: () => void }) {
  const { bridge, session, setSession, isCurrent, backendLost, role, requestTeacher } = useApp();
  const sid = session?.session_id ?? "";
  const [variant, setVariant] = useState<DeskScanVariant>("laptop");
  const [scan, setScan] = useState<DeskScanResult | null>(null);
  const [loadErr, setLoadErr] = useState<ApiErrorBody | null>(null);
  const [actionErr, setActionErr] = useState<{ ctx: string; error: ApiErrorBody } | null>(null);
  const [busy, setBusy] = useState<null | "start" | "skip" | "next">(null);
  const [timedOut, setTimedOut] = useState(false);
  const [skipOpen, setSkipOpen] = useState(false);
  const [pending, setPending] = useState<{ kind: "fixed" | "skip"; at: number } | null>(null);
  const startedAt = useRef<number>(Date.now());
  const [now, setNow] = useState(Date.now());

  const load = useCallback(async () => {
    if (!sid) return;
    setLoadErr(null);
    const r = await call(bridge.getDeskScan(sid));
    if (!isCurrent(sid)) return;
    if (!r.ok) return setLoadErr(r.error);
    if (r.data.state === "recording") startedAt.current = Date.now(); // restored mid-scan: countdown is approximate
    setScan(r.data);
  }, [bridge, sid, isCurrent]);

  useEffect(() => {
    void load();
  }, [load]);

  const recording = scan?.state === "recording" && !timedOut;

  // Poll every ~500 ms until the scan leaves "recording"; give up after duration + 20 s.
  useEffect(() => {
    if (!recording) return;
    let alive = true;
    const deadline = startedAt.current + pollDeadlineMs(DESK_SCAN_SECONDS);
    const t = setInterval(async () => {
      if (Date.now() > deadline) {
        clearInterval(t);
        if (alive) setTimedOut(true);
        return;
      }
      const r = await call(bridge.getDeskScan(sid));
      if (!alive || !isCurrent(sid)) return;
      if (r.ok && r.data.state !== "recording") setScan(r.data);
    }, DESK_SCAN_POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [recording, scan?.scan_id, bridge, sid, isCurrent]);

  // Countdown tick.
  useEffect(() => {
    if (!recording) return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [recording]);

  const start = async () => {
    const mode = modeForVariant(variant);
    if (!mode || !sid) return;
    setBusy("start");
    setActionErr(null);
    const r = await call(bridge.startDeskScan(sid, { duration_s: DESK_SCAN_SECONDS, mode }));
    setBusy(null);
    if (!isCurrent(sid)) return;
    if (!r.ok) return setActionErr({ ctx: "Осмотр рабочего места", error: r.error });
    startedAt.current = Date.now();
    setNow(Date.now());
    setTimedOut(false);
    setScan(r.data);
  };

  const skip = useCallback(
    async (reason: string) => {
      if (!sid) return;
      setBusy("skip");
      setActionErr(null);
      const r = await call(bridge.skipDeskScan(sid, { reason }));
      setBusy(null);
      if (!isCurrent(sid)) return;
      if (!r.ok) return setActionErr({ ctx: "Пропуск осмотра", error: r.error });
      setSkipOpen(false);
      setTimedOut(false);
      setScan(r.data);
    },
    [bridge, sid, isCurrent],
  );

  // Teacher decisions: the same PIN/operator unlock as the calibration skip (checked again in main).
  const confirmFixed = () => {
    if (role === "teacher") return void skip(FIXED_CAMERA_REASON);
    setPending({ kind: "fixed", at: Date.now() });
    requestTeacher();
  };
  const operatorSkip = () => {
    if (role === "teacher") return setSkipOpen(true);
    setPending({ kind: "skip", at: Date.now() });
    requestTeacher();
  };
  useEffect(() => {
    if (role !== "teacher" || !pending) return;
    setPending(null);
    if (Date.now() - pending.at > PENDING_MS) return;
    if (pending.kind === "fixed") void skip(FIXED_CAMERA_REASON);
    else setSkipOpen(true);
  }, [role, pending, skip]);

  const next = async () => {
    if (!sid) return;
    setBusy("next");
    setActionErr(null);
    const r = await call(bridge.calibrationStart(sid));
    if (!r.ok) {
      setBusy(null);
      return setActionErr({ ctx: "Калибровка", error: r.error });
    }
    const s = await call(bridge.getSession(sid));
    setBusy(null);
    if (s.ok) setSession(s.data);
  };

  if (!session) return null;

  const elapsedS = Math.max(0, (now - startedAt.current) / 1000);
  const remaining = Math.max(0, Math.ceil(DESK_SCAN_SECONDS - elapsedS));
  const view: DeskScanView | null = timedOut ? { tone: "danger", text: DESK_SCAN_TIMEOUT_TEXT } : scan ? deskScanView(scan) : null;
  const final = timedOut || (!!scan && isDeskScanFinal(scan.state));
  // Optional step: «Далее» in any final state (and when the desk-scan service itself is unavailable).
  const canNext = (final || !!loadErr) && !recording;
  const objectsFound = !timedOut && scan?.state === "objects_found";
  const fixedDone = scan?.state === "skipped" && scan.skip_reason === FIXED_CAMERA_REASON;
  const off = backendLost || busy !== null;

  return (
    <div className="screen desk-scan">
      <div className="screen-head">
        <div>
          <h1>Осмотр рабочего места</h1>
          <p className="lead">Покажите камере стол, чтобы преподаватель видел, что рядом нет посторонних предметов. Шаг необязательный.</p>
        </div>
        <SourceModeBadge mode={session.source_mode} fixture={bridge.transport === "fixture"} />
      </div>

      {loadErr && (
        <ErrorBanner context="Осмотр рабочего места недоступен" error={loadErr} onRetry={() => void load()} />
      )}
      {actionErr && <ErrorBanner context={actionErr.ctx} error={actionErr.error} onDismiss={() => setActionErr(null)} />}

      <div className="desk-layout">
        <div className="stack">
          <Card title="Как будем осматривать">
            <fieldset className="field" disabled={recording || busy !== null}>
              <legend>Вариант</legend>
              <div className="segmented desk-variants" role="radiogroup" aria-label="Вариант осмотра">
                {DESK_SCAN_VARIANTS.map((v) => (
                  <label key={v.id} className={`seg ${variant === v.id ? "seg-on" : ""}`}>
                    <input type="radio" name="desk-variant" value={v.id} checked={variant === v.id} onChange={() => setVariant(v.id)} />
                    {v.n}. {v.label}
                  </label>
                ))}
              </div>
            </fieldset>

            {recording ? (
              <div className="desk-live" aria-live="polite">
                <div className="desk-count" aria-label={`Осталось ${remaining} с`}>
                  {remaining > 0 ? remaining : <Spinner label="Обрабатываем осмотр…" />}
                </div>
                <p className="desk-hint">{remaining > 0 ? deskScanHint(variant, elapsedS) : "Верните камеру на место. Обрабатываем осмотр…"}</p>
              </div>
            ) : variant === "laptop" ? (
              <ol className="desk-plan">
                <li>{deskScanHint("laptop", 0)}</li>
                <li>{deskScanHint("laptop", 4)}</li>
                <li>{deskScanHint("laptop", 8)}</li>
              </ol>
            ) : (
              <p className="desk-hint desk-hint-idle">{deskScanHint(variant, 0)}</p>
            )}
            {!recording && variant !== "fixed" && <p className="hint">Осмотр длится {DESK_SCAN_SECONDS} секунд. Подсказки появятся на экране.</p>}

            {view && (
              <div className={`desk-result ${objectsFound ? "desk-result-big" : ""}`}>
                <Banner tone={view.tone} title={view.text} role={view.tone === "ok" || view.tone === "info" ? "status" : "alert"}>
                  {scan?.message_ru && scan.state !== "skipped" && !timedOut ? <span className="small">Сервис: {scan.message_ru}</span> : null}
                </Banner>
              </div>
            )}

            <div className="actions">
              {variant === "fixed" ? (
                <Button variant="primary" size="lg" busy={busy === "skip"} disabled={off || recording || fixedDone} onClick={confirmFixed}>
                  Подтвердить (PIN преподавателя)
                </Button>
              ) : (
                <Button variant={final ? "secondary" : "primary"} size="lg" busy={busy === "start" || recording} disabled={off || recording} onClick={() => void start()}>
                  {final ? "Повторить осмотр" : "Начать осмотр"}
                </Button>
              )}
              <Button
                variant={canNext ? "primary" : "secondary"}
                size="lg"
                busy={busy === "next"}
                disabled={off || !canNext}
                onClick={() => void next()}
                title={canNext ? "К калибровке" : "Сначала проведите осмотр (или преподаватель пропустит его)"}
              >
                Далее
              </Button>
              <Button variant="ghost" disabled={off || recording} onClick={operatorSkip} title="Решение преподавателя: потребуется PIN">
                Пропустить (оператор)
              </Button>
              <span className="spacer" />
              <Button variant="ghost" disabled={busy !== null || recording} onClick={onBack}>
                Назад к проверкам
              </Button>
            </div>
            {objectsFound && (
              <p className="desk-warn-next">Замеченные предметы попадут в отчёт для преподавателя. Лучше уберите их и повторите осмотр.</p>
            )}
            <p className="small muted desk-honesty">{DESK_SCAN_HONESTY}</p>
            {bridge.transport === "fixture" && (
              <p className="small muted">FIXTURE: осмотр имитируется сценарием, камера и детектор не используются.</p>
            )}
          </Card>
        </div>

        <div className="stack">
          <Card title="Камера">
            <PreviewPanel sessionId={session.session_id} />
            {variant === "fixed" && <p className="hint">{FIXED_CAMERA_TEXT}.</p>}
          </Card>
        </div>
      </div>

      {skipOpen && <DeskSkipDialog busy={busy === "skip"} onClose={() => setSkipOpen(false)} onSkip={(r) => void skip(r)} />}
    </div>
  );
}

function DeskSkipDialog({ busy, onClose, onSkip }: { busy: boolean; onClose: () => void; onSkip: (reason: string) => void }) {
  const [reason, setReason] = useState("");
  return (
    <Dialog
      title="Пропустить осмотр рабочего места"
      tone="warn"
      onClose={onClose}
      actions={
        <>
          <Button onClick={onClose}>Отмена</Button>
          <Button variant="warn" busy={busy} disabled={reason.trim().length < 3} onClick={() => onSkip(reason.trim())}>
            Пропустить осмотр
          </Button>
        </>
      }
    >
      <p>Решение преподавателя. Причина попадёт в отчёт.</p>
      <label className="field">
        <span>Причина</span>
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} maxLength={200} placeholder="например, место осмотрено преподавателем лично" />
      </label>
    </Dialog>
  );
}
