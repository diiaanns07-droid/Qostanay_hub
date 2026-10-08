"""Prepare/verify the A04 face model OUTSIDE the exam runtime (the backend never downloads).

    python -m proctor.attention.model_tool verify [--models-dir DIR]
    python -m proctor.attention.model_tool fetch  [--models-dir DIR]   # one-time, needs network

fetch downloads manifest.source_url to a temporary file next to the target, checks size and
sha256 against models.manifest.json and only then renames it into place. Exit code 0 = verified.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

from proctor.settings import Settings

from .landmarker import BackendUnavailable, load_manifest, resolve_model, sha256_file


def verify(models_dir: Path) -> int:
    manifest = load_manifest()
    try:
        path = resolve_model(models_dir, manifest)
    except BackendUnavailable as exc:
        print(f"FAIL {exc.code}: {exc.message}")
        return 1
    print(f"OK {manifest.model_id} {path} sha256={manifest.sha256}")
    return 0


def fetch(models_dir: Path) -> int:
    manifest = load_manifest()
    target = (Path(models_dir) / manifest.file).resolve()
    if target.is_file() and verify(models_dir) == 0:
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".download-", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(manifest.source_url, timeout=60) as resp:  # noqa: S310 (https only, manifest-validated)
            size = 0
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                size += len(chunk)
                if size > manifest.size_bytes:
                    raise ValueError("download larger than the manifest size")
                out.write(chunk)
        if tmp.stat().st_size != manifest.size_bytes or sha256_file(tmp) != manifest.sha256:
            print("FAIL downloaded file does not match models.manifest.json (size/sha256); nothing installed")
            return 1
        tmp.replace(target)
        os.chmod(target, 0o644)
    finally:
        if tmp.exists():
            tmp.unlink()
    return verify(models_dir)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("verify", "fetch"))
    ap.add_argument("--models-dir", type=Path, default=None, help="default: QORGAU_MODELS_DIR or proctoring/models")
    args = ap.parse_args(argv)
    models_dir = args.models_dir or Settings.from_env().models_dir
    return verify(models_dir) if args.command == "verify" else fetch(models_dir)


if __name__ == "__main__":
    sys.exit(main())
