"""T03 on the REAL C1 process, socket transport and auth gate (never the DEV server).

All observations are test fixtures. Source declarations exercise wire provenance, not
hardware: no camera, microphone, native guard or real student recording is used.
"""

from __future__ import annotations

import base64
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime

import httpx

from classroom.server.tests.harness import ServerProcess, TeacherStream, TINY_JPEG, wait_until

FEATURE = "classreview.classroom_feature:create_classroom_feature"


@contextmanager
def running(data, **extra):
    server = ServerProcess(data, {"QORGAU_CLASS_FEATURES": FEATURE, **extra})
    try:
        yield server
    finally:
        assert server.stop() == 0, "".join(server.stderr[-30:])


def assert_mounted(teacher):
    info = teacher.get("/api/teacher/info").json()
    feature = next(f for f in info["features"] if f["name"] == "history")
    assert feature["status"] == "mounted" and not feature.get("detail"), info


def join(server, teacher, *, run="test-run-1", **extra):
    session = teacher.post("/api/teacher/session", json={"title": "TEST FIXTURES — no hardware", "mode": "url"})
    assert session.status_code == 201, session.text
    student = server.student()
    welcome = student.hello(join_code=session.json()["join_code"], client_run_id=run,
                            student_label="TEST FIXTURE", **extra)
    assert welcome["type"] == "welcome", welcome
    return student, welcome, session.json()


def history(teacher, sid):
    response = teacher.get(f"/api/teacher/students/{sid}/incidents")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict), body  # proves production T03 replaced C1's fallback list
    return {item["incident_id"]: item for item in body["incidents"]}


def await_incident(teacher, sid, iid, predicate=lambda item: True):
    result = wait_until(lambda: (item := history(teacher, sid).get(iid)) and predicate(item) and item)
    assert result, history(teacher, sid)
    return result


def request_clip(teacher, student, sid, iid, **extra):
    response = teacher.post(f"/api/teacher/students/{sid}/commands",
                            json={"kind": "request_clip", "payload": {"incident_id": iid}, **extra})
    assert response.status_code == 202, response.text
    command = response.json()
    if student is not None:
        student.wait_for(lambda message: message.get("command_id") == command["command_id"])
    return command


def upload(server, token, iid, clip, source="test"):
    # A fresh client has no teacher cookie: student authorization must really work.
    return httpx.post(f"{server.base}/api/student/clips/{iid}", content=clip,
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
                               "X-Qorgau-Clip-Source": source})


