// Private desktop extension. Only the trusted app renderer receives this bridge.
export const EXAM_CHANNEL = { viewport: "adal:exam:viewport", status: "adal:exam:status", getStatus: "adal:exam:get-status", reload: "adal:exam:reload" } as const;
export interface ExamSurfaceStatus {
  kind: "none" | "url" | "invalid" | "unsupported";
  phase: "idle" | "hidden" | "loading" | "ready" | "error";
  title: string;
  origin: string | null;
  message: string;
}
export interface ExamSurfaceBridge {
  viewport(bounds: { x: number; y: number; width: number; height: number } | null): void;
  getStatus(): Promise<ExamSurfaceStatus>;
  onStatus(listener: (status: ExamSurfaceStatus) => void): () => void;
  reload(): void;
}
