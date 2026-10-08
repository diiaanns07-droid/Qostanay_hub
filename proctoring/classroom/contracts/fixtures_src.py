"""Source of the contract fixtures (owner: T01). Written to fixtures/v1/<Model>.<case>.json by generate.py.

All fixture data is invented (labelled example/simulated); no real student data.
"""

from __future__ import annotations

from typing import Any

from .models import CONTRACT_VERSION, WIRE_EXTENSION

T0 = "2026-10-09T09:00:00Z"
T1 = "2026-10-09T09:00:04.200000Z"
TOKEN = "3f" * 32
JPEG_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxOQERXRTc4UG1RV19iZ2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2P/wAARCAAYACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwAooooAKKKKACiiigAooooA/9k="  # real 32x24 JPEG (641 B)
EXAM = {"exam_id": "exam-cs-0001", "title": "Математика, вариант 1 (пример)", "mode": "url", "allowed_urls": ["https://exam.example.kz/*"], "allowed_apps": [], "instructions_ru": "Откройте только сайт экзамена."}
CARD_STUDENT = {
    "student_id": "st-0001", "session_id": "cs-0001", "student_label": "SIM-01 (симуляция)", "computer_name": "SIM-PC-01",
    "app_version": "qorgau-class-simulator/0.1", "origin": "simulated", "connection": "online", "paired_at": T0, "last_seen_at": T1,
    "connected_since": T0, "reconnects": 1, "capabilities": ["audio_start", "audio_stop", "finish_exam", "lock", "request_clip", "start_exam", "unlock"],
}
STATUS = {
    "student_id": "st-0001", "received_at": T1, "sent_at": T1, "exam_state": "running", "camera": "ok", "monitoring": "ok",
    "zone_reported": "yellow", "zone": "yellow", "zone_source": "student", "zone_reasons_ru": ["СИМУЛЯЦИЯ: телефон виден"],
    "incidents_total": 1, "incidents_by_priority": {"low": 0, "medium": 1, "high": 0}, "locked": False, "mic_active": False, "stale": False,
}
INCIDENT = {
    "incident_id": "inc-0001", "student_id": "st-0001", "session_id": "cs-0001", "rule_id": "phone_visible", "category": "phone",
    "priority": "medium", "state": "closed", "t_start_wall": T0, "duration_ms": 4200.0, "explanation_ru": "СИМУЛЯЦИЯ: телефон виден 4,2 с",
    "clip_available": True, "has_snapshot": False, "origin": "simulated", "first_received_at": T0, "last_received_at": T1, "events": 2,
}
COMMAND = {
    "command_id": "cmd-0001", "student_id": "st-0001", "kind": "lock", "payload": {"reason_ru": "Уберите телефон со стола"},
    "issued_by": "teacher", "issued_at": T0, "expires_at": "2026-10-09T09:02:00Z", "status": "succeeded", "status_at": T1,
    "attempts": 1, "sent_at": "2026-10-09T09:00:00.050000Z", "ack_deadline_at": "2026-10-09T09:00:10.050000Z", "unconfirmed": False,
    "ack": {"command_id": "cmd-0001", "ok": True, "code": None, "error_ru": None, "executed_at": "2026-10-09T09:00:00.400000Z", "received_at": "2026-10-09T09:00:00.420000Z", "late": False, "result": {"locked": True}},
    "history": [{"status": "queued", "at": T0, "note": None}, {"status": "sent", "at": "2026-10-09T09:00:00.050000Z", "note": "attempt 1"}, {"status": "succeeded", "at": "2026-10-09T09:00:00.420000Z", "note": None}],
}


