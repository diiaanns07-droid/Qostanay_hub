import { randomUUID } from "node:crypto";
import { session, WebContentsView, type BrowserWindow, type Session, type WebContents } from "electron";
import type { ShellState } from "@contracts/bridge";
import { compileExamPolicy, examBounds, examUrlAllowed, type ExamPolicy, type ExamPolicyResult } from "../security/exam-policy";
import type { ExamSurfaceStatus } from "./channels";
import { prepareContentSession, protectContent } from "../environment/content";
import type { ContentAction } from "../environment/content-policy";

// Registered before constructing the view (web-contents-created fires inside the constructor).
// The ordinary app/probe contents keep their original all-external-navigation prohibition.
const examSessions = new WeakSet<Session>();
export const isExamWebContents = (wc: WebContents): boolean => examSessions.has(wc.session);

export class ExamSurface {
  private config: ExamPolicyResult = { kind: "none", message: "Сайт экзамена не назначен" };
  private policy: ExamPolicy | null = null;
  private ses: Session | null = null;
  private view: WebContentsView | null = null;
  private attached = false;
  private rect: unknown = null;
  private shell: ShellState | null = null;
  private connected = false;
  private blockers = new Set<string>();
  private currentUrl: string | null = null;
  private state: ExamSurfaceStatus = { kind: "none", phase: "idle", title: "", origin: null, message: "" };
  constructor(private readonly window: () => BrowserWindow | null, private readonly publish: (s: ExamSurfaceStatus) => void,
    private readonly beforeInput?: (input: Electron.Input) => boolean,
    private readonly reportBlocked?: (id: ContentAction) => void) {}

  get status(): ExamSurfaceStatus { return { ...this.state }; }
  private emit(patch: Partial<ExamSurfaceStatus>): void {
    const next = { ...this.state, ...patch };
    if (JSON.stringify(next) === JSON.stringify(this.state)) return;
    this.state = next;
    this.publish(this.status);
  }

  consumeClassState(raw: unknown): void {
    if (!raw || typeof raw !== "object") return;
    const o = raw as Record<string, unknown>;
    if (o.type !== "class_state") return;
    this.blockers.delete("backend-stream"); // only a fresh authoritative replay can reopen after socket loss
    this.connected = o.connection === "connected";
    if (o.locked === false) this.blockers.delete("class-state-lock"); else this.blockers.add("class-state-lock");
    const config = compileExamPolicy(o.exam);
    const policy = config.kind === "url" ? config.policy : null;
    if (policy?.key !== this.policy?.key || config.kind !== this.config.kind) {
      this.destroyView();
      this.clearSession();
      this.currentUrl = null;
    }
    this.config = config;
    this.policy = policy;
    this.emit({ kind: config.kind, title: policy?.title ?? "Сайт экзамена", origin: policy ? new URL(policy.entry).origin : null,
      message: config.kind === "url" ? "Доступны только адреса, разрешённые преподавателем" : config.message });
    this.refresh();
  }

  setShell(state: ShellState): void {
    const previousId = this.shell?.session_id;
    this.shell = state;
    if (previousId !== state.session_id || state.mode === "normal" || state.mode === "error") {
      this.destroyView();
      this.clearSession();
      this.currentUrl = null;
    }
    this.refresh();
  }

  /** Synchronous hiding and network gate closure; Chromium destruction completes asynchronously. */
  setBlocked(reason: string, blocked: boolean): void {
    if (blocked) this.blockers.add(reason); else this.blockers.delete(reason);
    this.refresh();
  }

  setViewport(rect: unknown): void { this.rect = rect; this.refresh(); }
  resized(): void { this.refresh(); }
  reload(): void {
    if (!this.canRun()) return;
    if (!this.view) this.refresh();
    else if (this.currentUrl) this.navigate(this.currentUrl);
  }
  dispose(): void { this.connected = false; this.destroyView(); this.clearSession(); this.rect = null; }
  private canRun(): boolean {
    return !!this.policy && this.connected && this.blockers.size === 0 && this.shell?.mode === "exam" &&
      this.shell.backend === "ready" && this.shell.exam_mode_active && !this.shell.operator_unlocked;
  }

  private refresh(): void {
    const w = this.window();
    if (!w || w.isDestroyed()) { this.destroyView(); return; }
    if (!this.canRun()) {
      this.destroyView();
      this.emit({ phase: this.config.kind === "none" ? "idle" : "hidden" });
      return;
    }
    const [width = 0, height = 0] = w.getContentSize();
    const bounds = examBounds(this.rect, width, height, w.webContents.getZoomFactor());
    if (!bounds) { this.detach(); return; }
    if (!this.view) this.createView();
    const view = this.view;
    if (!view) return;
    view.setBounds(bounds);
    if (!this.attached) { w.contentView.addChildView(view); this.attached = true; }
  }

