"""ТЗ 2.1 phone raised / possible screen capture from geometry relative to the A04 face (a05-rules-1.4.0).

Builder face (attention template): bbox y 0.15..0.55 -> face line at 0.15 + 0.65 * 0.40 = 0.41.
"""

from __future__ import annotations

from proctor.fusion import create_incident_engine
from proctor.fusion.replay import final_incidents, replay
from proctor.fusion.scenario import ScenarioBuilder
from proctor.fusion.tests.checks import assert_contract_valid
from proctor.settings import Settings
from proctor_contracts.v1 import IncidentRule as R
from proctor_contracts.v1 import PhoneObservation
from proctor_contracts.v1 import ReviewPriority as P


def phone_det(top: float, cx: float = 0.45, h: float = 0.3, w: float = 0.12, conf: float = 0.8) -> dict:
    return {"bbox": {"x_min": cx - w / 2, "y_min": top, "x_max": cx + w / 2, "y_max": min(1.0, top + h)},
            "confidence": conf, "class_name": "cell phone", "class_index": 67}


def run(box_at, end: float = 15_000.0, face: bool = True):
    """box_at(t) -> detection dict or None, phone observations every 125 ms (A03 signals: visible only)."""
    b = ScenarioBuilder()
    obs = []
    t = 0.0
    while t < end:
        d = box_at(t)
        data = b.phone(t, visible="present" if d else "absent").model_dump(mode="json")
        data["detections"] = [d] if d else []
        obs.append(PhoneObservation.model_validate(data))
        t += 125.0
    att = b.run("attention", [{"from": 0, "to": end, "step": 100, "direction": "center", **({} if face else {"face_count": 0})}])
    eng = create_incident_engine("fx-session-0001", "synthetic", Settings())
    changes = replay(eng, sorted([*obs, *att], key=lambda o: o.t_session_ms), finish_t=end)
    assert_contract_valid(changes)
    final = final_incidents(changes).values()
    return [i for i in final if i.rule_id == R.PHONE_RAISED], [i for i in final if i.rule_id == R.POSSIBLE_SCREEN_CAPTURE]


def test_phone_at_face_level_for_1_5_s_is_raised_high():
    raised, _ = run(lambda t: phone_det(0.33, cx=0.40 + 0.0001 * t) if 4_000 <= t < 5_500 else None)
    assert len(raised) == 1 and raised[0].priority == P.HIGH
    facts = {f.key: f.value for f in raised[0].explanation.facts}
    assert facts.get("observations", 0) >= 8


def test_phone_held_at_chest_is_not_raised():
    raised, capture = run(lambda t: phone_det(0.62) if 3_000 <= t < 12_000 else None)
    assert raised == [] and capture == []


def test_short_raise_below_1_s_is_nothing():
    raised, _ = run(lambda t: phone_det(0.30) if 4_000 <= t < 4_600 else None)
    assert raised == []


def test_without_a_face_the_upper_part_of_the_frame_counts():
    raised, _ = run(lambda t: phone_det(0.25) if 4_000 <= t < 5_500 else None, face=False)
    assert len(raised) == 1
    raised, _ = run(lambda t: phone_det(0.40) if 4_000 <= t < 5_500 else None, face=False)  # below fallback 0.35
    assert raised == []


def test_raised_and_still_for_1_5_s_is_possible_capture_but_moving_is_not():
    _, capture = run(lambda t: phone_det(0.30, cx=0.50) if 4_000 <= t < 7_000 else None)
    assert len(capture) == 1 and capture[0].priority == P.HIGH
    assert "факт съёмки не установлен" in capture[0].explanation.summary_ru
    moving = lambda t: phone_det(0.30, cx=0.25 + 0.5 * ((t - 4_000) % 1_000) / 1_000) if 4_000 <= t < 7_000 else None
    raised, capture = run(moving)
    assert len(raised) == 1 and capture == []  # raised, but swinging: not the capture pattern
