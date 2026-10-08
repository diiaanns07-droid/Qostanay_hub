// Formatting only. Never invents values: null/undefined render as an explicit "нет данных".
import type { ExplanationFact } from "@contracts/qorgau-v1.generated";

export const NO_DATA = "нет данных";

const nf1 = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

export function num(v: number | null | undefined, digits: 1 | 2 = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return NO_DATA;
  return (digits === 1 ? nf1 : nf2).format(v);
}

/** 1600 -> "1,6 с"; 125000 -> "2 мин 05 с"; 3_725_000 -> "1 ч 02 мин". */
export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return NO_DATA;
  const neg = ms < 0;
  const a = Math.abs(ms);
  let s: string;
  if (a < 60_000) s = `${nf1.format(a / 1000)} с`;
  else if (a < 3_600_000) {
    const m = Math.floor(a / 60_000);
    const sec = Math.floor((a % 60_000) / 1000);
    s = `${m} мин ${String(sec).padStart(2, "0")} с`;
  } else {
    const h = Math.floor(a / 3_600_000);
    const m = Math.floor((a % 3_600_000) / 60_000);
    s = `${h} ч ${String(m).padStart(2, "0")} мин`;
  }
  return neg ? `−${s}` : s;
}

/** Countdown/clock "MM:SS" (or "H:MM:SS"). */
export function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Session-relative timestamp "+01:23.4" ("−00:00.1" when before the reference point). */
export function sessionT(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return NO_DATA;
  const a = Math.abs(ms);
  const tenths = Math.floor((a % 1000) / 100);
  return `${ms < 0 && a >= 50 ? "−" : "+"}${clock(a)}.${tenths}`;
}

/** Local wall time HH:MM:SS from an ISO timestamp (display only). */
export function wall(iso: string | null | undefined): string {
  if (!iso) return NO_DATA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return NO_DATA;
  return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function wallDate(iso: string | null | undefined): string {
  if (!iso) return NO_DATA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return NO_DATA;
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function ratio(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return NO_DATA;
  return nf2.format(v);
}

export function fact(f: ExplanationFact): string {
  const v = f.value;
  if (typeof v === "boolean") return v ? "да" : "нет";
  if (typeof v === "string") return v;
  switch (f.unit) {
    case "ms":
      return duration(v);
    case "s":
      return duration(v * 1000);
    case "count":
      return String(Math.round(v));
    case "ratio":
      return ratio(v);
    case "deg":
      return `${nf1.format(v)}°`;
    default:
      return nf2.format(v);
  }
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${nf1.format(n / 1024)} КиБ`;
  return `${nf1.format(n / 1024 / 1024)} МиБ`;
}
