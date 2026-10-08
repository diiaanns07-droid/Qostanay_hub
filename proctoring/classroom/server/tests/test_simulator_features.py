"""Simulator labelling, feature plug-in boundaries, migration mechanism."""

from __future__ import annotations

import json
import subprocess
import sys

import httpx
import pytest

from classroom.server.db import CORE_MIGRATIONS, Database, Migration, MigrationError
from classroom.server.tests.harness import PROCTORING, ServerProcess, env_for, wait_until


def test_simulator_students_are_labelled_simulated(tmp_path):
    srv = ServerProcess(tmp_path / "data")
    try:
        t = srv.teacher()
        code = t.post("/api/teacher/session", json={"title": "Нагрузочный прогон", "mode": "url"}).json()["join_code"]
        proc = subprocess.run(
            [sys.executable, "-m", "classroom.simulator", "--server", f"127.0.0.1:{srv.port}", "--code", code, "--students", "6",
             "--duration", "3", "--period", "0.3", "--incident-rate", "0.5", "--chaos", "0.1"],
            capture_output=True, text=True, cwd=str(PROCTORING), env=env_for(tmp_path / "data"), timeout=60,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
        assert summary["simulated_students"] == 6 and summary["connected_once"] == 6
        cards = t.get("/api/teacher/students").json()
        assert len(cards) == 6
        assert all(c["origin"] == "simulated" and c["student_label"].startswith("SIM-") for c in cards)
        info = t.get("/api/teacher/info").json()
        assert info["simulated_students"] == 6
        events = [e for c in cards for e in t.get(f"/api/teacher/students/{c['student_id']}/events").json()]
        assert events and all(e["origin"] == "simulated" and e["payload"]["explanation_ru"].startswith("СИМУЛЯЦИЯ") for e in events)
        ids = [(e["student_id"], e["event_id"]) for e in events]
        assert len(ids) == len(set(ids)), "re-sent offline queue must not create duplicate events"
        with_preview = [c for c in cards if c["preview_url"]]
        assert with_preview
        img = t.get(with_preview[0]["preview_url"])
        assert img.status_code == 200 and img.headers["x-qorgau-origin"] == "simulated"
    finally:
        assert srv.stop() == 0


# ------------------------------------------------------------------------------------------- features
def test_feature_plugin_routes_hooks_and_migrations(tmp_path):
    srv = ServerProcess(tmp_path, {"QORGAU_CLASS_FEATURES": "classroom.server.tests.sample_feature:create_classroom_feature,no_such_pkg:create"})
    try:
        t = srv.teacher()
        info = t.get("/api/teacher/info").json()
        status = {f["name"]: f["status"] for f in info["features"]}
        assert status["sample"] == "mounted" and status["no_such_pkg:create"] == "not_installed"
        with httpx.Client(base_url=srv.base, timeout=5) as anon:
            assert anon.get("/api/teacher/audio/sample/stats").status_code == 401  # the gate protects feature routes too
        code = t.post("/api/teacher/session", json={"title": "x", "mode": "url"}).json()["join_code"]
        s = srv.student()
        try:
            s.hello(join_code=code)
            s.status()
            s.incident(1, "inc-f")
            assert wait_until(lambda: t.get("/api/teacher/audio/sample/stats").json()["seen"].get("incident") == 1)
            body = t.get("/api/teacher/audio/sample/stats").json()
            assert body["teacher"] == "teacher" and body["rows"] >= 2
        finally:
            s.close()
    finally:
        assert srv.stop() == 0


def test_feature_route_outside_reservation_is_refused(tmp_path):
    srv = ServerProcess(tmp_path, {"QORGAU_CLASS_FEATURES": "classroom.server.tests.sample_feature:create_bad_route_feature"})
    try:
        t = srv.teacher()
        f = t.get("/api/teacher/info").json()["features"][0]
        assert f["status"] == "failed" and "outside its reserved prefixes" in f["detail"]
        assert t.get("/api/teacher/control/sample/stats").status_code == 404
    finally:
        assert srv.stop() == 0


def test_feature_hook_failure_is_visible_and_isolated(tmp_path):
    srv = ServerProcess(tmp_path, {"QORGAU_CLASS_FEATURES": "classroom.server.tests.sample_feature:create_exploding_feature"})
    try:
        t = srv.teacher()
        code = t.post("/api/teacher/session", json={"title": "x", "mode": "url"}).json()["join_code"]
        s = srv.student()
        try:
            sid = s.hello(join_code=code)["student_id"]
            s.incident(1, "inc-x")
            assert wait_until(lambda: t.get(f"/api/teacher/students/{sid}/incidents").json())  # core still works
            assert wait_until(lambda: "hook error" in t.get("/api/teacher/info").json()["features"][0]["detail"])
        finally:
            s.close()
    finally:
        assert srv.stop() == 0


# ------------------------------------------------------------------------------------------ migrations
def test_migration_ids_are_namespaced_by_owner():
    with pytest.raises(MigrationError):
        Migration("t03_0001_x", "T04", "CREATE TABLE t04_x (a)")
    with pytest.raises(MigrationError):
        Migration("reviews", "T03", "CREATE TABLE t03_x (a)")


def test_feature_tables_must_be_prefixed(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    try:
        db.migrate(CORE_MIGRATIONS)
        with pytest.raises(MigrationError):
            db.migrate([Migration("t03_0001_bad", "T03", "CREATE TABLE students2 (a)")])
        db.migrate([Migration("t03_0001_ok", "T03", "CREATE TABLE t03_reviews (a); CREATE INDEX t03_reviews_a ON t03_reviews(a)")])
        assert "t03_0001_ok" in db.applied()
    finally:
        db.close()


def test_edited_applied_migration_is_refused(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    try:
        db.migrate([Migration("t04_0001_a", "T04", "CREATE TABLE t04_a (x)")])
        assert db.migrate([Migration("t04_0001_a", "T04", "CREATE TABLE t04_a (x)")]) == []  # idempotent
        with pytest.raises(MigrationError):
            db.migrate([Migration("t04_0001_a", "T04", "CREATE TABLE t04_a (x, y)")])
    finally:
        db.close()


def test_failed_migration_rolls_back(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    try:
        with pytest.raises(Exception):
            db.migrate([Migration("t04_0001_b", "T04", "CREATE TABLE t04_b (x); CREATE TABLE t04_b (y)")])
        assert "t04_0001_b" not in db.applied()
        assert not db.query("SELECT name FROM sqlite_master WHERE name='t04_b'")
    finally:
        db.close()
