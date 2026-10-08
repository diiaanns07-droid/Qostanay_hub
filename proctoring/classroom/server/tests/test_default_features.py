"""Default class server = audio + T03 (history/clips/decisions) + T04 (exams/commands), INTERFACES.md §3.

Real server process (harness), default QORGAU_CLASS_FEATURES. The clip paths no longer answer 501; a clip goes
teacher request -> student upload (Bearer resume_token) -> teacher download. MP4 (browser codec) is served inline
for <video>; A02's MJPG .avi is served as an attachment ("open in a video player"), never as a broken <video>.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import httpx
import pytest

from classroom.server.tests.harness import ServerProcess


@pytest.fixture(scope="module")
def default_server(tmp_path_factory):
    from classroom.server.config import ServerConfig

    # the harness pins audio-only for core tests; here: the real config default (audio + T03 + T04)
    srv = ServerProcess(tmp_path_factory.mktemp("features-default"), {"QORGAU_CLASS_FEATURES": ServerConfig().features})
    yield srv
    assert srv.stop() == 0, "".join(srv.stderr[-20:])


def _wait(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met")


def _avi_mjpg() -> bytes:
    import cv2
    import numpy as np

    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "clip.avi")
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120))
        assert writer.isOpened()
        for i in range(10):
            img = np.full((120, 160, 3), 40 + i * 10, np.uint8)
            writer.write(img)
        writer.release()
        return Path(path).read_bytes()


def _mp4_vp9() -> bytes:
    from classreview.testclips import make_test_clip

    return make_test_clip(seconds=1.0, fps=10, size=(160, 120))


def _incidents(teacher, sid):
    body = teacher.get(f"/api/teacher/students/{sid}/incidents").json()
    return body["incidents"] if isinstance(body, dict) else body  # T03 shape {student_id, incidents, review}


def _student_with_clip_request(srv, teacher, students_made, incident_id):
    session = teacher.post("/api/teacher/session", json={"title": "Клипы", "mode": "url", "allowed_urls": []}).json()
    s = srv.student()
    students_made.append(s)
    welcome = s.hello(join_code=session["join_code"])
    sid, token = welcome["student_id"], welcome["resume_token"]
    s.status()
    s.incident(1, incident_id, state="closed", clip_available=True)
    _wait(lambda: any(i["incident_id"] == incident_id for i in _incidents(teacher, sid)))
    cmd = teacher.post(f"/api/teacher/students/{sid}/commands", json={"kind": "request_clip", "payload": {"incident_id": incident_id}})
    assert cmd.status_code == 202, cmd.text
    s.wait_for(lambda m: m["type"] == "command" and m.get("kind") == "request_clip")
    _wait(lambda: next(i for i in _incidents(teacher, sid) if i["incident_id"] == incident_id)["clip"]["state"] == "loading")
    return sid, token


def test_features_mounted_and_clip_paths_not_501(default_server):
    teacher = default_server.teacher()
    try:
        info = teacher.get("/api/teacher/info").json()
        status = {f["name"]: (f["owner"], f["status"]) for f in info["features"]}
        assert status["history"] == ("T03", "mounted"), info["features"]
        assert status["exams"] == ("T04", "mounted"), info["features"]
        r = teacher.get("/api/teacher/clips/no-such-incident")
        assert r.status_code == 404 and r.json()["error"]["code"] == "clip_not_found"
        assert teacher.get("/api/teacher/control/meta").status_code == 200  # T04 routes under its prefix
        anon = httpx.post(f"{default_server.base}/api/student/clips/x", content=b"x", headers={"Content-Type": "video/mp4"})
        assert anon.status_code == 401  # gate/T03 auth, not 501 feature_not_installed
    finally:
        teacher.close()


@pytest.mark.parametrize("kind", ["avi", "mp4"])
def test_clip_upload_and_download(default_server, kind):
    teacher = default_server.teacher()
    made = []
    try:
        incident_id = f"inc-clip-{kind}"
        sid, token = _student_with_clip_request(default_server, teacher, made, incident_id)
        data, media_type = (_avi_mjpg(), "video/x-msvideo") if kind == "avi" else (_mp4_vp9(), "video/mp4")
        up = httpx.post(f"{default_server.base}/api/student/clips/{incident_id}", content=data,
                        headers={"Authorization": f"Bearer {token}", "Content-Type": media_type, "X-Qorgau-Clip-Source": "test"})
        assert up.status_code == 201, up.text
        inc = next(i for i in _incidents(teacher, sid) if i["incident_id"] == incident_id)
        assert inc["clip"]["state"] == "available"
        got = teacher.get(f"/api/teacher/clips/{incident_id}?student_id={sid}")
        assert got.status_code == 200 and got.content == data
        assert got.headers["content-type"].startswith(media_type)
        disposition = got.headers["content-disposition"]
        if kind == "avi":  # MJPG AVI: the browser cannot play it -> download for a video player
            assert inc["clip"]["browser_playable"] is False and disposition.startswith("attachment;") and disposition.endswith('.avi"')
        else:  # MP4 VP9: plays in <video> with seeking
            assert inc["clip"]["browser_playable"] is True and disposition.startswith("inline;")
            part = teacher.get(f"/api/teacher/clips/{incident_id}?student_id={sid}", headers={"Range": "bytes=0-99"})
            assert part.status_code == 206 and len(part.content) == 100
    finally:
        for s in made:
            s.close()
        teacher.close()
