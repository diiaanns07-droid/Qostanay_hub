"""Review-only object episodes (contracts 1.1): foreign_object_visible (book), second_screen_visible (laptop/tv)."""

from __future__ import annotations

from proctor.fusion import create_incident_engine
from proctor.fusion.replay import final_incidents, replay
from proctor.fusion.scenario import ScenarioBuilder, merge
from proctor.fusion.tests.checks import assert_contract_valid
from proctor.fusion.zones import assess_session_zone
from proctor.settings import Settings
from proctor_contracts.v1 import IncidentCategory, PhoneObservation
from proctor_contracts.v1 import IncidentRule as R
from proctor_contracts.v1 import ReviewPriority as P

BASE_MS = 10_000.0  # object scene baseline (FusionConfig.object_baseline_ms)


def det(name: str, conf: float = 0.8, x: float = 0.6, y: float = 0.6, w: float = 0.2, h: float = 0.2, idx: int = 73) -> dict:
    return {"bbox": {"x_min": x, "y_min": y, "x_max": x + w, "y_max": y + h}, "confidence": conf, "class_name": name, "class_index": idx}


def phone(b: ScenarioBuilder, t: float, dets: list[dict], **kw) -> PhoneObservation:
    """Builder observation with its detections replaced (the builder itself only makes phone boxes)."""
    data = b.phone(t, **kw).model_dump(mode="json")
    data["detections"] = dets
    return PhoneObservation.model_validate(data)


def run(objects_at, end: float = 25_000.0, status_at=None):
    """objects_at(t) -> list of detection dicts for the phone observation at t (every 125 ms)."""
    b = ScenarioBuilder()
    obs = []
    t = 0.0
    while t < end:
        extra = {"status": status_at(t)} if status_at else {}
        obs.append(phone(b, t, objects_at(t), visible="absent", **extra))
        t += 125.0
    att = b.run("attention", [{"from": 0, "to": end, "step": 100, "direction": "center"}])
    eng = create_incident_engine("fx-session-0001", "synthetic", Settings())
    changes = replay(eng, merge(obs, att), finish_t=end)
    assert_contract_valid(changes)
    return [i for i in final_incidents(changes).values() if i.rule_id in (R.FOREIGN_OBJECT_VISIBLE, R.SECOND_SCREEN_VISIBLE)]


def test_book_appearing_for_3_s_is_one_medium_episode_with_one_line():
    incs = run(lambda t: [det("book")] if 12_000 <= t < 15_000 else [])
    assert len(incs) == 1
    i = incs[0]
    assert i.rule_id == R.FOREIGN_OBJECT_VISIBLE and i.priority == P.MEDIUM and i.category == IncidentCategory.OBJECTS
    assert i.explanation.summary_ru.startswith("В кадре книга или посторонний предмет 2,9 с")
    assert "\n" not in i.explanation.summary_ru
    assert not any(w in i.explanation.summary_ru.lower() for w in ("списыва", "нарушител", "вероятност"))
    assert any("COCO" in c for c in i.explanation.caveats_ru)


def test_laptop_or_tv_is_second_screen_medium_and_zone_reason():
    incs = run(lambda t: [det("tv", idx=62, x=0.05, y=0.05)] if 13_000 <= t < 17_000 else [])
    assert [(i.rule_id, i.priority) for i in incs] == [(R.SECOND_SCREEN_VISIBLE, P.MEDIUM)]
    za = assess_session_zone(incs, None, coverage=1.0)
    assert za.zone == "yellow" and za.reasons_ru[0].startswith("В кадре второй экран или ноутбук — 00:13")


def test_shorter_than_2_s_or_below_0_5_gives_nothing():
    assert run(lambda t: [det("book")] if 12_000 <= t < 13_500 else []) == []  # 1.4 s
    assert run(lambda t: [det("laptop", conf=0.45, idx=63)] if 12_000 <= t < 18_000 else []) == []


def test_objects_seen_at_the_start_are_the_room_and_never_open_an_episode():
    # computer-lab monitor behind the student from the first second to the end (measured on real clips)
    room = det("tv", conf=0.85, idx=62, x=0.0, y=0.55, w=0.25, h=0.2)
    assert run(lambda t: [room]) == []
    # ... but a NEW laptop elsewhere, appearing later, still counts
    new = det("laptop", conf=0.7, idx=63, x=0.6, y=0.1, w=0.3, h=0.25)
    incs = run(lambda t: [room] + ([new] if 14_000 <= t < 18_000 else []))
    assert [i.rule_id for i in incs] == [R.SECOND_SCREEN_VISIBLE]


def test_object_during_baseline_only_is_ignored():
    assert run(lambda t: [det("book")] if 2_000 <= t < 8_000 else []) == []


def test_unusable_frames_never_open_or_extend():
    incs = run(lambda t: [det("book")] if 12_000 <= t < 16_000 else [], status_at=lambda t: "unknown" if t >= 11_000 else "ok")
    assert incs == []


def test_phone_count_ignores_objects():
    b = ScenarioBuilder()
    obs = [phone(b, x * 125.0, [det("cell phone", idx=67, x=0.3, y=0.3), det("book")], visible="present") for x in range(40)]
    att = b.run("attention", [{"from": 0, "to": 5000, "step": 100, "direction": "center"}])
    eng = create_incident_engine("fx-session-0001", "synthetic", Settings())
    final = final_incidents(replay(eng, merge(obs, att), finish_t=5000.0))
    pv = [i for i in final.values() if i.rule_id == R.PHONE_VISIBLE][0]
    facts = {f.key: f.value for f in pv.explanation.facts}
    assert facts["max_phones"] == 1


def test_student_away_room_behind_does_not_count():
    """Measured on a real clip: when the student left the frame, lab monitors behind became visible."""
    b = ScenarioBuilder()
    end = 30_000.0
    revealed = det("tv", conf=0.85, idx=62, x=0.3, y=0.3, w=0.3, h=0.3)
    obs = [phone(b, x * 125.0, [revealed] if 14_000 <= x * 125.0 < 26_000 else [], visible="absent") for x in range(int(end / 125))]
    att = b.run("attention", [
        {"from": 0, "to": 14_000, "step": 100, "direction": "center"},
        {"from": 14_000, "to": 26_000, "step": 100, "face_count": 0},  # student away
        {"from": 26_000, "to": end, "step": 100, "direction": "center"},
    ])
    eng = create_incident_engine("fx-session-0001", "synthetic", Settings())
    final = final_incidents(replay(eng, merge(obs, att), finish_t=end))
    assert not [i for i in final.values() if i.rule_id == R.SECOND_SCREEN_VISIBLE]
    assert [i for i in final.values() if i.rule_id == R.FACE_MISSING]  # the absence itself is still reported
