"""A05 edge cases: contract compliance, late/duplicate/foreign input, clocks, pause/finish, boundaries."""

from __future__ import annotations

import json
import time
from datetime import timedelta

import pytest

from proctor.fusion import FusionConfig, create_incident_engine
from proctor.fusion.replay import final_incidents, replay
from proctor.fusion.scenario import FIXTURE_DIR, ScenarioBuilder, merge, parse_observation
from proctor.fusion.tests.checks import assert_contract_valid, assert_lifecycle
from proctor.settings import Settings
from proctor_contracts import interfaces as itf
from proctor_contracts.v1 import (
    IncidentChangeType,
    IncidentEndReason,
    IncidentRule,
    IncidentState,
    ReviewPriority,
    SourceMode,
)

SID = "fx-session-0001"
R = IncidentRule
FINISHED = IncidentEndReason.SESSION_FINISHED


def engine(mode: str = "synthetic", sid: str = SID, **overrides):
    return create_incident_engine(sid, mode, Settings(), config=overrides or None)


def phone_episode(b: ScenarioBuilder, start=2000.0, stop=6000.0, end=12000.0, step=125.0):
    return b.run(
        "phone",
        [
            {"from": 0, "to": start, "step": step, "visible": "absent"},
            {"from": start, "to": stop, "step": step, "visible": "present"},
            {"from": stop, "to": end, "step": step, "visible": "absent"},
        ],
    )


def attention_center(b: ScenarioBuilder, end=12000.0, **kw):
    return b.run("attention", [{"from": 0, "to": end, "step": 100, "direction": "center", **kw}])


def run(eng, observations, finish=None, **kw):
    changes = replay(eng, observations, finish_t=finish, **kw)
    assert_contract_valid(changes)
    return changes


def by_rule(final, rule):
    return [i for i in final.values() if i.rule_id == rule]


# --------------------------------------------------------------------------- contract / factory


def test_factory_implements_protocol_and_versions():
    eng = engine()
    assert isinstance(eng, itf.IncidentEngine)
    assert eng.rule_version and eng.config_version.startswith("a05-default-1+")
    snap = eng.config_snapshot()
    json.dumps(snap)  # JSON-serializable, stored in the report
    assert snap["config"]["phone_visible"]["min_duration_ms"] == 1000.0
    assert "hypotheses" in snap["note"] and "not a probability" in snap["priority_meaning"]
    # same config -> same version; any threshold change -> another version
    assert engine().config_version == eng.config_version
    tuned = engine(phone_visible={"min_duration_ms": 1500.0})
    assert tuned.config_version != eng.config_version
    assert tuned.config_snapshot()["config"]["phone_visible"]["min_duration_ms"] == 1500.0
    with pytest.raises(ValueError):
        FusionConfig.from_dict({"no_such_threshold": 1})
    with pytest.raises(ValueError):
        FusionConfig.from_dict({"phone_visible": {"min_secs": 1}})
    with pytest.raises(ValueError):
        FusionConfig.from_dict({"priority_base": {"phone_visible": "urgent"}})
    with pytest.raises(ValueError):
        FusionConfig.from_dict({"phone_visible": {"min_count": 0}})
    with pytest.raises(ValueError):
        FusionConfig.from_dict({"link_pairs": [["phone_visible", "no_such_rule"]]})


def test_every_contract_observation_fixture_is_consumable():
    names = sorted(p.name for p in FIXTURE_DIR.glob("*Observation.*.json"))
    assert len(names) >= 6
    rows = [parse_observation(json.loads((FIXTURE_DIR / n).read_text(encoding="utf-8"))) for n in names]
    for obs in rows:  # one by one, fresh engine each
        eng = engine()
        assert_contract_valid(eng.consume(obs) + eng.advance(obs.t_session_ms + 30000) + eng.finish(obs.t_session_ms + 31000, FINISHED))
    eng = engine()  # all together in session order
    rows.sort(key=lambda o: o.t_session_ms)
    changes = run(eng, rows, finish=31000.0)
    final = assert_lifecycle(changes)
    rules = {i.rule_id for i in final.values()}
    assert R.ENVIRONMENT_BLOCKED_ACTION in rules  # Alt+Tab detected_only
    assert R.MONITORING_DEGRADED in rules  # camera_disconnected + sparse fixtures = coverage gap
    mon = by_rule(final, R.MONITORING_DEGRADED)[0]
    facts = {f.key: f.value for f in mon.explanation.facts}
    assert facts["health.capture.code"] == "camera_disconnected"


