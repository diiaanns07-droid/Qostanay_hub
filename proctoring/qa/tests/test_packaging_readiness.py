"""The release check must reject missing, corrupt or escaping model assets."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("a09_preflight", Path(__file__).resolve().parents[2] / "packaging/preflight.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def asset(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "model.bin").write_bytes(b"test model bytes")
    manifest = tmp_path / "manifest.json"
    data = {"file": "model.bin", "sha256": hashlib.sha256(b"test model bytes").hexdigest(), "size_bytes": 16}
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return models, manifest, data


def test_verified_model_passes(tmp_path):
    models, manifest, _ = asset(tmp_path)
    assert module.verify_asset(manifest, models)["status"] == "PASS"


@pytest.mark.parametrize("change,reason", [
    ({"file": "absent.bin"}, "model_missing"),
    ({"size_bytes": 1}, "model_size_mismatch"),
    ({"sha256": "a" * 64}, "model_hash_mismatch"),
    ({"file": "../outside.bin"}, "invalid_manifest"),
    ({"sha256": "not-a-hash"}, "invalid_manifest"),
])
def test_invalid_asset_fails(tmp_path, change, reason):
    models, manifest, data = asset(tmp_path)
    data.update(change)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    result = module.verify_asset(manifest, models)
    assert result["status"] == "FAIL" and result["reason"].startswith(reason)


def test_missing_manifest_is_reported(tmp_path):
    assert module.verify_asset(tmp_path / "absent.json", tmp_path)["status"] == "FAIL"
