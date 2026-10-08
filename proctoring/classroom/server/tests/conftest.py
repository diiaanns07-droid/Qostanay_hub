"""Fixtures for class server tests (owner: T01). Run from proctoring/: python -m pytest -q classroom"""

from __future__ import annotations

import pytest

from classroom.server.tests.harness import ServerProcess


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    srv = ServerProcess(tmp_path_factory.mktemp("classdata"))
    yield srv
    rc = srv.stop()
    assert rc == 0, "".join(srv.stderr[-20:])


@pytest.fixture()
def teacher(server):
    client = server.teacher()
    yield client
    client.close()


@pytest.fixture()
def session(teacher):
    """A fresh open session (closes the previous one), so every test has its own students."""
    r = teacher.post("/api/teacher/session", json={"title": "Тест", "mode": "url", "allowed_urls": ["https://exam.example/*"]})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def students(server):
    made = []

    def make(**kw):
        c = server.student(**kw)
        made.append(c)
        return c

    yield make
    for c in made:
        c.close()
