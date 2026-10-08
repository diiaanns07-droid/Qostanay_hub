import type { IpcMain, IpcMainInvokeEvent, Session, WebContents } from "electron";
import type { BackendClient } from "./backend/client";
import { isTrustedUrl } from "./security/web";

export const AUDIO_CHANNEL = "qorgau:class-audio";

type Contents = Pick<WebContents, "mainFrame" | "isDestroyed">;

export function allowClassAudio(
  sender: Contents | null, main: Contents | null, permission: string,
  details: { isMainFrame?: boolean; requestingUrl?: string; mediaTypes?: string[]; mediaType?: string },
  devOrigin: string | null, enabled: boolean,
): boolean {
  return enabled && !!main && sender === main && !main.isDestroyed() && permission === "media" &&
    details.isMainFrame === true && !!details.requestingUrl &&
    details.requestingUrl === main.mainFrame.url && isTrustedUrl(details.requestingUrl, devOrigin) &&
    (details.mediaType === "audio" || (details.mediaTypes?.length === 1 && details.mediaTypes[0] === "audio"));
}

export function createClassAudio(
  client: BackendClient, trusted: (event: IpcMainInvokeEvent) => boolean,
  mainContents: () => WebContents | null, devOrigin: string | null,
) {
  let scope: string | null = null;
  let micRequested = false;
  let captureUntil = 0;
  const reset = () => { scope = null; micRequested = false; captureUntil = 0; };
  const observe = (value: unknown) => {
    const envelope = value as { message?: { type?: string; bridge_scope?: string; connection?: string; message?: Record<string, any> } };
    const message = envelope?.message;
    if (message?.type === "class_state" && message.connection !== "connected") { reset(); return; }
    if (message?.type !== "class_audio") return;
    const msg = message.message;
    if (msg?.type === "transport_lost") { reset(); return; }
    if (msg?.type === "command" && ["audio_start", "audio_update", "audio_stop"].includes(msg.kind)) {
      scope = message.bridge_scope ?? null;
      micRequested = msg.kind !== "audio_stop" && msg.payload?.listen === true;
      captureUntil = 0;
    }
  };
  return {
    observe, reset,
    installPermissions(session: Session) {
      session.setPermissionRequestHandler((wc, permission, callback, details) => {
        callback(allowClassAudio(wc, mainContents(), permission, details, devOrigin, micRequested && Date.now() < captureUntil));
      });
      session.setPermissionCheckHandler((wc, permission, _origin, details) =>
        allowClassAudio(wc, mainContents(), permission, details, devOrigin, micRequested && Date.now() < captureUntil));
    },
    register(ipc: IpcMain) {
      ipc.handle(AUDIO_CHANNEL, async (event, body: unknown) => {
        if (!trusted(event)) return { ok: false };
        if (!body || typeof body !== "object" || JSON.stringify(body).length > 80_000) return { ok: false };
        const request = body as { bridge_scope?: unknown; message?: { type?: unknown } };
        const kind = request.message?.type;
        if (kind === "capture") {
          if (!micRequested || !scope || request.bridge_scope !== scope) return { ok: false };
          // Renderer calls only after its notification has painted. Only this exact main frame may capture.
          captureUntil = Date.now() + 5000;
          return { ok: true, data: { permitted: true } };
        }
        if (!["ready", "dispose", "ack", "audio_signal", "audio_media"].includes(String(kind))) return { ok: false };
        if (kind === "dispose") reset();
        return client.json("POST", "/v1/class/audio", body, 5000);
      });
    },
  };
}
