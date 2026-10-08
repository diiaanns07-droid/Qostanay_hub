import type { QorgauBridge } from "@contracts/bridge";
import { StudentAudioEndpoint } from "../../../../class-audio/web/shared/student-endpoint.js";

declare global {
  interface Window {
    qorgauAudio?: { send(body: unknown): Promise<{ ok: boolean; data?: { bridge_scope?: string } }> };
  }
}

/** Installs one transport endpoint, outside React StrictMode remounts. */
export function installClassAudio(bridge: QorgauBridge): () => void {
  const transport = window.qorgauAudio;
  if (!transport) return () => {};
  let scope: string | null = null;
  let alive = true;
  let heartbeatBusy = false;
  const banner = document.createElement("aside");
  banner.dataset.testid = "class-audio-notice";
  banner.setAttribute("role", "status");
  banner.setAttribute("aria-live", "assertive");
  banner.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483647;box-sizing:border-box;padding:14px 24px;background:#fff1c2;color:#332100;border-bottom:3px solid #aa6b00;font:600 16px/1.4 system-ui";
  banner.hidden = true;
  const player = document.createElement("audio");
  player.autoplay = true;
  player.hidden = true;
  document.body.append(banner, player);
  const endpoint = new StudentAudioEndpoint({
    audioElement: player,
    send: (message: Record<string, unknown>) => {
      void transport.send({ bridge_scope: scope, message }).then((result) => {
        if (!result.ok) endpoint.transportLost();
      }).catch(() => endpoint.transportLost());
    },
    onIndicators: async (ind) => {
      banner.hidden = !ind.sessionActive;
      banner.textContent = ind.micLive ? "Микрофон включён преподавателем. Идёт аудиосвязь."
        : ind.micRequested ? "Преподаватель запросил микрофон. Сейчас начнётся передача звука."
        : ind.teacherSpeaking ? "Говорит преподаватель. Ваш микрофон выключен."
        : "Подключение к преподавателю. Ваш микрофон выключен.";
      // Two animation frames guarantee the warning is rendered before getUserMedia or ACK.
      if (ind.sessionActive) await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    },
    getUserMedia: async (constraints) => {
      if (banner.hidden || document.visibilityState !== "visible") throw new DOMException("Notice is not visible", "NotAllowedError");
      const result = await transport.send({ bridge_scope: scope, message: { type: "capture" } });
      if (!result.ok) throw new DOMException("Audio capture not authorized", "NotAllowedError");
      return navigator.mediaDevices.getUserMedia(constraints);
    },
  });
  const unsub = bridge.subscribeEvents((env) => {
    const msg = env.message as unknown as { type: string; bridge_scope?: string; connection?: string; message?: Record<string, unknown>; session?: { state?: string } };
    if (msg.type === "class_audio" && msg.bridge_scope && msg.message) {
      if (msg.message.type === "transport_lost") { endpoint.transportLost(); scope = null; return; }
      scope = msg.bridge_scope;
      void endpoint.handleMessage(msg.message);
    } else if (msg.type === "class_state" && msg.connection !== "connected") {
      endpoint.transportLost(); scope = null;
    } else if (msg.type === "session_state" && ["finished", "aborted", "failed"].includes(msg.session?.state ?? "")) {
      endpoint.stop("exam_ended");
    }
  });
  const shellUnsub = bridge.onShellState((state) => {
    if (state.backend !== "ready") endpoint.transportLost();
  });
  const heartbeat = async () => {
    if (!alive || heartbeatBusy) return;
    heartbeatBusy = true;
    try {
      const result = await transport.send({ message: { type: "ready" } });
      if (!result.ok || !result.data?.bridge_scope) { endpoint.transportLost(); scope = null; }
      else {
        if (scope && scope !== result.data.bridge_scope) endpoint.transportLost();
        scope = result.data.bridge_scope;
      }
    } catch { endpoint.transportLost(); scope = null; }
    finally { heartbeatBusy = false; }
  };
  const timer = setInterval(() => void heartbeat(), 2000);
  void heartbeat();
  const dispose = () => {
    alive = false;
    endpoint.stop("renderer_closed");
    void transport.send({ bridge_scope: scope, message: { type: "dispose" } });
    clearInterval(timer); unsub(); shellUnsub(); banner.remove(); player.remove();
  };
  window.addEventListener("beforeunload", dispose, { once: true });
  return dispose;
}