def test_incident_ids_valid_for_long_session_id():
    sid = "s" * 128
    b = ScenarioBuilder(session_id=sid)
    changes = run(engine(sid=sid), merge(phone_episode(b), attention_center(b)), finish=12000.0)
    final = assert_lifecycle(changes)
    assert final and all(len(i) <= 128 for i in final)


# --------------------------------------------------------------------------- input hygiene


def test_duplicates_same_timestamps_and_foreign_input_are_ignored():
    b = ScenarioBuilder()
    obs = merge(phone_episode(b), attention_center(b))
    base = final_incidents(run(engine(), obs, finish=12000.0))

    eng = engine()
    noisy = []
    other = ScenarioBuilder(session_id="other-session")
    for o in obs:
        noisy.append(o)
        if o.kind == "phone":
            noisy.append(o)  # re-delivery of the same observation_id
            noisy.append(other.phone(o.t_session_ms, visible="present"))  # another session
            noisy.append(b.phone(o.t_session_ms, visible="present", source_mode="live"))  # other mode
    got = final_incidents(run(eng, noisy, finish=12000.0))
    assert got == base
    stats = eng.stats()
    n_phone = sum(o.kind == "phone" for o in obs)
    assert stats["duplicate"] == n_phone and stats["foreign_session"] == n_phone and stats["mode_mismatch"] == n_phone

    # different observations with the SAME timestamp are fine (e.g. two results for one frame)
    eng2 = engine()
    doubled = []
    for o in obs:
        doubled.append(o)
        if o.kind == "phone":
            doubled.append(b.phone(o.t_session_ms, visible="present" if 2000 <= o.t_session_ms < 6000 else "absent"))
    final2 = final_incidents(run(eng2, doubled, finish=12000.0))
    inc = by_rule(final2, R.PHONE_VISIBLE)[0]
    assert (inc.t_start_ms, inc.t_end_ms) == (2000.0, 5875.0)
    assert inc.observation_count == 2 * by_rule(base, R.PHONE_VISIBLE)[0].observation_count


def test_live_engine_never_takes_fixture_observations():
    b = ScenarioBuilder(mode="synthetic")
    eng = engine(mode="live")
    changes = run(eng, merge(phone_episode(b), attention_center(b)), finish=12000.0)
    assert changes == [] or all(c.incident.rule_id == R.MONITORING_DEGRADED for c in changes)
    assert eng.stats()["mode_mismatch"] > 0 and "accepted_phone" not in eng.stats()


def test_out_of_order_observation_is_dropped_not_rewound():
    b = ScenarioBuilder()
    obs = merge(phone_episode(b), attention_center(b))
    base = final_incidents(run(engine(), obs, finish=12000.0))
    late = list(obs)
    idx = next(i for i, o in enumerate(late) if o.kind == "phone" and o.t_session_ms == 9000.0)
    late.insert(idx + 1, b.phone(4000.0, visible="present"))  # 5 s old phone result arrives late
    late.insert(idx + 2, b.attention(8500.0, direction="down"))  # attention older than its stream head
    eng = engine()
    assert final_incidents(run(eng, late, finish=12000.0)) == base
    assert eng.stats()["late"] == 2


def test_wall_clock_jump_back_does_not_reorder_or_shorten():
    b = ScenarioBuilder()
    obs = merge(phone_episode(b), attention_center(b))
    base = final_incidents(run(engine(), obs, finish=12000.0))
    jumped = [
        o.model_copy(update={"wall_time": o.wall_time - timedelta(hours=1)}) if o.t_session_ms >= 4000 else o for o in obs
    ]
    got = final_incidents(run(engine(), jumped, finish=12000.0))
    assert got == base  # wall times are anchor + t_session_ms
    for inc in got.values():
        assert inc.wall_end >= inc.wall_start
        assert inc.wall_end - inc.wall_start == timedelta(milliseconds=inc.t_end_ms - inc.t_start_ms)


