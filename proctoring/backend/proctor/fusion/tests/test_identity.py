"""identity_mismatch (contracts 1.1, A13 IdentityObservation): same_person = absent >= 3 s -> high."""

from __future__ import annotations


from proctor.fusion import create_incident_engine
from proctor.fusion.replay import final_incidents, replay
from proctor.fusion.scenario import ScenarioBuilder
from proctor.fusion.tests.checks import assert_contract_valid
from proctor.fusion.zones import assess_session_zone
from proctor.settings import Settings
from proctor_contracts.v1 import IdentityObservation, IncidentCategory, IncidentEndReason
from proctor_contracts.v1 import IncidentRule as R
from proctor_contracts.v1 import ReviewPriority as P


def identity(b: ScenarioBuilder, t: float, same: str, *, enrolled: bool = True, status: str = "ok", n: list[int] = [0]) -> IdentityObservation:
    base = b.attention(t).model_dump(mode="json")
    n[0] += 1
    return IdentityObservation.model_validate({
        "kind": "identity",
        "observation_id": f"id-{n[0]}",
        "session_id": base["session_id"],
        "frame_id": base["frame_id"],
        "t_session_ms": t,
        "wall_time": base["wall_time"],
        "source_mode": base["source_mode"],
        "producer": {"module": "identity", "version": "0.1.0"},
        "status": status,
        "same_person": same,
        "similarity": 0.2 if same == "absent" else 0.8,
        "enrolled": enrolled,
    })


def run(same_at, end: float = 20_000.0, step: float = 500.0, **kw):
    """same_at(t) -> "present" | "absent" | "unknown" | None (no observation at t)."""
    b = ScenarioBuilder()
    ids = []
    t = 0.0
    while t < end:
        s = same_at(t)
        if s is not None:
            ids.append(identity(b, t, s, **kw))
        t += step
    att = b.run("attention", [{"from": 0, "to": end, "step": 100, "direction": "center"}])
    ph = b.run("phone", [{"from": 0, "to": end, "step": 125, "visible": "absent"}])
    eng = create_incident_engine("fx-session-0001", "synthetic", Settings())
    changes = replay(eng, sorted([*ids, *att, *ph], key=lambda o: o.t_session_ms), finish_t=end)
    assert_contract_valid(changes)
    return [i for i in final_incidents(changes).values() if i.rule_id == R.IDENTITY_MISMATCH]


def test_absent_for_4_s_is_one_high_episode_with_one_line_text():
    incs = run(lambda t: "absent" if 63_000 <= t < 67_500 else "present", end=80_000.0)
    assert len(incs) == 1
    i = incs[0]
    assert i.priority == P.HIGH and i.category == IncidentCategory.IDENTITY
    assert i.explanation.summary_ru.startswith("Лицо не совпадает с лицом в начале экзамена — 01:03, 4 с")
    assert "\n" not in i.explanation.summary_ru
    assert i.end_reason == IncidentEndReason.CONDITION_CLEARED
    za = assess_session_zone(incs, None, coverage=1.0)
    assert za.zone == "red" and za.reasons_ru[0].startswith("Лицо не совпадает с лицом в начале экзамена — 01:03")


def test_short_mismatch_unknown_and_not_enrolled_give_nothing():
    assert run(lambda t: "absent" if 5_000 <= t < 7_000 else "present") == []  # 1.5 s of evidence
    assert run(lambda t: "unknown") == []
    assert run(lambda t: "absent", enrolled=False) == []  # nothing to compare with
    assert run(lambda t: "absent", status="error") == []


def test_unknown_inside_does_not_clear_and_stream_loss_is_source_lost():
    incs = run(lambda t: "absent" if t < 6_000 else ("unknown" if t < 8_000 else "absent"), end=12_000.0)
    assert len(incs) == 1 and incs[0].priority == P.HIGH  # unknown neither cleared nor split the episode
    lost = run(lambda t: "absent" if t < 6_000 else None, end=20_000.0)  # identity stops reporting
    assert len(lost) == 1 and lost[0].end_reason == IncidentEndReason.SOURCE_LOST


def test_engine_without_identity_observations_is_unchanged():
    assert run(lambda t: None) == []
