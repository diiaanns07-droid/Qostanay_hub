"""Prepare / check the local phone model BEFORE the exam (owner: A03). Not used by the runtime.

    python -m proctor.phone.prepare --check                 # verify models/phone/<file> (offline)
    python -m proctor.phone.prepare --download              # explicit download from manifest.source_url
    python -m proctor.phone.prepare --from-file <path>      # install a file copied by hand (USB, offline)
Options: --models-dir <dir> (default: Settings.from_env().models_dir).

A file is installed only if its size + SHA-256 match the committed manifest; it is written to
<file>.part first and atomically renamed. The backend itself never downloads anything.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

from proctor.settings import Settings

from .config import PhoneConfig
from .detector import DetectorLoadError, YoloOnnxDetector
from .manifest import MANIFEST_PATH, display_path, load_manifest, model_path, verify_model_file

_CHUNK = 1 << 20


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


def _iter_file(path: Path):
    with open(path, "rb") as fh:
        yield from iter(lambda: fh.read(_CHUNK), b"")


def _iter_url(url: str):
    if not url.startswith("https://"):
        raise ValueError("only https:// sources are allowed")
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - https only, explicit user action
        yield from iter(lambda: resp.read(_CHUNK), b"")


def check(models_dir: Path) -> int:
    manifest = load_manifest()
    result = verify_model_file(models_dir, manifest)
    print(f"[{result.code}] {result.message}")
    if not result.ok:
        return 1
    try:
        info = YoloOnnxDetector(result.path, manifest, PhoneConfig()).load()  # type: ignore[arg-type]
    except DetectorLoadError as exc:
        print(f"[{exc.code}] {exc.message}")
        return 1
    print(
        f"[model_loaded] provider={info.provider} phone_classes={info.phone_classes} input={info.input_size} "
        f"rect={info.rect} warmup_ms={info.warmup_ms:.1f} meta={info.model_meta}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m proctor.phone.prepare", description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="verify the installed file (offline)")
    mode.add_argument("--download", action="store_true", help="download from the manifest source_url (network)")
    mode.add_argument("--from-file", type=Path, help="install a local copy of the model file")
    ap.add_argument("--models-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    models_dir = args.models_dir or Settings.from_env().models_dir
    manifest = load_manifest()
    target = model_path(models_dir, manifest)
    print(f"manifest: {MANIFEST_PATH.name} model={manifest.model_id} license={manifest.license} sha256={manifest.sha256[:12]}...")
    if args.check:
        return check(models_dir)
    existing = verify_model_file(models_dir, manifest)
    if existing.ok:
        print(f"[model_ok] already installed: {display_path(models_dir, manifest)}")
        return check(models_dir)
    try:
        if args.download:
            print(f"downloading {manifest.source_url} (license {manifest.license}) -> {display_path(models_dir, manifest)}")
            _install(_iter_url(manifest.source_url), target, manifest.sha256, manifest.size_bytes)
        else:
            src = args.from_file
            if not src.is_file():
                print(f"[error] not a file: {src}")
                return 1
            if src.resolve() == target.resolve():
                print("[error] --from-file points at the target itself")
                return 1
            _install(_iter_file(src), target, manifest.sha256, manifest.size_bytes)
    except Exception as exc:
        print(f"[error] {type(exc).__name__}: {exc}")
        return 1
    print(f"[installed] {display_path(models_dir, manifest)}")
    return check(models_dir)


if __name__ == "__main__":
    sys.exit(main())
