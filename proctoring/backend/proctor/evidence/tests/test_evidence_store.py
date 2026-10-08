"""A08 store: roundtrip, idempotent re-delivery, session isolation, end-of-session policy, media, coverage."""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import timedelta

import pytest

from proctor.evidence import db
from proctor.evidence.media import MediaVault
from proctor_contracts import interfaces as itf
from proctor_contracts.v1 import AnswerUpsert, HumanReviewCreate, SessionState, utc_now


def rows(store, table: str, sid: str) -> int:
    return store._conn.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id=?", (sid,)).fetchone()[0]


def test_factory_satisfies_protocol_and_schema(store):
    assert isinstance(store, itf.EvidenceStore)
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.health().code == "ok"


def test_incident_roundtrip_review_history(store, fx):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid))
    store.record_observation(fx.phone_obs(sid, "fx-phone-120", 4000.0, 120))
    store.record_incident_change(fx.incident_change(sid, "inc-1", 0))
    store.record_incident_change(fx.incident_change(sid, "inc-1", 1, change="updated", duration_ms=1800.0))
    store.record_incident_change(fx.incident_change(sid, "inc-1", 2, change="closed"))

    [inc] = store.list_incidents(sid)
    assert inc.update_seq == 2 and inc.state.value == "closed" and inc.review_status.value == "pending"

    first = store.add_review(sid, "inc-1", HumanReviewCreate(decision="dismissed", comment="лежал экраном вниз", operator="t1"))
    second = store.add_review(sid, "inc-1", HumanReviewCreate(decision="confirmed", comment="", operator="t2"))
    assert second.supersedes_review_id == first.review_id
    detail = store.get_incident_detail(sid, "inc-1")
    assert [r.decision.value for r in detail.reviews] == ["dismissed", "confirmed"]
    assert detail.incident.review_status.value == "confirmed"
    # the automatic signal is untouched by the teacher's decision
    assert detail.incident.priority.value == "medium" and detail.incident.explanation.summary_ru
    # the referenced observation was persisted, unreferenced CV observations are not
    assert rows(store, "observations", sid) == 1
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("UPDATE reviews SET decision='dismissed' WHERE session_id=?", (sid,))


def test_review_double_submit_is_deduplicated(store, fx):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid))
    store.record_incident_change(fx.incident_change(sid))
    body = HumanReviewCreate(decision="inconclusive", comment="плохо видно", operator="t1")
    a = store.add_review(sid, "inc-1", body)
    b = store.add_review(sid, "inc-1", body)
    assert a.review_id == b.review_id and rows(store, "reviews", sid) == 1


def test_redelivery_is_idempotent(store, fx):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid))
    change = fx.incident_change(sid, "inc-1", 1)
    for _ in range(3):
        store.record_incident_change(change)
    store.record_incident_change(fx.incident_change(sid, "inc-1", 1, duration_ms=9999.0))  # same seq, other body
    store.record_incident_change(fx.incident_change(sid, "inc-1", 3, change="closed"))
    store.record_incident_change(fx.incident_change(sid, "inc-1", 2, change="updated"))  # late, older
    [inc] = store.list_incidents(sid)
    assert inc.update_seq == 3 and inc.state.value == "closed"
    assert rows(store, "incident_changes", sid) == 3
    env = fx.env_obs(sid, "env-1", 5000.0)
    store.record_observation(env)
    store.record_observation(env)
    obs = fx.phone_obs(sid, "p-1", 4000.0)
    store.record_observation(obs)
    store.record_observation(obs)
    counters = store._live[sid].counters
    assert counters["duplicate_incident_changes"] == 2
    assert counters["conflicting_incident_changes"] == 1
    assert counters["stale_incident_changes"] == 1
    assert counters["duplicate_observations"] == 2
    assert rows(store, "observations", sid) == 1  # env only; p-1 unreferenced


def test_two_sessions_are_isolated(store, fx):
    a, b = "s-a", "s-b"
    store.upsert_session(fx.session_info(a, retain_media=True))
    store.upsert_session(fx.session_info(b, retain_media=True))
    for sid in (a, b):  # same incident id in both sessions
        store.record_incident_change(fx.incident_change(sid, "inc-1", 0))
    store.add_review(a, "inc-1", HumanReviewCreate(decision="confirmed", comment="A", operator="t"))
    store.save_answer(a, "q1", AnswerUpsert(value=["a"], client_seq=1))
    ev_a = store.capture_snapshot(a, "inc-1", fx.frame(a))
    assert ev_a is not None
    # cross-session: frame of A offered for B, evidence of A requested through B
    assert store.capture_snapshot(b, "inc-1", fx.frame(a)) is None
    with pytest.raises(itf.NotFoundError):
        store.evidence_media(b, ev_a.evidence_id)
    assert store.get_incident_detail(b, "inc-1").reviews == []
    assert store.get_incident_detail(b, "inc-1").incident.review_status.value == "pending"
    assert store.list_answers(b) == []
    store.upsert_session(fx.session_info(a, state="finished"))
    store.delete_session(a)
    assert [i.incident_id for i in store.list_incidents(b)] == ["inc-1"]
    assert store.get_incident_detail(b, "inc-1").incident.evidence_ids == []


