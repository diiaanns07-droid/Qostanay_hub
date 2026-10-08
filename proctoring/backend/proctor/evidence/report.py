"""Teacher report: JSON export + self-contained printable HTML (owner: A08).

HTML safety rules:
  * every dynamic value goes through esc() (html.escape with quotes) — names, comments, answers,
    explanations and ids alike; nothing user-provided is placed in a tag, attribute name, style or URL;
  * no <script>, no event handlers, no links, no external URLs; images only as data:image/jpeg
    of files whose SHA-256 matched the database; CSP meta: default-src 'none';
  * synthetic/replay mode, unknown intervals and limitations are shown at the top.
Hashes in the manifest detect accidental change; they are not tamper-proofing against the
owner of the machine and not a cryptographic proof of authenticity.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
from typing import Any

from proctor_contracts.v1 import (
    CONTRACT_VERSION,
    EnvironmentObservation,
    ExportFile,
    ExportManifest,
    IncidentDetail,
    SessionInfo,
    SourceMode,
)

from proctor.settings import BACKEND_VERSION

from .store import ExportSnapshot
from .review_zones import ZONE_LABEL

REPORT_FORMAT = "qorgau.report.v1"
CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"

RULE_RU = {
    "phone_visible": "Телефон в кадре",
    "phone_raised": "Телефон поднят",
    "possible_screen_capture": "Возможная съёмка экрана (эвристика)",
    "gaze_prolonged_down": "Продолжительный взгляд вниз (приблизительно)",
    "gaze_prolonged_side": "Продолжительный взгляд в сторону (приблизительно)",
    "face_missing": "Лицо не обнаружено",
    "multiple_faces": "Больше одного лица в кадре",
    "environment_blocked_action": "Заблокированное действие в среде экзамена",
    "environment_escape": "Выход из окна экзамена",
    "monitoring_degraded": "Наблюдение ухудшено (технический эпизод)",
    "background_speech": "Возможная речь рядом",
    "headphones_visible": "Видны наушники",
    "identity_mismatch": "Лицо не совпадает с началом экзамена",
    "foreign_object_visible": "Посторонний предмет в кадре",
    "second_screen_visible": "Второй экран или ноутбук в кадре",
}
CATEGORY_RU = {"phone": "телефон", "attention": "внимание", "presence": "присутствие", "environment": "среда", "technical": "техника", "audio": "звук", "identity": "сверка лица", "objects": "предметы"}
PRIORITY_RU = {"low": "низкий", "medium": "средний", "high": "высокий"}
REVIEW_RU = {
    "pending": "не проверено",
    "confirmed": "подтверждено преподавателем",
    "dismissed": "отклонено преподавателем",
    "inconclusive": "не удалось определить",
}
STATE_RU = {
    "created": "создана",
    "preflight": "проверка",
    "calibrating": "калибровка",
    "ready": "готова",
    "running": "идёт",
    "paused": "пауза",
    "finished": "завершена",
    "aborted": "прервана оператором",
    "failed": "прервана сбоем",
}
END_RU = {
    "condition_cleared": "условие прекратилось",
    "session_finished": "сессия завершена",
    "session_paused": "пауза",
    "session_aborted": "сессия прервана",
    "source_lost": "потерян источник",
    "merged": "объединён с другим",
}
COMPONENT_RU = {
    "backend": "сессия",
    "capture": "источник кадров",
    "phone": "детектор телефона",
    "attention": "лицо и взгляд",
    "fusion": "объединение",
    "evidence": "хранилище",
    "environment": "среда",
}
GAP_RU = {
    "paused": "пауза",
    "no_observations": "нет наблюдений",
    "undetermined": "результат не определён",
    "camera_disconnected": "камера отключена",
    "model_unavailable": "модель недоступна",
}
ACTION_RU = {
    "shortcut_alt_tab": "Alt+Tab",
    "shortcut_ctrl_c": "Ctrl+C",
    "shortcut_ctrl_v": "Ctrl+V",
    "shortcut_ctrl_x": "Ctrl+X",
    "shortcut_ctrl_tab": "Ctrl+Tab",
    "shortcut_win": "клавиша Win",
    "shortcut_print_screen": "PrtScn",
    "shortcut_alt_f4": "Alt+F4",
    "focus_lost": "потеря фокуса окна",
    "focus_regained": "фокус возвращён",
    "foreign_window_foreground": "постороннее окно на переднем плане",
    "new_window_blocked": "новое окно заблокировано",
    "navigation_blocked": "переход заблокирован",
    "devtools_blocked": "DevTools заблокированы",
    "clipboard_blocked": "буфер обмена заблокирован",
    "display_changed": "изменение дисплея",
    "exam_mode_engaged": "режим экзамена включён",
    "exam_mode_released": "режим экзамена снят",
    "enforcement_error": "ошибка защиты",
}
ENFORCEMENT_RU = {
    "blocked": "заблокировано",
    "detected_only": "только зафиксировано",
    "allowed": "информация",
    "failed": "блокировка не сработала",
    "unsupported": "не поддерживается",
}
MODE_BANNER = {
    SourceMode.SYNTHETIC: ("synthetic", "СИНТЕТИЧЕСКИЕ ДАННЫЕ", "Сценарные наблюдения для проверки системы. Это не результат работы камеры и не сведения об участнике."),
    SourceMode.REPLAY: ("replay", "REPLAY — ВОСПРОИЗВЕДЕНИЕ ЗАПИСИ", "Данные получены из заранее записанного видео через тот же конвейер; это не живая сессия."),
    SourceMode.LIVE: ("live", "ЖИВАЯ СЕССИЯ (камера)", "Наблюдения получены с камеры во время экзамена."),
}
DISCLAIMER_RU = [
    "Эпизоды — объяснимые сигналы, помогающие преподавателю выбрать порядок проверки.",
    "Решение по каждому эпизоду принимает преподаватель; автоматических санкций нет.",
    "Приоритет показывает, какие эпизоды открыть в первую очередь.",
    "Интервалы без наблюдения означают «неизвестно», а не «нарушений нет».",
]
INTEGRITY_NOTE_RU = (
    "SHA-256 позволяет обнаружить случайное изменение файлов после экспорта. Это не защита от изменения "
    "владельцем компьютера и не криптографическое доказательство подлинности."
)
UNIT_RU = {"ms": "мс", "s": "с", "count": "шт.", "ratio": "доля", "deg": "°"}
EVIDENCE_STATUS_RU = {
    "missing": "файл снимка отсутствует",
    "hash_mismatch": "файл не совпадает с сохранённым SHA-256 — не показан",
    "unreadable": "файл недоступен для чтения",
    "not_embedded": "не встроен в отчёт (превышен лимит размера отчёта)",
}


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def fmt_t(ms: float | None) -> str:
    """Session time as m:ss.s (display)."""
    if ms is None:
        return "—"
    total_s = ms / 1000.0
    minutes = int(total_s // 60)
    return f"{minutes}:{total_s - minutes * 60:04.1f}"


def fmt_dur(ms: float | None) -> str:
    if ms is None:
        return "—"
    s = ms / 1000.0
    if s < 60:
        return f"{s:.1f} с".replace(".", ",")
    return f"{int(s // 60)} мин {s % 60:04.1f} с".replace(".", ",")


def fmt_dt(value: Any) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC") if hasattr(value, "strftime") else str(value)


def safe_file_stem(session_id: str) -> str:
    stem = "".join(c if (c.isascii() and (c.isalnum() or c in "._-")) else "_" for c in session_id)
    return f"qorgau-report-{stem}"[:120]


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------


def build_manifest(snap: ExportSnapshot, html_bytes: bytes) -> ExportManifest:
    files = [
        ExportFile(
            name="report.html",
            media_type="text/html",
            sha256=hashlib.sha256(html_bytes).hexdigest(),
            size_bytes=len(html_bytes),
        )
    ]
    for ev in snap.evidence.values():
        if ev.status != "ok":
            continue
        files.append(
            ExportFile(
                name=f"{ev.item.evidence_id}.jpg",
                media_type=ev.item.media_type,
                sha256=ev.item.sha256,
                size_bytes=ev.item.size_bytes,
            )
        )
    return ExportManifest(
        exported_at=snap.exported_at,
        session_id=snap.info.session_id,
        source_mode=snap.info.source_mode,
        backend_version=BACKEND_VERSION,
        config_versions=snap.config_versions,
        models=[],
        files=files,
        limitations_ru=[INTEGRITY_NOTE_RU, *snap.summary.limitations_ru],
    )


def build_json(snap: ExportSnapshot, html_bytes: bytes | None = None) -> dict[str, Any]:
    html_bytes = render_html(snap).encode("utf-8") if html_bytes is None else html_bytes
    manifest = build_manifest(snap, html_bytes)
    coverage = dict(snap.coverage)
    coverage["gaps"] = [g.model_dump(mode="json") for g in snap.coverage["gaps"]]
    return {
        "format": REPORT_FORMAT,
        "contract": "qorgau.v1",
        "contract_version": CONTRACT_VERSION,
        "manifest": manifest.model_dump(mode="json"),
        "session": snap.info.model_dump(mode="json"),
        "summary": snap.summary.model_dump(mode="json"),
        "coverage": coverage,
        "incidents": [d.model_dump(mode="json") for d in snap.incidents],
        "incidents_total": snap.incidents_total,
        "incidents_truncated": snap.incidents_total > len(snap.incidents),
        "observations": snap.observations,
        "observations_total": snap.observations_total,
        "missing_observation_refs": snap.missing_observation_refs,
        "answers": [a.model_dump(mode="json") for a in snap.answers],
        "producers": snap.producers,
        "evidence_files": [
            {"evidence_id": ev.item.evidence_id, "status": ev.status, "sha256": ev.item.sha256, "size_bytes": ev.item.size_bytes}
            for ev in snap.evidence.values()
        ],
        "evidence_expired": snap.evidence_expired,
        "storage_counters": snap.counters,
        "recovered_after_interruption": snap.recovered,
        "disclaimer_ru": DISCLAIMER_RU,
    }


def dumps_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

STYLE = """
:root { --ink:#1b1f24; --muted:#5b6470; --line:#c9cfd6; --bg:#ffffff; --warn:#8a5a00; --warnbg:#fff4d6;
        --syn:#6b2fa3; --synbg:#f1e8fb; --live:#1f6f43; --livebg:#e6f4ec; }
* { box-sizing: border-box; }
html { background: var(--bg); }
body { font: 14px/1.45 "Segoe UI", Arial, sans-serif; color: var(--ink); background: var(--bg); margin: 0 auto;
       max-width: 980px; padding: 24px 16px; overflow-wrap: anywhere; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 28px 0 8px; border-bottom: 2px solid var(--line); padding-bottom: 4px; }
h3 { font-size: 15px; margin: 0 0 6px; }
.muted { color: var(--muted); }
.banner { border: 3px solid; border-radius: 6px; padding: 10px 14px; margin: 14px 0; }
.banner strong { display: block; font-size: 16px; letter-spacing: .02em; }
.banner.synthetic { border-color: var(--syn); background: var(--synbg); color: var(--syn); }
.banner.replay { border-color: var(--warn); background: var(--warnbg); color: var(--warn); }
.banner.live { border-color: var(--live); background: var(--livebg); color: var(--live); }
.banner.alert { border-color: var(--warn); background: var(--warnbg); color: var(--warn); }
.zone-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
.zone-grid .banner { margin: 6px 0; }
.zone-green { border-color: #236044; background: #e6f4ec; }
.zone-yellow { border-color: #946000; background: #fff4d6; }
.zone-red { border-color: #a52636; background: #fff0f1; }
.zone-grey, .zone-none { border-color: #59616c; background: #f0f2f5; }
.note { border-left: 4px solid var(--line); padding: 6px 10px; margin: 8px 0; background: #f6f7f9; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; }
th, td { border: 1px solid var(--line); padding: 4px 6px; text-align: left; vertical-align: top; }
th { background: #eef1f4; font-weight: 600; }
td.num { text-align: right; white-space: nowrap; }
.kv th { width: 34%; overflow-wrap: break-word; }
.incident { border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; margin: 12px 0; }
.tag { display: inline-block; border: 1px solid var(--line); border-radius: 10px; padding: 0 8px; font-size: 12px; margin-right: 4px; }
.auto { background: #eef1f4; }
.human { background: #e6f4ec; }
.pending { background: #fff4d6; }
.split { display: grid; grid-template-columns: 1fr; gap: 10px; }
@media screen and (min-width: 900px) { .split { grid-template-columns: 1fr 1fr; } }
.box { border: 1px dashed var(--line); padding: 6px 8px; border-radius: 4px; }
figure { margin: 6px 0; }
figure img { max-width: 100%; height: auto; border: 1px solid var(--line); }
figcaption { font-size: 12px; color: var(--muted); }
.mono { font-family: Consolas, "Courier New", monospace; font-size: 12px; }
ul { margin: 4px 0 8px 20px; padding: 0; }
@page { size: A4; margin: 14mm 12mm; }
@media print {
  body { max-width: none; padding: 0; font-size: 11pt; }
  .box, tr, figure, .banner, .note { break-inside: avoid; }
  .incident { break-inside: auto; }
  h2 { break-after: avoid; }
}
"""


def render_html(snap: ExportSnapshot) -> str:
    info = snap.info
    out: list[str] = []
    w = out.append
    w("<!DOCTYPE html>")
    w('<html lang="ru"><head><meta charset="utf-8">')
    w(f'<meta http-equiv="Content-Security-Policy" content="{esc(CSP)}">')
    w('<meta name="viewport" content="width=device-width, initial-scale=1">')
    w('<meta name="referrer" content="no-referrer">')
    w(f"<title>Qorgau Exam — отчёт {esc(info.session_id)}</title>")
    w(f"<style>{STYLE}</style></head><body>")
    w("<header>")
    w("<h1>Qorgau Exam — отчёт о сессии экзамена</h1>")
    w(f'<div class="muted">Сессия <span class="mono">{esc(info.session_id)}</span> · экспортировано {esc(fmt_dt(snap.exported_at))}</div>')
    cls, title, text = MODE_BANNER[info.source_mode]
    w(f'<div class="banner {cls}"><strong>{esc(title)}</strong>{esc(text)}</div>')
    _zone_section(w, snap)
    if snap.recovered:
        w('<div class="banner alert"><strong>СЕССИЯ ПРЕРВАНА СБОЕМ</strong>'
          "Локальный сервис был остановлен без завершения сессии. Данные после последней записи отсутствуют.</div>")
    w("</header>")

    w("<section><h2>Как читать отчёт</h2><ul>")
    for line in DISCLAIMER_RU:
        w(f"<li>{esc(line)}</li>")
    w("</ul></section>")

    _session_section(w, snap)
    _coverage_section(w, snap)
    _incidents_section(w, snap)
    _environment_section(w, snap)
    _answers_section(w, snap)

    w("<section><h2>Ограничения</h2><ul>")
    for line in snap.summary.limitations_ru:
        w(f"<li>{esc(line)}</li>")
    if snap.missing_observation_refs:
        w(f"<li>{esc(f'Ссылок эпизодов на наблюдения без сохранённой записи: {snap.missing_observation_refs}.')}</li>")
    if snap.evidence_expired:
        w(f"<li>{esc(f'Снимков удалено по сроку хранения: {snap.evidence_expired}.')}</li>")
    w("</ul></section>")

    _integrity_section(w, snap)
    w('<footer class="muted"><p>')
    w(esc(f"Qorgau Exam backend {BACKEND_VERSION} · контракт qorgau.v1 {CONTRACT_VERSION} · формат {REPORT_FORMAT}. "
          "Локальный отчёт: без сетевых ресурсов и скриптов."))
    w("</p></footer></body></html>")
    return "\n".join(out)


def _zone_section(w, snap: ExportSnapshot) -> None:
    zone = snap.summary.review_zone
    # All CSS/indicator values come from this closed mapping, never report input.
    key = zone.value if zone is not None else "none"
    icon = {"red": "!!", "yellow": "!", "grey": "?", "green": "✓", "none": "—"}[key]
    w('<div class="zone-grid">')
    w(f'<section class="banner zone-{key}"><strong>{icon} {esc(ZONE_LABEL[zone])}</strong>')
    w('<div>Приоритет проверки преподавателем</div><ul>')
    for reason in snap.summary.review_zone_reasons_ru[:3]:
        w(f'<li>{esc(reason)}</li>')
    w('</ul>')
    if snap.summary.review_zone_rule_version:
        w(f'<small>Правило: {esc(snap.summary.review_zone_rule_version)}</small>')
    w('</section><section class="banner"><strong>Решения преподавателя</strong>')
    decisions = snap.summary.reviews_by_decision
    w('<ul>')
    for code, label in REVIEW_RU.items():
        if decisions.get(code, 0):
            w(f'<li>{esc(label)}: {decisions[code]}</li>')
    w('</ul><div>Решения показаны отдельно и не меняют зону.</div></section></div>')


def _kv(w, rows: list[tuple[str, Any]]) -> None:
    w('<table class="kv"><tbody>')
    for key, value in rows:
        w(f"<tr><th>{esc(key)}</th><td>{esc(value)}</td></tr>")
    w("</tbody></table>")


def _session_section(w, snap: ExportSnapshot) -> None:
    info: SessionInfo = snap.info
    cal = info.calibration
    w("<section><h2>Сессия</h2>")
    _kv(
        w,
        [
            ("Экзамен", info.exam_id),
            ("Участник (псевдоним)", info.student_label or "не указан"),
            ("Состояние", STATE_RU.get(info.state.value, info.state.value)),
            ("Режим источника", info.source_mode.value),
            ("Источник", _source_text(info)),
            ("Создана", fmt_dt(info.created_at)),
            ("Экзамен начат", fmt_dt(info.started_at)),
            ("Завершена", fmt_dt(info.finished_at)),
            ("Калибровка взгляда", cal.phase.value + (f" ({cal.message_code})" if cal.message_code else "")),
            ("Сохранение снимков", "включено" if info.retain_media else "выключено (только метаданные)"),
            ("Версия backend / контракта", f"{info.backend_version} / {info.contract_version}"),
            ("Последняя ошибка", f"{info.last_error.code.value}: {info.last_error.message}" if info.last_error else "—"),
        ],
    )
    w("</section>")


def _source_text(info: SessionInfo) -> str:
    s = info.source
    base = f"{s.width}×{s.height} @ {s.fps} fps"
    if s.mode == SourceMode.LIVE:
        return f"камера #{s.camera_index}, {base}"
    if s.mode == SourceMode.REPLAY:
        return f"запись {s.replay_id}, {base}"
    return f"синтетический генератор, {base}"


def _coverage_section(w, snap: ExportSnapshot) -> None:
    cov = snap.coverage
    w("<section><h2>Покрытие наблюдения</h2>")
    w(f'<p class="note">{esc(cov["definition_ru"])} Время указано от начала сессии (мин:сек).</p>')
    window = cov["window"]
    rows = [
        ("Интервал экзамена (время сессии)", f"{fmt_t(window['t_start_ms'])} — {fmt_t(window['t_end_ms'])}" + (" (оценка)" if window["end_estimated"] else "")),
        ("Длительность экзамена", fmt_dur(cov["exam_ms"])),
        ("Пауза", fmt_dur(cov["paused_ms"])),
        ("Наблюдаемое время (оба анализа)", fmt_dur(cov["observed_ms"])),
    ]
    _kv(w, rows)
    w("<table><thead><tr><th>Компонент</th><th>Определённые результаты</th><th>Не определено (unknown)</th>"
      "<th>Нет наблюдений</th><th>Наблюдений</th></tr></thead><tbody>")
    for comp, c in cov["components"].items():
        w(
            f"<tr><td>{esc(COMPONENT_RU.get(comp, comp))}</td><td class=\"num\">{esc(fmt_dur(c['observed_ms']))}</td>"
            f"<td class=\"num\">{esc(fmt_dur(c['undetermined_ms']))}</td><td class=\"num\">{esc(fmt_dur(c['unknown_ms']))}</td>"
            f"<td class=\"num\">{esc(c['observations'])}</td></tr>"
        )
    w("</tbody></table>")
    gaps = cov["gaps"]
    if gaps:
        w(f"<h3>Пропуски и неизвестные интервалы ({esc(cov['gaps_total'])})</h3>")
        w("<table><thead><tr><th>Начало</th><th>Конец</th><th>Длительность</th><th>Компонент</th><th>Причина</th></tr></thead><tbody>")
        for g in gaps:
            dur = (g.t_end_ms - g.t_start_ms) if g.t_end_ms is not None else None
            w(
                f"<tr><td class=\"num\">{esc(fmt_t(g.t_start_ms))}</td><td class=\"num\">{esc(fmt_t(g.t_end_ms) if g.t_end_ms is not None else 'продолжается')}</td>"
                f"<td class=\"num\">{esc(fmt_dur(dur))}</td><td>{esc(COMPONENT_RU.get(g.component.value, g.component.value))}</td>"
                f"<td>{esc(GAP_RU.get(g.reason, g.reason))}</td></tr>"
            )
        w("</tbody></table>")
        if cov["gaps_truncated"]:
            w('<p class="muted">Показаны первые пропуски; полный список ограничен.</p>')
    else:
        w('<p class="muted">Пропусков дольше порога не зафиксировано.</p>')
    w("</section>")


def _incidents_section(w, snap: ExportSnapshot) -> None:
    w(f"<section><h2>Эпизоды для проверки ({esc(snap.incidents_total)})</h2>")
    if not snap.incidents:
        w('<p class="muted">Эпизодов нет. Это не означает «нарушений нет» для интервалов без наблюдения (см. покрытие).</p></section>')
        return
    if snap.incidents_total > len(snap.incidents):
        w(f'<p class="note">{esc(f"Показаны первые {len(snap.incidents)} эпизодов из {snap.incidents_total}.")}</p>')
    w("<table><thead><tr><th>№</th><th>Время</th><th>Эпизод</th><th>Приоритет проверки</th><th>Длительность</th>"
      "<th>Решение преподавателя</th></tr></thead><tbody>")
    for n, d in enumerate(snap.incidents, 1):
        inc = d.incident
        w(
            f"<tr><td class=\"num\">{n}</td><td class=\"num\">{esc(fmt_t(inc.t_start_ms))}</td><td>{esc(RULE_RU.get(inc.rule_id.value, inc.rule_id.value))}</td>"
            f"<td>{esc(PRIORITY_RU[inc.priority.value])}</td><td class=\"num\">{esc(fmt_dur(inc.duration_ms))}</td>"
            f"<td>{esc(REVIEW_RU[inc.review_status.value])}</td></tr>"
        )
    w("</tbody></table>")
    for n, d in enumerate(snap.incidents, 1):
        _incident_card(w, snap, n, d)
    w("</section>")


def _incident_card(w, snap: ExportSnapshot, n: int, d: IncidentDetail) -> None:
    inc = d.incident
    ex = inc.explanation
    w('<article class="incident">')
    w(f"<h3>{n}. {esc(RULE_RU.get(inc.rule_id.value, inc.rule_id.value))} "
      f'<span class="muted mono">{esc(inc.incident_id)}</span></h3>')
    w(f'<div><span class="tag">{esc(CATEGORY_RU.get(inc.category.value, inc.category.value))}</span>'
      f'<span class="tag">приоритет проверки: {esc(PRIORITY_RU[inc.priority.value])}</span>'
      f'<span class="tag">{esc(inc.source_mode.value)}</span></div>')
    end = fmt_t(inc.t_end_ms) if inc.t_end_ms is not None else "не закрыт"
    w('<div class="split">')
    w('<div class="box auto"><strong>Автоматический сигнал</strong>')
    w(f"<p>{esc(ex.summary_ru)}</p>")
    _kv(
        w,
        [
            ("Время сессии", f"{fmt_t(inc.t_start_ms)} — {end}"),
            ("Время (UTC)", f"{fmt_dt(inc.wall_start)} — {fmt_dt(inc.wall_end)}"),
            ("Длительность", fmt_dur(inc.duration_ms)),
            ("Завершение", END_RU.get(inc.end_reason.value, inc.end_reason.value) if inc.end_reason else "—"),
            ("Макс. оценка модели (не вероятность)", "—" if inc.max_confidence is None else f"{inc.max_confidence:.2f}"),
            ("Среднее качество входа", "—" if inc.mean_quality is None else f"{inc.mean_quality:.2f}"),
            ("Наблюдений", inc.observation_count),
            ("Правило / конфигурация", f"{inc.rule_version} / {inc.config_version}"),
        ],
    )
    if ex.facts:
        w("<table><thead><tr><th>Факт</th><th>Значение</th></tr></thead><tbody>")
        for f in ex.facts:
            unit = "" if f.unit == "none" else f" {UNIT_RU.get(f.unit, f.unit)}"
            w(f"<tr><td>{esc(f.label_ru)}</td><td>{esc(f.value)}{esc(unit)}</td></tr>")
        w("</tbody></table>")
    if ex.caveats_ru:
        w("<ul>" + "".join(f"<li>{esc(c)}</li>" for c in ex.caveats_ru) + "</ul>")
    w("</div>")
    status_cls = "pending" if inc.review_status.value == "pending" else "human"
    w(f'<div class="box {status_cls}"><strong>Решение преподавателя: {esc(REVIEW_RU[inc.review_status.value])}</strong>')
    if d.reviews:
        w("<table><thead><tr><th>Время (UTC)</th><th>Решение</th><th>Оператор</th><th>Комментарий</th></tr></thead><tbody>")
        for r in d.reviews:
            w(
                f"<tr><td>{esc(fmt_dt(r.created_at))}</td><td>{esc(REVIEW_RU[r.decision.value])}</td><td>{esc(r.operator)}</td>"
                f"<td>{esc(r.comment) or '—'}</td></tr>"
            )
        w("</tbody></table>")
        if len(d.reviews) > 1:
            w('<p class="muted">История решений сохраняется полностью; действует последнее.</p>')
    else:
        w('<p class="muted">Эпизод ещё не проверен.</p>')
    w("</div></div>")
    _evidence_block(w, snap, d)
    w("</article>")


def _evidence_block(w, snap: ExportSnapshot, d: IncidentDetail) -> None:
    if not snap.info.retain_media:
        w('<p class="muted">Снимки не сохранялись (только метаданные).</p>')
        return
    files = [snap.evidence.get(item.evidence_id) for item in d.evidence]
    if not files:
        w('<p class="muted">Снимок для эпизода отсутствует.</p>')
        return
    for ev in files:
        if ev is None:
            continue
        item = ev.item
        caption = f"Кадр {item.frame_id if item.frame_id is not None else '—'} · {fmt_t(item.t_session_ms)} · SHA-256 {item.sha256[:16]}…"
        if ev.status == "ok" and ev.data is not None:
            data = base64.b64encode(ev.data).decode("ascii")
            w(f'<figure><img alt="Снимок эпизода" src="data:image/jpeg;base64,{data}"><figcaption>{esc(caption)}</figcaption></figure>')
        else:
            w(f'<p class="note">{esc(EVIDENCE_STATUS_RU.get(ev.status, ev.status))} — {esc(caption)}</p>')


def _environment_section(w, snap: ExportSnapshot) -> None:
    events = [o for o in snap.observations if o.get("kind") == "environment"]
    w(f"<section><h2>События среды экзамена ({len(events)})</h2>")
    if not events:
        w('<p class="muted">Событий среды не получено. Возможности защиты зависят от системы и измеряются отдельно.</p></section>')
        return
    w("<table><thead><tr><th>Время</th><th>Действие</th><th>Результат</th><th>Механизм</th><th>Подробности</th></tr></thead><tbody>")
    for raw in events:
        o = EnvironmentObservation.model_validate(raw)
        detail = ", ".join(
            x for x in (
                o.detail.shortcut and f"сочетание {o.detail.shortcut}",
                o.detail.process_name and f"процесс {o.detail.process_name}",
                o.detail.duration_ms is not None and f"длительность {fmt_dur(o.detail.duration_ms)}",
            ) if x
        )
        w(
            f"<tr><td class=\"num\">{esc(fmt_t(o.t_session_ms))}</td><td>{esc(ACTION_RU.get(o.action.value, o.action.value))}</td>"
            f"<td>{esc(ENFORCEMENT_RU.get(o.enforcement.value, o.enforcement.value))}</td>"
            f"<td class=\"mono\">{esc(o.mechanism)} ({esc(o.scope.value)})</td><td>{esc(detail) or '—'}</td></tr>"
        )
    w("</tbody></table>")
    w('<p class="muted">«Только зафиксировано» означает, что действие не было заблокировано. Блокировка системных сочетаний Windows не гарантируется.</p>')
    w("</section>")


def _answers_section(w, snap: ExportSnapshot) -> None:
    w(f"<section><h2>Ответы ({len(snap.answers)})</h2>")
    if not snap.answers:
        w('<p class="muted">Ответов не сохранено.</p></section>')
        return
    w("<table><thead><tr><th>Вопрос</th><th>Ответ</th><th>Сохранён (UTC)</th></tr></thead><tbody>")
    for a in snap.answers:
        value = ", ".join(a.value) if isinstance(a.value, list) else a.value
        w(f"<tr><td class=\"mono\">{esc(a.question_id)}</td><td>{esc(value)}</td><td>{esc(fmt_dt(a.saved_at))}</td></tr>")
    w("</tbody></table></section>")


def _integrity_section(w, snap: ExportSnapshot) -> None:
    w("<section><h2>Целостность и версии</h2>")
    w(f'<p class="note">{esc(INTEGRITY_NOTE_RU)}</p>')
    if snap.evidence:
        w("<table><thead><tr><th>Файл</th><th>Состояние</th><th>Размер, байт</th><th>SHA-256</th></tr></thead><tbody>")
        for ev in snap.evidence.values():
            state = "встроен" if ev.status == "ok" else EVIDENCE_STATUS_RU.get(ev.status, ev.status)
            w(
                f"<tr><td class=\"mono\">{esc(ev.item.evidence_id)}.jpg</td><td>{esc(state)}</td>"
                f"<td class=\"num\">{esc(ev.item.size_bytes)}</td><td class=\"mono\">{esc(ev.item.sha256)}</td></tr>"
            )
        w("</tbody></table>")
    w("<table><thead><tr><th>Компонент</th><th>Версия</th></tr></thead><tbody>")
    for key, value in snap.config_versions.items():
        w(f"<tr><td class=\"mono\">{esc(key)}</td><td class=\"mono\">{esc(value)}</td></tr>")
    w("</tbody></table>")
    if snap.counters:
        w("<h3>Служебные счётчики хранилища</h3><table><tbody>")
        for key, value in snap.counters.items():
            w(f"<tr><td class=\"mono\">{esc(key)}</td><td class=\"num\">{esc(value)}</td></tr>")
        w("</tbody></table>")
    w("</section>")