def test_c1_request_upload_range_decision_and_restart(tmp_path, clip):
    data = tmp_path / "c1"
    iid = "test-playable-clip"
    with running(data) as server, closing(server.teacher()) as teacher:
        assert_mounted(teacher)
        student, welcome, session = join(server, teacher)
        sid = welcome["student_id"]
        stream = TeacherStream(server, teacher.cookies.get("qorgau_teacher"))
        try:
            student.incident(1, iid, source_mode="synthetic", source_session_id="test-local-session",
                             explanation_ru="TEST FIXTURE — synthetic incident", clip_available=True,
                             snapshot_jpeg_b64=base64.b64encode(TINY_JPEG).decode())
            first = await_incident(teacher, sid, iid)
            assert first["origin"] == "simulated" and first["source_mode"] == "synthetic"
            assert first["class_session_id"] == session["session_id"]
            assert first["snapshot_available"] is True
            snapshot_url = f"/api/teacher/students/{sid}/incidents/{iid}/snapshot"
            assert teacher.get(snapshot_url).content == TINY_JPEG
            command = request_clip(teacher, student, sid, iid)
            pending = await_incident(teacher, sid, iid, lambda i: i["clip"]["state"] == "loading")
            assert datetime.fromisoformat(pending["clip"]["requested_at"]) == datetime.fromisoformat(command["issued_at"])
            stored = upload(server, welcome["resume_token"], iid, clip, source="live")
            assert stored.status_code == 201, stored.text
            assert stored.json()["source"] == "synthetic"  # forged header cannot promote evidence
            student.send("ack", command_id=command["command_id"], ok=True)
            available = await_incident(teacher, sid, iid, lambda i: i["clip"]["state"] == "available")
            assert available["clip"]["browser_playable"] is True
            assert available["event_provenance"] == first["event_provenance"]
            clip_url = f"/api/teacher/clips/{iid}?student_id={sid}"
            full = teacher.get(clip_url)
            assert full.status_code == 200 and full.content == clip
            part = teacher.get(clip_url, headers={"Range": "bytes=11-99"})
            assert part.status_code == 206 and part.content == clip[11:100]
            assert part.headers["content-range"] == f"bytes 11-99/{len(clip)}"
            assert part.headers["x-qorgau-clip-source"] == "synthetic"
            assert teacher.head(clip_url).headers["content-length"] == str(len(clip))
            assert teacher.get(clip_url, headers={"Range": f"bytes={len(clip)}-"}).status_code == 416
            first_decision = teacher.post(f"/api/teacher/students/{sid}/decision",
                json={"incident_id": iid, "decision": "needs_followup", "note_ru": "TEST: inspect clip"})
            assert first_decision.status_code == 200, first_decision.text
            second = teacher.post(f"/api/teacher/students/{sid}/decision",
                json={"incident_id": iid, "decision": "dismissed", "note_ru": "TEST: synthetic recording"})
            assert second.status_code == 200, second.text
            decisions = second.json()["decision_history"]
            assert len(decisions) == 2 and decisions[1]["supersedes"] == decisions[0]["decision_id"]
            stream.wait_for(lambda m: m["type"] == "feature_event" and m.get("event") == "history.changed"
                            and m["data"].get("incident", {}).get("decision") == "dismissed")
            assert teacher.get(f"/api/teacher/students/{sid}").json()["incidents_unreviewed"] == 0
            assert_mounted(teacher)
        finally:
            student.close()
            stream.close()
    with running(data) as restarted, closing(restarted.teacher()) as teacher:
        assert_mounted(teacher)
        restored = history(teacher, sid)[iid]
        assert restored["decision"] == "dismissed" and restored["decision_history"] == decisions
        assert restored["event_provenance"] == first["event_provenance"]
        assert teacher.get(clip_url).content == clip
        assert teacher.get(snapshot_url).content == TINY_JPEG


def test_c1_history_backfill_run_seq_reuse_and_immutable_provenance(tmp_path):
    data = tmp_path / "backfill"
    # Accept events with history absent, then enable the real adapter on the same C1 DB.
    with running(data, QORGAU_CLASS_FEATURES="") as server, closing(server.teacher()) as teacher:
        student, welcome, session = join(server, teacher, run="test-old-run")
        sid = welcome["student_id"]
        try:
            student.incident(1, "test-replay", source_mode="replay", source_session_id="test-replay-session",
                             snapshot_jpeg_b64=base64.b64encode(TINY_JPEG).decode())
            assert wait_until(lambda: len(teacher.get(f"/api/teacher/students/{sid}/events").json()) == 1)
        finally:
            student.close()
    with running(data) as server, closing(server.teacher()) as teacher:
        assert_mounted(teacher)
        old = history(teacher, sid)["test-replay"]
        assert old["origin"] == "replay" and old["snapshot_available"] is False
        # C1 logs snapshot presence but not its bytes; backfill must not invent an image.
        assert teacher.get(f"/api/teacher/students/{sid}/incidents/test-replay/snapshot").status_code == 404
        student = server.student()
        try:
            assert student.hello(resume_token=welcome["resume_token"], client_run_id="test-new-run")["student_id"] == sid
            student.status(source_mode="live", source_session_id="test-current-live")
            # Same wire seq, new backend run, accepted C1 event: history must keep it.
            student.incident(1, "test-synthetic", source_mode="synthetic", source_session_id="test-queued-synthetic")
            student.incident(2, "test-live", source_mode="live", source_session_id="test-live-session")
            student.incident(3, "test-unknown")
            # A later source must not relabel the first event's episode.
            student.incident(4, "test-replay", state="closed", source_mode="live", source_session_id="test-current-live")
            assert wait_until(lambda: len(history(teacher, sid)) == 4)
            replay = await_incident(teacher, sid, "test-replay", lambda i: len(i["event_provenance"]) == 2)
            assert replay["origin"] == "replay" and replay["source_session_id"] == "test-replay-session"
            assert replay["event_provenance"][0] == old["event_provenance"][0]
            assert [p["source_mode"] for p in replay["event_provenance"]] == ["replay", "live"]
            rows = history(teacher, sid)
            assert rows["test-synthetic"]["origin"] == "simulated"
            assert rows["test-live"]["origin"] == "real"
            assert rows["test-unknown"]["origin"] == "unknown"
            assert rows["test-synthetic"]["event_provenance"][0]["seq"] == 1
            assert rows["test-synthetic"]["event_provenance"][0]["client_run_id"] == "test-new-run"
            # Duplicate delivery and same-run seq conflict follow canonical C1 identities.
            student.incident(1, "test-synthetic", source_mode="synthetic", source_session_id="test-queued-synthetic")
            student.incident(1, "test-conflict", source_mode="synthetic", source_session_id="test-conflict-session")
            conflict = await_incident(teacher, sid, "test-conflict")
            assert conflict["event_provenance"][0]["seq_conflict"] is True
            assert len(history(teacher, sid)["test-synthetic"]["event_provenance"]) == 1
            assert_mounted(teacher)
        finally:
            student.close()


