"""Explicit setup command. This is the only audio module allowed to download."""
import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path

from .assets import LICENSE_URL, MODEL_NAME, MODEL_SHA256, MODEL_URL, REVISION, check, model_dir


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
        fd, temporary = tempfile.mkstemp(prefix="audio-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(content)
            os.replace(temporary, directory / name)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return check(directory)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare local Silero VAD; no microphone access")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--download", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--model-dir", type=Path)
    args = parser.parse_args()
    try:
        directory = args.model_dir if args.model_dir is not None else model_dir()
        print(download(directory) if args.download else check(directory))
    except Exception as exc:
        parser.exit(1, f"audio model unavailable: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
