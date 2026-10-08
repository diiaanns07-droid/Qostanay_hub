"""identity_mismatch in A05's FusionEngine: absent >= 3 s -> HIGH episode; unknown is never an episode."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from proctor.fusion.engine import FusionEngine
from proctor.fusion.zones import reason_ru
from proctor_contracts.v1 import IdentityObservation, IncidentEndReason, Producer

SID = "identity-fusion-test"
T0 = 30_000.0  # first observation the engine sees = exam start (session time includes preflight)


def obs(t, state="present", sim=0.85, enrolled=True, status="ok", reasons=None):
    if state != "absent" and state != "present":
        sim = None
    return IdentityObservation(
        observation_id=f"identity-{int(t)}", session_id=SID, frame_id=int(t // 100), t_session_ms=t,
        wall_time=datetime(2026, 10, 8, 9, tzinfo=timezone.utc) + timedelta(milliseconds=t), source_mode="live",
        producer=Producer(module="identity", version="0.1.0"), status=status, same_person=state, similarity=sim,
        enrolled=enrolled, reasons=reasons or ([] if state == "present" else ["below_threshold"] if state == "absent" else ["no_face"]))


def identity(changes):
    return [c for c in changes if c.incident.rule_id == "identity_mismatch"]


def feed(engine, rows):
    out = []
    for row in rows:
        out += identity(engine.consume(obs(*row) if isinstance(row, tuple) else row))
    return out


def test_absent_for_3s_opens_high_episode_with_explanation():
    engine = FusionEngine(SID, "live")
    rows = [(T0 + i * 1000, "present") for i in range(5)]  # 5 s same person
    rows += [(T0 + 5000 + i * 1000, "absent", 0.12 - 0.01 * i) for i in range(3)]  # 2 s span: not yet
    assert feed(engine, rows) == []
    opened = feed(engine, [(T0 + 8000, "absent", 0.08)])  # first..last absent = 3 s
    assert len(opened) == 1 and opened[0].change == "opened"
    inc = opened[0].incident
    assert (inc.category, inc.priority, inc.t_start_ms, inc.t_end_ms) == ("identity", "high", T0 + 5000, None)
    assert inc.explanation.summary_ru == "Лицо не совпадает с лицом в начале экзамена — 00:05, 3 с"
    facts = {f.key: f.value for f in inc.explanation.facts}
    assert facts["threshold"] == 0.363 and facts["similarity_min"] == 0.08 and facts["observations"] == 4
    assert facts["start_from_exam_ms"] == 5000 and facts["duration_ms"] == 3000
    updates = feed(engine, [(T0 + 9000, "absent", 0.1), (T0 + 10000, "absent", 0.1)])
    assert [c.change for c in updates] == ["updated", "updated"]
    closed = feed(engine, [(T0 + 11000, "present")])
    assert len(closed) == 1 and closed[0].change == "closed" and closed[0].incident.end_reason == "condition_cleared"
    done = closed[0].incident
    assert done.t_end_ms == T0 + 10000 and done.duration_ms == 5000 and done.observation_count == 6
    assert done.explanation.summary_ru == "Лицо не совпадает с лицом в начале экзамена — 00:05, 5 с"
    assert reason_ru(done, exam_start_ms=T0) == "Лицо не совпадает с лицом в начале экзамена — 00:05, 5 с"


def test_unknown_is_never_an_episode_and_breaks_the_run():
    engine = FusionEngine(SID, "live")
    rows = [(T0 + i * 1000, "unknown") for i in range(10)]  # no face / several faces for 10 s
    rows += [(T0 + 10000, "absent", 0.1), (T0 + 11000, "absent", 0.1), (T0 + 12000, "unknown"),
             (T0 + 13000, "absent", 0.1), (T0 + 14000, "absent", 0.1), (T0 + 15000, "absent", 0.1)]
    assert feed(engine, rows) == []  # 2 s + 2 s absent runs, split by unknown


def test_not_enrolled_or_error_is_not_evidence():
    engine = FusionEngine(SID, "live")
    rows = [obs(T0 + i * 1000, "unknown", enrolled=False, reasons=["enrolling"]) for i in range(3)]
    rows += [obs(T0 + 3000 + i * 1000, "unknown", status="error", reasons=["model_missing"]) for i in range(6)]
    assert feed(engine, rows) == []


def test_gap_in_stream_closes_as_source_lost_and_finish_closes():
    engine = FusionEngine(SID, "live")
    feed(engine, [(T0 + i * 1000, "absent", 0.1) for i in range(5)])
    closed = identity(engine.advance(T0 + 4000 + 2600))
    assert len(closed) == 1 and closed[0].incident.end_reason == "source_lost"
    engine2 = FusionEngine(SID, "live")
    feed(engine2, [(T0 + i * 1000, "absent", 0.1) for i in range(4)])
    fin = identity(engine2.finish(T0 + 3500, IncidentEndReason.SESSION_FINISHED))
    assert len(fin) == 1 and fin[0].incident.end_reason == "session_finished" and fin[0].incident.t_end_ms == T0 + 3000


def test_pause_closes_episode():
    engine = FusionEngine(SID, "live")
    feed(engine, [(T0 + i * 1000, "absent", 0.1) for i in range(4)])
    paused = identity(engine.set_paused(True, T0 + 3500))
    assert len(paused) == 1 and paused[0].incident.end_reason == "session_paused"


def test_same_person_never_opens():
    engine = FusionEngine(SID, "live")
    assert feed(engine, [(T0 + i * 1000, "present", 0.6 + 0.01 * (i % 30)) for i in range(120)]) == []