def test_dropped_frames_tolerated_but_long_gap_is_source_lost():
    b = ScenarioBuilder()
    # phone results only every 400 ms (frames skipped under load): still one episode
    sparse = b.run("phone", [{"from": 0, "to": 1000, "step": 400, "visible": "absent"},
                             {"from": 1200, "to": 5200, "step": 400, "visible": "present"},
                             {"from": 5200, "to": 9000, "step": 400, "visible": "absent"}])
    final = final_incidents(run(engine(), merge(sparse, attention_center(b, end=9000)), finish=9000.0))
    inc = by_rule(final, R.PHONE_VISIBLE)
    assert len(inc) == 1 and (inc[0].t_start_ms, inc[0].t_end_ms) == (1200.0, 4800.0)

    # a 2.5 s hole in phone results (> TTL 2000) inside the episode: closed as source_lost, never cleared
    b2 = ScenarioBuilder()
    holed = b2.run("phone", [{"from": 0, "to": 1000, "step": 125, "visible": "absent"},
                             {"from": 1000, "to": 3000, "step": 125, "visible": "present"},
                             {"from": 5500, "to": 8000, "step": 125, "visible": "present"},
                             {"from": 8000, "to": 14000, "step": 125, "visible": "absent"}])
    final = final_incidents(run(engine(), merge(holed, attention_center(b2, end=14000)), finish=14000.0))
    phones = sorted(by_rule(final, R.PHONE_VISIBLE), key=lambda i: i.t_start_ms)
    assert [(i.t_start_ms, i.t_end_ms, i.end_reason) for i in phones] == [
        (1000.0, 2875.0, IncidentEndReason.SOURCE_LOST),
        (5500.0, 7875.0, IncidentEndReason.CONDITION_CLEARED),
    ]
    mon = by_rule(final, R.MONITORING_DEGRADED)
    assert len(mon) == 1 and mon[0].t_start_ms == 2875.0  # the hole itself is reported


def test_low_quality_and_unknown_status_are_not_evidence():
    b = ScenarioBuilder()
    att = b.run("attention", [{"from": 0, "to": 8000, "step": 100, "direction": "down", "quality": 0.1}])
    eng = engine()
    final = final_incidents(run(eng, merge(att, b.run("phone", [{"from": 0, "to": 8000, "step": 125, "visible": "absent"}])), finish=8000.0))
    assert not by_rule(final, R.GAZE_PROLONGED_DOWN)
    mon = by_rule(final, R.MONITORING_DEGRADED)
    assert len(mon) == 1  # 8 s without a usable attention result is a coverage gap, not "all clear"
    assert {f.key for f in mon[0].explanation.facts} >= {"undetermined.attention_ms"}


def test_head_direction_fallback_and_uncalibrated_are_disclosed():
    b = ScenarioBuilder()
    att = b.run("attention", [{"from": 0, "to": 1000, "step": 100, "direction": "center", "calibrated": False},
                              {"from": 1000, "to": 5000, "step": 100, "direction": "unknown", "head": "down", "calibrated": False},
                              {"from": 5000, "to": 9000, "step": 100, "direction": "center", "calibrated": False}])
    ph = b.run("phone", [{"from": 0, "to": 9000, "step": 125, "visible": "absent"}])
    final = final_incidents(run(engine(), merge(att, ph), finish=9000.0))
    inc = by_rule(final, R.GAZE_PROLONGED_DOWN)[0]
    facts = {f.key: f.value for f in inc.explanation.facts}
    assert facts["head_direction_fallback"] == inc.observation_count == facts["uncalibrated_observations"]
    assert any("Калибровка" in c for c in inc.explanation.caveats_ru)


# --------------------------------------------------------------------------- boundaries


