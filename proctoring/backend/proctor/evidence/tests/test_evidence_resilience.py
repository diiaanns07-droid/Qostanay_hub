"""A08 failure behaviour: interrupted transactions, process kill, restart recovery, disk errors, races."""

from __future__ import annotations

import errno
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from proctor.evidence import create_evidence_store, db
from proctor.settings import PROCTORING_ROOT, Settings
from proctor_contracts import interfaces as itf
from proctor_contracts.v1 import AnswerUpsert, HumanReviewCreate


class Boom(RuntimeError):
    pass


def test_interrupted_transaction_rolls_back(store, fx):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid))
    store.record_observation(fx.phone_obs(sid, "fx-phone-120", 4000.0, 120))

    def fail(point: str) -> None:
        if point == "incident_change:after_incident":
            raise Boom(point)

    store._fault = fail
    store.record_incident_change(fx.incident_change(sid))  # must not raise into the fusion thread
    assert store._conn.execute("SELECT COUNT(*) FROM incident_changes").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
    assert store.health().code == "storage_write_failed"
    assert store._live[sid].counters["write_failures"] == 1
    store._fault = None
    store.record_incident_change(fx.incident_change(sid))  # re-delivery after the failure succeeds
    assert [i.incident_id for i in store.list_incidents(sid)] == ["inc-1"]
    assert store.export_snapshot(sid).missing_observation_refs == 0


def test_interrupted_delete_keeps_session_intact(store, fx):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid, retain_media=True))
    store.record_incident_change(fx.incident_change(sid))
    store.capture_snapshot(sid, "inc-1", fx.frame(sid))
    store.add_review(sid, "inc-1", HumanReviewCreate(decision="confirmed", comment="c", operator="t"))
    store.upsert_session(fx.session_info(sid, state="finished", retain_media=True))

    def fail(point: str) -> None:
        if point == "delete:reviews":
            raise Boom(point)

    store._fault = fail
    with pytest.raises(Boom):
        store.delete_session(sid)
    store._fault = None
    detail = store.get_incident_detail(sid, "inc-1")
    assert len(detail.reviews) == 1 and len(detail.evidence) == 1
    store.evidence_media(sid, detail.evidence[0].evidence_id)  # media untouched
    store.delete_session(sid)
    assert store.get_session(sid) is None


CHILD = textwrap.dedent(
    """
    import os, sys
    sys.path[:0] = [{backend!r}, {contracts!r}, {tests!r}]
    from pathlib import Path
    from proctor.evidence import create_evidence_store, EvidenceConfig
    from proctor.settings import Settings
    import conftest as fx
    from proctor_contracts.v1 import AnswerUpsert, HumanReviewCreate
    store = create_evidence_store(Settings(data_dir=Path({data!r})), EvidenceConfig(min_free_disk_bytes=0))
    assert store.open().code == "ok"
    sid = "s-crash"
    store.upsert_session(fx.session_info(sid, retain_media=True))
    store.record_observation(fx.phone_obs(sid, "fx-phone-120", 4000.0, 120))
    store.record_observation(fx.env_obs(sid, "env-1", 4500.0))
    store.record_incident_change(fx.incident_change(sid, "inc-1", 0))
    store.capture_snapshot(sid, "inc-1", fx.frame(sid))
    store.add_review(sid, "inc-1", HumanReviewCreate(decision="dismissed", comment="ok", operator="t"))
    store.save_answer(sid, "q1", AnswerUpsert(value=["b"], client_seq=1))
    def die(point):
        if point == "incident_change:before_commit":
            os._exit(17)   # hard kill inside an open transaction
    store._fault = die
    store.record_incident_change(fx.incident_change(sid, "inc-2", 0))
    """
)