  private createView(): void {
    const policy = this.policy;
    if (!policy) return;
    const ses = this.ses ?? session.fromPartition(`adal-exam-${randomUUID()}`, { cache: false });
    if (!this.ses) {
      this.ses = ses;
      examSessions.add(ses);
      prepareContentSession(ses);
      ses.setPermissionRequestHandler((_wc, _permission, cb) => cb(false));
      ses.setPermissionCheckHandler(() => false);
      ses.setDevicePermissionHandler(() => false);
      ses.setDisplayMediaRequestHandler((_req, cb) => cb({}));
      ses.setSpellCheckerEnabled(false);
      ses.on("will-download", (event) => { event.preventDefault(); this.blocked("Загрузка файлов запрещена"); this.reportBlocked?.("download"); });
      // No filter: every interceptable protocol is denied unless the strict HTTP(S) policy approves it.
      ses.webRequest.onBeforeRequest((details, cb) => {
        const allowed = this.canRun() && this.ses === ses && !!this.view && !!this.policy &&
          examUrlAllowed(this.policy, details.url, details.resourceType);
        cb({ cancel: !allowed });
      });
      ses.webRequest.onHeadersReceived((details, cb) => {
        const headers = { ...details.responseHeaders };
        // Merge with the server's CSP; an additional policy can only restrict, never relax it.
        const name = Object.keys(headers).find((key) => key.toLowerCase() === "content-security-policy") ?? "Content-Security-Policy";
        headers[name] = [...(headers[name] ?? []), "object-src 'none'; worker-src 'none'; base-uri 'self'; frame-src http: https:; sandbox allow-scripts allow-forms allow-same-origin"];
        headers["Permissions-Policy"] = ["camera=(), microphone=(), geolocation=(), display-capture=(), usb=(), serial=(), bluetooth=(), payment=(), fullscreen=()"];
        cb({ responseHeaders: headers });
      });
    }
    const view = new WebContentsView({ webPreferences: {
      session: ses, contextIsolation: true, sandbox: true, nodeIntegration: false, nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false, webviewTag: false, webSecurity: true, allowRunningInsecureContent: false,
      experimentalFeatures: false, navigateOnDragDrop: false, spellcheck: false, safeDialogs: true, devTools: false,
      // Only the session content-policy gate; no qorgau/lock/audio/auth bridge.
    } });
    this.view = view;
    const wc = view.webContents;
    protectContent(wc, () => this.canRun(), id => this.reportBlocked?.(id));
    wc.setWebRTCIPHandlingPolicy("disable_non_proxied_udp");
    wc.setWindowOpenHandler(() => { this.blocked("Новое окно запрещено; откройте вход в текущем окне"); this.reportBlocked?.("new_window"); return { action: "deny" }; });
    const nav = (event: { preventDefault(): void }, url: string) => {
      if (!this.canRun() || !this.policy || !examUrlAllowed(this.policy, url)) {
        event.preventDefault(); this.blocked("Переход за пределы разрешённых адресов запрещён"); this.reportBlocked?.("navigation");
      }
    };
    wc.on("will-navigate", nav);
    wc.on("will-redirect", nav);
    wc.on("will-frame-navigate", (event) => nav(event, event.url));
    wc.on("will-attach-webview", (event) => event.preventDefault());
    wc.on("context-menu", (event) => event.preventDefault());
    wc.on("before-input-event", (event, input) => { if (this.beforeInput?.(input)) event.preventDefault(); });
    wc.on("will-prevent-unload", (event) => event.preventDefault());
    wc.on("did-navigate", (_event, url) => {
      if (this.view === view && this.policy && examUrlAllowed(this.policy, url)) {
        this.currentUrl = url;
        this.emit({ origin: new URL(url).origin });
      }
    });
    wc.on("did-navigate-in-page", (_event, url, isMainFrame) => {
      if (!isMainFrame || this.view !== view || !this.policy) return;
      // pushState/replaceState bypass will-navigate: never leave an unapproved SPA URL visible.
      if (examUrlAllowed(this.policy, url)) this.currentUrl = url;
      else {
        this.destroyView();
        this.emit({ phase: "error", message: "Сайт изменил адрес за пределы разрешённого пути. Повторите загрузку." });
      }
    });
    wc.on("did-finish-load", () => { if (this.view === view) this.emit({ phase: "ready" }); });
    wc.on("did-fail-load", (_event, code, _description, _url, isMainFrame) => {
      if (isMainFrame && code !== -3 && this.view === view) this.emit({ phase: "error", message: "Сайт не загрузился. Проверьте связь и разрешённые адреса, затем повторите." });
    });
    wc.on("render-process-gone", () => { if (this.view === view) { this.destroyView(); this.emit({ phase: "error", message: "Окно сайта остановлено. Повторите загрузку." }); } });
    this.navigate(this.currentUrl ?? policy.entry);
  }

  private navigate(url: string): void {
    const view = this.view;
    if (!view || !this.policy || !examUrlAllowed(this.policy, url)) return;
    this.currentUrl = url;
    this.emit({ phase: "loading", message: "Доступны только адреса, разрешённые преподавателем" });
    void view.webContents.loadURL(url).catch(() => {
      if (this.view === view) this.emit({ phase: "error", message: "Сайт недоступен или переход запрещён. Проверьте список адресов." });
    });
  }
  private blocked(message: string): void { this.emit({ message }); }
  private detach(): void {
    const w = this.window();
    if (this.attached && this.view && w && !w.isDestroyed()) w.contentView.removeChildView(this.view);
    this.attached = false;
  }
  private destroyView(): void {
    this.detach();
    const view = this.view;
    this.view = null;
    if (view && !view.webContents.isDestroyed()) {
      view.setVisible(false);
      view.webContents.stop();
      view.webContents.close({ waitForBeforeUnload: false });
    }
    void this.ses?.closeAllConnections();
  }
  private clearSession(): void {
    const ses = this.ses;
    this.ses = null;
    if (ses) { void ses.clearStorageData(); void ses.clearCache(); }
  }
}
