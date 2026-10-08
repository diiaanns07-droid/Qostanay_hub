// ui/proctoring/desktop/renderer/src/lib/format.ts
var NO_DATA = "\u043D\u0435\u0442 \u0434\u0430\u043D\u043D\u044B\u0445";
var nf1 = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });
var nf2 = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
function num(v, digits = 1) {
  if (v === null || v === void 0 || !Number.isFinite(v)) return NO_DATA;
  return (digits === 1 ? nf1 : nf2).format(v);
}
function duration(ms) {
  if (ms === null || ms === void 0 || !Number.isFinite(ms)) return NO_DATA;
  const neg = ms < 0;
  const a = Math.abs(ms);
  let s;
  if (a < 6e4) s = `${nf1.format(a / 1e3)} \u0441`;
  else if (a < 36e5) {
    const m = Math.floor(a / 6e4);
    const sec = Math.floor(a % 6e4 / 1e3);
    s = `${m} \u043C\u0438\u043D ${String(sec).padStart(2, "0")} \u0441`;
  } else {
    const h = Math.floor(a / 36e5);
    const m = Math.floor(a % 36e5 / 6e4);
    s = `${h} \u0447 ${String(m).padStart(2, "0")} \u043C\u0438\u043D`;
  }
  return neg ? `\u2212${s}` : s;
}
function clock(ms) {
  const total = Math.max(0, Math.floor(ms / 1e3));
  const h = Math.floor(total / 3600);
  const m = Math.floor(total % 3600 / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}
function sessionT(ms) {
  if (ms === null || ms === void 0 || !Number.isFinite(ms)) return NO_DATA;
  const tenths = Math.floor(ms % 1e3 / 100);
  return `+${clock(ms)}.${tenths}`;
}
function wall(iso) {
  if (!iso) return NO_DATA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return NO_DATA;
  return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function wallDate(iso) {
  if (!iso) return NO_DATA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return NO_DATA;
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
function ratio(v) {
  if (v === null || v === void 0 || !Number.isFinite(v)) return NO_DATA;
  return nf2.format(v);
}
function fact(f) {
  const v = f.value;
  if (typeof v === "boolean") return v ? "\u0434\u0430" : "\u043D\u0435\u0442";
  if (typeof v === "string") return v;
  switch (f.unit) {
    case "ms":
      return duration(v);
    case "s":
      return duration(v * 1e3);
    case "count":
      return String(Math.round(v));
    case "ratio":
      return ratio(v);
    case "deg":
      return `${nf1.format(v)}\xB0`;
    default:
      return nf2.format(v);
  }
}
function bytes(n) {
  if (n < 1024) return `${n} \u0411`;
  if (n < 1024 * 1024) return `${nf1.format(n / 1024)} \u041A\u0438\u0411`;
  return `${nf1.format(n / 1024 / 1024)} \u041C\u0438\u0411`;
}
export {
  NO_DATA,
  bytes,
  clock,
  duration,
  fact,
  num,
  ratio,
  sessionT,
  wall,
  wallDate
};