@pytest.mark.parametrize("down_from,expected", [(4875.0, ReviewPriority.HIGH), (4900.0, ReviewPriority.MEDIUM)])
def test_correlation_overlap_boundary(down_from, expected):
    """phone [2000, 5875]; gaze 'down' covering exactly 1000 ms of it -> high; 975 ms -> stays medium."""
    b = ScenarioBuilder()
    att = b.run("attention", [{"from": 0, "to": down_from, "step": 25 if down_from % 100 else 100, "direction": "center"},
                              {"from": down_from, "to": 5875, "step": 25 if down_from % 100 else 100, "direction": "down"},
                              {"from": 5875, "to": 12000, "step": 25 if down_from % 100 else 100, "direction": "center"}])
    final = final_incidents(run(engine(), merge(phone_episode(b), att), finish=12000.0))
    inc = by_rule(final, R.PHONE_VISIBLE)[0]
    facts = {f.key: f.value for f in inc.explanation.facts}
    assert facts["gaze_down_overlap_ms"] == pytest.approx(5875.0 - down_from)
    assert inc.priority == expected


@pytest.mark.parametrize("capture_from,linked", [(4125.0, True), (4250.0, False)])
def test_link_gap_boundary(capture_from, linked):
    """phone_raised [2000, 3125] (>= 1 s, a05-rules-1.4.0); possible_screen_capture starting 1000 ms later -> linked; 1125 ms -> not."""
    b = ScenarioBuilder()
    segs = [{"from": 0, "to": 2000, "step": 125, "visible": "absent", "capture": "absent"},
            {"from": 2000, "to": 3250, "step": 125, "visible": "absent", "raised": "present", "capture": "absent"},
            {"from": 3250, "to": capture_from, "step": 125, "visible": "absent", "capture": "absent"},
            {"from": capture_from, "to": capture_from + 1000, "step": 125, "visible": "absent", "capture": "present"},
            {"from": capture_from + 1000, "to": 16000, "step": 125, "visible": "absent", "capture": "absent"}]
    final = final_incidents(run(engine(), merge(b.run("phone", segs), attention_center(b, end=16000)), finish=16000.0))
    raised, capture = by_rule(final, R.PHONE_RAISED)[0], by_rule(final, R.POSSIBLE_SCREEN_CAPTURE)[0]
    assert (capture.incident_id in raised.related_incident_ids) is linked
    assert (raised.incident_id in capture.related_incident_ids) is linked


def test_min_duration_boundary_is_inclusive():
    b = ScenarioBuilder()
    ph = b.run("phone", [{"from": 0, "to": 1000, "step": 125, "visible": "absent"},
                         {"from": 1000, "to": 2125, "step": 125, "visible": "present"},  # 1000..2000: span exactly 1000
                         {"from": 2125, "to": 8000, "step": 125, "visible": "absent"}])
    final = final_incidents(run(engine(), merge(ph, attention_center(b, end=8000)), finish=8000.0))
    assert [(i.t_start_ms, i.t_end_ms) for i in by_rule(final, R.PHONE_VISIBLE)] == [(1000.0, 2000.0)]
    b2 = ScenarioBuilder()
    ph2 = b2.run("phone", [{"from": 0, "to": 1000, "step": 125, "visible": "absent"},
                           {"from": 1000, "to": 2000, "step": 125, "visible": "present"},  # span 875
                           {"from": 2000, "to": 8000, "step": 125, "visible": "absent"}])
    final2 = final_incidents(run(engine(), merge(ph2, attention_center(b2, end=8000)), finish=8000.0))
    assert not by_rule(final2, R.PHONE_VISIBLE)


# --------------------------------------------------------------------------- lifecycle


def test_finish_keeps_last_episode_and_is_idempotent():
    b = ScenarioBuilder()
    ph = b.run("phone", [{"from": 0, "to": 1000, "step": 125, "visible": "absent"},
                         {"from": 1000, "to": 5000, "step": 125, "visible": "present"}])  # still visible at stop
    env = [b.environment(4500.0, "focus_lost")]  # never regained
    eng = engine()
    changes = run(eng, merge(ph, attention_center(b, end=5000), env))
    assert all(c.incident.state == IncidentState.OPEN for c in changes)
    closing = eng.finish(5200.0, FINISHED)
    assert_contract_valid(closing)
    final = assert_lifecycle(changes + closing)
    phone = by_rule(final, R.PHONE_VISIBLE)[0]
    assert (phone.t_end_ms, phone.end_reason) == (4875.0, FINISHED)  # last evidence, not finish time
    esc = by_rule(final, R.ENVIRONMENT_ESCAPE)[0]
    assert (esc.t_end_ms, esc.end_reason) == (5200.0, FINISHED)  # focus still lost until the end
    assert {f.key: f.value for f in esc.explanation.facts}["focus_lost_ms"] == 700.0
    assert eng.finish(6000.0, FINISHED) == []
    assert eng.consume(b.phone(5300.0)) == [] and eng.advance(9000.0) == []
    assert eng.stats()["after_finish"] == 1


