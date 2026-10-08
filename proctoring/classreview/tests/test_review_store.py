"""ReviewStore: ingestion/dedup, decisions log, clip states, restart persistence, risk accounting."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from conftest import incident

from classreview import ReviewError, zones


def test_one_episode_per_incident_not_per_frame(store):
    # 1 open + 40 updates of the same episode (e.g. duration growing) + close -> ONE episode
    store.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
    for seq in range(2, 42):
        store.ingest_incident("stu-a", incident(seq, duration_ms=seq * 100), class_session_id="cls-1")
    store.ingest_incident("stu-a", incident(42, state="closed", duration_ms=7000), class_session_id="cls-1")
    [ep] = store.list_incidents("stu-a")
    assert ep["state"] == "closed" and ep["duration_ms"] == 7000 and ep["seq"] == 42


def test_redelivery_and_late_messages(store):
    r1 = store.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
    assert r1.status == "new"
    assert store.ingest_incident("stu-a", incident(1), class_session_id="cls-1").status == "duplicate"
    assert store.ingest_incident("stu-a", incident(3, state="closed", duration_ms=5000), class_session_id="cls-1").status == "updated"
    # seq 2 arrives after 3 (reconnect): never overwrites the newer state
    assert store.ingest_incident("stu-a", incident(2, duration_ms=2000), class_session_id="cls-1").status == "stale"
    # a closed episode is never reopened, even with a higher seq
    assert store.ingest_incident("stu-a", incident(4, state="open"), class_session_id="cls-1").status == "stale"
    [ep] = store.list_incidents("stu-a")
    assert (ep["state"], ep["duration_ms"]) == ("closed", 5000)
    # same seq of ANOTHER student is not a duplicate
    assert store.ingest_incident("stu-b", incident(1), class_session_id="cls-1").status == "new"
    assert len(store.list_incidents("stu-b")) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seq", 0), ("seq", "1"), ("seq", True), ("incident_id", "../x"), ("incident_id", "a" * 129),
        ("rule_id", "Phone Visible"), ("priority", "critical"), ("state", "pending"), ("t_start_wall", "09:00"),
        ("t_start_wall", "2026-10-08T09:00:41"), ("duration_ms", -1), ("duration_ms", 1e12), ("explanation_ru", "x" * 1001),
        ("clip_available", "yes"), ("snapshot_jpeg_b64", "not base64!"), ("snapshot_jpeg_b64", "aGVsbG8="),
    ],
)
def test_invalid_incident_fields_are_rejected(store, field, value):
    with pytest.raises(ReviewError) as exc:
        store.ingest_incident("stu-a", {**incident(1), field: value}, class_session_id="cls-1")
    assert exc.value.status == 422
    assert store.list_incidents("stu-a") == []


def test_snapshot_is_kept(store):
    import base64

    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    store.ingest_incident("stu-a", incident(1, snapshot_jpeg_b64=base64.b64encode(jpeg).decode()), class_session_id="cls-1")
    assert store.list_incidents("stu-a")[0]["snapshot_available"]
    assert store.snapshot_file("stu-a", "inc-1").read_bytes() == jpeg


def test_decisions_are_an_append_only_log(store):
    store.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
    store.record_decision("stu-a", "inc-1", "needs_followup", "Плохо видно, нужен клип", "teacher")
    store.record_decision("stu-a", "inc-1", "needs_followup", "Плохо видно, нужен клип", "teacher")  # double click
    view = store.record_decision("stu-a", "inc-1", "dismissed", "Телефон лежал экраном вниз", "teacher")
    hist = view["decision_history"]
    assert [h["decision"] for h in hist] == ["needs_followup", "dismissed"]
    assert hist[1]["supersedes"] == hist[0]["decision_id"] and view["decision"] == "dismissed"
    assert view["decision_note_ru"] == "Телефон лежал экраном вниз"
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.conn.execute("UPDATE decisions SET decision='confirmed'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.conn.execute("DELETE FROM decisions")
    with pytest.raises(ReviewError):
        store.record_decision("stu-a", "inc-1", "guilty", "", "teacher")
    with pytest.raises(ReviewError):
        store.record_decision("stu-b", "inc-1", "confirmed", "", "teacher")  # not B's episode


def test_clip_state_machine(store, clip):
    store.ingest_incident("stu-a", incident(1, incident_id="inc-1"), class_session_id="cls-1")
    store.ingest_incident("stu-a", incident(2, incident_id="inc-2", clip_available=False), class_session_id="cls-1")
    store.ingest_incident("stu-a", incident(3, incident_id="inc-3"), class_session_id="cls-1")
    state = lambda iid: store.get_incident("stu-a", iid)["clip"]  # noqa: E731
    assert state("inc-1")["state"] == "not_requested"
    assert state("inc-2") | {} == {**state("inc-2"), "state": "unavailable", "reason_code": "not_recorded"}
    store.note_clip_requested("stu-a", "inc-1", "cmd-1")
    assert state("inc-1")["state"] == "loading"
    store.store_clip("stu-a", "inc-1", "video/mp4", clip, "test")
    s1 = state("inc-1")
    assert s1["state"] == "available" and s1["codec"] == "vp09" and s1["browser_playable"] and s1["source"] == "test"
    # the student refuses / fails -> unavailable with the reason, a new request is possible
    store.note_clip_requested("stu-a", "inc-3", "cmd-3")
    store.note_command_ack("cmd-3", False, "Камера была занята, клип не сохранён")
    s3 = state("inc-3")
    assert s3["state"] == "unavailable" and "Камера" in s3["reason_ru"] and s3["can_request"]
    store.note_clip_requested("stu-a", "inc-3", "cmd-3b")
    assert state("inc-3")["state"] == "loading"
    with pytest.raises(ReviewError) as exc:
        store.note_clip_requested("stu-a", "inc-1", "cmd-again")
    assert exc.value.code == "clip_already_available"


def test_clip_request_timeout(make_store):
    s = make_store(clip_request_timeout_s=0.0)
    s.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
    s.note_clip_requested("stu-a", "inc-1", "cmd-1")
    st = s.get_incident("stu-a", "inc-1")["clip"]
    assert st["state"] == "unavailable" and st["reason_code"] == "upload_timeout" and st["can_request"]


def test_bad_upload_marks_request_failed(store):
    store.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
    store.note_clip_requested("stu-a", "inc-1", "cmd-1")
    with pytest.raises(ReviewError) as exc:
        store.store_clip("stu-a", "inc-1", "video/mp4", b"definitely not an mp4 file" * 10)
    assert exc.value.status == 422
    st = store.get_incident("stu-a", "inc-1")["clip"]
    assert st["state"] == "unavailable" and "отклонён" in st["reason_ru"] and st["can_request"]
    assert list(store.clips.root.iterdir()) == []


def test_everything_survives_restart(make_store, clip, tmp_path):
    s1 = make_store()
    s1.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
    s1.ingest_incident("stu-a", incident(2, state="closed", duration_ms=7000), class_session_id="cls-1")
    s1.ingest_incident("stu-a", incident(3, incident_id="inc-2"), class_session_id="cls-1")
    s1.note_clip_requested("stu-a", "inc-1", "cmd-1")
    s1.store_clip("stu-a", "inc-1", "video/mp4", clip, "test")
    s1.note_clip_requested("stu-a", "inc-2", "cmd-2")
    s1.record_decision("stu-a", "inc-1", "confirmed", "Телефон в руке", "teacher")
    s1.record_decision("stu-a", "inc-1", "dismissed", "Пересмотрел клип: это калькулятор", "teacher")
    before = s1.list_incidents("stu-a")
    s1.close()
    s2 = make_store()  # same data_dir: "server restart"
    after = s2.list_incidents("stu-a")
    assert after == before
    assert after[0]["clip"]["state"] == "available" and after[1]["clip"]["state"] == "loading"
    assert [h["decision"] for h in after[0]["decision_history"]] == ["confirmed", "dismissed"]
    meta, path = s2.clip_file("inc-1")
    assert path.read_bytes() == clip
    # dedup memory survives too
    assert s2.ingest_incident("stu-a", incident(2, state="closed", duration_ms=7000), class_session_id="cls-1").status == "duplicate"


def _episode(store, seq, iid, priority, **over):
    store.ingest_incident("stu-a", incident(seq, incident_id=iid, priority=priority, **over), class_session_id="cls-1")


def test_dismissed_episodes_leave_the_shared_risk_rule(store):
    _episode(store, 1, "inc-h", "high", rule_id="multiple_faces", category="presence")
    _episode(store, 2, "inc-m", "medium")
    summary = lambda: store.review_summary("stu-a", reported_zone="red", monitoring="ok")  # noqa: E731
    assert summary()["zone_after_review"] == "red"  # A05 zone-rule-1: >= 1 high
    store.record_decision("stu-a", "inc-h", "dismissed", "Второе лицо — фото на стене", "teacher")
    s = summary()
    assert s["zone_after_review"] == "yellow" and s["dismissed_excluded"] == 1  # 1 medium left
    assert s["rule_version"] == "zone-rule-1" and s["reported_zone"] == "red"
    store.record_decision("stu-a", "inc-m", "confirmed", "", "teacher")
    assert summary()["zone_after_review"] == "yellow"  # confirmed keeps counting
    store.record_decision("stu-a", "inc-m", "needs_followup", "", "teacher")
    assert summary()["zone_after_review"] == "yellow"  # needs_followup keeps counting
    store.record_decision("stu-a", "inc-m", "dismissed", "", "teacher")
    assert summary()["zone_after_review"] == "green"
    store.record_decision("stu-a", "inc-h", "confirmed", "Пересмотрел", "teacher")  # decision changed back
    assert summary()["zone_after_review"] == "red"


def test_unknown_coverage_never_becomes_green(store):
    _episode(store, 1, "inc-m", "medium")
    store.record_decision("stu-a", "inc-m", "dismissed", "", "teacher")
    for reported, monitoring in (("grey", "ok"), (None, None), ("yellow", "degraded")):
        assert store.review_summary("stu-a", reported_zone=reported, monitoring=monitoring)["zone_after_review"] == "grey"


def test_missing_zone_module_is_explicit(store, monkeypatch):
    _episode(store, 1, "inc-m", "medium")
    monkeypatch.setattr(zones, "_assess_fn", lambda: None)
    s = store.review_summary("stu-a", reported_zone="yellow", monitoring="ok")
    assert s["zone_after_review"] is None and "не рассчитана" in s["reasons_ru"][0]


def test_reason_time_is_relative_to_class_session(make_store):
    s = make_store()
    start = datetime(2026, 10, 8, 9, 0, 0, tzinfo=timezone.utc)
    s.open_class_session("cls-2", start)
    s.ingest_incident("stu-a", incident(1, priority="high", state="closed", duration_ms=7000,
                                        t_start_wall=(start + timedelta(seconds=41)).isoformat()), class_session_id="cls-2")
    assert s.review_summary("stu-a", reported_zone="red", monitoring="ok")["reasons_ru"] == ["Телефон в кадре — 00:41, 7 с"]
