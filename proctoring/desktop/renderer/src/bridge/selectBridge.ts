// Chooses the bridge. The real one is window.qorgau (A06 preload). The FixtureBridge is used ONLY when there is
// no shell AND it was requested explicitly (Vite dev server or ?bridge=fixture). A packaged app whose preload
// failed shows an error instead of silently switching to fixture data.
import type { QorgauBridge } from "@contracts/bridge";
import { FixtureBridge } from "./fixtureBridge";

export type BridgeChoice =
  | { kind: "electron"; bridge: QorgauBridge }
  | { kind: "fixture"; bridge: FixtureBridge }
  | { kind: "missing"; reason: string };

export function selectBridge(): BridgeChoice {
  const shell = typeof window !== "undefined" ? window.qorgau : undefined;
  if (shell) {
    if (shell.bridgeVersion !== "1.0.0") {
      return { kind: "missing", reason: `Несовместимая версия моста оболочки: ${String(shell.bridgeVersion)} (ожидается 1.0.0)` };
    }
    return { kind: "electron", bridge: shell };
  }
  const params = new URLSearchParams(window.location.search);
  const requested = params.get("bridge") === "fixture" || import.meta.env.DEV;
  if (requested) return { kind: "fixture", bridge: new FixtureBridge() };
  return {
    kind: "missing",
    reason: "Оболочка Qorgau (window.qorgau) недоступна. Запустите приложение через Electron или откройте ?bridge=fixture для демонстрации на тестовых данных.",
  };
}