def test_c1_upload_auth_ownership_path_and_size_limits(tmp_path, clip):
    with running(tmp_path / "auth", QORGAU_CLASS_MAX_CLIP_BYTES="524288") as server, closing(server.teacher()) as teacher:
        assert_mounted(teacher)
        student, welcome, session = join(server, teacher)
        other = server.student()
        try:
            foreign = other.hello(join_code=session["join_code"], student_label="TEST OTHER")
            sid, iid = welcome["student_id"], "test-owned-incident"
            student.incident(1, iid, source_mode="synthetic", clip_available=True)
            await_incident(teacher, sid, iid)
            assert upload(server, welcome["resume_token"], iid, clip).status_code == 409
            request_clip(teacher, student, sid, iid)
            assert upload(server, "forged-token", iid, clip).status_code == 401
            assert upload(server, foreign["resume_token"], iid, clip).status_code == 404
            assert upload(server, welcome["resume_token"], iid, b"x" * 524289).status_code == 413
            assert upload(server, welcome["resume_token"], "bad%5Cid", clip).status_code == 422
            assert upload(server, welcome["resume_token"], "bad%2Fid", clip).status_code == 404
            assert history(teacher, sid)[iid]["clip"]["state"] == "loading"
            with httpx.Client(base_url=server.base) as anonymous:
                for path in (f"/api/teacher/students/{sid}/incidents", f"/api/teacher/clips/{iid}",
                             "/api/teacher/history/assets/register.js"):
                    assert anonymous.get(path).status_code == 401
                    assert anonymous.get(path, headers={"Authorization": f"Bearer {welcome['resume_token']}"}).status_code == 401
                assert anonymous.post(f"/api/teacher/students/{sid}/decision",
                                      json={"incident_id": iid, "decision": "confirmed"}).status_code == 401
            for name in ("register.js", "review-module.js", "review.css"):
                asset = teacher.get(f"/api/teacher/history/assets/{name}")
                assert asset.status_code == 200 and asset.headers["cache-control"] == "no-store"
            assert teacher.get("/api/teacher/history/assets/config.py").status_code == 404
            stored = upload(server, welcome["resume_token"], iid, clip)
            assert stored.status_code == 201, stored.text
            assert upload(server, welcome["resume_token"], iid, clip).status_code == 200
            assert upload(server, welcome["resume_token"], iid, clip + b"changed").status_code == 409
            assert teacher.get(f"/api/teacher/clips/{iid}?student_id={foreign['student_id']}").status_code == 404
            assert_mounted(teacher)
        finally:
            student.close()
            other.close()


