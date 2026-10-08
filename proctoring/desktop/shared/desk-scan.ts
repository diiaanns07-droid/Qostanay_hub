// A15 desk scan ("Осмотр рабочего места") bridge methods (desktop side, contract 1.2).
// contracts/ts/bridge.ts (A01) does not list these methods yet, so the desktop extends QorgauBridge here;
// preload (main), FixtureBridge and the renderer all use DeskScanBridge from this file.
//   GET  /v1/sessions/{sid}/desk-scan                      -> DeskScanResult
//   POST /v1/sessions/{sid}/desk-scan?mode=laptop|usb      body DeskScanRequest {duration_s}
//   POST /v1/sessions/{sid}/desk-scan/skip  (operator/PIN) body {reason}
import type { BridgeResult, QorgauBridge } from "@contracts/bridge";
import type { DeskScanResult } from "@contracts/qorgau-v1.generated";

/** How the camera is moved during the scan; goes to the query string. */
export type DeskScanMode = "laptop" | "usb";
export const DESK_SCAN_MODES: readonly DeskScanMode[] = ["laptop", "usb"];
export const DESK_SCAN_DURATION = { min: 5, max: 30, default: 12 } as const;

export interface DeskScanStartArgs {
  duration_s: number;
  mode: DeskScanMode;
}

export interface DeskScanSkipRequest {
  reason: string;
}

export interface DeskScanBridge {
  getDeskScan(sessionId: string): Promise<BridgeResult<DeskScanResult>>;
  startDeskScan(sessionId: string, body: DeskScanStartArgs): Promise<BridgeResult<DeskScanResult>>;
  /** Operator only (requires operator_unlocked in main), like calibrationSkip. */
  skipDeskScan(sessionId: string, body: DeskScanSkipRequest): Promise<BridgeResult<DeskScanResult>>;
}

export type AppBridge = QorgauBridge & DeskScanBridge;
