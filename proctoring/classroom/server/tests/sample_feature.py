"""Example feature plug-in used by the tests (owner: T01). Copy this shape in T03/T04/T05 packages.

    QORGAU_CLASS_FEATURES=classroom.server.tests.sample_feature:create_classroom_feature
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from classroom.server.db import Migration


class SampleFeature:
    name = "sample"
    owner = "T05"

    def __init__(self, ctx: Any, bad_route: bool = False, explode: bool = False):
        self.ctx = ctx
        self.explode = explode
        self.seen: dict[str, int] = {}
        self.router = APIRouter()
        prefix = "/api/teacher/control" if bad_route else "/api/teacher/audio"

        @self.router.get(prefix + "/sample/stats")
        def stats(request: Request) -> dict[str, Any]:
            principal = ctx.teacher(request)  # authentication already enforced by the server gate
            rows = ctx.db.query("SELECT count(*) AS n FROM t05_sample_events")
            return {"teacher": principal.teacher_id, "seen": self.seen, "rows": rows[0]["n"]}

    def migrations(self) -> list[Migration]:
        return [Migration("t05_0001_sample", "T05", "CREATE TABLE t05_sample_events (student_id TEXT NOT NULL, type TEXT NOT NULL)")]

    def on_student_message(self, student_id: str, message: dict[str, Any]) -> None:
        if self.explode:
            raise RuntimeError("sample feature exploded on purpose")
        self.seen[message["type"]] = self.seen.get(message["type"], 0) + 1
        self.ctx.db.execute("INSERT INTO t05_sample_events(student_id, type) VALUES (?,?)", (student_id, message["type"]))
        self.ctx.publish("sample.message", {"student_id": student_id, "type": message["type"]})


def create_classroom_feature(ctx: Any) -> SampleFeature:
    return SampleFeature(ctx)


def create_bad_route_feature(ctx: Any) -> SampleFeature:
    return SampleFeature(ctx, bad_route=True)


def create_exploding_feature(ctx: Any) -> SampleFeature:
    return SampleFeature(ctx, explode=True)