def test_source_mode_is_never_mixed(store, fx):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid, mode="synthetic"))
    store.record_observation(fx.env_obs(sid, "env-live", 1.0, mode="live"))
    store.record_incident_change(fx.incident_change(sid, mode="live"))
    store.upsert_session(fx.session_info(sid, mode="live"))  # a session cannot change mode
    assert rows(store, "observations", sid) == 0 and store.list_incidents(sid) == []
    assert store.get_session(sid).source_mode.value == "synthetic"
    assert store._live[sid].counters["source_mode_mismatch"] == 2


def test_nothing_is_appended_after_finish(store, fx):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid, retain_media=True))
    store.record_incident_change(fx.incident_change(sid))
    store.save_answer(sid, "q1", AnswerUpsert(value="x", client_seq=1))
    store.upsert_session(fx.session_info(sid, state="finished", retain_media=True))
    store.record_observation(fx.env_obs(sid, "late-env", 99_000.0))
    store.record_incident_change(fx.incident_change(sid, "inc-late", 0))
    assert store.capture_snapshot(sid, "inc-1", fx.frame(sid)) is None
    with pytest.raises(itf.InvalidStateError):
        store.save_answer(sid, "q1", AnswerUpsert(value="y", client_seq=2))
    store.upsert_session(fx.session_info(sid, state="running"))  # no resurrection
    assert store.get_session(sid).state == SessionState.FINISHED
    assert [i.incident_id for i in store.list_incidents(sid)] == ["inc-1"]
    assert store._live[sid].counters["rejected_after_end"] == 3
    assert store._live[sid].ring == {}  # temporary buffer dropped at the end


def test_answers_last_writer_wins_by_client_seq(store, fx):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid))
    store.save_answer(sid, "q1", AnswerUpsert(value=["a"], client_seq=1))
    store.save_answer(sid, "q1", AnswerUpsert(value=["c"], client_seq=3))
    stale = store.save_answer(sid, "q1", AnswerUpsert(value=["b"], client_seq=2))
    same = store.save_answer(sid, "q1", AnswerUpsert(value=["z"], client_seq=3))
    assert stale.value == ["c"] and same.value == ["c"]
    store.save_answer(sid, "q2", AnswerUpsert(value="<b>текст</b>", client_seq=1))
    assert {a.question_id: a.value for a in store.list_answers(sid)} == {"q1": ["c"], "q2": "<b>текст</b>"}


def test_snapshots_only_when_retain_media(store, fx):
    off, on = "s-off", "s-on"
    store.upsert_session(fx.session_info(off, retain_media=False))
    store.upsert_session(fx.session_info(on, retain_media=True))
    for sid in (off, on):
        store.record_incident_change(fx.incident_change(sid))
    assert store.capture_snapshot(off, "inc-1", fx.frame(off)) is None
    assert store.capture_snapshot(on, "inc-404", fx.frame(on)) is None  # unknown incident
    items = [store.capture_snapshot(on, "inc-1", fx.frame(on, frame_id=i)) for i in range(5)]
    kept = [i for i in items if i is not None]
    assert len(kept) == store.config.max_snapshots_per_incident
    item, data = store.evidence_media(on, kept[0].evidence_id)
    assert data[:3] == b"\xff\xd8\xff" and len(data) == item.size_bytes
    token = store._live[on].media_token
    files = sorted(p.name for p in (store.vault.root / token).iterdir())
    assert len(files) == 3 and all(MediaVault.new_file_name("image/jpeg")[-4:] == f[-4:] for f in files)
    assert not (store.vault.root / store._live[off].media_token).exists()
    assert store.list_incidents(on)[0].evidence_ids == [k.evidence_id for k in kept]


