"""Fixtures for T03 tests. All clips are SYNTHETIC (classreview.testclips) or hand-built byte layouts."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

PROCTORING = Path(__file__).resolve().parents[2]
for p in (Path(__file__).resolve().parent, PROCTORING, PROCTORING / "backend", PROCTORING / "contracts" / "python"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from classreview import AuthError, ReviewConfig, ReviewStore, create_review_router  # noqa: E402

TOKENS = {"tok-a": "stu-a", "tok-b": "stu-b"}
TEACHER_HEADER = {"X-Test-Teacher": "yes"}


def incident(seq: int, incident_id: str = "inc-1", **over) -> dict:
    msg = {
        "seq": seq,
        "incident_id": incident_id,
        "rule_id": "phone_visible",
        "category": "phone",
        "priority": "medium",
        "state": "open",
        "t_start_wall": "2026-10-08T09:00:41Z",
        "duration_ms": 1000,
        "explanation_ru": "СИНТЕТИКА: телефон виден 1 с",
        "clip_available": True,
    }
    msg.update(over)
    return msg


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def tiny_mp4(codec: bytes = b"avc1", faststart: bool = True, mdat: bytes = b"\x00" * 64) -> bytes:
    """Hand-built ISO BMFF layout (structure only, no decodable frames) for parser tests."""
    mvhd = box(b"mvhd", b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1000, 6000) + b"\x00" * 80)
    hdlr = box(b"hdlr", b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00" + b"vide" + b"\x00" * 13)
    entry = struct.pack(">I", 16) + codec + b"\x00" * 8
    stsd = box(b"stsd", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + entry)
    trak = box(b"trak", box(b"mdia", hdlr + box(b"minf", box(b"stbl", stsd))))
    moov = box(b"moov", mvhd + trak)
    ftyp = box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2")
    md = box(b"mdat", mdat)
    return ftyp + (moov + md if faststart else md + moov)


def tiny_avi(extra: bytes = b"\x00" * 64) -> bytes:
    strh = b"strh" + struct.pack("<I", 56) + b"vids" + b"MJPG" + b"\x00" * 48
    body = b"AVI " + b"LIST" + struct.pack("<I", 4 + len(strh)) + b"hdrl" + strh + extra
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.fixture(scope="session")
def clip() -> bytes:
    cv2 = pytest.importorskip("cv2")  # noqa: F841
    from classreview.testclips import make_test_clip

    return make_test_clip(seconds=4.0, fps=10)


@pytest.fixture(scope="session")
def clip_b() -> bytes:
    pytest.importorskip("cv2")
    from classreview.testclips import make_test_clip

    return make_test_clip(seconds=3.0, fps=10)


@pytest.fixture()
def make_store(tmp_path):
    stores: list[ReviewStore] = []

    def factory(**cfg) -> ReviewStore:
        s = ReviewStore(ReviewConfig(data_dir=tmp_path / "data", **cfg))
        s.open()
        stores.append(s)
        return s

    yield factory
    for s in stores:
        s.close()


@pytest.fixture()
def store(make_store) -> ReviewStore:
    s = make_store()
    s.open_class_session("cls-1")
    return s


def teacher_guard(request: Request) -> str:
    if request.headers.get("x-test-teacher") != "yes":
        raise AuthError(401, "teacher_auth_required", "Нужен вход преподавателя")
    return "teacher-test"


@pytest.fixture()
def client(store) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_review_router(
            store,
            teacher_guard=teacher_guard,
            student_resolver=TOKENS.get,
            status_provider=lambda sid: {"zone": "yellow", "monitoring": "ok"},
        )
    )
    c = TestClient(app)
    c.store = store  # type: ignore[attr-defined]
    return c


def upload(client: TestClient, incident_id: str, data: bytes, token: str = "tok-a", media_type: str = "video/mp4", **headers):
    return client.post(
        f"/api/student/clips/{incident_id}",
        content=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": media_type, **headers},
    )
