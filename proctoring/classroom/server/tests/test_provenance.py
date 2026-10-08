"""Source provenance is per data item, not inferred from a running client process."""

import base64

import pytest

from classroom.contracts import models as m
from classroom.server.config import ServerConfig
from classroom.server.core import ClassroomCore, envelope
from classroom.server.db import CORE_MIGRATIONS, Database
from classroom.server.hub import TeacherHub
from classroom.server.tests.harness import TINY_JPEG, wait_until


@pytest.fixture
def core(tmp_path):
    db = Database(tmp_path / "class.sqlite")
    db.migrate(CORE_MIGRATIONS)
    value = ClassroomCore(ServerConfig(data_dir=tmp_path, preview_min_interval_s=0), db, TeacherHub())
    value.create_session(m.SessionCreate(title="Provenance test", mode="url"))
    yield value
    db.close()


def join(core, **extra):
    return core.pair(m.Hello(**envelope(type="hello"), protocol="qorgau.class.v1",
                             join_code=core.current_session().join_code, **extra), "127.0.0.1")


def status(core, student, mode=None):
    fields = {} if mode is None else {"source_mode": mode, "source_session_id": "local-session"}
    core.on_status(student, m.Status(**envelope(type="status"), exam_state="running", camera="ok", monitoring="ok", **fields))


def incident(core, student, seq, iid, mode=None):
    fields = {} if mode is None else {"source_mode": mode, "source_session_id": "old-local-session"}
    return core.on_incident(student, m.IncidentMsg(**envelope(type="incident"), seq=seq, incident_id=iid,
        rule_id="phone_visible", category="phone", priority="medium", state="closed", t_start_wall=m.utc_now(), **fields))[0]


def test_unknown_until_explicit_source_and_source_changes_are_persisted(core):
    student, _, _ = join(core)
    assert core.card(student).origin.value == "unknown"
    status(core, student)  # legacy camera=ok is not source evidence
    assert core.card(student).origin.value == "unknown"
    for mode, origin in [("synthetic", "simulated"), ("live", "real"), ("replay", "replay"), ("unknown", "unknown")]:
        status(core, student, mode)
        assert core.card(student).origin.value == origin
        persisted = core.db.query("SELECT origin FROM students WHERE student_id=?", (student.student_id,))[0]
        assert persisted["origin"] == origin


def test_backlog_origin_is_its_own_source_not_the_latest_card(core):
    student, _, _ = join(core)
    status(core, student, "live")
    queued = incident(core, student, 1, "queued-synthetic", "synthetic")
    assert queued.origin.value == "simulated"
    status(core, student, "synthetic")
    old_live = incident(core, student, 2, "queued-live", "live")
    assert old_live.origin.value == "real"
    replay = incident(core, student, 3, "queued-replay", "replay")
    assert replay.origin.value == "replay"
    legacy = incident(core, student, 4, "unmarked")
    assert legacy.origin.value == "unknown"
    events = {e.payload["incident_id"]: e for e in core.events_for(student.student_id)}
    assert events["queued-synthetic"].origin.value == "simulated"
    assert events["queued-synthetic"].payload["source_session_id"] == "old-local-session"
    assert events["queued-live"].origin.value == "real"
    # Changing current source cannot rewrite already persisted observations.
    loaded = ClassroomCore(core.config, core.db, TeacherHub())
    assert loaded.incidents_for(student.student_id)[0].origin == queued.origin
    assert loaded.students[student.student_id].origin.value == "simulated"


@pytest.mark.parametrize("legacy", [{"simulated": True}, {"app_version": "qorgau-class-simulator/0.1"}])
def test_legacy_simulator_keeps_explicit_test_origin(core, legacy):
    student, token, _ = join(core, **legacy)
    status(core, student)
    assert core.card(student).origin.value == "simulated"
    assert incident(core, student, 1, "legacy-sim").origin.value == "simulated"
    # A simulator cannot accidentally promote itself to real via a status.
    status(core, student, "live")
    assert core.card(student).origin.value == "simulated"
    resumed, _, _ = core.pair(m.Hello(**envelope(type="hello"), protocol="qorgau.class.v1", resume_token=token, **legacy), "127.0.0.1")
    assert core.card(resumed).origin.value == "simulated"


def test_resume_without_source_does_not_reuse_previous_live_claim(core):
    student, token, _ = join(core)
    status(core, student, "live")
    resumed, _, _ = core.pair(m.Hello(**envelope(type="hello"), protocol="qorgau.class.v1", resume_token=token), "127.0.0.1")
    assert resumed.student_id == student.student_id
    assert core.card(resumed).origin.value == "unknown"


def test_preview_origin_belongs_to_frame_even_after_status_changes(server, teacher, session, students):
    student = students()
    sid = student.hello(join_code=session["join_code"])["student_id"]
    student.status(source_mode="live", source_session_id="new-live-session")
    student.send("preview", jpeg_b64=base64.b64encode(TINY_JPEG).decode(), frame_wall=m.utc_now().isoformat(),
                 source_mode="synthetic", source_session_id="old-synthetic-session")
    url = f"/api/teacher/students/{sid}/preview.jpg"
    assert wait_until(lambda: teacher.get(url).status_code == 200)
    assert teacher.get(url).headers["x-qorgau-origin"] == "simulated"
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{sid}").json()["origin"] == "real")
    student.status(source_mode="replay", source_session_id="next-replay-session")
    assert wait_until(lambda: teacher.get(f"/api/teacher/students/{sid}").json()["origin"] == "replay")
    assert teacher.get(url).headers["x-qorgau-origin"] == "simulated"