def test_abort_reason_is_kept():
    b = ScenarioBuilder()
    eng = engine()
    changes = run(eng, merge(phone_episode(b, stop=8000.0, end=8000.0), attention_center(b, end=8000)))
    closing = eng.finish(8000.0, IncidentEndReason.SESSION_ABORTED)
    final = assert_lifecycle(changes + closing)
    assert by_rule(final, R.PHONE_VISIBLE)[0].end_reason == IncidentEndReason.SESSION_ABORTED


def test_pause_closes_everything_and_resume_starts_fresh():
    b = ScenarioBuilder()
    eng = engine()
    first = merge(phone_episode(b, stop=5000.0, end=5000.0), attention_center(b, end=5000))
    changes = run(eng, first)
    changes += eng.set_paused(True, 5000.0)
    assert any(c.change == IncidentChangeType.CLOSED and c.incident.end_reason == IncidentEndReason.SESSION_PAUSED for c in changes)
    assert eng.consume(b.phone(6000.0)) == [] and eng.advance(20000.0) == []  # ignored while paused
    assert eng.set_paused(True, 7000.0) == []  # idempotent
    changes += eng.set_paused(False, 30000.0)
    second = merge(b.run("phone", [{"from": 30000, "to": 34000, "step": 125, "visible": "present"},
                                   {"from": 34000, "to": 40000, "step": 125, "visible": "absent"}]),
                   b.run("attention", [{"from": 30000, "to": 40000, "step": 100, "direction": "center"}]))
    changes += run(eng, second)
    changes += eng.finish(40000.0, FINISHED)
    final = assert_lifecycle(changes)
    phones = sorted(by_rule(final, R.PHONE_VISIBLE), key=lambda i: i.t_start_ms)
    assert [(i.t_start_ms, i.end_reason) for i in phones] == [
        (2000.0, IncidentEndReason.SESSION_PAUSED),
        (30000.0, IncidentEndReason.CONDITION_CLEARED),
    ]
    assert not by_rule(final, R.MONITORING_DEGRADED)  # the pause itself is not a "lost source"
    assert eng.stats()["while_paused"] == 1


def test_health_problem_survives_pause():
    b = ScenarioBuilder()
    eng = engine()
    changes = run(eng, merge(attention_center(b, end=3000), b.run("phone", [{"from": 0, "to": 3000, "step": 125, "visible": "absent"}]),
                             [b.health(2000.0, component="phone", status="unavailable", code="model_missing")]))
    changes += eng.set_paused(True, 3000.0)
    changes += eng.set_paused(False, 10000.0)  # no new health report: the model is still missing
    reopened = [c for c in changes if c.change == IncidentChangeType.OPENED and c.incident.rule_id == R.MONITORING_DEGRADED]
    assert len(reopened) == 2 and reopened[1].incident.t_start_ms == 10000.0
    changes += eng.finish(11000.0, FINISHED)
    assert_lifecycle(changes)


def test_startup_silence_is_reported_after_grace():
    eng = engine()
    assert eng.advance(1000.0) == []  # engine created at exam start
    assert eng.advance(5900.0) == []  # within startup grace (5 s)
    opened = eng.advance(6100.0)
    assert_contract_valid(opened)
    assert len(opened) >= 1 and opened[0].incident.rule_id == R.MONITORING_DEGRADED
    facts = {f.key: f.value for f in opened[-1].incident.explanation.facts}
    assert facts["stale.phone.code"] == "no_observations_since_start"
    assert_lifecycle(opened + eng.finish(7000.0, FINISHED))


