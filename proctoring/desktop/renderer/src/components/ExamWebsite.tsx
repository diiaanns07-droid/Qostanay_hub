import { useEffect, useRef, useState } from "react";
import type { ExamSurfaceBridge, ExamSurfaceStatus } from "../../../main/src/exam/channels";
import { useApp } from "../lib/appContext";
import { call } from "../lib/result";
import { Banner, Button, Dialog, ErrorBanner } from "./ui";
import type { ApiErrorBody } from "@contracts/qorgau-v1.generated";

declare global { interface Window { qorgauExam?: ExamSurfaceBridge } }
export function useExamWebsite(): ExamSurfaceStatus | null {
  const [status, setStatus] = useState<ExamSurfaceStatus | null>(null);
  useEffect(() => {
    const api = window.qorgauExam;
    if (!api) return;
    let active = true;
    let eventSeen = false;
    const unsub = api.onStatus((s) => { eventSeen = true; if (active) setStatus(s); });
    void api.getStatus().then((s) => { if (active && !eventSeen) setStatus(s); });
    return () => { active = false; unsub(); };
  }, []);
  return status;
}

export function ExamWebsite({ status }: { status: ExamSurfaceStatus }) {
  const { bridge, session, setSession, backendLost } = useApp();
  const slot = useRef<HTMLDivElement>(null);
  const [dialog, setDialog] = useState<"finish" | "exit" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiErrorBody | null>(null);
  const running = session?.state === "running" && !backendLost;
  useEffect(() => {
    const api = window.qorgauExam;
    const element = slot.current;
    if (!api || !element) return;
    const update = () => {
      // Native child views sit above HTML: remove them before ANY shell dialog, including teacher PIN.
      if (!running || dialog || document.hidden || document.querySelector('[role="dialog"], [role="alertdialog"]')) return api.viewport(null);
      const r = element.getBoundingClientRect();
      api.viewport({ x: r.x, y: r.y, width: r.width, height: r.height });
    };
    const size = new ResizeObserver(update);
    const dialogs = new MutationObserver(update);
    size.observe(element);
    dialogs.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["role", "inert"] });
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    document.addEventListener("visibilitychange", update);
    update();
    return () => {
      size.disconnect(); dialogs.disconnect();
      window.removeEventListener("resize", update); window.removeEventListener("scroll", update, true);
      document.removeEventListener("visibilitychange", update); api.viewport(null);
    };
  }, [running, dialog]);

  const finish = async () => {
    if (!session) return;
    setBusy(true); setError(null);
    const result = dialog === "exit" ? await call(bridge.requestEmergencyExit("website_exam_emergency_exit")) : await call(bridge.finishExam(session.session_id));
    setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    if (dialog === "finish") setSession(result.data as typeof session);
    else { const next = await call(bridge.getSession(session.session_id)); if (next.ok) setSession(next.data); }
    setDialog(null);
  };

  return <div className="screen exam">
    <div className="exam-head"><div><div className="eyebrow">Сайт экзамена</div><h1 className="exam-title">{status.title}</h1></div>
      <Button onClick={() => window.qorgauExam?.reload()} disabled={!running || status.kind !== "url"}>Повторить загрузку</Button></div>
    <p className="small" role="status">{status.origin && <strong>{status.origin} · </strong>}{status.message}</p>
    {status.kind === "url" ? <div ref={slot} aria-label="Защищённое окно сайта экзамена" style={{ height: "min(60vh, 620px)", minHeight: 240, background: "#f3f5f8", borderRadius: 8 }}>
      {status.phase === "loading" && <p>Загрузка сайта…</p>}
      {status.phase === "error" && <Banner tone="danger" title="Сайт недоступен">Повторите загрузку или обратитесь к преподавателю.</Banner>}
      {!running && <p>Сайт скрыт, пока экзамен приостановлен или нет связи с сервисом.</p>}
    </div> : <Banner tone="warn" title="Этот режим экзамена недоступен">{status.message}</Banner>}
    <footer className="exam-foot"><span className="small muted">Ограничения адресов действуют в окне сайта Adal.</span><span className="spacer" />
      <Button onClick={() => setDialog("exit")}>Аварийный выход</Button>
      <Button onClick={() => setDialog("finish")} disabled={backendLost}>Завершить экзамен</Button></footer>
    {dialog && <Dialog title={dialog === "exit" ? "Прервать экзамен?" : "Завершить экзамен?"} onClose={() => setDialog(null)} actions={<>
      <Button onClick={() => setDialog(null)}>Отмена</Button><Button variant="danger" busy={busy} onClick={() => void finish()}>Подтвердить</Button></>}>
      <p>Сначала отправьте ответы на самом сайте экзамена. Adal не сохраняет ответы внешнего сайта.</p>
      {error && <ErrorBanner error={error} />}
    </Dialog>}
  </div>;
}
