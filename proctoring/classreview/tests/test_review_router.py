"""HTTP routes: authorization, foreign data, uploads (re-upload, wrong/too large file), playback with seeking."""

from __future__ import annotations

import os

import pytest
from conftest import TEACHER_HEADER, incident, tiny_avi, tiny_mp4, upload
from fastapi import FastAPI
from fastapi.testclient import TestClient

from classreview import create_review_router


def _requested(client, student="stu-a", iid="inc-1", seq=1, **over):
    client.store.ingest_incident(student, incident(seq, incident_id=iid, **over), class_session_id="cls-1")
    client.store.note_clip_requested(student, iid, f"cmd-{student}-{iid}")


def test_secure_default_refuses_everything(store, clip):
    c = TestClient(_app(create_review_router(store)))
    store.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
    assert c.get("/api/teacher/students/stu-a/incidents").status_code == 401
    assert c.post("/api/teacher/students/stu-a/decision", json={"incident_id": "inc-1", "decision": "confirmed"}).status_code == 401
    assert c.get("/api/teacher/clips/inc-1").status_code == 401
    assert upload(c, "inc-1", clip).status_code == 401


def _app(router):
    app = FastAPI()
    app.include_router(router)
    return app


def test_upload_requires_student_token_and_ownership(client, clip):
    _requested(client)
    assert client.post("/api/student/clips/inc-1", content=clip, headers={"Content-Type": "video/mp4"}).status_code == 401
    assert upload(client, "inc-1", clip, token="forged-token").status_code == 401
    # student B uploads for A's episode: B has no such episode -> 404, nothing stored
    r = upload(client, "inc-1", clip, token="tok-b")
    assert r.status_code == 404 and r.json()["error"]["code"] == "incident_not_found"
    assert client.store.get_incident("stu-a", "inc-1")["clip"]["state"] == "loading"
    assert list(client.store.clips.root.iterdir()) == []
    # the owner can
    r = upload(client, "inc-1", clip, **{"X-Qorgau-Clip-Source": "test"})
    assert r.status_code == 201 and r.json()["status"] == "stored" and r.json()["source"] == "test"


def test_student_cannot_use_teacher_routes(client):
    _requested(client)
    auth = {"Authorization": "Bearer tok-a"}
    assert client.get("/api/teacher/students/stu-a/incidents", headers=auth).status_code == 401
    assert client.get("/api/teacher/clips/inc-1", headers=auth).status_code == 401
    r = client.post("/api/teacher/students/stu-a/decision", headers=auth, json={"incident_id": "inc-1", "decision": "dismissed"})
    assert r.status_code == 401 and client.store.get_incident("stu-a", "inc-1")["decision"] is None


def test_unrequested_clip_is_refused(client, clip):
    client.store.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
    r = upload(client, "inc-1", clip)
    assert r.status_code == 409 and r.json()["error"]["code"] == "clip_not_requested"


def test_reupload_is_idempotent_and_evidence_is_not_replaced(client, clip, clip_b):
    _requested(client)
    assert upload(client, "inc-1", clip).status_code == 201
    again = upload(client, "inc-1", clip)
    assert again.status_code == 200 and again.json()["status"] == "duplicate"
    other = upload(client, "inc-1", clip_b)
    assert other.status_code == 409 and other.json()["error"]["code"] == "clip_already_stored"
    assert len(list(client.store.clips.root.iterdir())) == 1
    assert client.get("/api/teacher/clips/inc-1", headers=TEACHER_HEADER).content == clip


@pytest.mark.parametrize(
    ("media_type", "data", "status", "code"),
    [
        ("image/jpeg", b"\xff\xd8\xff\xe0data", 415, "unsupported_media_type"),
        ("video/webm", b"\x1a\x45\xdf\xa3", 415, "unsupported_media_type"),
        ("video/mp4", b"<html>not a video</html>", 422, "not_mp4"),
        ("video/mp4", tiny_mp4()[:-7], 422, "truncated_mp4"),
        ("video/x-msvideo", tiny_mp4(), 422, "not_avi"),
        ("video/mp4", b"", 422, "empty"),
    ],
)
def test_wrong_files_are_rejected(client, media_type, data, status, code):
    _requested(client)
    r = upload(client, "inc-1", data, media_type=media_type)
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert list(client.store.clips.root.iterdir()) == []
    if status == 422:  # the teacher sees why and may request again
        st = client.store.get_incident("stu-a", "inc-1")["clip"]
        assert st["state"] == "unavailable" and st["can_request"]


def test_avi_is_accepted_but_marked_not_playable(client):
    _requested(client)
    r = upload(client, "inc-1", tiny_avi(), media_type="video/x-msvideo")
    assert r.status_code == 201
    st = client.store.get_incident("stu-a", "inc-1")["clip"]
    assert st["state"] == "available" and st["browser_playable"] is False


def test_too_large(store):
    from classreview import ReviewConfig, ReviewStore

    small = ReviewStore(ReviewConfig(data_dir=store.data_dir.parent / "small", max_clip_bytes=1000))
    small.open()
    try:
        small.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
        small.note_clip_requested("stu-a", "inc-1", "cmd-1")
        c = TestClient(_app(create_review_router(small, teacher_guard=lambda r: "t", student_resolver={"tok-a": "stu-a"}.get)))
        big = tiny_mp4(mdat=b"\x00" * 2000)
        r = upload(c, "inc-1", big)  # declared Content-Length over the limit
        assert r.status_code == 413
        r = c.post("/api/student/clips/inc-1", content=iter([big[:600], big[600:]]),
                   headers={"Authorization": "Bearer tok-a", "Content-Type": "video/mp4"})  # chunked, no length
        assert r.status_code == 413
        assert list(small.clips.root.iterdir()) == []
    finally:
        small.close()


