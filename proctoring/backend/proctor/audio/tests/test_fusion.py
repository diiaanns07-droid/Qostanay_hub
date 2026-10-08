from datetime import datetime, timedelta, timezone

from proctor_contracts.v1 import AudioObservation, IncidentEndReason, Producer
from proctor.fusion.engine import FusionEngine


def observation(t, state="present", status="ok", reasons=None):
    return AudioObservation(observation_id=f"audio-{t}", session_id="audio-test", frame_id=None,
        t_session_ms=t, wall_time=datetime(2026, 10, 8, tzinfo=timezone.utc) + timedelta(milliseconds=t),
        source_mode="live", producer=Producer(module="test", version="1"), status=status,
        voice_like=state, voice_probability=0.9 if state == "present" else None, reasons=reasons or [])


def audio(changes):
    return [c for c in changes if c.incident.rule_id == "background_speech"]


def test_threshold_and_finish():
    engine = FusionEngine("audio-test", "live")
    for t in range(0, 4000, 500):
        assert audio(engine.consume(observation(t))) == []
    changes = audio(engine.consume(observation(4000)))
    assert len(changes) == 1 and changes[0].change == "opened"
    incident = changes[0].incident
    assert incident.priority == "low" and incident.category == "audio"
    assert incident.duration_ms == 4000 and incident.t_end_ms is None
    assert incident.trigger_frame_id is None
    closed = audio(engine.finish(4200, IncidentEndReason.SESSION_FINISHED))
    assert closed[0].change == "closed" and closed[0].incident.t_end_ms == 4000
    assert not audio(engine.finish(4200, IncidentEndReason.SESSION_FINISHED))


def test_three_episodes_medium():
    engine = FusionEngine("audio-test", "live")
    changes = []
    for t in range(0, 50000, 500):
        state = "present" if t % 16000 < 5000 else "absent"
        changes += audio(engine.consume(observation(t, state)))
    opened = [c.incident for c in changes if c.change == "opened"]
    assert len(opened) == 3
    assert [i.priority.value for i in opened] == ["low", "low", "medium"]
    assert len({i.incident_id for i in opened}) == 3


def test_unknown_pause_missing_stream():
    for interruption in ("unknown", "pause", "stale"):
        engine = FusionEngine("audio-test", "live")
        for t in range(0, 4500, 500):
            engine.consume(observation(t))
        if interruption == "unknown":
            changes = engine.consume(observation(4500, "unknown", "degraded"))
        elif interruption == "pause":
            changes = engine.set_paused(True, 4500)
        else:
            changes = engine.advance(4601)
        closed = audio(changes)
        assert len(closed) == 1 and closed[0].change == "closed"
        assert closed[0].incident.end_reason == ("session_paused" if interruption == "pause" else "source_lost")


def test_fallback_and_duplicate_and_foreign():
    engine = FusionEngine("audio-test", "live")
    for t in range(0, 4500, 500):
        obs = observation(t, status="degraded", reasons=["energy_fallback"])
        changes = audio(engine.consume(obs))
        assert not audio(engine.consume(obs))
    assert changes[0].incident.priority == "low"
    assert not audio(engine.consume(observation(4500).model_copy(update={"session_id": "foreign"})))


def test_repeat_window_expires():
    engine = FusionEngine("audio-test", "live")
    priorities = []
    for start in (0, 10000, 400000):
        for t in range(start, start + 4500, 500):
            priorities += [c.incident.priority.value for c in audio(engine.consume(observation(t))) if c.change == "opened"]
        engine.consume(observation(start + 4500, "unknown", "degraded"))
    assert priorities == ["low", "low", "low"]