def build() -> dict[str, Any]:
    env = {"v": 1, "msg_id": "m-0001", "sent_at": T0}
    fixtures = {
        # ------------------------------------------------------------------ student -> server (v1 + v1.1)
        "Hello.join_code_v1": {**env, "type": "hello", "protocol": "qorgau.class.v1", "join_code": "482915", "computer_name": "PC-07", "student_label": "Студент 7", "app_version": "qorgau-exam/0.1.0"},
        "Hello.resume_v11": {**env, "type": "hello", "protocol": "qorgau.class.v1", "resume_token": TOKEN, "computer_name": "SIM-PC-01", "student_label": "SIM-01 (симуляция)", "app_version": "qorgau-class-simulator/0.1", "client_run_id": "run-a1b2c3", "simulated": True, "capabilities": {"commands": ["apply_policy"], "modes": ["url", "app"], "command_progress": True, "command_expiry": True, "site_timer_pause": False}},
        "Status.running_yellow": {**env, "type": "status", "exam_state": "running", "camera": "ok", "monitoring": "ok", "zone": "yellow", "zone_reasons_ru": ["Телефон виден 4 с"], "incidents_total": 1, "incidents_by_priority": {"low": 0, "medium": 1, "high": 0}, "locked": False, "mic_active": False},
        "IncidentMsg.phone_open_v1": {**env, "type": "incident", "seq": 1, "incident_id": "inc-0001", "rule_id": "phone_visible", "category": "phone", "priority": "medium", "state": "open", "t_start_wall": T0, "duration_ms": 1000.0, "explanation_ru": "Телефон виден", "clip_available": False},
        "IncidentMsg.phone_closed_v11": {**env, "type": "incident", "seq": 2, "incident_id": "inc-0001", "rule_id": "phone_visible", "category": "phone", "priority": "medium", "state": "closed", "t_start_wall": T0, "duration_ms": 4200.0, "explanation_ru": "Телефон виден 4,2 с", "clip_available": True, "event_id": "inc-0001:closed"},
        "Preview.small": {**env, "type": "preview", "jpeg_b64": JPEG_B64, "frame_wall": T0},
        "Ack.ok_v1": {**env, "type": "ack", "command_id": "cmd-0001", "ok": True},
        "Ack.failed_v11": {**env, "type": "ack", "command_id": "cmd-0002", "ok": False, "error_ru": "Срок команды истёк", "code": "expired", "executed_at": None},
        "CommandProgress.received": {**env, "type": "command_progress", "command_id": "cmd-0001", "state": "received"},
        "AudioSignalIn.answer": {**env, "type": "audio_signal", "command_id": "cmd-0003", "sdp": "v=0\r\no=- 1 1 IN IP4 192.168.1.20\r\n"},
        "Pong.v1": {**env, "type": "pong"},
        # ------------------------------------------------------------------ server -> student
        "Welcome.url_exam": {**env, "type": "welcome", "student_id": "st-0001", "resume_token": TOKEN, "server_time": T0, "exam": EXAM, "session_id": "cs-0001", "resumed": False},
        "CommandMsg.lock_v11": {**env, "type": "command", "command_id": "cmd-0001", "kind": "lock", "payload": {"reason_ru": "Уберите телефон со стола"}, "issued_at": T0, "expires_at": "2026-10-09T09:02:00Z", "ttl_ms": 120000, "attempt": 1},
        "Ping.v1": {**env, "type": "ping"},
        "ErrorMsg.join_rejected": {**env, "type": "error", "code": "join_rejected", "message_ru": "Неверный код подключения"},
        # ------------------------------------------------------------------ teacher API records
        "Student.simulated_online": CARD_STUDENT,
        "DeviceStatus.yellow": STATUS,
        "StudentCard.simulated": {**{k: v for k, v in CARD_STUDENT.items() if k not in ("paired_at",)}, **{k: v for k, v in STATUS.items() if k not in ("student_id", "received_at", "sent_at")}, "connected": True, "last_status_at": T1, "last_event_at": T1, "incidents_open": 0, "incidents_unreviewed": None, "preview_url": "/api/teacher/students/st-0001/preview.jpg?seq=3", "preview_at": T1},
        "Session.open": {"session_id": "cs-0001", "title": "Математика, вариант 1 (пример)", "state": "open", "created_at": T0, "closed_at": None, "exam": EXAM, "join_code": "482915", "students_total": 2},
        "ObservationEvent.incident_closed": {"event_id": "inc-0001:closed", "student_id": "st-0001", "session_id": "cs-0001", "kind": "incident", "seq": 2, "client_run_id": "run-a1b2c3", "event_time": T1, "sent_at": T1, "received_at": "2026-10-09T09:00:04.250000Z", "origin": "simulated", "seq_conflict": False, "payload": {"incident_id": "inc-0001", "state": "closed"}},
        "Incident.closed": INCIDENT,
        "ClipMetadata.mp4": {"clip_id": "clip-0001", "incident_id": "inc-0001", "student_id": "st-0001", "media_type": "video/mp4", "size_bytes": 2_400_000, "sha256": "ab" * 32, "uploaded_at": T1, "url": "/api/teacher/clips/inc-0001", "origin": "simulated"},
        "Command.succeeded": COMMAND,
        "CommandAck.late": {"command_id": "cmd-0004", "ok": True, "code": None, "error_ru": None, "executed_at": None, "received_at": "2026-10-09T09:03:00Z", "late": True, "result": None},
        "AudioSession.active": {"audio_session_id": "au-0001", "student_id": "st-0001", "direction": "listen", "state": "active", "started_by": "teacher", "start_command_id": "cmd-0003", "stop_command_id": None, "requested_at": T0, "active_at": T1, "ended_at": None, "end_reason": None},
        "ExamPolicy.app_v11": {"exam_id": "exam-cs-0002", "title": "Программирование (пример)", "mode": "app", "allowed_urls": [], "allowed_apps": ["code.exe"], "instructions_ru": "", "policy_id": "pol-1", "version": 2, "start_url": None, "auth_domains": []},
        "ServerInfo.example": {"contract": "qorgau.classroom", "contract_version": CONTRACT_VERSION, "wire_protocol": "qorgau.class.v1", "wire_extension": WIRE_EXTENSION, "server_version": "0.1.0", "server_time": T0, "session": None, "students_online": 0, "students_total": 0, "simulated_students": 0, "features": [{"name": "history", "owner": "T03", "status": "not_installed", "detail": "classreview is not installed in this build"}]},
        # ------------------------------------------------------------------ teacher request bodies
        "LoginRequest.pin": {"pin": "123456"},
        "SessionCreate.url": {"title": "Математика, вариант 1 (пример)", "mode": "url", "allowed_urls": ["https://exam.example.kz/*"], "allowed_apps": [], "instructions_ru": ""},
        "CommandCreate.lock": {"kind": "lock", "payload": {"reason_ru": "Уберите телефон со стола"}, "ttl_ms": 60000},
        # ------------------------------------------------------------------ teacher stream
        "TStudentUpdate.simulated": {"type": "student_update", "seq": 3, "sent_at": T1, "student": {**{k: v for k, v in CARD_STUDENT.items() if k not in ("paired_at",)}, **{k: v for k, v in STATUS.items() if k not in ("student_id", "received_at", "sent_at")}, "connected": True, "last_status_at": T1, "last_event_at": None, "incidents_open": 0, "incidents_unreviewed": None, "preview_url": None, "preview_at": None}},
        "TIncident.closed": {"type": "incident", "seq": 4, "sent_at": T1, "student_id": "st-0001", "incident": INCIDENT, "duplicate": False},
        "TPreview.metadata_only": {"type": "preview", "seq": 5, "sent_at": T1, "student_id": "st-0001", "preview_seq": 3, "frame_wall": T1, "received_at": T1, "byte_length": 9300, "url": "/api/teacher/students/st-0001/preview.jpg?seq=3", "origin": "simulated", "jpeg_b64": None},
        "TCommandUpdate.succeeded": {"type": "command_update", "seq": 6, "sent_at": T1, "command": COMMAND},
        "TResync.overflow": {"type": "resync_required", "seq": 7, "sent_at": T1, "reason": "queue_overflow"},
        "TeacherAudioSignal.offer": {"type": "audio_signal", "student_id": "st-0001", "command_id": "cmd-0003", "sdp": "v=0\r\n"},
    }
    fixtures.update({
        "Hello.source_unknown_v12": {**fixtures["Hello.join_code_v1"], "source_mode": "unknown", "source_session_id": None},
        "Status.synthetic_v12": {**fixtures["Status.running_yellow"], "source_mode": "synthetic", "source_session_id": "local-synthetic-1"},
        "IncidentMsg.replay_v12": {**fixtures["IncidentMsg.phone_closed_v11"], "source_mode": "replay", "source_session_id": "local-replay-1"},
        "Preview.synthetic_v12": {**fixtures["Preview.small"], "source_mode": "synthetic", "source_session_id": "local-synthetic-1"},
        "StudentCard.source_unknown": {**fixtures["StudentCard.simulated"], "origin": "unknown", "app_version": "qorgau-exam-uplink-0.1.0", "student_label": "EXAMPLE: source not selected"},
        "StudentCard.replay": {**fixtures["StudentCard.simulated"], "origin": "replay", "app_version": "qorgau-exam-uplink-0.1.0", "student_label": "EXAMPLE: recorded source"},
    })
    return fixtures
