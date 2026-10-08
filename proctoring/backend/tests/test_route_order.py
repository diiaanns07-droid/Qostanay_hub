"""Store routes registered at startup must not be shadowed by /v1/sessions/{session_id}."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from proctor.app import create_app

from .test_qa_regressions import AUTH, TOKEN, _settings


def test_sessions_overview_is_not_captured_by_session_id_route(tmp_path):
    router_mod = pytest.importorskip("proctor.evidence.router")
    if "/sessions/overview" not in open(router_mod.__file__, encoding="utf-8").read():
        pytest.skip("evidence store without an overview route")
    app = create_app(_settings(tmp_path), TOKEN)  # real A08 evidence store
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as c:
        r = c.get("/v1/sessions/overview")
        assert r.status_code == 200, r.text  # was 404 SESSION_NOT_FOUND "session overview"
        assert isinstance(r.json(), list)
