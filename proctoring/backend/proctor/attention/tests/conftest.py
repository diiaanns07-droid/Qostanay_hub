"""Shared fixtures for A04 tests (fake backend; no model, no MediaPipe, no face images)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from proctor.attention.analyzer import MediaPipeAttentionAnalyzer
from proctor.attention.config import AttentionConfig
from proctor.attention.testing import FakeFaceBackend, make_frame, textured_image
from proctor.settings import Settings
from proctor_contracts.v1 import HealthStatus, SourceMode

FPS = 15.0
DT = 1000.0 / FPS


@dataclass
class Feeder:
    """Feeds frames at 15 fps of session time with strictly increasing frame ids."""

    analyzer: MediaPipeAttentionAnalyzer
    backend: FakeFaceBackend
    session_id: str = "sess-a04"
    frame_id: int = 0
    t_ms: float = 0.0
    image: object = field(default_factory=textured_image)

    def feed(self, faces, n: int = 1, image=None):
        out = []
        for _ in range(n):
            self.backend.set_faces(faces)
            frame = make_frame(self.session_id, self.frame_id, self.t_ms, image if image is not None else self.image)
            out.extend(self.analyzer.process(frame))
            self.frame_id += 1
            self.t_ms += DT
        return out


def make_analyzer(tmp_path, backend: FakeFaceBackend, cfg: AttentionConfig | None = None) -> MediaPipeAttentionAnalyzer:
    a = MediaPipeAttentionAnalyzer(Settings(models_dir=tmp_path, data_dir=tmp_path / "data"), cfg or AttentionConfig(),
                                   backend_factory=lambda c: backend)
    assert a.load().status == HealthStatus.OK
    return a


@pytest.fixture()
def backend() -> FakeFaceBackend:
    return FakeFaceBackend()


@pytest.fixture()
def analyzer(tmp_path, backend):
    a = make_analyzer(tmp_path, backend)
    a.start_session("sess-a04", SourceMode.REPLAY)
    yield a
    a.close()


@pytest.fixture()
def feeder(analyzer, backend) -> Feeder:
    return Feeder(analyzer, backend)


@pytest.fixture()
def analyzer_factory(tmp_path):
    """make(backend, cfg=None, session='sess-a04') -> (analyzer, Feeder); closed after the test."""
    made = []

    def make(backend: FakeFaceBackend, cfg: AttentionConfig | None = None, session: str = "sess-a04"):
        a = make_analyzer(tmp_path, backend, cfg)
        a.start_session(session, SourceMode.REPLAY)
        made.append(a)
        return a, Feeder(a, backend, session_id=session)

    yield make
    for a in made:
        a.close()
