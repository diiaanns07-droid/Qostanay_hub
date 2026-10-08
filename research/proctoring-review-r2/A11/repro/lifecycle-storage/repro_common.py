"""Shared helpers for A11 isolated repros (combo tree = A01r2 29cadde backend + A08 5509950 evidence package).

ISOLATED REPRO, not an integration run: capture/phone/attention/fusion are hidden so that A01's labelled
bootstrap (synthetic) parts are used; the evidence store is the REAL A08 package discovered by A01's registry.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from proctor.app import create_app
from proctor.settings import Settings

TOKEN = "q" * 48
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONSENT = {"accepted": True, "text_version": "consent-ru-1", "accepted_at": "2026-10-08T09:00:00Z"}
HIDE_ALL_BUT_EVIDENCE = {"capture": None, "phone": None, "attention": None, "fusion": None}


def settings(data_dir: Path) -> Settings:
    return Settings(
        data_dir=data_dir,
        models_dir=data_dir / "models",
        replay_dir=data_dir / "replay",
        exam_path=data_dir / "missing_exam.json",
        synthetic_fps=30.0,
        fusion_tick_ms=50.0,
    )


def make_app(data_dir: Path):
    app = create_app(settings(data_dir), TOKEN, module_overrides=dict(HIDE_ALL_BUT_EVIDENCE))
    return app


def client(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1", headers=AUTH)


def record_stream(app) -> list:
    """Wrap the StreamHub.publish of this app instance and record every published model."""
    hub = app.state.hub
    seen: list = []
    orig = hub.publish

    def publish(message, session_id):
        seen.append(message)
        return orig(message, session_id)

    hub.publish = publish  # instance attribute: SessionRuntime._bus is this hub object
    return seen


def running_synthetic(c, retain_media: bool = False) -> str:
    r = c.post(
        "/v1/sessions",
        json={"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "consent": CONSENT, "retain_media": retain_media},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    pf = c.post(f"/v1/sessions/{sid}/preflight").json()
    assert pf["ready"] is True, pf
    c.post(f"/v1/sessions/{sid}/calibration/skip", json={"reason": "repro"})
    info = c.post(f"/v1/sessions/{sid}/start").json()
    assert info["state"] == "running", info
    return sid


def wait(pred, timeout=15.0, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


def evidence_store(app):
    return app.state.registry.loaded["evidence"].impl
