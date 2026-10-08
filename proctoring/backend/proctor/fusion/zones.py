"""Review zones for the teacher (owner: A05). Rule ``zone-rule-1``.

A zone is a PRIORITY OF REVIEW for the teacher, never a verdict:

* ``red``    "Проверить в первую очередь" — >= 1 high OR >= 3 medium episodes;
* ``yellow`` "Требует внимания"           — otherwise >= 1 medium OR >= 3 low;
* ``grey``   "Недостаточно данных"         — otherwise coverage < 80 % of the exam time without pauses
  (or coverage unknown: missing data is never "all clear");
* ``green``  "Без замечаний"               — otherwise.

Pure function: no clock, I/O or globals. Thresholds are hypotheses kept in ``ZoneConfig`` and
versioned (``rule_version`` + ``config_version``). The teacher's review decision never changes the
zone: reviews are not an input (a UI shows the decision next to the zone).

Input incidents may be contract ``Incident`` objects, plain dicts with the same field names, or any
object with ``rule_id`` / ``priority`` / ``t_start_ms`` / ``t_end_ms`` (e.g. ``proctor.fusion.audio_rules``
episodes for audio/headphones, which have no contract type yet).

``monitoring_degraded`` incidents are NOT counted as behaviour: a monitoring gap lowers coverage
(-> grey), it is not "requires attention" about the student. Configurable (``coverage_rules``).

Usage (A08)::

    from proctor.fusion.zones import assess_session_zone
    za = assess_session_zone(incidents, summary)          # summary: contract SessionSummary or None
    za.zone, za.reasons_ru, za.rule_version               # "red", ["Второе лицо в кадре — 00:36, 4 с"], "zone-rule-1"
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

ZONE_RULE_VERSION = "zone-rule-1"
ZONES = ("red", "yellow", "grey", "green")  # also the overview sort order
ZONE_LABELS_RU = {
    "red": "Проверить в первую очередь",
    "yellow": "Требует внимания",
    "grey": "Недостаточно данных",
    "green": "Без замечаний",
}
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

#: short Russian names for reasons_ru (what), time and duration are appended
RULE_LABELS_RU = {
    "phone_visible": "Телефон в кадре",
    "phone_raised": "Телефон поднят",
    "possible_screen_capture": "Телефон направлен на экран",
    "gaze_prolonged_down": "Долгий взгляд вниз",
    "gaze_prolonged_side": "Долгий взгляд в сторону",
    "face_missing": "Лицо не видно в кадре",
    "multiple_faces": "Второе лицо в кадре",
    "environment_blocked_action": "Ограниченные действия в экзамене",
    "environment_escape": "Выход из окна экзамена",
    "monitoring_degraded": "Наблюдение было неполным",
    "background_speech": "Возможная речь или фоновый разговор",
    "headphones_visible": "Видны наушники",
}


@dataclass(frozen=True)
class ZoneConfig:
    """Thresholds of zone-rule-1 (hypotheses, versioned)."""

    red_high_min: int = 1
    red_medium_min: int = 3
    yellow_medium_min: int = 1
    yellow_low_min: int = 3
    grey_coverage_below: float = 0.8  # coverage < this -> grey (exactly 0.8 is NOT grey)
    max_reasons: int = 3
    coverage_rules: tuple[str, ...] = ("monitoring_degraded",)  # gaps, not behaviour

    def __post_init__(self) -> None:
        if min(self.red_high_min, self.red_medium_min, self.yellow_medium_min, self.yellow_low_min) < 1:
            raise ValueError("zone thresholds must be >= 1")
        if not 0.0 <= self.grey_coverage_below <= 1.0:
            raise ValueError("grey_coverage_below must be within [0, 1]")
        if not 1 <= self.max_reasons <= 3:
            raise ValueError("max_reasons must be 1..3")

    @property
    def version(self) -> str:
        canonical = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return f"{ZONE_RULE_VERSION}+{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]}"


@dataclass(frozen=True)
class ZoneAssessment:
    zone: str  # "green" | "yellow" | "red" | "grey" (contract 1.1 ReviewZone values)
    reasons_ru: list[str]
    rule_version: str = ZONE_RULE_VERSION
    config_version: str = ""
    label_ru: str = ""
    incidents_by_priority: dict[str, int] = field(default_factory=dict)
    coverage: float | None = None  # 0..1, None = unknown


# --------------------------------------------------------------------------- input access


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _str(value: Any) -> str:
    return str(getattr(value, "value", value)) if value is not None else ""


def mmss(t_ms: float) -> str:
    total = max(0, int(round(t_ms / 1000.0)))
    return f"{total // 60:02d}:{total % 60:02d}"


def reason_ru(incident: Any, exam_start_ms: float = 0.0) -> str:
    """'Телефон направлен на экран — 00:41, 7 с' (duration omitted for point events / < 1 s)."""
    rule = _str(_get(incident, "rule_id"))
    label = RULE_LABELS_RU.get(rule, rule.replace("_", " "))
    t0 = float(_get(incident, "t_start_ms", 0.0) or 0.0)
    t1 = _get(incident, "t_end_ms")
    text = f"{label} — {mmss(t0 - exam_start_ms)}"
    if t1 is not None:
        dur_s = int(round((float(t1) - t0) / 1000.0))
        if dur_s >= 1:
            text += f", {dur_s} с"
    return text


def coverage_from_summary(summary: Any) -> float | None:
    """observed_ms / (exam duration - paused_ms). Exam duration = finished_at - started_at.
    None when it cannot be computed (no summary, exam not started/finished, zero length)."""
    if summary is None:
        return None
    observed = _get(summary, "observed_ms")
    paused = float(_get(summary, "paused_ms", 0.0) or 0.0)
    session = _get(summary, "session")
    started, finished = _get(session, "started_at"), _get(session, "finished_at")
    if observed is None or started is None or finished is None:
        return None
    if isinstance(started, str):
        started = datetime.fromisoformat(started.replace("Z", "+00:00"))
    if isinstance(finished, str):
        finished = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    active_ms = (finished - started).total_seconds() * 1000.0 - paused
    if active_ms <= 0:
        return None
    return max(0.0, min(1.0, float(observed) / active_ms))


# --------------------------------------------------------------------------- the rule


def assess_session_zone(
    incidents: Iterable[Any],
    summary: Any = None,
    config: ZoneConfig | None = None,
    *,
    coverage: float | None = None,
    extra_uncovered_ms: float = 0.0,
) -> ZoneAssessment:
    """zone-rule-1. ``coverage`` (0..1) overrides the value computed from ``summary``;
    ``extra_uncovered_ms`` (e.g. audio device missing/degraded) is subtracted from observed time."""
    cfg = config or ZoneConfig()
    session = _get(summary, "session") if summary is not None else None
    exam_start = float(_get(session, "exam_started_t_ms", 0.0) or 0.0) if session is not None else 0.0

    counted: list[Any] = []
    for inc in incidents:
        rule = _str(_get(inc, "rule_id"))
        prio = _str(_get(inc, "priority"))
        if prio not in PRIORITY_ORDER:
            raise ValueError(f"incident priority must be low/medium/high, got {prio!r}")
        if rule in cfg.coverage_rules:
            continue
        counted.append(inc)
    by = {p: sum(1 for i in counted if _str(_get(i, "priority")) == p) for p in ("high", "medium", "low")}

    cov = coverage if coverage is not None else coverage_from_summary(summary)
    if cov is not None and extra_uncovered_ms > 0 and summary is not None and coverage is None:
        observed = float(_get(summary, "observed_ms", 0.0) or 0.0)
        cov = cov * max(0.0, observed - extra_uncovered_ms) / observed if observed > 0 else 0.0

    if by["high"] >= cfg.red_high_min or by["medium"] >= cfg.red_medium_min:
        zone = "red"
    elif by["medium"] >= cfg.yellow_medium_min or by["low"] >= cfg.yellow_low_min:
        zone = "yellow"
    elif cov is None or cov < cfg.grey_coverage_below:
        zone = "grey"
    else:
        zone = "green"

    reasons: list[str] = []
    if zone in ("red", "yellow"):
        ranked = sorted(counted, key=lambda i: (PRIORITY_ORDER[_str(_get(i, "priority"))], float(_get(i, "t_start_ms", 0.0) or 0.0)))
        reasons = [reason_ru(i, exam_start) for i in ranked[: cfg.max_reasons]]
    elif zone == "grey":
        reasons = [
            "Покрытие наблюдения не измерено" if cov is None
            else f"Наблюдение неполное: {int(cov * 100)}% времени экзамена без пауз (порог {int(cfg.grey_coverage_below * 100)}%)"
        ]
    else:
        reasons = ["Эпизодов нет, наблюдение полное"]
    return ZoneAssessment(
        zone=zone,
        reasons_ru=reasons[: cfg.max_reasons],
        rule_version=ZONE_RULE_VERSION,
        config_version=cfg.version,
        label_ru=ZONE_LABELS_RU[zone],
        incidents_by_priority=by,
        coverage=None if cov is None else round(cov, 4),
    )
