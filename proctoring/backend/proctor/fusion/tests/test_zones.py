"""zone-rule-1 (proctor.fusion.zones) and audio/headphones rules (proctor.fusion.audio_rules)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from proctor.fusion.audio_rules import AuxEpisode, background_speech, headphones_visible, uncovered_ms
from proctor.fusion.zones import ZONES, ZoneConfig, assess_session_zone, coverage_from_summary, mmss, reason_ru

FORBIDDEN = re.compile(r"списыва|виновн|вероятност", re.IGNORECASE)


def inc(priority: str, rule: str = "gaze_prolonged_side", t0: float = 0.0, t1: float | None = 5000.0) -> dict:
    return {"rule_id": rule, "priority": priority, "t_start_ms": t0, "t_end_ms": t1}


def summary(observed_ms: float, exam_ms: float = 600_000.0, paused_ms: float = 0.0, **extra) -> dict:
    start = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)
    return {
        "session": {"started_at": start, "finished_at": start + timedelta(milliseconds=exam_ms), "exam_started_t_ms": 0.0},
        "observed_ms": observed_ms,
        "paused_ms": paused_ms,
        **extra,
    }


FULL = summary(600_000.0)


@pytest.mark.parametrize(
    "prios, expected",
    [
        (["high"], "red"),
        (["medium"] * 3, "red"),  # exactly 3 medium
        (["medium"] * 2, "yellow"),
        (["medium"], "yellow"),
        (["low"] * 3, "yellow"),  # exactly 3 low
        (["low"] * 2, "green"),
        (["low"] * 2 + ["medium"] * 2, "yellow"),
        (["low"] * 10 + ["medium"] * 2, "yellow"),
        (["high", "low"], "red"),
        ([], "green"),
    ],
)
def test_every_branch_with_full_coverage(prios, expected):
    za = assess_session_zone([inc(p, t0=i * 10_000.0) for i, p in enumerate(prios)], FULL)
    assert za.zone == expected and za.rule_version == "zone-rule-1"
    assert za.config_version.startswith("zone-rule-1+")


def test_grey_below_80_and_exact_80_is_not_grey():
    assert assess_session_zone([], summary(479_999.0)).zone == "grey"
    assert assess_session_zone([], summary(480_000.0)).zone == "green"  # exactly 80 % -> not grey
    za = assess_session_zone([], summary(300_000.0))
    assert za.zone == "grey" and za.coverage == 0.5 and "50%" in za.reasons_ru[0]


def test_pauses_are_excluded_from_exam_time():
    # 10 min exam, 3 min paused, 6 min observed -> 6/7 = 86 % -> green
    assert assess_session_zone([], summary(360_000.0, paused_ms=180_000.0)).coverage == pytest.approx(0.8571, abs=1e-4)
    assert assess_session_zone([], summary(360_000.0, paused_ms=180_000.0)).zone == "green"


def test_red_and_yellow_win_over_grey():
    low_cov = summary(60_000.0)
    assert assess_session_zone([inc("high")], low_cov).zone == "red"
    assert assess_session_zone([inc("medium")], low_cov).zone == "yellow"
    assert assess_session_zone([inc("low")] * 3, low_cov).zone == "yellow"
    assert assess_session_zone([inc("low")], low_cov).zone == "grey"


def test_empty_session_and_unknown_coverage_are_grey_not_green():
    za = assess_session_zone([], None)
    assert za.zone == "grey" and za.coverage is None and za.reasons_ru == ["Покрытие наблюдения не измерено"]
    not_finished = summary(1000.0)
    not_finished["session"]["finished_at"] = None
    assert assess_session_zone([], not_finished).zone == "grey"


def test_teacher_decision_does_not_change_zone():
    incidents = [inc("high", "multiple_faces")]
    a = assess_session_zone(incidents, FULL)
    reviewed = summary(600_000.0, reviews_by_decision={"dismissed": 1})
    b = assess_session_zone([{**incidents[0], "review": {"decision": "dismissed"}}], reviewed)
    assert a.zone == b.zone == "red" and a.reasons_ru == b.reasons_ru


def test_monitoring_gaps_are_coverage_not_behaviour():
    gaps = [inc("low", "monitoring_degraded", t0=i * 1000.0) for i in range(5)] + [inc("medium", "monitoring_degraded")]
    za = assess_session_zone(gaps, FULL)
    assert za.zone == "green" and za.incidents_by_priority == {"high": 0, "medium": 0, "low": 0}


def test_reasons_ru_format_order_and_limit():
    incidents = [
        inc("low", "gaze_prolonged_side", 5_000, 10_000),
        inc("high", "possible_screen_capture", 41_000, 48_000),
        inc("medium", "face_missing", 125_000, 131_400),
        inc("high", "multiple_faces", 300_000, 303_600),
    ]
    za = assess_session_zone(incidents, FULL)
    assert za.zone == "red" and za.label_ru == "Проверить в первую очередь"
    assert za.reasons_ru == [
        "Телефон направлен на экран — 00:41, 7 с",
        "Второе лицо в кадре — 05:00, 4 с",
        "Лицо не видно в кадре — 02:05, 6 с",
    ]
    for r in za.reasons_ru:
        assert not FORBIDDEN.search(r) and re.search(r"— \d\d:\d\d", r)


def test_reason_time_is_relative_to_exam_start_and_point_events_have_no_duration():
    assert reason_ru(inc("medium", "headphones_visible", 200_000, None), exam_start_ms=10_000) == "Видны наушники — 03:10"
    assert reason_ru(inc("medium", "face_missing", 0, 400)) == "Лицо не видно в кадре — 00:00"
    assert mmss(3_599_000) == "59:59" and mmss(3_600_000) == "60:00"


def test_contract_incident_objects_are_accepted():
    from proctor.fusion.scenario import ScenarioBuilder  # noqa: F401  (contract enums via a real engine run)
    from proctor_contracts.v1 import IncidentRule, ReviewPriority

    class Obj:
        rule_id = IncidentRule.MULTIPLE_FACES
        priority = ReviewPriority.HIGH
        t_start_ms = 36_600.0
        t_end_ms = 40_100.0

    assert assess_session_zone([Obj()], FULL).zone == "red"


def test_invalid_priority_and_config_are_rejected():
    with pytest.raises(ValueError):
        assess_session_zone([inc("critical")], FULL)
    with pytest.raises(ValueError):
        ZoneConfig(red_medium_min=0)
    assert ZoneConfig(red_medium_min=4).version != ZoneConfig().version  # thresholds are versioned


def test_config_thresholds_change_the_result():
    assert assess_session_zone([inc("medium")] * 3, FULL, ZoneConfig(red_medium_min=4)).zone == "yellow"


def test_explicit_coverage_and_audio_uncovered_time():
    assert assess_session_zone([], FULL, coverage=0.79).zone == "grey"
    # audio device missing for 3 of 10 minutes -> 70 % -> grey
    assert assess_session_zone([], FULL, extra_uncovered_ms=180_000.0).zone == "grey"
    assert coverage_from_summary(None) is None
    assert set(ZONES) == {"red", "yellow", "grey", "green"}


# --------------------------------------------------------------------------- audio / headphones


def _samples(on: list[tuple[float, float]], until: float, step: float = 500.0, none: list[tuple[float, float]] = ()):
    out = []
    t = 0.0
    while t < until:
        if any(a <= t < b for a, b in none):
            v = None
        else:
            v = any(a <= t < b for a, b in on)
        out.append((t, v))
        t += step
    return out


def test_background_speech_low_then_medium_on_third_within_5_min():
    # three 6 s voice episodes at 01:00, 02:00, 03:00 and a fourth after 5 more minutes
    on = [(60_000, 66_000), (120_000, 126_000), (180_000, 186_000), (520_000, 526_000)]
    eps = background_speech(_samples(on, 600_000))
    assert [e.priority for e in eps] == ["low", "low", "medium", "low"]
    assert eps[0].summary_ru == "Возможная речь или фоновый разговор — 01:00, 6 с"
    assert all(isinstance(e, AuxEpisode) and e.rule_id == "background_speech" for e in eps)


def test_short_or_sparse_speech_is_not_an_episode():
    assert background_speech(_samples([(10_000, 13_500)], 60_000)) == []  # 3.5 s < 4 s
    sparse = [(10_000 + i * 2000, 10_000 + i * 2000 + 1000) for i in range(10)]  # 1 s of every 2 s: 3 s / 6 s
    assert background_speech(_samples(sparse, 60_000)) == []


def test_audio_unknown_is_uncovered_time_not_silence():
    s = _samples([], 60_000, none=[(0, 30_000)])
    assert background_speech(s) == [] and uncovered_ms(s) == pytest.approx(30_000.0)
    assert uncovered_ms([(0.0, True), (10_000.0, False)]) == pytest.approx(9_400.0)  # hole in the stream


def test_headphones_visible_3s_medium_and_unknown_breaks_the_run():
    eps = headphones_visible(_samples([(190_000, 194_000)], 200_000))
    assert len(eps) == 1 and eps[0].priority == "medium" and eps[0].summary_ru == "Видны наушники — 03:10"
    assert headphones_visible(_samples([(10_000, 12_500)], 20_000)) == []  # 2.5 s
    broken = _samples([(10_000, 14_000)], 20_000, none=[(11_500, 12_000)])
    assert headphones_visible(broken) == []  # 1.5 s + unknown + 2 s: never bridged through missing data
    za = assess_session_zone(eps, FULL)
    assert za.zone == "yellow" and za.reasons_ru == ["Видны наушники — 03:10, 4 с"]
