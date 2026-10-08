import { examChecks } from "../lib/examChecks";
import { Badge, Card } from "./ui";

export function ExamChecks(props: Parameters<typeof examChecks>[0]) {
  const rows = examChecks(props);
  const render = (items: typeof rows.modules) => <ul className="exam-checks-list">
    {items.map((r) => <li key={r.id} data-testid={`exam-check-${r.id}`}>
      <div className="exam-check-heading"><strong>{r.label}</strong><Badge tone={r.tone}>{r.status}</Badge></div>
      {r.detail && <p className="small muted">{r.detail}</p>}
    </li>)}
  </ul>;
  return <section aria-label="Что проверяет Adal на этом экзамене" data-testid="exam-checks">
    <Card title="Что проверяет Adal на этом экзамене">
      <p className="small muted">До начала здесь показана готовность проверок по текущим данным сервиса. Неизвестные и недоступные проверки отмечены отдельно.</p>
      {render(rows.modules)}
      <h3>Защита клавиш во время экзамена</h3>
      {render(rows.keys)}
    </Card>
  </section>;
}
