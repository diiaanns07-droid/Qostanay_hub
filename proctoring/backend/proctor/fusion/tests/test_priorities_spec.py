"""Episode priorities required by the zones spec (a05-rules-1.1.0) and "normal behaviour = no episode"."""

from __future__ import annotations

import pytest

from proctor.fusion import create_incident_engine
from proctor.fusion.replay import final_incidents, replay
from proctor.fusion.scenario import ScenarioBuilder, merge
from proctor.fusion.tests.checks import assert_contract_valid
from proctor.fusion.zones import assess_session_zone
from proctor.settings import Settings
from proctor_contracts.v1 import IncidentRule as R
from proctor_contracts.v1 import ReviewPriority as P


def _final(*streams, finish: float):
    eng = create_incident_engine("fx-session-0001", "synthetic", Settings())
    changes = replay(eng, merge(*streams), finish_t=finish)
    assert_contract_valid(changes)
    return list(final_incidents(changes).values())


def _no_phone(b: ScenarioBuilder, end: float):
    return b.run("phone", [{"from": 0, "to": end, "step": 125, "visible": "absent"}])


def _gaze(segments, end=20000.0):
    b = ScenarioBuilder()
    att = b.run("attention", segments)
    return _final(att, _no_phone(b, end), finish=end)


@pytest.mark.parametrize("to,expected", [(9100.0, P.LOW), (9600.0, P.MEDIUM)])
def test_gaze_3_to_8_s_is_low_and_longer_than_8_s_is_medium(to, expected):
    # true observations every 100 ms from 1000 to to-100: duration 8000 ms (low) / 8500 ms (medium)
    incs = _gaze([
        {"from": 0, "to": 1000, "step": 100, "direction": "center"},
        {"from": 1000, "to": to, "step": 100, "direction": "left"},
        {"from": to, "to": 20000, "step": 100, "direction": "center"},
    ])
    side = [i for i in incs if i.rule_id == R.GAZE_PROLONGED_SIDE]
    assert len(side) == 1 and side[0].priority == expected


def test_repeated_short_gaze_within_one_episode_is_not_raised_above_low():
    incs = _gaze([
        {"from": 0, "to": 1000, "step": 100, "direction": "center"},
        {"from": 1000, "to": 4100, "step": 100, "direction": "down"},  # opens (3.0 s)
        {"from": 4100, "to": 4600, "step": 100, "direction": "center"},  # < merge_gap 1.5 s
        {"from": 4600, "to": 6100, "step": 100, "direction": "down"},
        {"from": 6100, "to": 6600, "step": 100, "direction": "center"},
        {"from": 6600, "to": 8100, "step": 100, "direction": "down"},
        {"from": 8100, "to": 20000, "step": 100, "direction": "center"},
    ])
    down = [i for i in incs if i.rule_id == R.GAZE_PROLONGED_DOWN]
    assert len(down) == 1
    facts = {f.key: f.value for f in down[0].explanation.facts}
    assert facts["appearances"] == 3 and facts["priority_basis"] == "base_low"
    assert down[0].priority == P.LOW  # 3 appearances, 7 s: still low (gaze priority = duration only)


def test_head_turned_wins_over_eye_estimate_up():
    """Real A04 output (camera clip, yaw -50 deg): head 'left' while gaze flips to 'up'."""
    incs = _gaze([
        {"from": 0, "to": 1000, "step": 100, "direction": "up", "head": "up"},
        {"from": 1000, "to": 6000, "step": 100, "direction": "up", "head": "left"},
        {"from": 6000, "to": 20000, "step": 100, "direction": "up", "head": "up"},
    ])
    side = [i for i in incs if i.rule_id == R.GAZE_PROLONGED_SIDE]
    assert len(side) == 1 and side[0].priority == P.LOW
    facts = {f.key: f.value for f in side[0].explanation.facts}
    assert facts["head_direction_fallback"] > 0  # disclosed


def test_long_face_missing_stays_medium():
    b = ScenarioBuilder()
    att = b.run("attention", [
        {"from": 0, "to": 1000, "step": 100, "direction": "center"},
        {"from": 1000, "to": 31000, "step": 100, "face_count": 0},
        {"from": 31000, "to": 33000, "step": 100, "direction": "center"},
    ])
    incs = _final(att, _no_phone(b, 33000.0), finish=33000.0)
    fm = [i for i in incs if i.rule_id == R.FACE_MISSING]
    assert len(fm) == 1 and fm[0].priority == P.MEDIUM


def test_normal_behaviour_gives_no_episodes_and_green():
    b = ScenarioBuilder()
    att = b.run("attention", [
        {"from": 0, "to": 3000, "step": 100, "direction": "center"},
        {"from": 3000, "to": 3200, "step": 100, "face_count": 0},  # blink / detector miss 200 ms
        {"from": 3200, "to": 5000, "step": 100, "direction": "center"},
        {"from": 5000, "to": 7000, "step": 100, "direction": "right"},  # 2 s glance aside
        {"from": 7000, "to": 9000, "step": 100, "direction": "center"},
        {"from": 9000, "to": 10500, "step": 100, "direction": "down"},  # 1.5 s look at the keyboard
        {"from": 10500, "to": 20000, "step": 100, "direction": "center"},
    ])
    incs = _final(att, _no_phone(b, 20000.0), finish=20000.0)
    behaviour = [i for i in incs if i.rule_id != R.MONITORING_DEGRADED]
    assert behaviour == []
    assert assess_session_zone(incs, None, coverage=1.0).zone == "green"


def test_phone_raised_is_high():
    b = ScenarioBuilder()
    ph = b.run("phone", [
        {"from": 0, "to": 2000, "step": 125, "visible": "absent"},
        {"from": 2000, "to": 3500, "step": 125, "visible": "present", "raised": "present"},  # >= 1 s (1.4.0)
        {"from": 3500, "to": 12000, "step": 125, "visible": "absent"},
    ])
    att = b.run("attention", [{"from": 0, "to": 12000, "step": 100, "direction": "center"}])
    incs = _final(att, ph, finish=12000.0)
    raised = [i for i in incs if i.rule_id == R.PHONE_RAISED]
    assert raised and all(i.priority == P.HIGH for i in raised)
    assert assess_session_zone(incs, None, coverage=1.0).zone == "red"
