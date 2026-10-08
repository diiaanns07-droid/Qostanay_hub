"""A15 desk scan tests. The analyzer is a STUB (no model, no camera): frames come from the synthetic
bootstrap capture (A02 stand-in) through add_consumer, exactly as in a real session.
These tests check the logic, not detection accuracy."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from proctor.app import MODULES, create_app
from proctor.deskscan import FIXED_CAMERA_REASON, DeskScanAggregator, preflight_check
from proctor.evidence import create_evidence_store
from proctor.settings import Settings
from proctor_contracts.v1 import CheckStatus, Component, DeskScanResult, DeskScanState, Health, HealthStatus

TOKEN = "t" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}
HIDDEN = {key: None for key in MODULES}
SCAN_S = 5.0  # contract minimum (DeskScanRequest.duration_s >= 5)


class StubAnalyzer:
    """STUB of the A03 phone analyzer (public factory shape): scenario-driven detections."""

    name = "phone"
    scenario = "empty"  # "phone_1s" | "empty" | "error" | "unavailable"

    def __init__(self, settings):
        self.t0: float | None = None

    def load(self) -> Health:
        if StubAnalyzer.scenario == "unavailable":
            return Health(component=Component.PHONE, status=HealthStatus.UNAVAILABLE, code="model_missing")
        return Health(component=Component.PHONE, status=HealthStatus.OK, code="model_loaded", message="STUB")

    def start_session(self, session_id, mode) -> None:
        self.t0 = None

    def end_session(self) -> None:
        pass

    def close(self) -> None:
        pass

    def process(self, frame):
        if StubAnalyzer.scenario == "error":
            raise RuntimeError("stub inference failure")
        t = frame.t_session_ms
        self.t0 = t if self.t0 is None else self.t0
        dets = []
        if StubAnalyzer.scenario == "phone_1s" and 1000.0 <= t - self.t0 <= 2000.0:
            dets.append(SimpleNamespace(class_name="cell phone", confidence=0.81))
        if StubAnalyzer.scenario == "phone_1s" and t - self.t0 <= 300.0:  # a single weak flash: ignored
            dets.append(SimpleNamespace(class_name="book", confidence=0.9))
        return [SimpleNamespace(status="ok", detections=dets)]


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        models_dir=tmp_path / "models",
        replay_dir=tmp_path / "replay",
        exam_path=tmp_path / "missing_exam.json",
        synthetic_fps=30.0,
        fusion_tick_ms=50.0,
    )


@pytest.fixture()
def client(tmp_path):
    StubAnalyzer.scenario = "empty"
    overrides = {**HIDDEN, "phone": StubAnalyzer, "evidence": create_evidence_store}
    app = create_app(_settings(tmp_path), TOKEN, module_overrides=overrides)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        yield c


def _create(client, retain_media=False) -> str:
    body = {"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": CONSENT, "retain_media": retain_media}
    r = client.post("/v1/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def _preflight(client, sid):
    r = client.post(f"/v1/sessions/{sid}/preflight")
    assert r.status_code == 200, r.text
    return r.json()


def _scan(client, sid, mode="laptop") -> dict:
    r = client.post(f"/v1/sessions/{sid}/desk-scan?mode={mode}", json={"duration_s": SCAN_S})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "recording"
    deadline = time.monotonic() + SCAN_S + 15
    while time.monotonic() < deadline:
        res = client.get(f"/v1/sessions/{sid}/desk-scan").json()
        if res["state"] != "recording":
            return res
        time.sleep(0.2)
    raise AssertionError("desk scan did not finish")


# ------------------------------------------------------------------------------------ aggregation
def test_aggregator_needs_half_second_of_stable_confident_detection():
    agg = DeskScanAggregator()
    for i in range(20):  # 4 fps, 5 s
        t = i * 250.0
        dets = []
        if 1000 <= t <= 2000:
            dets.append(("cell phone", 0.7))
        if t == 0:
            dets.append(("book", 0.95))  # one frame only: not stable
        if 3000 <= t <= 4000:
            dets.append(("tv", 0.4))  # below the confidence threshold
        agg.add_frame(t, dets)
    objs = agg.objects()
    assert [o.class_name for o in objs] == ["cell phone"]
    assert objs[0].label_ru == "телефон" and objs[0].max_confidence == pytest.approx(0.7) and objs[0].seen_ms == 1000.0


def test_preflight_check_states():
    assert preflight_check(None).status == CheckStatus.NOT_RUN and preflight_check(None).required is False
    clear = DeskScanResult(state=DeskScanState.CLEAR)
    assert preflight_check(clear).status == CheckStatus.PASS
    found = DeskScanResult(state=DeskScanState.OBJECTS_FOUND, objects=[{"class_name": "cell phone", "label_ru": "телефон", "max_confidence": 0.8, "seen_ms": 900}])
    check = preflight_check(found)
    assert check.status == CheckStatus.WARN and check.message_ru == "На столе замечено: телефон — уберите его"


# --------------------------------------------------------------------------------------- the API
def test_phone_for_one_second_gives_objects_found_and_summary(client):
    StubAnalyzer.scenario = "phone_1s"
    sid = _create(client)
    pre = _preflight(client, sid)
    desk = [c for c in pre["checks"] if c["check_id"] == "desk_scan"][0]
    assert desk["status"] == "not_run" and desk["required"] is False and pre["ready"] is True
    assert client.get(f"/v1/sessions/{sid}/desk-scan").json()["state"] == "not_started"
    res = _scan(client, sid, "laptop")
    assert res["state"] == "objects_found", res
    assert [o["class_name"] for o in res["objects"]] == ["cell phone"]
    assert res["objects"][0]["label_ru"] == "телефон" and res["objects"][0]["seen_ms"] >= 500
    assert res["message_ru"].startswith("Вариант: ноутбук.") and "телефон" in res["message_ru"]
    assert res["evidence_id"] is None  # media retention is off: no clip, only the list
    summary = client.get(f"/v1/sessions/{sid}/summary").json()
    assert summary["desk_scan"]["state"] == "objects_found"
    pre = _preflight(client, sid)  # preflight may be re-run before calibration
    desk = [c for c in pre["checks"] if c["check_id"] == "desk_scan"][0]
    assert desk["status"] == "warn" and "телефон" in desk["message_ru"]
    # report: section present (HTML + JSON)
    assert client.post(f"/v1/sessions/{sid}/finish").status_code == 200
    html = client.get(f"/v1/sessions/{sid}/report.html").text
    assert "Осмотр рабочего места" in html and "телефон" in html and "Клип не сохранялся" in html
    assert "не доказательство нарушения" in html
    report = client.get(f"/v1/sessions/{sid}/report.json").json()
    assert report["desk_scan"]["state"] == "objects_found"


def test_usb_variant_empty_desk_is_clear_and_rescan_overwrites(client):
    sid = _create(client)
    _preflight(client, sid)
    StubAnalyzer.scenario = "phone_1s"
    first = _scan(client, sid, "laptop")
    assert first["state"] == "objects_found"
    StubAnalyzer.scenario = "empty"
    second = _scan(client, sid, "usb")
    assert second["state"] == "clear" and second["objects"] == [] and second["scan_id"] != first["scan_id"]
    assert second["message_ru"].startswith("Вариант: USB-камера.") and "не замечено" in second["message_ru"]
    assert client.get(f"/v1/sessions/{sid}/summary").json()["desk_scan"]["scan_id"] == second["scan_id"]
    desk = [c for c in _preflight(client, sid)["checks"] if c["check_id"] == "desk_scan"][0]
    assert desk["status"] == "pass"


def test_camera_failure_is_failed_not_clear(client):
    sid = _create(client)
    _preflight(client, sid)
    app_state = client.app.state.proctor
    capture = app_state["manager"].runtime(sid).pipeline.capture.impl
    capture.add_consumer = lambda *a, **k: None  # camera delivers no frames to the scan
    res = _scan(client, sid)
    assert res["state"] == "failed" and "кадр" in res["message_ru"] and res["objects"] == []


def test_analyzer_error_and_missing_model_are_failed(client):
    sid = _create(client)
    _preflight(client, sid)
    StubAnalyzer.scenario = "error"
    res = _scan(client, sid)
    assert res["state"] == "failed" and "анализ" in res["message_ru"]
    client.app.state.desk_scan._analyzer = None
    StubAnalyzer.scenario = "unavailable"
    r = client.post(f"/v1/sessions/{sid}/desk-scan", json={"duration_s": SCAN_S})
    assert r.status_code == 200
    time.sleep(0.5)
    res = client.get(f"/v1/sessions/{sid}/desk-scan").json()
    assert res["state"] == "failed" and "модель" in res["message_ru"]


def test_scan_not_allowed_while_running_or_before_preflight(client):
    sid = _create(client)
    r = client.post(f"/v1/sessions/{sid}/desk-scan", json={"duration_s": SCAN_S})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"  # created: no camera yet
    _preflight(client, sid)
    assert client.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "test"}).status_code == 200
    assert client.post(f"/v1/sessions/{sid}/start").status_code == 200
    r = client.post(f"/v1/sessions/{sid}/desk-scan", json={"duration_s": SCAN_S})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    r = client.post(f"/v1/sessions/{sid}/desk-scan/skip", json={"reason": FIXED_CAMERA_REASON})
    assert r.status_code == 409
    assert client.post(f"/v1/sessions/{sid}/desk-scan", json={"duration_s": 3}).status_code == 422  # contract: 5..30
    assert client.post(f"/v1/sessions/{sid}/desk-scan?mode=phone", json={"duration_s": 12}).status_code == 422


def test_second_scan_while_recording_is_409(client):
    sid = _create(client)
    _preflight(client, sid)
    assert client.post(f"/v1/sessions/{sid}/desk-scan", json={"duration_s": SCAN_S}).status_code == 200
    r = client.post(f"/v1/sessions/{sid}/desk-scan", json={"duration_s": SCAN_S})
    assert r.status_code == 409
    deadline = time.monotonic() + SCAN_S + 15
    while client.get(f"/v1/sessions/{sid}/desk-scan").json()["state"] == "recording" and time.monotonic() < deadline:
        time.sleep(0.2)


def test_fixed_camera_variant_is_skipped_with_teacher_confirmation(client):
    sid = _create(client)
    _preflight(client, sid)
    r = client.post(f"/v1/sessions/{sid}/desk-scan/skip", json={"reason": FIXED_CAMERA_REASON})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["state"] == "skipped" and res["skip_reason"] == "fixed_camera_teacher_check"
    assert res["message_ru"].startswith("Вариант: камера не двигается.")
    assert client.post(f"/v1/sessions/{sid}/desk-scan/skip", json={}).status_code == 422  # reason required
    assert client.post(f"/v1/sessions/{sid}/finish").status_code == 200
    html = client.get(f"/v1/sessions/{sid}/report.html").text
    assert "Осмотр камерой невозможен (стационарная камера) — подтверждён преподавателем" in html


def test_operator_skip_with_reason(client):
    sid = _create(client)
    _preflight(client, sid)
    res = client.post(f"/v1/sessions/{sid}/desk-scan/skip", json={"reason": "нет второй камеры"}).json()
    assert res["state"] == "skipped" and res["skip_reason"] == "нет второй камеры" and "оператор" in res["message_ru"]


def test_unknown_session_is_404(client):
    assert client.get("/v1/sessions/s-missing/desk-scan").status_code == 404
    assert client.post("/v1/sessions/s-missing/desk-scan", json={"duration_s": 12}).status_code == 404