def test_kill_inside_transaction_then_restart_recovers(tmp_path, make_store):
    data = tmp_path / "data"
    script = CHILD.format(
        backend=str(PROCTORING_ROOT / "backend"),
        contracts=str(PROCTORING_ROOT / "contracts" / "python"),
        tests=str(Path(__file__).parent),
        data=str(data),
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 17, proc.stderr[-2000:]
    store = make_store(data)
    assert store.open().code == "ok"
    info = store.get_session("s-crash")
    assert info.state.value == "failed" and info.last_error is not None
    assert [i.incident_id for i in store.list_incidents("s-crash")] == ["inc-1"]  # inc-2 rolled back
    detail = store.get_incident_detail("s-crash", "inc-1")
    assert detail.reviews[0].comment == "ok" and len(detail.evidence) == 1
    store.evidence_media("s-crash", detail.evidence[0].evidence_id)
    assert [a.value for a in store.list_answers("s-crash")] == [["b"]]
    summary = store.summary("s-crash")
    assert any("сбоем" in line for line in summary.limitations_ru)
    store.delete_session("s-crash")  # a recovered session is deletable


def test_clean_restart_keeps_finished_session(tmp_path, make_store, fx):
    data = tmp_path / "data"
    first = make_store(data)
    first.open()
    first.upsert_session(fx.session_info("s", retain_media=True))
    first.record_incident_change(fx.incident_change("s"))
    item = first.capture_snapshot("s", "inc-1", fx.frame("s"))
    first.upsert_session(fx.session_info("s", state="finished", retain_media=True))
    first.close()
    second = make_store(data)
    assert second.open().code == "ok"
    assert second.get_session("s").state.value == "finished"
    assert second.evidence_media("s", item.evidence_id)[0].sha256 == item.sha256
    assert second.export_snapshot("s").recovered is False


def test_second_backend_on_same_data_dir_is_refused(tmp_path, make_store):
    data = tmp_path / "data"
    a, b = make_store(data), make_store(data)
    assert a.open().code == "ok"
    health = b.open()
    assert health.status.value == "error" and health.code == "store_in_use"
    with pytest.raises(itf.StorageError):
        b.list_sessions()


def test_sqlite_disk_full_is_reported_not_raised(store, fx):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid))
    store.record_incident_change(fx.incident_change(sid))
    pages = store._conn.execute("PRAGMA page_count").fetchone()[0]
    store._conn.execute(f"PRAGMA max_page_count = {pages}")  # real SQLITE_FULL from now on
    big = "x" * 1000
    for i in range(40):  # fusion-thread writes: never raise
        store.record_incident_change(fx.incident_change(sid, f"inc-{i + 2}", 0, explanation={"summary_ru": big, "facts": [], "caveats_ru": []}))
    health = store.health()
    assert health.status.value == "degraded" and health.code == "storage_write_failed"
    assert store._live[sid].counters["write_failures"] >= 1
    with pytest.raises(itf.StorageError) as exc:  # API path: 503 STORAGE_ERROR, retryable
        for i in range(40):
            store.add_review(sid, "inc-1", HumanReviewCreate(decision="confirmed", comment=big * 2 if i % 2 else big, operator="t"))
    assert exc.value.http_status == 503 and exc.value.retryable
    store._conn.execute("PRAGMA max_page_count = 1073741823")
    store.record_incident_change(fx.incident_change(sid, "inc-after", 0))
    assert "inc-after" in {i.incident_id for i in store.list_incidents(sid)}


def test_media_write_error_leaves_no_partial_file(store, fx, monkeypatch):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid, retain_media=True))
    store.record_incident_change(fx.incident_change(sid))
    real_fsync = os.fsync

    def enospc(fd):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", enospc)
    assert store.capture_snapshot(sid, "inc-1", fx.frame(sid)) is None
    monkeypatch.setattr(os, "fsync", real_fsync)
    directory = store.vault.root / store._live[sid].media_token
    assert list(directory.iterdir()) == []
    assert store.health().code == "storage_write_failed" and "ENOSPC" in store.health().message
    assert store._conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0


def test_db_insert_failure_removes_written_media(store, fx):
    sid = "s-a"
    store.upsert_session(fx.session_info(sid, retain_media=True))
    store.record_incident_change(fx.incident_change(sid))

    def fail(point: str) -> None:
        if point == "snapshot:before_commit":
            raise Boom(point)

    store._fault = fail
    assert store.capture_snapshot(sid, "inc-1", fx.frame(sid)) is None
    assert list((store.vault.root / store._live[sid].media_token).iterdir()) == []


def test_unusable_data_dir_reports_error(tmp_path, make_store, fx):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    store = make_store(blocker)
    health = store.open()  # never raises
    assert health.status.value == "error" and health.code == "storage_unavailable"
    with pytest.raises(itf.StorageError):
        store.list_sessions()
    store.upsert_session(fx.session_info("s"))  # recording on a broken store is counted, never raised
    store.record_observation(fx.env_obs("s", "e", 1.0))
    assert store.health().details["write_errors"] >= 1


