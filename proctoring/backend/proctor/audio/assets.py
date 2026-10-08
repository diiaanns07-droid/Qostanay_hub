"""Pinned official Silero artifact; offline validation only."""
import hashlib
import json
import os
from pathlib import Path

REVISION = "be95df9152c0d7618fa1edfeb296fc3dae32376f"  # official v6.2
MODEL_NAME = "silero_vad.onnx"
MODEL_URL = f"https://raw.githubusercontent.com/snakers4/silero-vad/{REVISION}/src/silero_vad/data/{MODEL_NAME}"
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
LICENSE_URL = f"https://raw.githubusercontent.com/snakers4/silero-vad/{REVISION}/LICENSE"


def model_dir() -> Path:
    configured = os.environ.get("QORGAU_MODELS_DIR")
    if configured:
        return Path(configured) / "audio"
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("LOCALAPPDATA is required; pass an explicit model directory on non-Windows hosts")
    return Path(base) / "QorgauExam" / "models" / "audio"


def check(directory: Path | None = None) -> Path:
    directory = directory if directory is not None else model_dir()
    path = directory / MODEL_NAME
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("sha256") != MODEL_SHA256 or manifest.get("revision") != REVISION:
        raise ValueError("unexpected audio model manifest")
    if hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError("audio model SHA256 mismatch")
    return path
