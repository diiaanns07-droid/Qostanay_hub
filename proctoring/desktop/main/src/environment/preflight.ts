import type { EnvironmentCapabilities } from "@contracts/qorgau-v1.generated";

/** Current machine conditions carried by existing capability fields, not a new contract. */
export function withDisplayCheck(caps: EnvironmentCapabilities, count: number): EnvironmentCapabilities {
  const multiple = count > 1;
  const valid = Number.isInteger(count) && count >= 1;
  const note = multiple ? "Отключите второй монитор, чтобы начать" :
    valid ? "Подключён один монитор" : "Не удалось проверить количество мониторов";
  return { ...caps, exam_mode_supported: caps.exam_mode_supported && valid && !multiple,
    items: [...caps.items.filter(i => i.action !== "display_changed"), {
      action: "display_changed", status: valid ? "detected_only" : "unverified",
      mechanism: `electron.display_count.${multiple ? "multiple" : valid ? "single" : "unavailable"}`,
      verified_on: null, note_ru: note,
    }] };
}

export function environmentBlockReason(caps: EnvironmentCapabilities | null): string | null {
  const blocked = caps?.items.find(i => i.mechanism === "electron.display_count.multiple" ||
    i.mechanism === "electron.display_count.unavailable");
  return blocked?.note_ru ?? null;
}
