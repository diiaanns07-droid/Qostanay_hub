"""Model manifest + local weight verification (owner: A03). Never downloads anything.

The committed manifest (``models.manifest.json`` next to this file) is a contract ``ModelManifest``.
Weights live outside Git at ``settings.models_dir / manifest.file`` (``models/**`` is git-ignored).
A file is accepted only if its size and SHA-256 match the manifest.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from proctor_contracts.v1 import ModelManifest

MANIFEST_PATH = Path(__file__).resolve().parent / "models.manifest.json"
_CHUNK = 1 << 20


@dataclass(frozen=True)
class ModelCheck:
    """Result of verifying the local weight file against the manifest."""

    ok: bool
    code: str  # "model_ok" | "model_missing" | "model_invalid" | "manifest_invalid"
    message: str
    path: Path | None = None
    actual_sha256: str | None = None


def load_manifest(path: Path = MANIFEST_PATH) -> ModelManifest:
    """Parse + validate the manifest (raises ValueError/OSError on problems)."""
    manifest = ModelManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if manifest.module != "phone":
        raise ValueError(f"manifest module is {manifest.module!r}, expected 'phone'")
    if manifest.format != "onnx":
        raise ValueError(f"manifest format is {manifest.format!r}; the phone runtime supports only 'onnx'")
    return manifest


def model_path(models_dir: Path, manifest: ModelManifest) -> Path:
    """Path of the weight file. manifest.file is validated as relative without '.'/'..' segments."""
    root = os.path.abspath(models_dir)
    path = os.path.abspath(os.path.join(root, manifest.file))  # normalizes '..' without following symlinks
    if os.path.commonpath([root, path]) != root or path == root:
        raise ValueError("model file must be inside models_dir")  # defence in depth; sha256 pins the content
    return Path(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(models_dir: Path, manifest: ModelManifest) -> str:
    """Path shown in health/UI without the user's home directory: 'models/<file>'."""
    return f"{Path(models_dir).name}/{manifest.file}"


def verify_model_file(models_dir: Path, manifest: ModelManifest) -> ModelCheck:
    try:
        path = model_path(models_dir, manifest)
    except ValueError as exc:
        return ModelCheck(False, "manifest_invalid", str(exc))
    shown = display_path(models_dir, manifest)
    if not path.is_file():
        return ModelCheck(
            False,
            "model_missing",
            f"Phone model file not found: {shown} ({manifest.model_id}). Prepare it before the exam: "
            f"python -m proctor.phone.prepare --download (or --from-file <file>); the runtime never downloads.",
            path,
        )
    try:
        size = path.stat().st_size
        if size != manifest.size_bytes:
            return ModelCheck(
                False,
                "model_invalid",
                f"{shown}: size {size} B != manifest {manifest.size_bytes} B (wrong or partial file)",
                path,
            )
        actual = sha256_file(path)
    except OSError as exc:
        return ModelCheck(False, "model_invalid", f"{shown}: cannot read ({type(exc).__name__})", path)
    if actual != manifest.sha256:
        return ModelCheck(
            False,
            "model_invalid",
            f"{shown}: sha256 {actual[:12]}... != manifest {manifest.sha256[:12]}...",
            path,
            actual,
        )
    return ModelCheck(True, "model_ok", f"{shown} verified (sha256 {actual[:12]}...)", path, actual)