def test_c1_request_failure_and_timeout_are_persisted_across_restart(tmp_path):
    data = tmp_path / "timeout"
    with running(data, QORGAU_CLASS_REVIEW_CLIP_TIMEOUT_S="1") as server, closing(server.teacher()) as teacher:
        assert_mounted(teacher)
        student, welcome, _ = join(server, teacher)
        sid = welcome["student_id"]
        try:
            student.incident(1, "test-refused", source_mode="synthetic", clip_available=True)
            student.incident(2, "test-timeout", source_mode="synthetic", clip_available=True)
            await_incident(teacher, sid, "test-timeout")
            refused = request_clip(teacher, student, sid, "test-refused")
            student.send("ack", command_id=refused["command_id"], ok=False, error_ru="TEST: clip not recorded")
            row = await_incident(teacher, sid, "test-refused", lambda i: i["clip"]["state"] == "unavailable")
            assert row["clip"]["reason_code"] == "student_refused"
            pending = request_clip(teacher, student, sid, "test-timeout")
            # Successful command ACK alone does not mean the clip bytes reached C1.
            student.send("ack", command_id=pending["command_id"], ok=True)
            row = await_incident(teacher, sid, "test-timeout", lambda i: i["clip"]["state"] == "unavailable")
            assert row["clip"]["reason_code"] == "upload_timeout"
            # Read only: prove tick persisted expiry; GET's derived timeout alone is insufficient.
            def persisted():
                with sqlite3.connect(data / "features" / "history" / "class-review.sqlite3") as conn:
                    return conn.execute("SELECT status FROM clip_requests WHERE command_id=?", (pending["command_id"],)).fetchone()[0] == "failed"
            assert wait_until(persisted)
            assert_mounted(teacher)
        finally:
            student.close()
    with running(data, QORGAU_CLASS_REVIEW_CLIP_TIMEOUT_S="1") as server, closing(server.teacher()) as teacher:
        assert_mounted(teacher)
        rows = history(teacher, sid)
        assert rows["test-refused"]["clip"]["reason_code"] == "student_refused"
        assert rows["test-timeout"]["clip"]["reason_code"] == "upload_timeout"
        with sqlite3.connect(data / "features" / "history" / "class-review.sqlite3") as conn:
            requested_at, count = conn.execute("SELECT requested_at,COUNT(*) FROM clip_requests WHERE command_id=?",
                                              (pending["command_id"],)).fetchone()
        assert datetime.fromisoformat(requested_at) == datetime.fromisoformat(pending["issued_at"]) and count == 1


def test_c1_closed_episode_retains_accepted_clip_readiness(tmp_path):
    with running(tmp_path / "clip-ready") as server, closing(server.teacher()) as teacher:
        student, welcome, _ = join(server, teacher)
        sid = welcome["student_id"]
        try:
            student.incident(1, "test-ready-before-close", source_mode="synthetic", clip_available=True)
            await_incident(teacher, sid, "test-ready-before-close")
            student.incident(2, "test-ready-before-close", state="closed", source_mode="synthetic")
            row = await_incident(teacher, sid, "test-ready-before-close", lambda i: i["state"] == "closed")
            assert row["clip_available"] is True and row["clip"]["can_request"] is True
            student.incident(3, "test-delayed-ready", state="closed", source_mode="synthetic")
            await_incident(teacher, sid, "test-delayed-ready")
            # Backlogged open/ready event arrives after close. C1 preserves closed state and merges evidence.
            student.incident(4, "test-delayed-ready", source_mode="synthetic", clip_available=True,
                             snapshot_jpeg_b64=base64.b64encode(TINY_JPEG).decode())
            row = await_incident(teacher, sid, "test-delayed-ready", lambda i: len(i["event_provenance"]) == 2)
            assert row["state"] == "closed" and row["clip_available"] is True
            assert row["snapshot_available"] is True
            assert teacher.get(f"/api/teacher/students/{sid}/incidents/test-delayed-ready/snapshot").content == TINY_JPEG
            assert_mounted(teacher)
        finally:
            student.silent = True
            student.close()
