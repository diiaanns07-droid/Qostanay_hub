"""A13 manifest + prepare: committed facts about the OpenCV Zoo files, sha256 gate, runtime never downloads."""

from __future__ import annotations

import hashlib
import urllib.request

import pytest

from proctor.identity import IdentityAnalyzer
from proctor.identity import prepare as prep
from proctor.identity.manifest import IdentityModel, load_manifest, verify_model_file
from proctor.settings import Settings


def test_manifest_lists_yunet_mit_and_sface_apache():
    manifest = load_manifest()
    det, emb = manifest.by_role("face_detector"), manifest.by_role("face_embedder")
    assert det.file == "identity/face_detection_yunet_2023mar.onnx" and det.license == "MIT"
    assert emb.file == "identity/face_recognition_sface_2021dec.onnx" and emb.license == "Apache-2.0"
    for entry in (det, emb):
        assert entry.source_url.startswith("https://github.com/opencv/opencv_zoo/raw/main/models/")
        assert entry.license_url.startswith("https://github.com/opencv/opencv_zoo/blob/main/models/")
        assert len(entry.sha256) == 64 and entry.size_bytes > 0


def test_missing_files_are_reported(tmp_path):
    manifest = load_manifest()
    for entry in manifest.models:
        result = verify_model_file(tmp_path, entry)
        assert not result.ok and result.code == "model_missing"
    assert prep.main(["--check", "--models-dir", str(tmp_path)]) == 1


def _entry(data: bytes) -> IdentityModel:
    base = load_manifest().by_role("face_detector").model_dump()
    return IdentityModel(**{**base, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})


def test_install_accepts_only_matching_bytes(tmp_path):
    good = b"onnx-bytes" * 100
    entry = _entry(good)
    target = tmp_path / entry.file
    with pytest.raises(ValueError):
        prep._install(iter([b"x" * len(good)]), target, entry.sha256, entry.size_bytes)
    assert not target.exists() and not list(target.parent.glob("*.part"))
    prep._install(iter([good[:500], good[500:]]), target, entry.sha256, entry.size_bytes)
    assert verify_model_file(tmp_path, entry).ok
    target.write_bytes(good[:-1])
    assert verify_model_file(tmp_path, entry).code == "model_invalid"


def test_only_https_sources():
    with pytest.raises(ValueError):
        list(prep._iter_url("http://example.com/model.onnx"))


def test_installed_manifest_copy_has_url_license_sha(tmp_path):
    import json

    path = prep._write_installed_manifest(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "identity" / "models.manifest.json"
    assert {m["license"] for m in data["models"]} == {"MIT", "Apache-2.0"}
    assert all(m["source_url"] and m["sha256"] for m in data["models"]) and data["installed_at"]


def test_default_models_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("QORGAU_MODELS_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert prep.default_models_dir() == tmp_path / "QorgauExam" / "models"
    monkeypatch.setenv("QORGAU_MODELS_DIR", str(tmp_path / "m"))
    assert prep.default_models_dir() == tmp_path / "m"


def test_runtime_load_never_touches_the_network(tmp_path, monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("runtime must not download")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    analyzer = IdentityAnalyzer(Settings(data_dir=tmp_path / "d", models_dir=tmp_path / "m"))
    assert analyzer.load().code == "model_missing"
    assert not (tmp_path / "m").exists()
