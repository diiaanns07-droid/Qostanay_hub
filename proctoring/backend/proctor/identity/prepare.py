"""Prepare / check the identity models BEFORE the exam (owner: A13). Not used by the runtime.

    python -m proctor.identity.prepare --download     # explicit download from the manifest source_url (network)
    python -m proctor.identity.prepare --check        # verify size + sha256 and load in OpenCV (offline)
Options: --models-dir <dir>. Default: QORGAU_MODELS_DIR, else %LOCALAPPDATA%\\QorgauExam\\models
(files land in <models-dir>\\identity\\). Start the backend with the same QORGAU_MODELS_DIR.

A file is installed only if its size + SHA-256 match the committed manifest (written to <file>.part, then
atomically renamed). A copy of the manifest (URL, license, sha256) is written next to the weights.
The backend itself never downloads anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import IdentityConfig
from .face import EngineLoadError, FaceEngine
from .manifest import INSTALLED_MANIFEST_NAME, MANIFEST_PATH, display_path, load_manifest, model_path, read_verified, verify_model_file

_CHUNK = 1 << 20


def default_models_dir() -> Path:
    env = os.environ.get("QORGAU_MODELS_DIR")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "QorgauExam" / "models"


def _install(source_iter, target: Path, expected_sha: str, expected_size: int) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    digest, size = hashlib.sha256(), 0
    try:
        with open(part, "wb") as out:
            for chunk in source_iter:
                size += len(chunk)
                if size > expected_size:
                    raise ValueError(f"file is larger than the manifest size {expected_size} B")
                digest.update(chunk)
                out.write(chunk)
        actual = digest.hexdigest()
        if size != expected_size or actual != expected_sha:
            raise ValueError(f"verification failed: size {size} B, sha256 {actual[:12]}... (manifest {expected_sha[:12]}...)")
        os.replace(part, target)
        return actual
    finally:
        if part.exists():
            part.unlink()


def _iter_url(url: str):
    if not url.startswith("https://"):
        raise ValueError("only https:// sources are allowed")
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - https only, explicit user action
        if not resp.geturl().startswith("https://"):
            raise ValueError("redirected to a non-https URL")
        yield from iter(lambda: resp.read(_CHUNK), b"")


def _write_installed_manifest(models_dir: Path) -> Path:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    data["installed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    target = Path(models_dir) / INSTALLED_MANIFEST_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def check(models_dir: Path) -> int:
    manifest = load_manifest()
    buffers = []
    for entry in manifest.models:
        result = verify_model_file(models_dir, entry)
        print(f"[{result.code}] {entry.role}: {result.message} (license {entry.license})")
        if not result.ok:
            return 1
    for role in ("face_detector", "face_embedder"):
        check_result, data = read_verified(models_dir, manifest.by_role(role))
        if data is None:
            print(f"[{check_result.code}] {check_result.message}")
            return 1
        buffers.append(data)
    try:
        FaceEngine.from_buffers(buffers[0], buffers[1], IdentityConfig())
    except EngineLoadError as exc:
        print(f"[{exc.code}] {exc.message}")
        return 1
    print("[model_loaded] cv2.FaceDetectorYN + cv2.FaceRecognizerSF created from the verified files (warm-up ok)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m proctor.identity.prepare", description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="verify installed files and load them in OpenCV (offline)")
    mode.add_argument("--download", action="store_true", help="download from the manifest source_url (network)")
    ap.add_argument("--models-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    models_dir = args.models_dir or default_models_dir()
    manifest = load_manifest()
    print(f"manifest: {MANIFEST_PATH.name}; models dir: {models_dir}")
    if args.check:
        return check(models_dir)
    for entry in manifest.models:
        target = model_path(models_dir, entry)
        if verify_model_file(models_dir, entry).ok:
            print(f"[model_ok] already installed: {display_path(models_dir, entry)}")
            continue
        print(f"downloading {entry.source_url} (license {entry.license}, {entry.size_bytes} B) -> {display_path(models_dir, entry)}")
        try:
            _install(_iter_url(entry.source_url), target, entry.sha256, entry.size_bytes)
        except Exception as exc:
            print(f"[error] {entry.model_id}: {type(exc).__name__}: {exc}")
            return 1
        print(f"[installed] {display_path(models_dir, entry)}")
    print(f"[manifest] {_write_installed_manifest(models_dir)}")
    return check(models_dir)


if __name__ == "__main__":
    sys.exit(main())
