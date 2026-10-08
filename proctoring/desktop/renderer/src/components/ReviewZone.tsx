import { Badge, Card, type Tone } from "./ui";

const ZONES = {
  green: { text: "Зелёная зона — без замечаний", tone: "ok" },
  yellow: { text: "Жёлтая зона — требует внимания", tone: "warn" },
  red: { text: "Красная зона — проверить в первую очередь", tone: "danger" },
  grey: { text: "Серая зона — недостаточно данных", tone: "neutral" },
} as const;

/** A08's additive summary fields. Classification stays in A05; absent/invalid data is never green. */
export function parseReviewZone(value: unknown) {
  const r = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const zone = typeof r.review_zone === "string" && Object.hasOwn(ZONES, r.review_zone) ? r.review_zone as keyof typeof ZONES : null;
  const reasons = Array.isArray(r.review_zone_reasons_ru) ? r.review_zone_reasons_ru.filter((v): v is string => typeof v === "string").slice(0, 3) : [];
  return { zone, reasons, ruleVersion: typeof r.review_zone_rule_version === "string" ? r.review_zone_rule_version : null };
}

export function ReviewZone({ summary }: { summary: unknown }) {
  const { zone, reasons, ruleVersion } = parseReviewZone(summary);
  const display: { text: string; tone: Tone } = zone ? ZONES[zone] : { text: "Зона не рассчитана", tone: "neutral" };
  return <section data-testid="review-zone" data-zone={zone ?? "none"}>
    <Card title="Приоритет проверки преподавателем" aside={<Badge tone={display.tone}>{display.text}</Badge>}>
      {reasons.length > 0 ? <ul className="limits">{reasons.map((r, i) => <li key={i}>{r}</li>)}</ul> : <p className="muted">Причины не переданы сервисом. Просмотрите эпизоды.</p>}
      <p className="small muted">Зона помогает выбрать порядок проверки. Решение принимает преподаватель.</p>
      {ruleVersion && <p className="small muted">Правило: {ruleVersion}</p>}
    </Card>
  </section>;
}
