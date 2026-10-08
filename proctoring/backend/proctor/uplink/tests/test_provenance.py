"""C2 provenance uses session/incident/frame metadata; no devices are opened."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from proctor.uplink.backend_view import BackendView, Snapshot, _incident
from proctor.uplink.client import Uplink
from proctor.uplink.tests.test_recovery import HandshakeSocket
from proctor.uplink.tests.test_uplink import FakeView, make_cfg


def test_hello_and_no_session_status_are_unknown(tmp_path):
    up = Uplink(make_cfg(tmp_path, "127.0.0.1:8765"), FakeView(tmp_path))
    try:
        socket = HandshakeSocket({"type": "welcome", "student_id": "st-1", "resume_token": "a" * 64})
        assert asyncio.run(up._handshake(socket)) == "ok"
        assert socket.sent[0]["source_mode"] == "unknown"
        assert socket.sent[0]["source_session_id"] is None
        status = BackendView(lambda: None).snapshot().status_fields()
        assert status["source_mode"] == "unknown" and status["source_session_id"] is None
    finally:
        up.stop()


def test_snapshot_uses_session_source_and_incident_queue_keeps_its_own(tmp_path):
    runtime = NS(info=NS(session_id="synthetic-session", source=NS(mode="synthetic"), state="created"),
                 pipeline=NS(capture=NS(impl=None), store=NS(impl=None)))
    manager = NS(active_session_id=lambda: "synthetic-session", runtime=lambda _: runtime)
    snapshot = BackendView(lambda: manager).snapshot()
    assert snapshot.status_fields()["source_mode"] == "synthetic"
    assert snapshot.status_fields()["source_session_id"] == "synthetic-session"

    raw = NS(incident_id="queued", session_id="previous-session", source_mode="replay", rule_id="phone_visible",
             category="phone", priority="medium", state="closed", t_start_ms=100, t_end_ms=200,
             wall_start=datetime.now(timezone.utc), duration_ms=100, explanation=NS(summary_ru="Recorded test"))
    normalized = _incident(raw)
    up = Uplink(make_cfg(tmp_path, "127.0.0.1:8765"), FakeView(tmp_path))
    try:
        snapshot.incidents = [normalized]
        assert up._diff_incidents(snapshot)
        queued = up.outbox.pending()[0]
        assert queued["source_mode"] == "replay"
        assert queued["source_session_id"] == "previous-session"
    finally:
        up.stop()


def test_preview_uses_frame_metadata_instead_of_current_runtime_source(monkeypatch):
    monkeypatch.setattr("proctor.uplink.backend_view.shrink_jpeg", lambda data: data)
    frame = NS(session_id="previous-synthetic", source_mode="synthetic", wall_time=datetime.now(timezone.utc))
    runtime = NS(info=NS(session_id="current-live", source=NS(mode="live")), preview=lambda: (frame, b"jpeg-test-data"))
    manager = NS(active_session_id=lambda: "current-live", runtime=lambda _: runtime)
    data, provenance = BackendView(lambda: manager).preview_packet()
    assert data == b"jpeg-test-data"
    assert provenance == {"source_mode": "synthetic", "source_session_id": "previous-synthetic", "frame_wall": frame.wall_time.isoformat()}
