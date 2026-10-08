"""Negative and offline tests for adal_offline_kit.py (stdlib only; no real weights, no network).

Run:  python -m unittest discover -s proctoring/acceptance/offline/tests -v
A synthetic checkout is generated in a temporary directory whose path contains spaces, Cyrillic and Kazakh
letters. Its pins describe small synthetic files, so every failure mode can be produced deterministically.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent / "adal_offline_kit.py"
REAL_REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import adal_offline_kit as kit  # noqa: E402

UNICODE_DIR = "Мои файлы Adal қазақ ü"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pe(machine: int = 0x8664, size: int = 512) -> bytes:
    head = bytearray(size)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, 0x80)
    head[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", head, 0x84, machine)
    return bytes(head)


def make_wheel(path: Path, name: str, version: str, license_expr: str | None = "MIT") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        meta = f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n"
        if license_expr:
            meta += f"License-Expression: {license_expr}\n"
        archive.writestr(f"{name.replace('-', '_')}-{version}.dist-info/METADATA", meta)
        archive.writestr(f"{name.replace('-', '_')}/__init__.py", "")
    data = buffer.getvalue()
    path.write_bytes(data)
    return data


def make_electron_zip(path: Path, version: str, machine: int = 0x8664) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("electron.exe", pe(machine, 4096))
        archive.writestr("version", version)
        archive.writestr("resources.pak", b"r" * 100)
        archive.writestr("LICENSE", "MIT")
    data = buffer.getvalue()
    path.write_bytes(data)
    return data


class Fixture:
    """Synthetic checkout + prepared inputs under a Unicode path with spaces."""

    PACKAGES = [("fastapi", "0.141.1", "runtime"), ("numpy", "2.4.6", "runtime"), ("mediapipe", "0.10.35", "cv"),
                ("colorama", "0.4.6", "dev")]

    def __init__(self, root: Path):
        self.root = root
        self.repo = root / UNICODE_DIR / "checkout" / "proctoring"
        self.prep = root / UNICODE_DIR / "подготовка с пробелами"
        self.models = self.prep / "models"
        self.audio = self.models / "audio"
        self.wheels = self.prep / "wheels"
        self.electron = self.prep / "electron cache"
        self.payload = {
            "phone/yolo.onnx": b"phone-model" * 50,
            "attention/face.task": b"face-model" * 40,
            "identity/det.onnx": b"det" * 30,
            "identity/emb.onnx": b"emb" * 30,
        }
        self.audio_model = b"silero" * 20
        self.audio_license = b"MIT License\nCopyright\n"
        self.electron_version = "43.7.5"
        self._write_repo()
        self._write_inputs()

    # -- checkout with pins ------------------------------------------------------------------------
    def _write_repo(self):
        repo = self.repo
        (repo / "backend" / "proctor").mkdir(parents=True)
        shutil.copy(REAL_REPO / "pyproject.toml", repo / "pyproject.toml")
        for module, rel, licence in (("phone", "phone/yolo.onnx", "AGPL-3.0"), ("attention", "attention/face.task", "Apache-2.0")):
            data = self.payload[rel]
            self.write_json(repo / "backend" / "proctor" / module / "models.manifest.json", {
                "model_id": module, "module": module, "file": rel, "sha256": sha(data), "size_bytes": len(data),
                "source_url": f"https://example.invalid/{module}", "license": licence, "version": "1"})
        self.write_json(repo / "backend" / "proctor" / "identity" / "models.manifest.json", {
            "schema": "qorgau.identity.models/1", "module": "identity", "models": [
                {"file": rel, "sha256": sha(self.payload[rel]), "size_bytes": len(self.payload[rel]),
                 "source_url": "https://example.invalid/id", "license": "MIT", "license_url": "https://example.invalid/l"}
                for rel in ("identity/det.onnx", "identity/emb.onnx")]})
        audio = repo / "backend" / "proctor" / "audio"
        audio.mkdir(parents=True)
        (audio / "assets.py").write_text(textwrap.dedent(f'''
            REVISION = "abc123"
            MODEL_NAME = "silero_vad.onnx"
            MODEL_URL = f"https://example.invalid/{{REVISION}}/{{MODEL_NAME}}"
            MODEL_SHA256 = "{sha(self.audio_model)}"
            LICENSE_URL = f"https://example.invalid/{{REVISION}}/LICENSE"
        '''), encoding="utf-8")
        (audio / "prepare.py").write_text('manifest = dict(license="MIT")\n', encoding="utf-8")
        (repo / "acceptance" / "offline").mkdir(parents=True)
        self.electron_zip_bytes = make_electron_zip(self.prep_dir("electron cache") / f"electron-v{self.electron_version}-win32-x64.zip",
                                                    self.electron_version)
        self.write_json(repo / "acceptance" / "offline" / "kit_pins.json", {
            "schema": "adal.offline-kit.pins/1",
            "electron": {self.electron_version: {"artifact": f"electron-v{self.electron_version}-win32-x64.zip",
                                                 "sha256": sha(self.electron_zip_bytes), "license": "MIT",
                                                 "source_url": "https://example.invalid/electron.zip"}},
            "audio": {"LICENSE.silero.txt": {"sha256": sha(self.audio_license), "size_bytes": len(self.audio_license)}}})
        (repo / "desktop").mkdir()
        self.write_json(repo / "desktop" / "package-lock.json", {"packages": {
            "node_modules/electron": {"version": self.electron_version, "license": "MIT"}}})
        (repo / "class-panel").mkdir()
        (repo / "class-panel" / "index.html").write_text("<!doctype html>", encoding="utf-8")
        self.wheel_bytes = {}
        self.wheels.mkdir(parents=True)
        lines = []
        for name, version, _ in self.PACKAGES:
            data = make_wheel(self.wheels / f"{name}-{version}-py3-none-any.whl", name, version)
            self.wheel_bytes[name] = data
            marker = " ; platform_machine == 'AMD64' and sys_platform == 'win32'" if name == "colorama" else \
                " ; (platform_machine == 'x86_64' and sys_platform == 'linux') or (platform_machine == 'AMD64' and sys_platform == 'win32')"
            lines.append(f"{name}=={version}{marker} \\\n    --hash=sha256:{sha(data)}\n    # via qorgau-exam")
        lines.append("linuxonly==1.0 ; sys_platform == 'linux' \\\n    --hash=sha256:" + "0" * 64 + "\n    # via qorgau-exam")
        (repo / "requirements").mkdir()
        (repo / "requirements" / "full.txt").write_text("# synthetic\n" + "\n".join(lines) + "\n", encoding="utf-8")

    def prep_dir(self, name: str) -> Path:
        path = self.prep / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_inputs(self):
        for rel, data in self.payload.items():
            target = self.models / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.audio.mkdir(parents=True, exist_ok=True)
        (self.audio / "silero_vad.onnx").write_bytes(self.audio_model)
        (self.audio / "LICENSE.silero.txt").write_bytes(self.audio_license)
        self.write_json(self.audio / "manifest.json", {"sha256": sha(self.audio_model), "revision": "abc123"})

    @staticmethod
    def write_json(path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def inputs_args(self) -> list[str]:
        return ["--models-dir", str(self.models), "--audio-dir", str(self.audio), "--wheelhouse", str(self.wheels),
                "--electron-zip", str(self.electron)]

    # -- a prepared student PC ---------------------------------------------------------------------
    def prepared_pc(self, python_machine: int = 0x8664, version: str = "3.12.10") -> Path:
        venv = self.repo / ".venv"
        home = self.root / "Python312"
        home.mkdir(exist_ok=True)
        (venv / "Scripts").mkdir(parents=True, exist_ok=True)
        (venv / "Scripts" / "python.exe").write_bytes(pe(python_machine))
        (venv / "pyvenv.cfg").write_text(f"home = {home}\nversion = {version}\n", encoding="utf-8")
        site = venv / "Lib" / "site-packages"
        for name, version_, _ in self.PACKAGES:
            info = site / f"{name}-{version_}.dist-info"
            info.mkdir(parents=True, exist_ok=True)
            (info / "METADATA").write_text(f"Name: {name}\nVersion: {version_}\n", encoding="utf-8")
            module = site / name
            module.mkdir(exist_ok=True)
            (module / "__init__.py").write_text("x = 1\n", encoding="utf-8")
            digest = kit.hashlib.sha256(b"x = 1\n").digest()
            import base64
            encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            (info / "RECORD").write_text(f"{name}/__init__.py,sha256={encoded},6\n{name}-{version_}.dist-info/RECORD,,\n",
                                         encoding="utf-8")
        electron = self.repo / "desktop" / "node_modules" / "electron"
        (electron / "dist").mkdir(parents=True, exist_ok=True)
        self.write_json(electron / "package.json", {"name": "electron", "version": self.electron_version})
        with zipfile.ZipFile(io.BytesIO(self.electron_zip_bytes)) as archive:
            archive.extractall(electron / "dist")
        (electron / "path.txt").write_text("electron.exe", encoding="utf-8")
        for rel in ("dist/main/main.cjs", "dist/preload/preload.cjs", "dist/renderer/index.html"):
            path = self.repo / "desktop" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("built", encoding="utf-8")
        return venv / "Scripts" / "python.exe"


def run_tool(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = kit.main(args)
    return code, out.getvalue(), err.getvalue()


def run_json(args: list[str]) -> tuple[int, dict]:
    code, out, err = run_tool(args + ["--json"])
    try:
        return code, json.loads(out)
    except ValueError:
        raise AssertionError(f"no JSON (code {code}): {out}\n{err}")


def by_id(report: dict) -> dict[str, dict]:
    return {item["id"]: item for item in report["items"]}


def snapshot(*roots: Path) -> dict[str, tuple[int, int, str]]:
    state = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                state[str(path)] = (path.stat().st_size, path.stat().st_mtime_ns, sha(path.read_bytes()))
            elif path.is_dir():
                state[str(path) + os.sep] = (0, 0, "dir")
    return state


class OfflineKitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="adal kit ")
        self.fx = Fixture(Path(self._tmp.name))
        self.base = ["--repo", str(self.fx.repo)]
        self._env = {k: os.environ.get(k) for k in ("QORGAU_MODELS_DIR", "LOCALAPPDATA")}
        os.environ.pop("QORGAU_MODELS_DIR", None)
        os.environ.pop("LOCALAPPDATA", None)

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    # ---------------------------------------------------------------- inventory: positive + diagnostics
    def test_inventory_ready_with_spaces_and_unicode_paths(self):
        code, report = run_json(self.base + ["inventory"] + self.fx.inputs_args())
        self.assertEqual(code, 0, report)
        items = by_id(report)
        self.assertEqual(items["model:phone/yolo.onnx"]["status"], "PASS")
        self.assertEqual(items["wheel:colorama==0.4.6"]["status"], "PASS", "win32-only marker must apply to the target")
        self.assertNotIn("wheel:linuxonly==1.0", items, "linux-only requirement must not be required for Windows")
        self.assertIn("AGPL", items["model:phone/yolo.onnx"]["distribution"])

    def test_missing_required_model_names_file_and_preparation(self):
        (self.fx.models / "phone" / "yolo.onnx").unlink()
        code, out, _ = run_tool(self.base + ["inventory"] + self.fx.inputs_args())
        self.assertEqual(code, 1)
        self.assertIn("[НЕТ] model:phone/yolo.onnx", out)
        self.assertIn(str(self.fx.models / "phone" / "yolo.onnx"), out)
        self.assertIn("proctor.phone.prepare --download", out)
        self.assertIn("до отключения интернета", out)
        self.assertIn("Итог: НЕ ГОТОВО", out)

    def test_truncated_and_bitflipped_models_are_corrupt(self):
        phone = self.fx.models / "phone" / "yolo.onnx"
        phone.write_bytes(phone.read_bytes()[:-1])
        face = self.fx.models / "attention" / "face.task"
        data = bytearray(face.read_bytes())
        data[10] ^= 0xFF
        face.write_bytes(bytes(data))
        code, report = run_json(self.base + ["inventory"] + self.fx.inputs_args())
        self.assertEqual(code, 1)
        items = by_id(report)
        self.assertEqual((items["model:phone/yolo.onnx"]["status"], items["model:phone/yolo.onnx"]["reason"]),
                         ("CORRUPT", "size_mismatch"))
        self.assertEqual((items["model:attention/face.task"]["status"], items["model:attention/face.task"]["reason"]),
                         ("CORRUPT", "hash_mismatch"))

    def test_optional_failure_is_reported_but_never_pass(self):
        (self.fx.models / "identity" / "det.onnx").write_bytes(b"broken")
        shutil.rmtree(self.fx.audio)
        code, out, _ = run_tool(self.base + ["inventory"] + self.fx.inputs_args())
        self.assertEqual(code, 0)
        self.assertIn("[БИТЫЙ, необяз.] model:identity/det.onnx", out)
        self.assertIn("[НЕТ, необяз.] model:audio/silero_vad.onnx", out)
        self.assertIn("это не PASS", out)

    def test_wrong_platform_wheel(self):
        name, version = "numpy", "2.4.6"
        (self.fx.wheels / f"{name}-{version}-py3-none-any.whl").unlink()
        linux = self.fx.wheels / f"{name}-{version}-cp312-cp312-manylinux_2_28_x86_64.whl"
        data = make_wheel(linux, name, version)
        req = self.fx.repo / "requirements" / "full.txt"
        req.write_text(req.read_text(encoding="utf-8").replace(sha(self.fx.wheel_bytes[name]), sha(data)), encoding="utf-8")
        code, report = run_json(self.base + ["inventory"] + self.fx.inputs_args())
        self.assertEqual(code, 1)
        self.assertEqual(by_id(report)[f"wheel:{name}=={version}"]["status"], "WRONG_PLATFORM")

    def test_wheel_not_matching_lock_hash(self):
        make_wheel(self.fx.wheels / "fastapi-0.141.1-py3-none-any.whl", "fastapi", "0.141.1", license_expr="BSD")
        code, report = run_json(self.base + ["inventory"] + self.fx.inputs_args())
        self.assertEqual(code, 1)
        self.assertEqual(by_id(report)["wheel:fastapi==0.141.1"]["reason"], "hash_mismatch")

    def test_unconfirmed_licence_or_source_is_blocked(self):
        manifest = self.fx.repo / "backend" / "proctor" / "attention" / "models.manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["license"] = ""
        manifest.write_text(json.dumps(data), encoding="utf-8")
        code, report = run_json(self.base + ["inventory"] + self.fx.inputs_args())
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(by_id(report)["model:attention/face.task"]["reason"], "pin_unconfirmed")

    def test_wheel_without_licence_metadata_is_blocked(self):
        data = make_wheel(self.fx.wheels / "fastapi-0.141.1-py3-none-any.whl", "fastapi", "0.141.1", license_expr=None)
        req = self.fx.repo / "requirements" / "full.txt"
        req.write_text(req.read_text(encoding="utf-8").replace(sha(self.fx.wheel_bytes["fastapi"]), sha(data)), encoding="utf-8")
        code, report = run_json(self.base + ["inventory"] + self.fx.inputs_args())
        self.assertEqual(code, 2)
        self.assertEqual(by_id(report)["wheel:fastapi==0.141.1"]["reason"], "license_unconfirmed")

    def test_electron_archive_for_wrong_architecture(self):
        zip_path = self.fx.electron / f"electron-v{self.fx.electron_version}-win32-x64.zip"
        data = make_electron_zip(zip_path, self.fx.electron_version, machine=0xAA64)
        pins = self.fx.repo / "acceptance" / "offline" / "kit_pins.json"
        pins.write_text(pins.read_text(encoding="utf-8").replace(sha(self.fx.electron_zip_bytes), sha(data)), encoding="utf-8")
        code, report = run_json(self.base + ["inventory"] + self.fx.inputs_args())
        self.assertEqual(by_id(report)[f"electron:electron-v{self.fx.electron_version}-win32-x64.zip"]["status"], "WRONG_PLATFORM")
        self.assertEqual(code, 0, "electron archive is an optional backup; the failure is listed, not hidden")

    def test_relative_requirements_path_and_runtime_subset(self):
        cwd = os.getcwd()
        try:
            os.chdir(self.fx.repo)
            code, report = run_json(self.base + ["inventory", "--role", "teacher", "--wheelhouse", str(self.fx.wheels),
                                                 "--requirements", "requirements/full.txt"])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0, report)
        self.assertFalse(any(i.startswith(("model:", "electron:")) for i in by_id(report)))

    def test_malformed_requirements_are_blocked_not_guessed(self):
        req = self.fx.repo / "requirements" / "full.txt"
        req.write_text("fastapi>=0.1 --hash=sha256:" + "0" * 64 + "\n", encoding="utf-8")
        code, _, err = run_tool(self.base + ["inventory"] + self.fx.inputs_args())
        self.assertEqual(code, 2)
        self.assertIn("BLOCKED", err)

    def test_missing_venv_is_reported_once(self):
        code, out, _ = run_tool(self.base + ["check-pc", "--role", "teacher"])
        self.assertEqual(code, 1)
        self.assertEqual(out.count("Как исправить: py -3.12 -m venv"), 2, out)  # interpreter + one package line

    def test_only_windows_target_is_accepted(self):
        code, _, err = run_tool(self.base + ["inventory", "--target", "linux-x64"] + self.fx.inputs_args())
        self.assertEqual(code, 64)
        self.assertIn("windows-x64", err)

    # ---------------------------------------------------------------- build / verify
    def build(self) -> Path:
        out = Path(self._tmp.name) / "Флешка E" / "Adal kit"
        code, out_text, err = run_tool(self.base + ["build", "--out", str(out)] + self.fx.inputs_args())
        self.assertEqual(code, 0, out_text + err)
        return out

    def test_build_contains_only_listed_files_and_no_source_paths(self):
        out = self.build()
        manifest = json.loads((out / kit.MANIFEST_NAME).read_text(encoding="utf-8"))
        files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
        listed = sorted(i["bundle_path"] for i in manifest["items"] if i.get("included"))
        self.assertEqual(files, sorted(listed + [kit.MANIFEST_NAME, kit.SUMS_NAME]))
        text = (out / kit.MANIFEST_NAME).read_text(encoding="utf-8")
        self.assertNotIn(str(self.fx.prep), text, "absolute paths of the preparing PC must not be recorded")
        self.assertNotIn("подготовка", text)
        for line in (out / kit.SUMS_NAME).read_text(encoding="utf-8").splitlines():
            digest, rel = line.split("  ", 1)
            self.assertEqual(sha((out / rel).read_bytes()), digest)
        self.assertEqual(manifest["target"]["name"], "windows-x64")
        self.assertFalse(any(p.suffix in (".py", ".sqlite", ".db") for p in out.rglob("*")))

    def test_build_and_check_never_write_into_inputs(self):
        before = snapshot(self.fx.repo, self.fx.prep)
        out = self.build()
        kit_before = snapshot(out)
        run_tool(self.base + ["verify", "--kit", str(out)])
        run_tool(self.base + ["check-pc", "--role", "student", "--kit", str(out), "--models-dir", str(self.fx.models)])
        run_tool(self.base + ["inventory"] + self.fx.inputs_args())
        self.assertEqual(before, snapshot(self.fx.repo, self.fx.prep))
        self.assertEqual(kit_before, snapshot(out), "verify/check-pc must not write into the kit")

    def test_build_refuses_unsafe_output_locations(self):
        for out in (self.fx.repo / "kit", self.fx.models / "kit", self.fx.prep):
            code, _, err = run_tool(self.base + ["build", "--out", str(out)] + self.fx.inputs_args())
            self.assertEqual(code, 64, (out, err))
            self.assertFalse((self.fx.repo / "kit").exists())
        busy = Path(self._tmp.name) / "busy"
        busy.mkdir()
        (busy / "old.txt").write_text("x")
        code, _, _ = run_tool(self.base + ["build", "--out", str(busy)] + self.fx.inputs_args())
        self.assertEqual(code, 64)

    def test_build_refused_when_required_missing_and_nothing_created(self):
        (self.fx.models / "attention" / "face.task").unlink()
        out = Path(self._tmp.name) / "kit"
        code, out_text, _ = run_tool(self.base + ["build", "--out", str(out)] + self.fx.inputs_args())
        self.assertEqual(code, 1)
        self.assertIn("сборка НЕ выполнена", out_text)
        self.assertFalse(out.exists())
        self.assertFalse(out.with_name("kit.incomplete").exists())

    def test_verify_detects_damage_after_copy(self):
        out = self.build()
        code, report = run_json(self.base + ["verify", "--kit", str(out)])
        self.assertEqual(code, 0, report)
        phone = out / "models" / "phone" / "yolo.onnx"
        data = bytearray(phone.read_bytes())
        data[-1] ^= 1
        phone.write_bytes(bytes(data))
        wheel = next((out / "wheels").glob("numpy-*.whl"))
        wheel.unlink()
        (out / "models" / "stray.onnx").write_bytes(b"?")
        code, report = run_json(self.base + ["verify", "--kit", str(out)])
        self.assertEqual(code, 1)
        items = by_id(report)
        self.assertEqual(items["model:phone/yolo.onnx"]["reason"], "hash_mismatch")
        self.assertEqual(items["wheel:numpy==2.4.6"]["status"], "MISSING")
        self.assertEqual(items["kit:extra:models/stray.onnx"]["status"], "WARN")

    def test_verify_rejects_manifest_edited_to_match_a_bad_file(self):
        out = self.build()
        phone = out / "models" / "phone" / "yolo.onnx"
        phone.write_bytes(b"tampered")
        manifest_path = out / kit.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["items"]:
            if item["id"] == "model:phone/yolo.onnx":
                item["sha256"], item["size_bytes"] = sha(b"tampered"), len(b"tampered")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        code, report = run_json(self.base + ["verify", "--kit", str(out)])
        self.assertEqual(code, 1)
        self.assertEqual(by_id(report)["model:phone/yolo.onnx"]["reason"], "pin_changed")

    def test_verify_rejects_pinned_wheel_for_wrong_platform(self):
        out = self.build()
        name, version = "numpy", "2.4.6"
        linux = out / "wheels" / f"{name}-{version}-cp312-cp312-manylinux_2_28_x86_64.whl"
        data = make_wheel(linux, name, version)
        req = self.fx.repo / "requirements" / "full.txt"
        req.write_text(req.read_text(encoding="utf-8").replace(
            f"--hash=sha256:{sha(self.fx.wheel_bytes[name])}",
            f"--hash=sha256:{sha(self.fx.wheel_bytes[name])} --hash=sha256:{sha(data)}"), encoding="utf-8")
        manifest_path = out / kit.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["items"]:
            if item["id"] == f"wheel:{name}=={version}":
                (out / item["bundle_path"]).unlink()
                item.update(bundle_path=f"wheels/{linux.name}", sha256=sha(data), size_bytes=len(data))
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        code, report = run_json(self.base + ["verify", "--kit", str(out)])
        self.assertEqual((code, by_id(report)[f"wheel:{name}=={version}"]["status"]), (1, "WRONG_PLATFORM"))

    def test_build_refuses_drive_root_like_output(self):
        code, _, err = run_tool(self.base + ["build", "--out", os.path.abspath(os.sep)] + self.fx.inputs_args())
        self.assertEqual(code, 64, err)

    def test_verify_kit_built_for_other_pins(self):
        out = self.build()
        manifest = self.fx.repo / "backend" / "proctor" / "phone" / "models.manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["sha256"] = "1" * 64
        manifest.write_text(json.dumps(data), encoding="utf-8")
        code, report = run_json(self.base + ["verify", "--kit", str(out)])
        self.assertEqual(code, 1)
        self.assertEqual(by_id(report)["model:phone/yolo.onnx"]["reason"], "pin_changed")

    def test_verify_missing_or_foreign_manifest(self):
        empty = Path(self._tmp.name) / "empty kit"
        empty.mkdir()
        code, report = run_json(self.base + ["verify", "--kit", str(empty)])
        self.assertEqual((code, by_id(report)["kit:manifest"]["status"]), (1, "MISSING"))
        (empty / kit.MANIFEST_NAME).write_text(json.dumps({"schema": kit.SCHEMA, "target": {"name": "linux-x64"}}))
        code, report = run_json(self.base + ["verify", "--kit", str(empty)])
        self.assertEqual((code, by_id(report)["kit:manifest"]["status"]), (1, "CORRUPT"))

    # ---------------------------------------------------------------- check-pc
    def check_pc(self, *extra: str) -> tuple[int, dict]:
        return run_json(self.base + ["check-pc", "--role", "student", "--models-dir", str(self.fx.models),
                                     "--audio-dir", str(self.fx.audio), *extra])

    def test_check_pc_prepared_student_ready(self):
        self.fx.prepared_pc()
        out = self.build()
        code, report = self.check_pc("--kit", str(out), "--deep")
        self.assertEqual(code, 0, json.dumps(report, ensure_ascii=False, indent=1))
        items = by_id(report)
        self.assertEqual(items["electron:binary"]["status"], "PASS")
        self.assertEqual(items["electron:dist-vs-kit"]["status"], "PASS")
        self.assertIn("RECORD", items["package:numpy"]["detail"])

    def test_check_pc_teacher_does_not_need_models_or_electron(self):
        self.fx.prepared_pc()
        shutil.rmtree(self.fx.models)
        code, report = run_json(self.base + ["check-pc", "--role", "teacher"])
        self.assertEqual(code, 0, report)
        self.assertFalse(any(i.startswith(("model:", "electron:")) for i in by_id(report)))

    def test_check_pc_wrong_python(self):
        self.fx.prepared_pc(python_machine=0xAA64)
        code, report = self.check_pc()
        self.assertEqual((code, by_id(report)["python:interpreter"]["status"]), (1, "WRONG_PLATFORM"))
        self.fx.prepared_pc(version="3.13.1")
        code, report = self.check_pc()
        self.assertEqual(by_id(report)["python:interpreter"]["reason"], "wrong_python")

    def test_check_pc_venv_copied_from_another_pc(self):
        self.fx.prepared_pc()
        shutil.rmtree(Path(self._tmp.name) / "Python312")
        code, report = self.check_pc()
        self.assertEqual((code, by_id(report)["python:interpreter"]["reason"]), (1, "venv_moved"))

    def test_check_pc_package_version_and_record(self):
        python = self.fx.prepared_pc()
        site = python.parents[1] / "Lib" / "site-packages"
        (site / "fastapi-0.141.1.dist-info" / "METADATA").write_text("Name: fastapi\nVersion: 0.140.0\n")
        (site / "numpy" / "__init__.py").write_text("tampered\n")
        code, report = self.check_pc()
        self.assertEqual(by_id(report)["package:fastapi"]["reason"], "version_mismatch")
        self.assertEqual(by_id(report)["package:numpy"]["status"], "PASS", "fast mode does not hash installed files")
        code, report = self.check_pc("--deep")
        self.assertEqual(by_id(report)["package:numpy"]["reason"], "record_mismatch")
        self.assertEqual(code, 1)

    def test_check_pc_electron_states(self):
        self.fx.prepared_pc()
        base = self.fx.repo / "desktop" / "node_modules" / "electron"
        (base / "path.txt").unlink()
        code, report = self.check_pc()
        self.assertEqual(code, 0)
        self.assertEqual(by_id(report)["electron:path.txt"]["reason"], "implicit_download_trap")
        (base / "dist" / "version").write_text("42.0.0")
        code, report = self.check_pc()
        self.assertEqual((code, by_id(report)["electron:binary"]["reason"]), (1, "dist_version_mismatch"))
        (base / "dist" / "electron.exe").write_bytes(b"\x7fELF" + b"\0" * 100)
        code, report = self.check_pc()
        self.assertEqual(by_id(report)["electron:binary"]["status"], "WRONG_PLATFORM")

    def test_check_pc_electron_dist_differs_from_kit(self):
        self.fx.prepared_pc()
        out = self.build()
        (self.fx.repo / "desktop" / "node_modules" / "electron" / "dist" / "resources.pak").write_bytes(b"r" * 99)
        code, report = self.check_pc("--kit", str(out))
        self.assertEqual((code, by_id(report)["electron:dist-vs-kit"]["reason"]), (1, "dist_mismatch"))

    def test_check_pc_models_dir_from_environment_and_non_ascii_warning(self):
        self.fx.prepared_pc()
        os.environ["QORGAU_MODELS_DIR"] = str(self.fx.models)
        code, report = run_json(self.base + ["check-pc", "--role", "student", "--audio-dir", str(self.fx.audio)])
        self.assertEqual(code, 0, report)
        items = by_id(report)
        self.assertIn("QORGAU_MODELS_DIR", items["model:phone/yolo.onnx"]["detail"])
        self.assertEqual(items["models:path-ascii"]["status"], "WARN")
        os.environ.pop("QORGAU_MODELS_DIR")
        code, report = run_json(self.base + ["check-pc", "--role", "student"])
        self.assertEqual(code, 1, "default proctoring/models is empty in the fixture")
        self.assertIn(str(self.fx.repo / "models"), by_id(report)["model:phone/yolo.onnx"]["detail"])

    def test_check_pc_backend_only_skips_electron_but_not_models(self):
        self.fx.prepared_pc()
        shutil.rmtree(self.fx.repo / "desktop" / "node_modules")
        shutil.rmtree(self.fx.repo / "desktop" / "dist")
        code, report = self.check_pc("--backend-only")
        self.assertEqual(code, 0, report)
        self.assertFalse(any(i.startswith(("electron:", "build:")) for i in by_id(report)))
        (self.fx.models / "phone" / "yolo.onnx").unlink()
        code, report = self.check_pc("--backend-only")
        self.assertEqual(code, 1)
        code, _, _ = run_tool(self.base + ["check-pc", "--role", "teacher", "--backend-only"])
        self.assertEqual(code, 64)

    def test_check_pc_missing_build(self):
        self.fx.prepared_pc()
        (self.fx.repo / "desktop" / "dist" / "renderer" / "index.html").unlink()
        code, report = self.check_pc()
        self.assertEqual((code, by_id(report)["build:dist/renderer/index.html"]["status"]), (1, "MISSING"))

    def test_report_file_is_never_written_into_kit(self):
        out = self.build()
        code, _, err = run_tool(self.base + ["verify", "--kit", str(out), "--report", str(out / "r.json")])
        self.assertEqual(code, 64)
        self.assertFalse((out / "r.json").exists())


class RealCheckoutPins(unittest.TestCase):
    """The committed pins of this checkout are well-formed (no weights needed)."""

    def test_model_pins_are_confirmed(self):
        pins = kit.load_model_pins(REAL_REPO, kit.load_kit_pins(REAL_REPO))
        self.assertEqual({p.module for p in pins}, {"phone", "attention", "identity", "audio"})
        for pin in pins:
            self.assertEqual(pin.problems, [], pin)

    def test_requirements_resolve_for_windows(self):
        reqs = kit.parse_requirements(REAL_REPO / "requirements" / "full.txt")
        kit.classify_requirements(REAL_REPO, reqs)
        groups = {kit.norm_name(r.name): r.group for r in reqs}
        self.assertTrue(all(r.hashes for r in reqs))
        self.assertTrue(all(r.applies for r in reqs), "every pinned package applies to win32/AMD64")
        self.assertEqual((groups["fastapi"], groups["mediapipe"], groups["certifi"], groups["pytest"], groups["colorama"]),
                         ("runtime", "cv", "cv", "dev", "dev"))

    def test_electron_pin_matches_lockfile(self):
        pin = kit.load_electron_pin(REAL_REPO, kit.load_kit_pins(REAL_REPO))
        self.assertEqual(pin.problems, [])
        self.assertRegex(pin.sha256, r"^[0-9a-f]{64}$")

    def test_marker_parser_fails_closed(self):
        self.assertTrue(kit.evaluate_marker("(platform_machine == 'AMD64' and sys_platform == 'win32')"))
        self.assertFalse(kit.evaluate_marker("sys_platform == 'linux'"))
        for bad in ("python_version >= '3.8'", "extra == 'x' and", "unknown_var == '1'"):
            with self.assertRaises(ValueError):
                kit.evaluate_marker(bad)


class NoNetworkNoProcess(unittest.TestCase):
    """The tool must not import network/process modules and must not trigger such events at run time."""

    FORBIDDEN_MODULES = {"socket", "ssl", "urllib", "http", "ftplib", "smtplib", "subprocess", "asyncio", "requests",
                         "httpx", "ctypes", "webbrowser", "multiprocessing", "importlib"}

    def test_source_imports(self):
        import ast
        tree = ast.parse(TOOL.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                self.assertNotRegex(node.attr, r"^(system|popen|exec\w*|spawn\w*|startfile|fork\w*)$")
        self.assertFalse(imported & self.FORBIDDEN_MODULES, imported & self.FORBIDDEN_MODULES)

    def test_runtime_audit_hook_sees_no_network_or_process(self):
        with tempfile.TemporaryDirectory(prefix="adal audit ") as tmp:
            fx = Fixture(Path(tmp))
            fx.prepared_pc()
            out = Path(tmp) / "kit out"
            script = textwrap.dedent(f"""
                import sys, json
                events = []
                def hook(event, args):
                    if event.split('.')[0] in ('socket', 'subprocess', 'urllib', 'http', 'ftplib', 'smtplib', 'webbrowser') \\
                            or event in ('os.system', 'os.exec', 'os.posix_spawn', 'os.spawn', 'os.startfile', 'os.fork',
                                         'ctypes.dlopen', 'ctypes.dlsym'):
                        events.append(event)
                        raise RuntimeError('forbidden ' + event)
                sys.addaudithook(hook)
                sys.path.insert(0, {str(TOOL.parent)!r})
                import adal_offline_kit as kit
                repo = ['--repo', {str(fx.repo)!r}]
                inputs = {fx.inputs_args()!r}
                codes = [kit.main(repo + ['plan']),
                         kit.main(repo + ['inventory', '--json'] + inputs),
                         kit.main(repo + ['build', '--out', {str(out)!r}] + inputs),
                         kit.main(repo + ['verify', '--kit', {str(out)!r}]),
                         kit.main(repo + ['check-pc', '--role', 'student', '--deep', '--kit', {str(out)!r},
                                          '--models-dir', {str(fx.models)!r}, '--audio-dir', {str(fx.audio)!r}]),
                         kit.main(repo + ['check-pc', '--role', 'teacher'])]
                print('AUDIT', json.dumps({{'codes': codes, 'events': events}}))
            """)
            env = {k: v for k, v in os.environ.items() if k not in ("QORGAU_MODELS_DIR", "LOCALAPPDATA")}
            env.update(HTTPS_PROXY="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", PYTHONIOENCODING="utf-8")
            result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True,
                                    encoding="utf-8", env=env, timeout=300)
            line = next((l for l in result.stdout.splitlines() if l.startswith("AUDIT ")), None)
            self.assertIsNotNone(line, result.stdout[-2000:] + result.stderr[-2000:])
            audit = json.loads(line[6:])
            self.assertEqual(audit["events"], [])
            self.assertEqual(audit["codes"], [0, 0, 0, 0, 0, 0], result.stdout[-3000:])

    def test_legacy_console_encoding_does_not_crash(self):
        with tempfile.TemporaryDirectory(prefix="adal қ ") as tmp:
            fx = Fixture(Path(tmp))
            (fx.models / "phone" / "yolo.onnx").unlink()
            env = {k: v for k, v in os.environ.items() if k not in ("QORGAU_MODELS_DIR",)}
            env["PYTHONIOENCODING"] = "cp1251"  # -I/-E would ignore it; cwd is an empty temp dir instead
            result = subprocess.run([sys.executable, "-s", str(TOOL), "--repo", str(fx.repo), "inventory",
                                     *fx.inputs_args()], capture_output=True, env=env, timeout=120, cwd=tmp)
            self.assertEqual(result.returncode, 1, result.stderr.decode("cp1251", "replace"))
            text = result.stdout.decode("cp1251")
            self.assertIn("[НЕТ] model:phone/yolo.onnx", text)
            self.assertIn("\\u049b", text, "Kazakh letter is escaped, not a crash")


class LauncherAudit(unittest.TestCase):
    def test_current_launchers_are_scanned(self):
        items = {i.id: i for i in kit.launcher_audit(REAL_REPO, "student")}
        student = items.get("launcher:acceptance/classroom/Start-Student.ps1")
        self.assertIsNotNone(student)
        self.assertEqual(student.status, "PASS", student.detail)  # messages that mention npm ci are not commands
        packaging = items.get("launcher:packaging/launch-windows.ps1")
        if packaging is not None:
            self.assertEqual(packaging.status, "WARN")
            self.assertIn("electron.cmd", packaging.detail)


if __name__ == "__main__":
    unittest.main()
