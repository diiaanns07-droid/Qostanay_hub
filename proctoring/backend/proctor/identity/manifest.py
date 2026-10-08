"""Identity model manifest + local weight verification (owner: A13). Never downloads anything.

The committed manifest (``models.manifest.json`` next to this file) lists two OpenCV Zoo files: YuNet (face
detector) and SFace (face feature extractor). Contract ``ModelManifest.module`` only allows phone/attention
(contract 1.1 is frozen), so this module keeps its own small schema with the same fields (+ role, license_url).
Weights live outside Git at ``<models_dir>/<file>``; a file is accepted only if size and SHA-256 match.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MANIFEST_PATH = Path(__file__).resolve().parent / "models.manifest.json"
INSTALLED_MANIFEST_NAME = "identity/models.manifest.json"  # copy written by prepare next to the weights
_CHUNK = 1 << 20


class IdentityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]
    role: Literal["face_detector", "face_embedder"]
    file: Annotated[str, Field(pattern=r"^identity/[A-Za-z0-9._-]+$", max_length=200)]
    format: Literal["onnx"]
    version: Annotated[str, Field(max_length=100)]
    source_url: Annotated[str, Field(pattern=r"^https://", max_length=500)]
    source_page: Annotated[str, Field(pattern=r"^https://", max_length=500)]
    license: Annotated[str, Field(max_length=64)]
    license_url: Annotated[str, Field(pattern=r"^https://", max_length=500)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[int, Field(gt=0)]
    opencv_api: Annotated[str, Field(max_length=64)]
    notes: Annotated[str, Field(max_length=1000)] = ""

    @model_validator(mode="after")
    def _no_dot_segments(self) -> "IdentityModel":
        if any(part in (".", "..") for part in self.file.split("/")):
            raise ValueError("file must be relative without '.' or '..' segments")
        return self


class IdentityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_: Literal["qorgau.identity.models/1"] = Field(alias="schema")
    module: Literal["identity"]
    models: list[IdentityModel]

    @model_validator(mode="after")
    def _one_per_role(self) -> "IdentityManifest":
        roles = sorted(m.role for m in self.models)
        if roles != ["face_detector", "face_embedder"]:
            raise ValueError(f"expected exactly one face_detector and one face_embedder, got {roles}")
        return self

    def by_role(self, role: str) -> IdentityModel:
        return next(m for m in self.models if m.role == role)


@dataclass(frozen=True)
class ModelCheck:
    ok: bool
    code: str  # "model_ok" | "model_missing" | "model_invalid" | "manifest_invalid"
    message: str
    path: Path | None = None
    actual_sha256: str | None = None


def load_manifest(path: Path = MANIFEST_PATH) -> IdentityManifest:
    """Parse + validate (raises ValueError/OSError)."""
    return IdentityManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def model_path(models_dir: Path, entry: IdentityModel) -> Path:
    root = os.path.abspath(models_dir)
    path = os.path.abspath(os.path.join(root, entry.file))
    if os.path.commonpath([root, path]) != root or path == root:
        raise ValueError("model file must be inside models_dir")
    return Path(path)


def display_path(models_dir: Path, entry: IdentityModel) -> str:
    """Shown in health/UI without the user's home directory: 'models/identity/<file>'."""
    return f"{Path(models_dir).name}/{entry.file}"


def _missing(models_dir: Path, entry: IdentityModel, path: Path) -> ModelCheck:
    return ModelCheck(
        False,
        "model_missing",
        f"Identity model file not found: {display_path(models_dir, entry)} ({entry.model_id}). Prepare it before the "
        f"exam: python -m proctor.identity.prepare --download; the runtime never downloads.",
        path,
    )


def _compare(models_dir: Path, entry: IdentityModel, path: Path, size: int, actual: str) -> ModelCheck:
    shown = display_path(models_dir, entry)
    if size != entry.size_bytes:
        return ModelCheck(False, "model_invalid", f"{shown}: size {size} B != manifest {entry.size_bytes} B (wrong or partial file)", path)
    if actual != entry.sha256:
        return ModelCheck(False, "model_invalid", f"{shown}: sha256 {actual[:12]}... != manifest {entry.sha256[:12]}...", path, actual)
    return ModelCheck(True, "model_ok", f"{shown} verified (sha256 {actual[:12]}...)", path, actual)


def verify_model_file(models_dir: Path, entry: IdentityModel) -> ModelCheck:
    """Streamed size + SHA-256 check (prepare --check)."""
    try:
        path = model_path(models_dir, entry)
    except ValueError as exc:
        return ModelCheck(False, "manifest_invalid", str(exc))
    if not path.is_file():
        return _missing(models_dir, entry, path)
    try:
        size = path.stat().st_size
        if size != entry.size_bytes:
            return _compare(models_dir, entry, path, size, "")
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:
        return ModelCheck(False, "model_invalid", f"{display_path(models_dir, entry)}: cannot read ({type(exc).__name__})", path)
    return _compare(models_dir, entry, path, size, digest.hexdigest())


def read_verified(models_dir: Path, entry: IdentityModel) -> tuple[ModelCheck, bytes | None]:
    """Read the whole file once and verify THOSE bytes (runtime). The bytes are what OpenCV loads (from a buffer:
    no second read, no non-ASCII path issues in OpenCV)."""
    try:
        path = model_path(models_dir, entry)
    except ValueError as exc:
        return ModelCheck(False, "manifest_invalid", str(exc)), None
    if not path.is_file():
        return _missing(models_dir, entry, path), None
    try:
        if path.stat().st_size != entry.size_bytes:
            return _compare(models_dir, entry, path, path.stat().st_size, ""), None
        data = path.read_bytes()
    except OSError as exc:
        return ModelCheck(False, "model_invalid", f"{display_path(models_dir, entry)}: cannot read ({type(exc).__name__})", path), None
    check = _compare(models_dir, entry, path, len(data), hashlib.sha256(data).hexdigest())
    return check, (data if check.ok else None)
