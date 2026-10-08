"""Pytest fixtures for A02 capture tests (helpers live in helpers.py)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

import pytest

pytest.importorskip("cv2")

from proctor.capture import FrameCaptureService  # noqa: E402
from proctor.capture.tests.helpers import FakeDevice, sha256, wait_until, write_manifest, write_video  # noqa: E402
from proctor.settings import Settings  # noqa: E402


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    return Settings(
        data_dir=tmp_path / "data",
        replay_dir=replay_dir,
        exam_path=tmp_path / "missing_exam.json",
        synthetic_fps=30.0,
        preview_fps=15.0,
        frame_ring_seconds=3.0,
    )


@pytest.fixture()
def make_service(settings: Settings):
    created: list[FrameCaptureService] = []

    def factory(**kwargs: Any) -> FrameCaptureService:
        svc = FrameCaptureService(kwargs.pop("settings", settings), **kwargs)
        created.append(svc)
        return svc

    yield factory
    for svc in created:
        svc.close(timeout_s=2.0)


@pytest.fixture()
def device() -> FakeDevice:
    d = FakeDevice()
    yield d
    d.unblocked.set()  # never leave a hanging fake read behind


@pytest.fixture()
def clip(settings: Settings) -> Callable[..., str]:
    """clip(replay_id, n=50, fps=25, **manifest_fields) -> replay_id with media under replay_dir/media/."""

    def make(replay_id: str = "clip1", n: int = 50, fps: float = 25.0, width: int = 320, height: int = 240, media: dict | None = None, **fields: Any) -> str:
        media_dir = settings.replay_dir / "media"
        media_dir.mkdir(exist_ok=True)
        video = write_video(media_dir / f"{replay_id}.avi", n=n, fps=fps, width=width, height=height)
        m = {"kind": "video", "path": f"media/{replay_id}.avi", "sha256": sha256(video)}
        m.update(media or {})
        write_manifest(settings.replay_dir, replay_id, m, **fields)
        return replay_id

    return make


@pytest.fixture()
def thread_baseline():
    """Assert the test left no capture threads running."""
    before = {t.ident for t in threading.enumerate()}
    yield
    def leftovers() -> list[str]:
        return [t.name for t in threading.enumerate() if t.ident not in before and t.name.startswith("capture")]

    assert wait_until(lambda: not leftovers(), timeout=3.0), f"leaked threads: {leftovers()}"