def test_replay_mode_ignores_wall_clock_ticks():
    b = ScenarioBuilder(mode="replay")
    eng = engine(mode="replay")
    obs = merge(phone_episode(b, stop=4000.0, end=4000.0), attention_center(b, end=4000))
    changes = run(eng, obs)
    assert eng.advance(4000.0 + 3_600_000.0) == []  # an hour of wall clock, no recorded data: no gap
    changes += eng.finish(4000.0, FINISHED)
    final = assert_lifecycle(changes)
    assert [i.rule_id for i in final.values()] == [R.PHONE_VISIBLE]
    inc = next(iter(final.values()))
    assert inc.source_mode == SourceMode.REPLAY and inc.explanation.caveats_ru[0].startswith("ЗАПИСЬ")


def test_environment_monitoring_events():
    b = ScenarioBuilder()
    base = merge(attention_center(b, end=20000), b.run("phone", [{"from": 0, "to": 20000, "step": 125, "visible": "absent"}]))
    env = [b.environment(3000.0, "exam_mode_released", enforcement="allowed"),
           b.environment(6000.0, "exam_mode_engaged", enforcement="allowed"),
           b.environment(15000.0, "enforcement_error", enforcement="failed"),
           b.environment(16000.0, "focus_regained", enforcement="allowed")]  # unpaired: ignored
    eng = engine()
    final = assert_lifecycle(run(eng, merge(base, env), finish=20000.0))
    mons = sorted(by_rule(final, R.MONITORING_DEGRADED), key=lambda i: i.t_start_ms)
    assert [(m.t_start_ms, m.t_end_ms) for m in mons] == [(3000.0, 6000.0), (15000.0, 15000.0)]
    assert eng.stats()["focus_regained_unpaired"] == 1


def test_many_restricted_actions_stay_one_incident():
    b = ScenarioBuilder()
    base = merge(attention_center(b, end=30000), b.run("phone", [{"from": 0, "to": 30000, "step": 125, "visible": "absent"}]))
    env = [b.environment(1000.0 + 100 * i, "shortcut_alt_tab", enforcement="blocked") for i in range(50)]
    changes = run(engine(), merge(base, env), finish=30000.0)
    final = assert_lifecycle(changes)
    burst = by_rule(final, R.ENVIRONMENT_BLOCKED_ACTION)
    assert len(burst) == 1 and burst[0].observation_count == 50
    assert burst[0].priority == ReviewPriority.MEDIUM  # all blocked (low) + repeated (>= 5)
    assert sum(c.incident.rule_id == R.ENVIRONMENT_BLOCKED_ACTION for c in changes) < 15  # not 50 alerts


def test_long_session_is_fast_and_bounded():
    """30 min at 8 fps phone + 15 fps attention through the engine (no CV): a throughput sanity check."""
    b = ScenarioBuilder()
    minutes = 30
    ph = b.run("phone", [{"from": 0, "to": minutes * 60000, "step": 125, "visible": "absent"}])
    att = b.run("attention", [{"from": 0, "to": minutes * 60000, "step": 66.667, "direction": "center"}])
    obs = merge(ph, att)
    eng = engine()
    t0 = time.perf_counter()
    changes = replay(eng, obs, tick_ms=250.0, finish_t=minutes * 60000.0)
    elapsed = time.perf_counter() - t0
    assert changes == []
    assert elapsed < 20.0, f"{len(obs)} observations took {elapsed:.1f}s"
    assert len(eng._series["gaze_down"].ts) < 5000  # pruned


def test_series_overlap_matches_brute_force():
    import random

    from proctor.fusion.engine import _Series

    rng = random.Random(7)
    for _ in range(200):
        s = _Series(600.0)
        t = 0.0
        samples = []
        for _ in range(rng.randint(0, 60)):
            t += rng.choice([0.0, 33.3, 66.7, 125.0, 400.0, 900.0])
            v = rng.choice([True, False, None])
            s.add(t, v)
            samples.append((t, v))

        def brute(a: float, b: float) -> float:
            total = 0.0
            for k, (tk, vk) in enumerate(samples):
                if vk is not True:
                    continue
                end = tk + 600.0 if k + 1 == len(samples) else min(tk + 600.0, samples[k + 1][0])
                total += max(0.0, min(b, end) - max(a, tk))
            return total

        for _ in range(20):
            a = rng.uniform(-100, t + 700)
            b = a + rng.uniform(0, t + 700)
            assert s.true_ms(a, b) == pytest.approx(brute(a, b), abs=1e-6)
