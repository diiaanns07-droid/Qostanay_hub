"""Real YuNet + SFace through OpenCV (skipped when the verified files are not installed).

Models dir: QORGAU_MODELS_DIR, else %LOCALAPPDATA%\\QorgauExam\\models (python -m proctor.identity.prepare --download).
No face images are committed (people's photos stay out of Git), so this checks loading, the no-face path and
timing only; similarity on real faces is measured LIVE (handoffs/A13/STATUS.md), not here.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from proctor.identity import IdentityAnalyzer
from proctor.identity.manifest import load_manifest, verify_model_file
from proctor.identity.prepare import default_models_dir
from proctor.identity.tests.helpers import make_frame
from proctor.settings import Settings
from proctor_contracts.v1 import HealthStatus, SignalState, SourceMode


@pytest.fixture(scope="module")
def models_dir():
    path = default_models_dir()
    if not all(verify_model_file(path, e).ok for e in load_manifest().models):
        pytest.skip("identity models not installed (python -m proctor.identity.prepare --download)")
    return path


def test_real_models_load_and_report_no_face_on_empty_frame(models_dir, tmp_path):
    analyzer = IdentityAnalyzer(Settings(models_dir=models_dir, data_dir=tmp_path / "d"))
    health = analyzer.load()
    assert health.status == HealthStatus.OK and health.code == "model_loaded", health.message
    assert str(models_dir) not in health.model_dump_json()
    analyzer.start_session("s-real", SourceMode.LIVE)
    analyzer.exam_started(0.0)
    image = np.full((480, 640, 3), 120, dtype=np.uint8)
    image.flags.writeable = False
    t0 = time.perf_counter()
    (obs,) = analyzer.process(make_frame(0, 0.0, image, session_id="s-real", mode=SourceMode.LIVE))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert obs.same_person == SignalState.UNKNOWN and obs.reasons == ["no_face"] and not obs.enrolled
    assert obs.producer.model_id == "sface-2021dec" and obs.producer.model_sha256 == load_manifest().by_role("face_embedder").sha256
    assert elapsed_ms < 1000  # one analysis per second must fit comfortably
    assert not image.flags.writeable