def test_missing_and_broken_clips(client, clip):
    client.store.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
    r = client.get("/api/teacher/clips/inc-1", headers=TEACHER_HEADER)
    assert r.status_code == 404 and r.json()["error"]["code"] == "clip_not_found"
    assert client.get("/api/teacher/clips/no-such", headers=TEACHER_HEADER).status_code == 404
    for bad in ("..", "a" * 129, "x y"):
        assert client.get(f"/api/teacher/clips/{bad}", headers=TEACHER_HEADER).status_code in (404, 422)
    client.store.note_clip_requested("stu-a", "inc-1", "cmd-1")
    upload(client, "inc-1", clip)
    [f] = list(client.store.clips.root.iterdir())
    f.write_bytes(b"X" + clip[1:])  # same size, changed content
    r = client.get("/api/teacher/clips/inc-1", headers=TEACHER_HEADER)
    assert r.status_code == 500 and r.json()["error"]["code"] == "clip_integrity"
    os.remove(f)
    r = client.get("/api/teacher/clips/inc-1", headers=TEACHER_HEADER)
    assert r.status_code == 404 and r.json()["error"]["code"] == "clip_file_missing"


def test_playback_supports_seeking(client, clip):
    _requested(client)
    upload(client, "inc-1", clip)
    url = "/api/teacher/clips/inc-1"
    full = client.get(url, headers=TEACHER_HEADER)
    assert full.status_code == 200 and full.content == clip and full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-type"] == "video/mp4" and full.headers["cache-control"] == "no-store"
    size = len(clip)
    for rng, (a, b) in {"bytes=0-1023": (0, 1023), f"bytes={size // 2}-": (size // 2, size - 1), "bytes=-500": (size - 500, size - 1)}.items():
        r = client.get(url, headers={**TEACHER_HEADER, "Range": rng})
        assert r.status_code == 206 and r.content == clip[a : b + 1]
        assert r.headers["content-range"] == f"bytes {a}-{b}/{size}" and int(r.headers["content-length"]) == b - a + 1
    r = client.get(url, headers={**TEACHER_HEADER, "Range": f"bytes={size}-"})
    assert r.status_code == 416 and r.headers["content-range"] == f"bytes */{size}"
    etag = full.headers["etag"]
    assert client.get(url, headers={**TEACHER_HEADER, "Range": "bytes=0-9", "If-Range": etag}).status_code == 206
    assert client.get(url, headers={**TEACHER_HEADER, "Range": "bytes=0-9", "If-Range": '"other"'}).status_code == 200
    head = client.head(url, headers=TEACHER_HEADER)
    assert head.status_code == 200 and int(head.headers["content-length"]) == size and head.content == b""


def test_same_incident_id_for_two_students(client, clip, clip_b):
    _requested(client, "stu-a")
    _requested(client, "stu-b")
    upload(client, "inc-1", clip, token="tok-a")
    upload(client, "inc-1", clip_b, token="tok-b")
    r = client.get("/api/teacher/clips/inc-1", headers=TEACHER_HEADER)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ambiguous_incident"
    assert client.get("/api/teacher/clips/inc-1?student_id=stu-a", headers=TEACHER_HEADER).content == clip
    assert client.get("/api/teacher/clips/inc-1?student_id=stu-b", headers=TEACHER_HEADER).content == clip_b


def test_incident_list_shape_and_decisions(client):
    client.store.ingest_incident("stu-a", incident(1), class_session_id="cls-1")
    client.store.ingest_incident("stu-b", incident(1, incident_id="inc-b"), class_session_id="cls-1")
    body = client.get("/api/teacher/students/stu-a/incidents", headers=TEACHER_HEADER).json()
    [ep] = body["incidents"]
    for key in ("incident_id", "student_id", "rule_id", "category", "priority", "state", "t_start_wall",
                "duration_ms", "explanation_ru", "clip_available", "decision"):
        assert key in ep, key  # what T02's normalizeIncident reads
    assert ep["student_id"] == "stu-a" and body["review"]["zone_after_review"] == "yellow"
    url = "/api/teacher/students/stu-a/decision"
    for bad in ({"incident_id": "inc-1", "decision": "guilty"}, {"incident_id": "inc-1"},
                {"incident_id": "inc-1", "decision": "confirmed", "extra": 1},
                {"incident_id": "inc-1", "decision": "confirmed", "note_ru": "x" * 1001}):
        assert client.post(url, json=bad, headers=TEACHER_HEADER).status_code == 422
    assert client.post(url, json={"incident_id": "inc-b", "decision": "confirmed"}, headers=TEACHER_HEADER).status_code == 404
    r = client.post(url, json={"incident_id": "inc-1", "decision": "dismissed", "note_ru": "<b>лежал</b>"}, headers=TEACHER_HEADER)
    assert r.status_code == 200 and r.json()["decision"] == "dismissed" and r.json()["decision_history"][0]["operator"] == "teacher-test"
    body = client.get("/api/teacher/students/stu-a/incidents", headers=TEACHER_HEADER).json()
    assert body["review"]["zone_after_review"] == "green" and body["review"]["dismissed_excluded"] == 1
    assert client.get("/api/teacher/students/stu-b/incidents", headers=TEACHER_HEADER).json()["incidents"][0]["decision"] is None