def test_snapshot_limits(make_store, fx):
    small = make_store(max_snapshot_bytes=100)
    small.open()
    small.upsert_session(fx.session_info("s", retain_media=True))
    small.record_incident_change(fx.incident_change("s"))
    assert small.capture_snapshot("s", "inc-1", fx.frame("s")) is None
    assert small._live["s"].counters["snapshot_skipped_limit"] == 1


def test_low_disk_space_refuses_media(make_store, fx):
    s = make_store(min_free_disk_bytes=1 << 62)
    s.open()
    s.upsert_session(fx.session_info("s", retain_media=True))
    s.record_incident_change(fx.incident_change("s"))
    assert s.capture_snapshot("s", "inc-1", fx.frame("s")) is None
    assert s._live["s"].counters["snapshot_skipped_low_disk"] == 1


def test_evidence_paths_cannot_escape(store, fx, tmp_path):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid, retain_media=True))
    store.record_incident_change(fx.incident_change(sid))
    item = store.capture_snapshot(sid, "inc-1", fx.frame(sid))
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"\xff\xd8\xff secret")
    # a tampered database row pointing outside the media root is refused
    for bad in ("../../secret.jpg", "/etc/passwd", "..", "a" * 32 + ".jpg/../../x"):
        store._conn.execute("UPDATE evidence SET file_name=? WHERE evidence_id=?", (bad, item.evidence_id))
        with pytest.raises(itf.NotFoundError):
            store.evidence_media(sid, item.evidence_id)
    # tampered media token
    store._conn.execute("UPDATE sessions SET media_token='../..' WHERE session_id=?", (sid,))
    with pytest.raises(itf.NotFoundError):
        store.evidence_media(sid, item.evidence_id)


def test_missing_and_tampered_media(store, fx):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid, retain_media=True))
    store.record_incident_change(fx.incident_change(sid))
    a = store.capture_snapshot(sid, "inc-1", fx.frame(sid, frame_id=1))
    b = store.capture_snapshot(sid, "inc-1", fx.frame(sid, frame_id=2))
    directory = store.vault.root / store._live[sid].media_token
    name_a = store._conn.execute("SELECT file_name FROM evidence WHERE evidence_id=?", (a.evidence_id,)).fetchone()[0]
    name_b = store._conn.execute("SELECT file_name FROM evidence WHERE evidence_id=?", (b.evidence_id,)).fetchone()[0]
    (directory / name_a).unlink()
    (directory / name_b).write_bytes(b"\xff\xd8\xff" + b"tampered")
    with pytest.raises(itf.NotFoundError) as missing:
        store.evidence_media(sid, a.evidence_id)
    assert missing.value.details["reason"] == "media_missing"
    with pytest.raises(itf.StorageError) as mismatch:
        store.evidence_media(sid, b.evidence_id)
    assert mismatch.value.details["reason"] == "hash_mismatch"
    snap = store.export_snapshot(sid)
    assert {e.status for e in snap.evidence.values()} == {"missing", "hash_mismatch"}


def test_delete_removes_session_completely(store, fx):
    sid, other = "s-del", "s-keep"
    marker = "UNIQUE-MARKER-7f3a9c"
    store.upsert_session(fx.session_info(sid, retain_media=True, student_label=marker))
    store.upsert_session(fx.session_info(other))
    store.record_observation(fx.phone_obs(sid, "p-1", 4000.0))
    store.record_observation(fx.env_obs(sid, "env-1", 4100.0))
    store.record_incident_change(fx.incident_change(sid, observation_ids=["p-1"]))
    store.capture_snapshot(sid, "inc-1", fx.frame(sid))
    store.add_review(sid, "inc-1", HumanReviewCreate(decision="confirmed", comment=marker, operator="t"))
    store.save_answer(sid, "q1", AnswerUpsert(value=marker, client_seq=1))
    with pytest.raises(itf.InvalidStateError):
        store.delete_session(sid)  # still running
    token = store._live[sid].media_token
    store.upsert_session(fx.session_info(sid, state="finished", retain_media=True, student_label=marker))
    store.delete_session(sid)
    for table in db.SESSION_TABLES:
        assert rows(store, table, sid) == 0, table
    assert not (store.vault.root / token).exists()
    assert store.get_session(other) is not None
    with pytest.raises(itf.NotFoundError):
        store.list_incidents(sid)
    store.delete_session(sid)  # idempotent
    with pytest.raises(itf.NotFoundError):
        store.delete_session("never-existed")
    # late writes cannot resurrect the session
    store.upsert_session(fx.session_info(sid))
    store.record_incident_change(fx.incident_change(sid))
    assert store.get_session(sid) is None
    # secure_delete + WAL checkpoint: the marker is not left in the database files
    # (best effort for SQLite pages; not a forensic erase of the disk)
    store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    blob = b"".join(p.read_bytes() for p in store.data_dir.glob("qorgau-evidence.sqlite3*")
                    if p.is_file() and p.name != store.db_path.name + ".lock")
    assert marker.encode() not in blob


