"""Explicit setup command. This is the only audio module allowed to download."""
import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path

from .assets import LICENSE_URL, MODEL_NAME, MODEL_SHA256, MODEL_URL, REVISION, check, model_dir
from . import yamnet_assets as yamnet


def atomic_write(directory: Path, name: str, content: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix="audio-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
        os.replace(temporary, directory / name)
    finally:
        Path(temporary).unlink(missing_ok=True)


def download_yamnet(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(yamnet.MODEL_URL, timeout=60) as response:
        data = response.read(20 * 1024 * 1024)
    labels = yamnet.labels_from_model(data)  # hash, embedded license and class names before install
    with urllib.request.urlopen(yamnet.LICENSE_URL, timeout=30) as response:
        license_text = response.read(65536)
    if b"Apache License" not in license_text or b"Version 2.0" not in license_text:
        raise ValueError("unexpected Apache license text")
    manifest = dict(name=yamnet.MODEL_NAME, version=yamnet.MODEL_VERSION, source=yamnet.MODEL_URL,
        documentation=yamnet.DOCUMENTATION_URL, sha256=yamnet.MODEL_SHA256, bytes=len(data),
        license="Apache-2.0", license_source="embedded TFLite metadata", license_url=yamnet.LICENSE_URL,
        license_sha256=hashlib.sha256(license_text).hexdigest(), sample_rate=16000,
        window_samples=15600, class_count=len(labels),
        speech_classes={name: labels.index(name) for name in yamnet.SPEECH_CLASSES},
        absent_requested_classes=list(yamnet.ABSENT_REQUESTED_CLASSES))
    for name, content in ((yamnet.MODEL_NAME, data), ("LICENSE.yamnet.txt", license_text),
                          (yamnet.LABEL_FILE, ("\n".join(labels) + "\n").encode("utf-8")),
                          (yamnet.MANIFEST_NAME, json.dumps(manifest, indent=2).encode("utf-8"))):
        atomic_write(directory, name, content)
    return yamnet.check(directory)


def download(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(MODEL_URL, timeout=60) as response:
        data = response.read(10 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise ValueError("downloaded audio model SHA256 mismatch")
    with urllib.request.urlopen(LICENSE_URL, timeout=30) as response:
        license_text = response.read(65536)
    manifest = dict(name=MODEL_NAME, sha256=MODEL_SHA256, revision=REVISION,
                    source=MODEL_URL, license="MIT", license_source=LICENSE_URL,
                    sample_rate=16000, block_samples=512, context_samples=64)
    for name, content in ((MODEL_NAME, data), ("LICENSE.silero.txt", license_text),
                          ("manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))):
        atomic_write(directory, name, content)
    return check(directory)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare local Silero VAD and MediaPipe YAMNet; no microphone access")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--download", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--model", choices=("all", "silero", "yamnet"), default="all")
    args = parser.parse_args()
    try:
        directory = args.model_dir if args.model_dir is not None else model_dir()
        if args.model in ("all", "silero"):
            print(download(directory) if args.download else check(directory))
        if args.model in ("all", "yamnet"):
            print(download_yamnet(directory) if args.download else yamnet.check(directory))
    except Exception as exc:
        parser.exit(1, f"audio model unavailable: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
