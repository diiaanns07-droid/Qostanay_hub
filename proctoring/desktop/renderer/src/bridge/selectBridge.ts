// Chooses the bridge. The real one is window.qorgau (A06 preload). The FixtureBridge is a DEV/DEMO path only:
//  * it exists in the bundle only for the Vite dev server or a build made with VITE_QORGAU_FIXTURE=1
//    (`npm run build:renderer` for the product does not contain it: no fixture data, no fixture PIN);
//  * even then it is used only when there is no shell AND it was requested (?bridge=fixture or dev server).
// A packaged app whose preload failed shows an error instead of silently switching to fixture data.
import type { QorgauBridge } from "@contracts/bridge";
import type { FixtureBridge } from "./fixtureBridge";

export type BridgeChoice =
  | { kind: "electron"; bridge: QorgauBridge }
  | { kind: "fixture"; bridge: FixtureBridge }
  | { kind: "missing"; reason: string };

const FIXTURE_BUILD = import.meta.env.DEV || import.meta.env.VITE_QORGAU_FIXTURE === "1";

export async function selectBridge(): Promise<BridgeChoice> {
  const shell = typeof window !== "undefined" ? window.qorgau : undefined;
  if (shell) {
    if (shell.bridgeVersion !== "1.0.0") {
      return { kind: "missing", reason: `Несовместимая версия моста оболочки: ${String(shell.bridgeVersion)} (ожидается 1.0.0)` };
    }
    return { kind: "electron", bridge: shell };
  }
  if (FIXTURE_BUILD) {
    const params = new URLSearchParams(window.location.search);
    if (params.get("bridge") === "fixture" || import.meta.env.DEV) {
      const { FixtureBridge } = await import("./fixtureBridge");
      return { kind: "fixture", bridge: new FixtureBridge() };
    }
  }
  return {
    kind: "missing",
    reason: "Оболочка Qorgau (window.qorgau) недоступна: приложение нужно запускать через Electron. Тестовые данные в этой сборке не подставляются.",
  };
}