def test_schema_newer_than_code_is_not_touched(tmp_path, make_store):
    data = tmp_path / "data"
    data.mkdir()
    conn = sqlite3.connect(data / "qorgau-evidence.sqlite3")
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    store = make_store(data)
    health = store.open()
    assert health.code == "schema_too_new"
    conn = sqlite3.connect(data / "qorgau-evidence.sqlite3")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 99
    conn.close()


def test_open_cleans_debris_and_orphans(tmp_path, make_store, fx):
    data = tmp_path / "data"
    first = make_store(data)
    first.open()
    first.upsert_session(fx.session_info("s", retain_media=True))
    first.record_incident_change(fx.incident_change("s"))
    first.capture_snapshot("s", "inc-1", fx.frame("s"))
    token = first._live["s"].media_token
    first.upsert_session(fx.session_info("s", state="finished", retain_media=True))
    first.close()
    root = data / "evidence-media"
    (root / token / ("c" * 32 + ".jpg.tmp")).write_bytes(b"partial")
    orphan = root / ("d" * 32)
    orphan.mkdir()
    (orphan / ("e" * 32 + ".jpg")).write_bytes(b"\xff\xd8\xff")
    (root / ".trash-x").mkdir()
    second = make_store(data)
    second.open()
    assert not orphan.exists() and not (root / ".trash-x").exists()
    assert [p.name for p in (root / token).iterdir() if p.name.endswith(".tmp")] == []
    assert len(list((root / token).iterdir())) == 1


def test_pending_media_deletion_is_retried(tmp_path, make_store, fx, monkeypatch):
    data = tmp_path / "data"
    first = make_store(data)
    first.open()
    first.upsert_session(fx.session_info("s", retain_media=True))
    first.record_incident_change(fx.incident_change("s"))
    first.capture_snapshot("s", "inc-1", fx.frame("s"))
    token = first._live["s"].media_token
    first.upsert_session(fx.session_info("s", state="finished", retain_media=True))
    monkeypatch.setattr(first.vault, "remove_session_dir", lambda t: False)  # e.g. file locked on Windows
    first.delete_session("s")
    assert first.get_session("s") is None and first.health().details["media_delete_pending"] == 1
    first.close()
    monkeypatch.undo()
    second = make_store(data)
    second.open()
    assert not (data / "evidence-media" / token).exists()
    assert second._conn.execute("SELECT COUNT(*) FROM pending_media_deletions").fetchone()[0] == 0


def test_delete_export_race(store, fx):
    """Policy: export and delete of one session are serialized. An export either returns a complete,
    hash-verified snapshot or SESSION_NOT_FOUND — never a half-deleted mix."""
    sid = "s-race"
    store.upsert_session(fx.session_info(sid, retain_media=True))
    for i in range(3):
        store.record_incident_change(fx.incident_change(sid, f"inc-{i}", 0))
        store.capture_snapshot(sid, f"inc-{i}", fx.frame(sid, frame_id=i))
    store.upsert_session(fx.session_info(sid, state="finished", retain_media=True))
    results: list[str] = []
    errors: list[BaseException] = []
    started = threading.Event()

    def exporter() -> None:
        while True:
            try:
                snap = store.export_snapshot(sid)
                started.set()
                ok = len(snap.incidents) == 3 and len(snap.evidence) == 3 and all(e.status == "ok" for e in snap.evidence.values())
                results.append("complete" if ok else "partial")
            except itf.NotFoundError:
                results.append("not_found")
                return
            except BaseException as exc:  # anything else is a bug
                errors.append(exc)
                return

    threads = [threading.Thread(target=exporter) for _ in range(3)]
    for t in threads:
        t.start()
    started.wait(5)
    store.delete_session(sid)
    for t in threads:
        t.join(10)
    assert not errors
    assert "partial" not in results and results.count("not_found") == 3 and "complete" in results


def test_concurrent_recording_two_sessions(store, fx):
    """Fusion-thread writes for one session while API threads read/review another."""
    a, b = "s-a", "s-b"
    store.upsert_session(fx.session_info(a))
    store.upsert_session(fx.session_info(b, state="finished"))

    def writer() -> None:
        for i in range(300):
            store.record_observation(fx.phone_obs(a, f"p-{i}", 1000.0 + i * 50, i))
            if i % 30 == 0:
                store.record_incident_change(fx.incident_change(a, f"inc-{i}", 0, observation_ids=[f"p-{i}"]))

    def reader() -> None:
        for _ in range(100):
            store.list_sessions()
            store.summary(b)
            store.list_incidents(a)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(store.list_incidents(a)) == 10 and store.list_incidents(b) == []
    assert store.health().code == "ok"
