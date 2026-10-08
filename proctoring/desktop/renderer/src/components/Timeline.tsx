// Session timeline: one lane per category, x = t_session_ms. Bars are buttons (keyboard reachable).
import type { CoverageGap, Incident, IncidentCategory } from "@contracts/qorgau-v1.generated";
import { IncidentCategoryValues } from "@contracts/qorgau-v1.generated";
import { CATEGORY, RULE, REVIEW_STATUS } from "../lib/labels";
import { clock, duration } from "../lib/format";

export function Timeline({
  incidents,
  startMs,
  endMs,
  gaps,
  selected,
  onSelect,
  categories,
}: {
  incidents: Incident[];
  startMs: number;
  endMs: number;
  gaps: CoverageGap[];
  selected: string | null;
  onSelect: (id: string) => void;
  categories: Set<IncidentCategory>;
}) {
  const span = Math.max(endMs - startMs, 10_000);
  const pct = (t: number) => `${Math.max(0, Math.min(100, ((t - startMs) / span) * 100))}%`;
  const width = (a: number, b: number) => `${Math.max(0.6, Math.min(100, ((b - a) / span) * 100))}%`;
  const ticks = 5;
  const lanes = IncidentCategoryValues.filter((c) => categories.has(c));

  return (
    <div className="timeline" role="group" aria-label="Временная шкала эпизодов">
      <div className="tl-axis" aria-hidden="true">
        {Array.from({ length: ticks + 1 }, (_, i) => (
          <span key={i} style={{ left: `${(i / ticks) * 100}%` }}>
            {clock((span * i) / ticks)}
          </span>
        ))}
      </div>
      {lanes.map((cat) => (
        <div className="tl-lane" key={cat}>
          <span className="tl-lane-label">{CATEGORY[cat]}</span>
          <div className="tl-track">
            {cat === "technical" &&
              gaps.map((g, i) => (
                <span
                  key={`g${i}`}
                  className="tl-gap"
                  style={{ left: pct(g.t_start_ms), width: width(g.t_start_ms, g.t_end_ms ?? endMs) }}
                  title={`Пробел наблюдения: ${g.reason}`}
                />
              ))}
            {incidents
              .filter((i) => i.category === cat)
              .map((i) => {
                const end = i.t_end_ms ?? i.t_start_ms + i.duration_ms;
                return (
                  <button
                    key={i.incident_id}
                    type="button"
                    className={`tl-bar tl-${i.category} rs-${i.review_status} ${i.state === "open" ? "tl-open" : ""} ${selected === i.incident_id ? "tl-sel" : ""}`}
                    style={{ left: pct(i.t_start_ms), width: width(i.t_start_ms, end) }}
                    onClick={() => onSelect(i.incident_id)}
                    aria-label={`${RULE[i.rule_id]}, начало ${clock(i.t_start_ms - startMs)}, ${duration(i.duration_ms)}, ${REVIEW_STATUS[i.review_status]}`}
                    title={`${RULE[i.rule_id]} · ${duration(i.duration_ms)}`}
                  />
                );
              })}
          </div>
        </div>
      ))}
    </div>
  );
}
