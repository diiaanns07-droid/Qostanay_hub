"""Shared fixtures for A03 phone tests.

Two kinds of tests live here and must not be confused:
* fake-detector tests (FakeDetector / injected boxes): check tracking, signals, contracts, error
  handling. They say NOTHING about CV accuracy.
* real-model tests (marker ``real_model``): run the actual local YOLO11n ONNX with onnxruntime CPU;
  skipped when the verified weights are not installed (python -m proctor.phone.prepare --download).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from proctor.phone.tests.helpers import installed_model_dir
from proctor.settings import Settings


def pytest_configure(config):
    config.addinivalue_line("markers", "real_model: runs the real local ONNX model (skipped when weights are absent)")


@pytest.fixture()
def settings(tmp_path) -> Settings:
    """Settings with an EMPTY models dir (no weights) and data outside the repo."""
    return Settings(data_dir=tmp_path / "data", models_dir=tmp_path / "models")


@pytest.fixture(scope="session")
def real_models_dir() -> Path:
    path = installed_model_dir()
    if path is None:
        pytest.skip("verified phone weights not installed (python -m proctor.phone.prepare --download)")
    return path
