"""Zone after the teacher's review — through the SHARED rule (A05 zone-rule-1), no local copy.

Input to ``proctor.fusion.zones.assess_session_zone``: the student's episodes WITHOUT those the teacher
dismissed. Confirmed / needs_followup / not yet reviewed episodes keep counting. The student's own zone
(computed by A05 on the student computer, sent in ``status.zone``) stays visible next to it.

Coverage is not known on the class server. It is taken as complete only when the student reports a
non-grey zone and ``monitoring == "ok"``; otherwise coverage is unknown, so without counted episodes the
result is grey ("Недостаточно данных") — never an implicit green.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

ZONES = ("red", "yellow", "grey", "green")


def _assess_fn():
    try:
        from proctor.fusion.zones import assess_session_zone  # A05 public function
    except Exception:  # module not on the path in this deployment
        return None
    return assess_session_zone


def zone_after_review(
    incidents: Iterable[dict[str, Any]],
    *,
    session_started_at: datetime | None,
    reported_zone: str | None,
    monitoring: str | None,
) -> dict[str, Any]:
    incidents = list(incidents)
    dismissed = [i for i in incidents if i.get("decision") == "dismissed"]
    counted = [i for i in incidents if i.get("decision") != "dismissed"]
    result: dict[str, Any] = {
        "reported_zone": reported_zone if reported_zone in ZONES else None,
        "zone_after_review": None,
        "reasons_ru": [],
        "rule_version": None,
        "config_version": None,
        "dismissed_excluded": len(dismissed),
        "counted": len(counted),
        "counted_by_priority": {p: sum(1 for i in counted if i.get("priority") == p) for p in ("high", "medium", "low")},
        "unreviewed": sum(1 for i in incidents if i.get("decision") is None),
        "note_ru": "Отклонённые преподавателем эпизоды не учитываются; подтверждённые, требующие проверки и непроверенные — учитываются.",
    }
    fn = _assess_fn()
    if fn is None:
        result["reasons_ru"] = ["Зона после проверки не рассчитана: модуль зон A05 недоступен на сервере."]
        return result
    starts = [datetime.fromisoformat(i["t_start_wall"].replace("Z", "+00:00")) for i in incidents]
    # times in reasons are "mm:ss from the class session start"; an episode reported before the session
    # start (clock skew, re-joined student) moves the origin back instead of collapsing to 00:00
    t0 = min([session_started_at, *starts]) if session_started_at else (min(starts) if starts else None)
    rows = []
    for i in counted:
        start = datetime.fromisoformat(i["t_start_wall"].replace("Z", "+00:00"))
        t_start_ms = max(0.0, (start - t0).total_seconds() * 1000.0) if t0 else 0.0
        rows.append(
            {
                "rule_id": i["rule_id"],
                "priority": i["priority"],
                "t_start_ms": t_start_ms,
                "t_end_ms": t_start_ms + float(i["duration_ms"]) if i.get("state") == "closed" else None,
            }
        )
    coverage = 1.0 if reported_zone in ("green", "yellow", "red") and monitoring == "ok" else None
    za = fn(rows, None, coverage=coverage)
    result.update(
        zone_after_review=za.zone,
        reasons_ru=list(za.reasons_ru),
        rule_version=za.rule_version,
        config_version=za.config_version,
    )
    return result
