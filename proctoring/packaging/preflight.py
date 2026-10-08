"""Read-only readiness check. Never opens a camera, downloads files or enables exam restrictions."""
from __future__ import annotations
import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify_asset(manifest_path: Path, models_dir: Path) -> dict:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        relative = Path(manifest["file"])
        base = models_dir.resolve()
        target = (base / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(base):
            raise ValueError("model file escapes models directory")
        expected = manifest["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("manifest SHA256 is missing or malformed")
        if not target.is_file():
            return {"status": "FAIL", "reason": "model_missing", "file": manifest["file"]}
        if target.stat().st_size != manifest["size_bytes"]:
            return {"status": "FAIL", "reason": "model_size_mismatch", "file": manifest["file"]}
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return {"status": "PASS" if digest == expected else "FAIL",
                "reason": "checksum_verified" if digest == expected else "model_hash_mismatch",
                "file": manifest["file"], "sha256": digest}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "FAIL", "reason": f"invalid_manifest: {type(exc).__name__}: {exc}"}


def inspect(root: Path, profile: str, models_dir: Path) -> dict:
    rows = []
    def row(name, ok, detail=""):
        rows.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
    row("python_3_12", sys.version_info[:2] == (3, 12), platform.python_version())
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    deps = config["project"]["dependencies"]
    if profile == "desktop":
        deps += config["project"]["optional-dependencies"]["cv"]
    import_names = {"opencv-contrib-python": "cv2"}
    for dep in deps:
        name, wanted = dep.split("==", 1)
        try:
            installed = importlib.metadata.version(name)
            importlib.import_module(import_names.get(name, name.replace("-", "_")))
            row("dependency:" + name, installed == wanted, f"installed={installed}; pinned={wanted}")
        except Exception as exc:
            row("dependency:" + name, False, type(exc).__name__)
    for path in ("backend/proctor/__main__.py", "contracts/python/proctor_contracts/v1.py"):
        row("file:" + path, (root / path).is_file())
    if profile == "desktop":
        for module in ("capture", "phone", "attention", "fusion", "evidence"):
            row("module:" + module, (root / "backend/proctor" / module / "__init__.py").is_file())
        for path in ("desktop/dist/main/main.cjs", "desktop/dist/preload/preload.cjs",
                     "desktop/dist/renderer/index.html", "desktop/node_modules/electron/dist/electron.exe",
                     "demo/exams/demo_exam.json"):
            row("file:" + path, (root / path).is_file())
        for module in ("phone", "attention"):
            asset = verify_asset(root / "backend/proctor" / module / "models.manifest.json", models_dir)
            rows.append({"check": "model:" + module, **asset})
        row("target_windows", sys.platform == "win32", platform.platform())
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True)
    return {"profile": profile, "tested_sha": head.stdout.strip() if head.returncode == 0 else None,
            "platform": platform.platform(), "checks": rows,
            "ready_for_launch": all(r["status"] == "PASS" for r in rows),
            "cv_accuracy_verified": False, "windows_enforcement_verified": False,
            "note": "Files, imports and checksums only. Camera, Electron rendering and restrictions need candidate acceptance tests."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("backend", "desktop"), default="desktop")
    parser.add_argument("--models-dir", type=Path, default=Path(os.environ.get("QORGAU_MODELS_DIR", ROOT / "models")))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = inspect(ROOT, args.profile, args.models_dir)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["ready_for_launch"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
