"""Facts -> review priority -> Russian explanation, all computed by code from episode data.

Rules of this module:
  * every number shown in ``summary_ru`` is the rendering of an ``ExplanationFact`` value
    (ms facts are shown in seconds with one decimal and a comma, counts as integers);
  * priority is the priority of HUMAN REVIEW from the transparent table in FusionConfig, never a
    probability of guilt; the basis is spelled out in the ``priority_basis`` fact;
  * model confidence, input quality and priority stay separate facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from proctor_contracts.v1 import (
    EnforcementResult,
    EnvironmentAction,
    Explanation,
    ExplanationFact,
    IncidentEndReason,
    IncidentRule,
    ReviewPriority,
    SourceMode,
)

from .config import FusionConfig
from .episode import Episode

R = IncidentRule
LEVELS = (ReviewPriority.LOW, ReviewPriority.MEDIUM, ReviewPriority.HIGH)
REVIEW = "требуется проверка преподавателем"
MAX_FACTS = 32
MAX_CAVEATS = 8

ACTION_LABELS: dict[str, str] = {
    EnvironmentAction.SHORTCUT_ALT_TAB.value: "Alt+Tab",
    EnvironmentAction.SHORTCUT_CTRL_C.value: "Ctrl+C",
    EnvironmentAction.SHORTCUT_CTRL_V.value: "Ctrl+V",
    EnvironmentAction.SHORTCUT_CTRL_X.value: "Ctrl+X",
    EnvironmentAction.SHORTCUT_CTRL_TAB.value: "Ctrl+Tab",
    EnvironmentAction.SHORTCUT_WIN.value: "Win",
    EnvironmentAction.SHORTCUT_PRINT_SCREEN.value: "PrtScn",
    EnvironmentAction.SHORTCUT_ALT_F4.value: "Alt+F4",
    EnvironmentAction.NEW_WINDOW_BLOCKED.value: "новое окно",
    EnvironmentAction.NAVIGATION_BLOCKED.value: "переход по ссылке",
    EnvironmentAction.DEVTOOLS_BLOCKED.value: "инструменты разработчика",
    EnvironmentAction.CLIPBOARD_BLOCKED.value: "буфер обмена",
    EnvironmentAction.DISPLAY_CHANGED.value: "смена дисплеев",
}
ENFORCEMENT_LABELS: dict[str, str] = {
    EnforcementResult.BLOCKED.value: "заблокировано",
    EnforcementResult.DETECTED_ONLY.value: "только зафиксировано",
    EnforcementResult.FAILED.value: "блокировка не сработала",
    EnforcementResult.UNSUPPORTED.value: "блокировка не поддерживается",
    EnforcementResult.ALLOWED.value: "пропущено",
}
CAUSE_LABELS: dict[str, str] = {
    "health.capture": "камера",
    "health.phone": "модуль телефона",
    "health.attention": "модуль лица/взгляда",
    "health.fusion": "объединение событий",
    "health.evidence": "хранилище",
    "health.environment": "защита среды",
    "health.backend": "сервер",
    "stale.phone": "нет данных анализа телефона",
    "stale.attention": "нет данных анализа лица/взгляда",
    "undetermined.phone": "анализ телефона не дал результата",
    "undetermined.attention": "анализ лица/взгляда не дал результата",
    "environment.enforcement_error": "сбой механизма защиты среды",
    "environment.exam_mode_released": "режим экзамена снят оболочкой",
}

CAVEAT_MODE = {
    SourceMode.SYNTHETIC: "СИНТЕТИКА: сценарные данные, не результат CV.",
    SourceMode.REPLAY: "ЗАПИСЬ (replay): анализ воспроизведённого видео, не живой камеры.",
}
CAVEAT_PHONE = "Обнаружение телефона не доказывает фотографирование экрана."
CAVEAT_RAISED = "Подъём определён эвристикой по траектории рамки телефона."
CAVEAT_CAPTURE = "Направление камеры телефона по изображению может быть неразличимо; факт съёмки не установлен."
CAVEAT_UNOBSERVABLE = "По кадру нельзя определить, направлена ли камера телефона на экран."
CAVEAT_GAZE = "Направление взгляда — приблизительная оценка по голове/глазам, не трекинг глаз и не доказательство списывания."
CAVEAT_SIDES = "«Влево/вправо» — со стороны студента."
CAVEAT_UNCALIBRATED = "Калибровка взгляда не выполнена: оценка направления менее точна."
CAVEAT_CORRELATION = "Совпадение по времени повышает приоритет просмотра, но не доказывает использование телефона."
CAVEAT_FACE_MISSING = "Лицо может не находиться из-за освещения, ракурса или перекрытия кадра."
CAVEAT_MULTI = "Второе лицо может быть фото, отражением или ложным срабатыванием; личности не определяются."
CAVEAT_ENV = "Учтено только то, что сообщила оболочка; возможности блокировки зависят от системы."
CAVEAT_ESCAPE = "Потерю фокуса может вызвать системное окно или уведомление."
CAVEAT_MONITORING = "Отсутствие эпизодов в этот период не означает отсутствие нарушений."
CAVEAT_SOURCE_LOST = "Эпизод прерван: данные источника стали недоступны или неопределённы; окончание не наблюдалось."
CAVEAT_UNKNOWN = "Часть наблюдений в эпизоде была неопределённой."
CAVEAT_GAPS = "В эпизоде были пропуски данных (пропущенные кадры)."
CAVEAT_LOW_QUALITY = "Качество входных данных низкое."


def sec(ms: float) -> str:
    """Session milliseconds -> seconds with one decimal and a comma (ru)."""
    return f"{ms / 1000.0:.1f}".replace(".", ",")


def ratio(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def render_fact(fact: ExplanationFact) -> str:
    """How a fact value appears in summary_ru (used by tests to check that numbers come from facts)."""
    value = fact.value
    if isinstance(value, bool) or isinstance(value, str):
        return str(value)
    if fact.unit == "ms":
        return sec(float(value))
    if fact.unit == "ratio":
        return ratio(float(value))
    if fact.unit == "count":
        return str(int(value))
    return str(value)


def raise_level(level: ReviewPriority, steps: int = 1) -> ReviewPriority:
    return LEVELS[min(len(LEVELS) - 1, LEVELS.index(level) + steps)]


@dataclass
class Built:
    explanation: Explanation
    priority: ReviewPriority
    correlated: bool


class _Facts:
    def __init__(self) -> None:
        self.items: list[ExplanationFact] = []
        self.values: dict[str, Any] = {}

    def add(self, key: str, value: Any, unit: str, label: str) -> None:
        if isinstance(value, float):
            value = round(value, 4 if unit == "ratio" else 1)
        self.values[key] = value
        if len(self.items) < MAX_FACTS:
            self.items.append(ExplanationFact(key=key, value=value, unit=unit, label_ru=label[:200]))

    def s(self, key: str) -> str:
        return sec(float(self.values[key]))


def build(
    ep: Episode,
    duration_ms: float,
    ctx: dict[str, float],
    cfg: FusionConfig,
    mode: SourceMode,
) -> Built:
    f = _Facts()
    caveats: list[str] = []
    summary: list[str] = []
    rule = ep.rule
    c = ep.counters
    base = ReviewPriority(cfg.priority_base[rule.value])
    level = base
    basis = [f"base_{base.value}"]
    correlated = False

    def escalate(reason: str, to: ReviewPriority | None = None) -> None:
        # the reason is always listed, even when the level is already at the top
        nonlocal level
        new = to if to is not None else raise_level(level)
        if LEVELS.index(new) > LEVELS.index(level):
            level = new
        basis.append(reason)

    params = getattr(cfg, rule.value, None)
    long_ms = getattr(params, "long_ms", None)

    if rule == R.PHONE_VISIBLE:
        f.add("phone_visible_ms", duration_ms, "ms", "Телефон виден")
        f.add("appearances", ep.appearances, "count", "Появлений телефона в эпизоде")
        overlap = ctx.get("gaze_down_overlap_ms", 0.0)
        f.add("gaze_down_overlap_ms", overlap, "ms", "Взгляд оценён как «вниз» в это же время")
        f.add("max_phones", int(c.get("max_phones", 0)), "count", "Наибольшее число телефонов в кадре")
        summary.append(f"Телефон виден {f.s('phone_visible_ms')} с")
        if ep.appearances > 1:
            summary.append(f"появлений: {ep.appearances}")
        if overlap > 0:
            summary.append(f"взгляд оценён как «вниз» {f.s('gaze_down_overlap_ms')} с в это же время")
        caveats.append(CAVEAT_PHONE)
        if c.get("capture_unobservable", 0):
            f.add("screen_capture_unobservable", int(c["capture_unobservable"]), "count",
                  "Наблюдений, где направление камеры телефона неразличимо")
            caveats.append(CAVEAT_UNOBSERVABLE)
        if overlap >= cfg.corr_min_overlap_ms:
            correlated = True
            escalate("phone_with_gaze_down", to=ReviewPriority.HIGH)
    elif rule == R.PHONE_RAISED:
        f.add("phone_raised_ms", duration_ms, "ms", "Длительность сигнала подъёма")
        f.add("raise_signals", ep.count, "count", "Наблюдений с сигналом подъёма")
        f.add("appearances", ep.appearances, "count", "Отдельных подъёмов (объединены в эпизод)")
        head = f"Эвристика зафиксировала подъём телефона (сигналов: {ep.count}"
        head += f", подъёмов: {ep.appearances})" if ep.appearances > 1 else ")"
        summary.append(head)
        if duration_ms > 0:
            summary.append(f"сигнал держался {f.s('phone_raised_ms')} с")
        caveats += [CAVEAT_RAISED, CAVEAT_PHONE]
    elif rule == R.POSSIBLE_SCREEN_CAPTURE:
        f.add("screen_capture_signal_ms", duration_ms, "ms", "Признак держался")
        f.add("screen_capture_signals", ep.count, "count", "Наблюдений с признаком")
        summary.append(
            f"Возможная попытка направить телефон на экран: признак держался {f.s('screen_capture_signal_ms')} с "
            f"(наблюдений: {ep.count}); факт съёмки не установлен"
        )
        caveats += [CAVEAT_CAPTURE, CAVEAT_PHONE]
    elif rule == R.GAZE_PROLONGED_DOWN:
        f.add("gaze_down_ms", duration_ms, "ms", "Взгляд оценён как «вниз»")
        f.add("appearances", ep.appearances, "count", "Отрезков взгляда вниз в эпизоде")
        overlap = ctx.get("phone_visible_overlap_ms", 0.0)
        f.add("phone_visible_overlap_ms", overlap, "ms", "Телефон виден в это же время")
        summary.append(f"Взгляд оценён как направленный вниз {f.s('gaze_down_ms')} с")
        if overlap > 0:
            summary.append(f"в это же время виден телефон {f.s('phone_visible_overlap_ms')} с")
        caveats.append(CAVEAT_GAZE)
        if overlap >= cfg.corr_min_overlap_ms:
            correlated = True
            escalate("phone_with_gaze_down", to=ReviewPriority.HIGH)
    elif rule == R.GAZE_PROLONGED_SIDE:
        f.add("gaze_side_ms", duration_ms, "ms", "Взгляд оценён как «в сторону»")
        f.add("gaze_left_ms", float(c.get("left_ms", 0.0)), "ms", "Из них влево (со стороны студента)")
        f.add("gaze_right_ms", float(c.get("right_ms", 0.0)), "ms", "Из них вправо (со стороны студента)")
        f.add("appearances", ep.appearances, "count", "Отрезков взгляда в сторону в эпизоде")
        sides = []
        if f.values["gaze_left_ms"] > 0:
            sides.append(f"влево {f.s('gaze_left_ms')} с")
        if f.values["gaze_right_ms"] > 0:
            sides.append(f"вправо {f.s('gaze_right_ms')} с")
        text = f"Взгляд оценён как направленный в сторону {f.s('gaze_side_ms')} с"
        summary.append(text + (f" ({', '.join(sides)})" if sides else ""))
        caveats += [CAVEAT_GAZE, CAVEAT_SIDES]
    elif rule == R.FACE_MISSING:
        f.add("face_missing_ms", duration_ms, "ms", "Лицо не обнаружено")
        f.add("appearances", ep.appearances, "count", "Отрезков без лица в эпизоде")
        summary.append(f"Лицо не обнаружено в кадре {f.s('face_missing_ms')} с")
        caveats.append(CAVEAT_FACE_MISSING)
    elif rule == R.MULTIPLE_FACES:
        f.add("multiple_faces_ms", duration_ms, "ms", "Несколько лиц в кадре")
        f.add("max_face_count", int(c.get("max_faces", 2)), "count", "Наибольшее число лиц")
        summary.append(
            f"В кадре одновременно обнаружено несколько лиц (до {f.values['max_face_count']}) "
            f"в течение {f.s('multiple_faces_ms')} с"
        )
        caveats.append(CAVEAT_MULTI)
    elif rule == R.ENVIRONMENT_BLOCKED_ACTION:
        f.add("actions_total", ep.count, "count", "Ограниченных действий")
        f.add("burst_ms", duration_ms, "ms", "За время")
        parts = []
        for action in sorted(k for k in c if k.startswith("action.")):
            name = action.split(".", 1)[1]
            f.add(action, int(c[action]), "count", f"Действие: {ACTION_LABELS.get(name, name)}")
            parts.append(f"{ACTION_LABELS.get(name, name)} ×{int(c[action])}")
        outcome = []
        for enf in EnforcementResult:
            key = f"enforcement.{enf.value}"
            if c.get(key):
                f.add(key, int(c[key]), "count", f"Результат: {ENFORCEMENT_LABELS[enf.value]}")
                outcome.append(f"{ENFORCEMENT_LABELS[enf.value]}: {int(c[key])}")
        text = f"Ограниченные в экзамене действия: {ep.count}"
        if duration_ms > 0:
            text += f" за {f.s('burst_ms')} с"
        summary.append(text + (f" ({', '.join(parts)})" if parts else ""))
        if outcome:
            summary.append(", ".join(outcome))
        caveats.append(CAVEAT_ENV)
        not_blocked = ep.count - int(c.get(f"enforcement.{EnforcementResult.BLOCKED.value}", 0))
        if not_blocked > 0:  # the protection was bypassed, not just tried: zones spec = high
            escalate("not_blocked", to=ReviewPriority.HIGH)
        if ep.count >= cfg.env_repeat_count:
            escalate("repeated")
    elif rule == R.ENVIRONMENT_ESCAPE:
        lost_ms = float(c.get("focus_lost_ms", 0.0))
        lost_n = int(c.get("focus_lost_count", 0))
        foreign_n = int(c.get("foreign_window_count", 0))
        f.add("escape_ms", duration_ms, "ms", "Длительность эпизода")
        f.add("focus_lost_ms", lost_ms, "ms", "Окно экзамена без фокуса")
        f.add("focus_lost_count", lost_n, "count", "Потерь фокуса")
        f.add("foreign_window_count", foreign_n, "count", "Постороннее окно на переднем плане")
        procs = sorted(c.get("processes", ()))
        if procs:
            f.add("processes", ", ".join(procs)[:200], "none", "Процессы на переднем плане (имя файла)")
        if lost_n:
            summary.append(f"Окно экзамена теряло фокус: {lost_n} раз(а), всего {f.s('focus_lost_ms')} с")
        if foreign_n:
            text = f"постороннее окно на переднем плане: {foreign_n} раз(а)"
            summary.append(text[0].upper() + text[1:] if not summary else text)
        if procs:
            summary.append(f"процессы: {f.values['processes']}")
        caveats += [CAVEAT_ENV, CAVEAT_ESCAPE]
        if lost_ms >= cfg.env_escape_long_ms:
            escalate("long_episode")
        if lost_n >= cfg.repeat_segments:
            escalate("repeated")
    elif rule == R.MONITORING_DEGRADED:
        f.add("degraded_ms", duration_ms, "ms", "Длительность неполного наблюдения")
        causes = c.get("causes", {})
        ref = ep.t_end if ep.t_end is not None else ep.t_last
        parts = []
        totals = []
        for key in sorted(causes):
            cause = causes[key]
            label = CAUSE_LABELS.get(key, key)
            total = float(cause["total_ms"]) + (max(0.0, ref - cause["start"]) if cause["active"] else 0.0)
            totals.append(total)
            f.add(f"{key}_ms", total, "ms", f"Пробел данных: {label}")
            f.add(f"{key}.code", cause["code"], "none", f"Код: {label}")
            text = f"{label} — {cause['code']}"
            if total > 0:
                text += f", {f.s(key + '_ms')} с"
            parts.append(text)
        summary.append(f"Наблюдение было неполным {f.s('degraded_ms')} с ({'; '.join(parts)})")
        summary.append("эпизоды за этот период могли быть не зафиксированы")
        caveats.append(CAVEAT_MONITORING)
        if any(total >= cfg.monitoring_long_ms for total in totals):
            escalate("long_gap")

    if long_ms is not None and duration_ms > long_ms:
        escalate("long_episode")
    if rule.value in cfg.repeat_escalation_rules and ep.appearances >= cfg.repeat_segments:
        escalate("repeated")

    # --- common facts: confidence, quality and priority stay separate ---
    if rule not in (R.ENVIRONMENT_BLOCKED_ACTION, R.ENVIRONMENT_ESCAPE, R.MONITORING_DEGRADED):
        f.add("observations", ep.count, "count", "Наблюдений с признаком")
    if ep.conf_max is not None:
        f.add("max_confidence", ep.conf_max, "ratio", "Макс. оценка модели (не вероятность нарушения)")
    if ep.mean_quality is not None:
        f.add("mean_quality", ep.mean_quality, "ratio", "Среднее качество входных данных")
    if ep.unknown_n:
        f.add("unknown_observations", ep.unknown_n, "count", "Неопределённых наблюдений в эпизоде")
    if ep.max_gap_ms > cfg.sample_hold_ms:
        f.add("max_data_gap_ms", ep.max_gap_ms, "ms", "Наибольший пропуск данных")
    if c.get("uncalibrated"):
        f.add("uncalibrated_observations", int(c["uncalibrated"]), "count", "Наблюдений без калибровки взгляда")
    if c.get("head_fallback"):
        f.add("head_direction_fallback", int(c["head_fallback"]), "count", "Направление по положению головы")
    f.add("priority_basis", ",".join(basis), "none", "Основание приоритета просмотра")
    if ep.end_reason is not None:
        f.add("end_reason", ep.end_reason.value, "none", "Причина закрытия эпизода")

    # --- caveats (most important first, capped) ---
    if correlated:
        caveats.append(CAVEAT_CORRELATION)
    if c.get("uncalibrated"):
        caveats.append(CAVEAT_UNCALIBRATED)
    if ep.end_reason == IncidentEndReason.SOURCE_LOST:
        caveats.append(CAVEAT_SOURCE_LOST)
    if ep.unknown_n:
        caveats.append(CAVEAT_UNKNOWN)
    if ep.max_gap_ms > cfg.sample_hold_ms:
        caveats.append(CAVEAT_GAPS)
    if ep.mean_quality is not None and ep.mean_quality < 0.5:
        caveats.append(CAVEAT_LOW_QUALITY)
    mode_caveat = CAVEAT_MODE.get(mode)
    if mode_caveat:
        caveats.insert(0, mode_caveat)
    seen: list[str] = []
    for item in caveats:
        if item not in seen:
            seen.append(item[:300])

    text = "; ".join(summary)
    if rule != R.MONITORING_DEGRADED:
        text += f"; {REVIEW}"
    text = text + "."
    return Built(
        explanation=Explanation(summary_ru=text[:1000], summary_kk=None, facts=f.items, caveats_ru=seen[:MAX_CAVEATS]),
        priority=level,
        correlated=correlated,
    )