def test_purge_expired_media_keeps_metadata(make_store, fx):
    s = make_store(media_ttl_s=0.0)
    s.open()
    s.upsert_session(fx.session_info("s", retain_media=True))
    s.record_incident_change(fx.incident_change("s"))
    item = s.capture_snapshot("s", "inc-1", fx.frame("s"))
    assert s.purge_expired_media() == 1
    with pytest.raises(itf.NotFoundError) as exc:
        s.evidence_media("s", item.evidence_id)
    assert exc.value.details["reason"] == "ttl_expired"
    assert s.list_incidents("s")[0].evidence_ids == []
    assert s.export_snapshot("s").evidence_expired == 1


def test_observation_buffer_is_bounded(make_store, fx):
    s = make_store(observation_buffer_max=10)
    s.open()
    s.upsert_session(fx.session_info("s"))
    for i in range(50):
        s.record_observation(fx.phone_obs("s", f"p-{i}", 1000.0 + i * 100, i))
    assert len(s._live["s"].ring) <= 10
    s.record_incident_change(fx.incident_change("s", observation_ids=["p-0", "p-49"]))
    snap = s.export_snapshot("s")
    assert snap.missing_observation_refs == 1 and [o["observation_id"] for o in snap.observations] == ["p-49"]


def test_coverage_gaps_and_unknown(store, fx):
    sid = "s-cov"
    created = utc_now() - timedelta(seconds=120)
    base = dict(created_at=created, exam_started_t_ms=10_000.0, paused_total_ms=0.0)
    store.upsert_session(fx.session_info(sid, **base))
    i = 0
    t = 10_000.0
    while t <= 60_000.0:  # phone every 125 ms, the whole exam
        store.record_observation(fx.phone_obs(sid, f"p-{i}", t, i))
        if t <= 30_000.0 or t >= 50_000.0:
            store.record_observation(fx.attention_obs(sid, f"a-{i}", t, i))
        elif t >= 40_000.0:
            store.record_observation(fx.attention_obs(sid, f"a-{i}", t, i, status="unknown"))
        t += 125.0
        i += 1
    store.record_observation(fx.health_obs(sid, "h-1", 55_000.0, ok=False))
    store.record_observation(fx.health_obs(sid, "h-2", 57_000.0, ok=True))
    store.upsert_session(fx.session_info(sid, state="finished", finished_at=created + timedelta(seconds=60), **base))
    summary = store.summary(sid)
    cov = store.export_snapshot(sid).coverage
    assert cov["components"]["phone"]["observed_ms"] == pytest.approx(50_000.0)
    assert cov["components"]["attention"]["observed_ms"] == pytest.approx(30_000.0, abs=300)
    assert cov["components"]["attention"]["undetermined_ms"] == pytest.approx(10_000.0, abs=300)
    assert summary.observed_ms == pytest.approx(30_000.0, abs=300)
    reasons = {(g.component.value, g.reason): (g.t_start_ms, g.t_end_ms) for g in summary.gaps}
    assert reasons[("attention", "no_observations")][0] == pytest.approx(30_000.0, abs=200)
    assert reasons[("attention", "undetermined")][1] == pytest.approx(50_000.0, abs=200)
    assert reasons[("capture", "camera_disconnected")] == (55_000.0, 57_000.0)
    assert any("неизвестно" in line for line in summary.limitations_ru)


def test_pause_is_a_coverage_gap(store, fx):
    sid = "s-pause"
    created = utc_now() - timedelta(seconds=30)
    base = dict(created_at=created, exam_started_t_ms=1_000.0)
    store.upsert_session(fx.session_info(sid, paused_total_ms=0.0, **base))
    store.upsert_session(fx.session_info(sid, state="paused", paused_total_ms=0.0, **base))
    time.sleep(0.3)
    store.upsert_session(fx.session_info(sid, state="finished", paused_total_ms=300.0, finished_at=utc_now(), **base))
    summary = store.summary(sid)
    paused = [g for g in summary.gaps if g.reason == "paused"]
    assert len(paused) == 1 and paused[0].t_start_ms == pytest.approx(30_000.0, abs=2_000)
    assert paused[0].t_end_ms - paused[0].t_start_ms == pytest.approx(300.0, abs=150)
    assert summary.paused_ms == 300.0
