"""Negative and positive checks of adal_offline_kit.py. No network, camera, Electron or real weights.

Run with any Python >= 3.11 (the target interpreter is 3.12); no pytest dependency:
    python -B proctoring/acceptance/offline/tests/test_offline_kit.py
Synthetic fixtures (fake weights, fake wheels, fake Electron zip) live in a temporary directory whose path contains
spaces and non-ASCII characters; they test the tool's logic only and prove nothing about the real Adal artifacts.
Every CLI run goes through a subprocess with a tripwire sitecustomize that records and blocks any socket,
DNS lookup, subprocess or urllib call.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
import zipfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
OFFLINE = HERE.parent
TOOL = OFFLINE / "adal_offline_kit.py"
REAL_ROOT = OFFLINE.parents[1]
REAL_SPEC = OFFLINE / "kit_spec.json"
sys.path.insert(0, str(OFFLINE))
import adal_offline_kit as kit  # noqa: E402

FORBIDDEN_IMPORTS = {"socket", "ssl", "http", "urllib", "subprocess", "requests", "ftplib", "smtplib", "xmlrpc",
                     "asyncio", "multiprocessing", "ctypes", "platform", "webbrowser", "telnetlib"}

TRIPWIRE = textwrap.dedent('''
    import os, socket, subprocess, sys
    _log = os.environ.get("ADAL_TRIPWIRE_LOG")
    _protected = [os.path.realpath(p) for p in os.environ.get("ADAL_TRIPWIRE_PROTECT", "").split(os.pathsep) if p]
    def _write(line):
        if _log:
            with open(_log, "a", encoding="utf-8") as stream:
                stream.write(line + "\\n")
    def _record(what):
        _write(what)
        raise PermissionError("tripwire: " + what)
    def _inside(path):
        try:
            real = os.path.realpath(os.fsdecode(path))
        except (TypeError, ValueError):
            return False
        return any(real == p or real.startswith(p + os.sep) for p in _protected)
    _NET = ("socket.", "urllib.Request", "http.client", "ftplib.", "smtplib.", "webbrowser.")
    _PROC = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile", "os.fork",
             "_winapi.CreateProcess", "pty.spawn")
    _MUTATE = ("os.remove", "os.unlink", "os.rename", "os.replace", "os.rmdir", "os.mkdir", "os.chmod", "os.utime",
               "os.truncate", "shutil.copyfile", "shutil.copytree", "shutil.rmtree", "shutil.move", "os.symlink", "os.link")
    _busy = False
    def _hook(event, args):
        global _busy
        if _busy:
            return
        _busy = True
        try:
            if event.startswith(_NET) and event not in ("socket.__new__",):
                _write("audit " + event)
            elif event.startswith(_PROC):
                _write("audit " + event)
            elif event == "open" and _protected and len(args) >= 2 and args[0] is not None and not isinstance(args[0], int):
                mode, flags = args[1], (args[2] if len(args) > 2 else 0)
                writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \\
                          (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
                if writing and _inside(args[0]):
                    _write("audit write-open " + os.fsdecode(args[0]))
            elif event.startswith(_MUTATE) and _protected and args:
                paths = [a for a in args if isinstance(a, (str, bytes, os.PathLike))]
                if event in ("shutil.copyfile", "shutil.copytree"):
                    paths = paths[1:2]  # reading the source is allowed; only the destination is written
                if any(_inside(a) for a in paths):
                    _write("audit " + event + " " + repr([os.fsdecode(a) for a in paths]))
        finally:
            _busy = False
    _write("loaded")
    sys.addaudithook(_hook)
    socket.socket.connect = lambda self, *a, **k: _record("socket.connect %r" % (a,))
    socket.socket.connect_ex = lambda self, *a, **k: _record("socket.connect_ex %r" % (a,))
    socket.socket.sendto = lambda self, *a, **k: _record("socket.sendto")
    socket.create_connection = lambda *a, **k: _record("socket.create_connection %r" % (a,))
    socket.getaddrinfo = lambda *a, **k: _record("socket.getaddrinfo %r" % (a[:2],))
    socket.gethostbyname = lambda *a, **k: _record("socket.gethostbyname")
    subprocess.Popen.__init__ = lambda self, *a, **k: _record("subprocess %r" % (a[:1],))
    os.system = lambda *a, **k: _record("os.system")
    for _name in ("execv", "execve", "execvp", "execvpe", "spawnv", "spawnve", "posix_spawn", "posix_spawnp", "startfile"):
        if hasattr(os, _name):
            setattr(os, _name, (lambda n: (lambda *a, **k: _record("os." + n)))(_name))
    try:
        import urllib.request
        urllib.request.urlopen = lambda *a, **k: _record("urllib.urlopen")
    except Exception:
        pass
''')


# ------------------------------------------------------------------------------------------- fixtures


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sri(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def pe_header(machine: int) -> bytes:
    head = bytearray(b"MZ" + b"\0" * 0x3E)
    head[0x3C:0x40] = (0x80).to_bytes(4, "little")
    head += b"\0" * (0x80 - len(head))
    head += b"PE\0\0" + machine.to_bytes(2, "little") + b"\0" * 64
    return bytes(head)


def make_wheel(path: Path, name: str, version: str, license_expr: str | None = "MIT") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        meta = f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n"
        if license_expr:
            meta += f"License-Expression: {license_expr}\n"
        archive.writestr(f"{name}-{version}.dist-info/METADATA", meta)
        archive.writestr(f"{name}-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        archive.writestr(f"{name}/__init__.py", "")
    data = buffer.getvalue()
    path.write_bytes(data)
    return data


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def make_tgz(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def cacache_put(cache: Path, data: bytes) -> str:
    integrity = sri(data)
    target = kit.cacache_content_path(cache, integrity)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return integrity


class Fixture:
    """A synthetic proctoring/ root plus prepared inputs, under a path with spaces and Unicode."""

    ELECTRON = "1.2.3"

    def __init__(self, base: Path):
        self.base = base
        self.root = base / "репозиторий Adal" / "proctoring"
        self.inputs = base / "Подготовлено заранее ✓"
        self.models = self.inputs / "модели с пробелами"
        self.wheels = self.inputs / "wheels"
        self.npm_cache = self.inputs / "npm cache"
        self.out_parent = base / "Выход комплект"
        self.out_parent.mkdir(parents=True)
        self.spec = self.root / "acceptance" / "offline" / "kit_spec.json"
        self._root()

    def write(self, relative: str, data: bytes | str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def model(self, relative: str, data: bytes) -> None:
        path = self.models / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _root(self) -> None:
        self.write("pyproject.toml", "[project]\nname='fake'\n")
        self.spec.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REAL_SPEC, self.spec)
        # --- models (fake bytes; hashes are computed here, never taken from the tool)
        self.phone, self.face = b"phone-weights" * 100, b"face-task" * 50
        self.det, self.emb, self.vad = b"yunet" * 10, b"sface" * 30, b"silero" * 20
        self.write("backend/proctor/phone/models.manifest.json", json.dumps({
            "model_id": "fake-phone", "module": "phone", "file": "phone/fake.onnx", "sha256": sha(self.phone),
            "size_bytes": len(self.phone), "source_url": "https://example.invalid/fake.onnx", "license": "AGPL-3.0"}))
        self.write("backend/proctor/attention/models.manifest.json", json.dumps({
            "model_id": "fake-face", "module": "attention", "file": "attention/face.task", "sha256": sha(self.face),
            "size_bytes": len(self.face), "source_url": "https://example.invalid/face.task", "license": "Apache-2.0"}))
        self.write("backend/proctor/identity/models.manifest.json", json.dumps({"schema": "x", "module": "identity", "models": [
            {"model_id": "fake-det", "file": "identity/det.onnx", "sha256": sha(self.det), "size_bytes": len(self.det),
             "source_url": "https://example.invalid/det", "license": "MIT", "license_url": "https://example.invalid/l"},
            {"model_id": "fake-emb", "file": "identity/emb.onnx", "sha256": sha(self.emb), "size_bytes": len(self.emb),
             "source_url": "https://example.invalid/emb", "license": "Apache-2.0", "license_url": "https://example.invalid/l"}]}))
        self.revision = "a" * 40
        self.write("backend/proctor/audio/assets.py", textwrap.dedent(f'''\
            REVISION = "{self.revision}"
            MODEL_NAME = "silero_vad.onnx"
            MODEL_URL = f"https://example.invalid/{{REVISION}}/{{MODEL_NAME}}"
            MODEL_SHA256 = "{sha(self.vad)}"
            LICENSE_URL = f"https://example.invalid/{{REVISION}}/LICENSE"
            '''))
        self.model("phone/fake.onnx", self.phone)
        self.model("attention/face.task", self.face)
        self.model("identity/det.onnx", self.det)
        self.model("identity/emb.onnx", self.emb)
        self.model("audio/silero_vad.onnx", self.vad)
        self.model("audio/manifest.json", json.dumps({"sha256": sha(self.vad), "revision": self.revision}).encode())
        self.model("audio/LICENSE.silero.txt", b"MIT License\n")
        # --- python wheels + hash-pinned requirements + uv.lock
        self.wheels.mkdir(parents=True)
        win = make_wheel(self.wheels / "fakepkg-1.0-cp312-cp312-win_amd64.whl", "fakepkg", "1.0")
        lin = make_wheel(self.base / "fakepkg-1.0-cp312-cp312-manylinux_2_17_x86_64.whl", "fakepkg", "1.0")
        pure = make_wheel(self.wheels / "purepkg-2.0-py3-none-any.whl", "purepkg", "2.0", None)
        wonly = make_wheel(self.wheels / "winonly-0.1-py3-none-win_amd64.whl", "winonly", "0.1", "BSD-3-Clause")
        lonly = make_wheel(self.base / "linuxonly-0.1-py3-none-any.whl", "linuxonly", "0.1")
        self.linux_wheel = self.base / "fakepkg-1.0-cp312-cp312-manylinux_2_17_x86_64.whl"
        both = "(platform_machine == 'x86_64' and sys_platform == 'linux') or (platform_machine == 'AMD64' and sys_platform == 'win32')"
        full = (f"fakepkg==1.0 ; {both} \\\n    --hash=sha256:{sha(win)} \\\n    --hash=sha256:{sha(lin)}\n"
                f"purepkg==2.0 \\\n    --hash=sha256:{sha(pure)}\n    # via fakepkg\n"
                f"winonly==0.1 ; platform_machine == 'AMD64' and sys_platform == 'win32' \\\n    --hash=sha256:{sha(wonly)}\n"
                f"linuxonly==0.1 ; sys_platform == 'linux' \\\n    --hash=sha256:{sha(lonly)}\n")
        self.write("requirements/full.txt", "# generated\n" + full)
        self.write("requirements/runtime.txt", f"purepkg==2.0 \\\n    --hash=sha256:{sha(pure)}\n")

        def lock(name, version, files):
            rows = ",\n".join(f'  {{ url = "https://files.example.invalid/{f}", hash = "sha256:{sha(d)}", size = {len(d)} }}'
                              for f, d in files)
            return f'[[package]]\nname = "{name}"\nversion = "{version}"\nwheels = [\n{rows},\n]\n'
        self.write("uv.lock", "version = 1\n\n" + "\n".join([
            lock("fakepkg", "1.0", [("fakepkg-1.0-cp312-cp312-manylinux_2_17_x86_64.whl", lin),
                                    ("fakepkg-1.0-cp312-cp312-win_amd64.whl", win)]),
            lock("purepkg", "2.0", [("purepkg-2.0-py3-none-any.whl", pure)]),
            lock("winonly", "0.1", [("winonly-0.1-py3-none-win_amd64.whl", wonly)]),
            lock("linuxonly", "0.1", [("linuxonly-0.1-py3-none-any.whl", lonly)])]))
        # --- Electron zip + locked electron tarball (checksums.json) + npm cache
        self.zip_name = f"electron-v{self.ELECTRON}-win32-x64.zip"
        self.electron_zip = self.inputs / "electron download" / self.zip_name
        self.electron_zip.parent.mkdir(parents=True)
        self.zip_bytes = make_zip({"electron.exe": pe_header(0x8664), "version": self.ELECTRON.encode(),
                                   "LICENSE": b"MIT", "LICENSES.chromium.html": b"<html></html>"})
        self.electron_zip.write_bytes(self.zip_bytes)
        self.set_checksums({self.zip_name: sha(self.zip_bytes)})

    def set_checksums(self, checksums: dict[str, str]) -> None:
        tgz = make_tgz({"package/package.json": json.dumps({"version": self.ELECTRON}).encode(),
                        "package/checksums.json": json.dumps(checksums).encode()})
        if self.npm_cache.exists():
            shutil.rmtree(self.npm_cache)
        electron_integrity = cacache_put(self.npm_cache, tgz)
        plain, winx = b"plain-package", b"esbuild-win32-x64"
        lock = {"lockfileVersion": 3, "packages": {
            "": {"name": "desktop"},
            "node_modules/electron": {"version": self.ELECTRON, "resolved": "https://registry.invalid/electron.tgz",
                                      "integrity": electron_integrity, "license": "MIT", "dev": True},
            "node_modules/plain": {"version": "1.0.0", "resolved": "https://registry.invalid/plain.tgz",
                                   "integrity": cacache_put(self.npm_cache, plain), "license": "ISC"},
            "node_modules/@esb/win32-x64": {"version": "0.1.0", "os": ["win32"], "cpu": ["x64"], "optional": True,
                                            "integrity": cacache_put(self.npm_cache, winx), "license": "MIT"},
            "node_modules/@esb/linux-x64": {"version": "0.1.0", "os": ["linux"], "cpu": ["x64"], "optional": True,
                                            "integrity": sri(b"never-needed-for-windows"), "license": "MIT"}}}
        self.write("desktop/package-lock.json", json.dumps(lock))

    def build_args(self, out: Path, *extra: str) -> list[str]:
        return ["build", "--scenario", "student", "--out", str(out), "--models-dir", str(self.models),
                "--audio-models-dir", str(self.models / "audio"), "--wheels-dir", str(self.wheels),
                "--electron-zip", str(self.electron_zip), "--npm-cache", str(self.npm_cache), *extra]


class ToolTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="adal offline тест ")
        self.base = Path(self.tmp.name)
        self.fx = Fixture(self.base)
        self.tripwire_dir = self.base / "tripwire"
        self.tripwire_dir.mkdir()
        (self.tripwire_dir / "sitecustomize.py").write_text(TRIPWIRE, encoding="utf-8")
        self.tripwire_log = self.base / "tripwire.log"

    def tearDown(self):
        for dirpath, dirs, files in os.walk(self.base):
            for name in dirs + files:
                try:
                    os.chmod(Path(dirpath) / name, stat.S_IRWXU)
                except OSError:
                    pass
        self.tmp.cleanup()

    def run_tool(self, *args: str, root: Path | None = None, env: dict | None = None,
                 protect: list[Path] | None = None) -> subprocess.CompletedProcess:
        """Run the CLI under the tripwire. Default protected (never written) trees: the source root and inputs."""
        if protect is None:
            protect = [self.fx.root, self.fx.inputs]
            if args and args[0] == "check" and "--kit" in args:
                protect.append(Path(args[args.index("--kit") + 1]))
        environment = {k: v for k, v in os.environ.items() if not k.startswith(("QORGAU_", "PYTHON"))}
        environment.update(PYTHONPATH=str(self.tripwire_dir), ADAL_TRIPWIRE_LOG=str(self.tripwire_log),
                           ADAL_TRIPWIRE_PROTECT=os.pathsep.join(str(p) for p in protect), PYTHONIOENCODING="utf-8")
        environment.update(env or {})
        root = root or self.fx.root
        if self.tripwire_log.exists():
            self.tripwire_log.unlink()  # one log per run, so "loaded" proves this very run was guarded
        result = subprocess.run([sys.executable, str(TOOL), "--root", str(root), "--spec", str(self.fx.spec), *args],
                                cwd=str(self.base), env=environment, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=120)
        log = self.tripwire_log.read_text(encoding="utf-8").splitlines() if self.tripwire_log.exists() else []
        self.assertIn("loaded", log, "tripwire sitecustomize was not active; the no-network proof would be void")
        self.assertEqual([line for line in log if line != "loaded"], [],
                         f"network/process attempt during {args[0]}: {log}\n{result.stdout}\n{result.stderr}")
        return result

    def build_ok(self, name: str = "kit ok", *extra: str) -> Path:
        out = self.fx.out_parent / name
        result = self.run_tool(*self.fx.build_args(out, "--accept-distribution", "fake-phone", "--feature", "audio",
                                                   "--feature", "identity", *extra))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ADAL_OFFLINE_BUILD READY", result.stdout)
        return out

    def manifest(self, out: Path) -> dict:
        return json.loads((out / kit.MANIFEST_NAME).read_text(encoding="utf-8"))

    def test_tripwire_catches_network_and_processes(self):
        """The no-network proof is only as good as the tripwire: show that it records real attempts."""
        protected = self.base / "защищено"
        protected.mkdir()
        environment = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        environment.update(PYTHONPATH=str(self.tripwire_dir), ADAL_TRIPWIRE_LOG=str(self.tripwire_log),
                           ADAL_TRIPWIRE_PROTECT=str(protected), PROBE_DIR=str(protected))
        probe = ("import os, shutil, socket, subprocess, urllib.request\n"
                 "for call in (lambda: socket.create_connection(('pypi.org', 443)), lambda: socket.getaddrinfo('pypi.org', 443),\n"
                 "             lambda: socket.socket().connect(('1.1.1.1', 53)),\n"
                 "             lambda: subprocess.run(['npm', 'ci']), lambda: urllib.request.urlopen('https://pypi.org'),\n"
                 "             lambda: open(os.path.join(os.environ['PROBE_DIR'], 'x.txt'), 'w').close(),\n"
                 "             lambda: os.mkdir(os.path.join(os.environ['PROBE_DIR'], 'd'))):\n"
                 "    try:\n        call()\n    except PermissionError:\n        pass\n")
        subprocess.run([sys.executable, "-c", probe], env=environment, timeout=60, check=True)
        log = self.tripwire_log.read_text(encoding="utf-8")
        for expected in ("socket.create_connection", "socket.getaddrinfo", "socket.connect", "subprocess",
                         "urllib.urlopen", "audit write-open", "audit os.mkdir"):
            self.assertIn(expected, log)

    # ----------------------------------------------------------------------------------- positive path

    def test_build_and_check_ready_with_complete_manifest(self):
        before = kit.snapshot_tree(self.fx.inputs)
        out = self.build_ok()
        self.assertEqual(kit.snapshot_tree(self.fx.inputs), before, "build must not modify its inputs")
        manifest = self.manifest(out)
        self.assertTrue(manifest["technical_ready"])
        self.assertEqual(manifest["target"]["id"], "win-x64")
        by_id = {a["id"]: a for a in manifest["artifacts"]}
        for required in ("model:fake-phone", "model:fake-face", "model:fake-det", "model:fake-emb",
                         f"electron:{Fixture.ELECTRON}:win32-x64", f"npm:electron@{Fixture.ELECTRON}",
                         "wheel:fakepkg", "wheel:purepkg", "wheel:winonly"):
            self.assertIn(required, by_id)
            artifact = by_id[required]
            for key in ("size", "sha256", "source", "license", "compatibility", "path"):
                self.assertIn(key, artifact)
            data = (out / artifact["path"]).read_bytes()
            self.assertEqual(sha(data), artifact["sha256"])
            self.assertEqual(len(data), artifact["size"])
        self.assertNotIn("wheel:linuxonly", by_id, "a linux-only requirement must not be bundled for win-x64")
        self.assertEqual(by_id["wheel:fakepkg"]["compatibility"]["tag"], "cp312-cp312-win_amd64")
        self.assertEqual(by_id["wheel:purepkg"]["license"]["status"], "UNCONFIRMED")
        self.assertIn("wheel:purepkg", manifest["distribution"]["license_unconfirmed"])
        self.assertEqual(by_id["wheel:winonly"]["license"]["id"], "BSD-3-Clause")
        self.assertIn("distribution_decision", by_id["model:fake-phone"]["license"])
        self.assertTrue(by_id[f"electron:{Fixture.ELECTRON}:win32-x64"]["path"].startswith("electron-cache/"))
        sums = (out / kit.SUMS_NAME).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(sums), len(manifest["artifacts"]) + 1)
        for line in sums:
            digest, relative = line.split("  ", 1)
            self.assertEqual(sha((out / relative).read_bytes()), digest, relative)
        for forbidden in (".venv", "node_modules", ".git", "data"):
            self.assertFalse(any(part == forbidden for p in out.rglob("*") for part in p.relative_to(out).parts))
        result = self.run_tool("check", "--kit", str(out))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("ADAL_OFFLINE_CHECK READY", result.stdout)

    def test_check_is_read_only_even_on_a_read_only_kit(self):
        out = self.build_ok()
        before_kit, before_root = kit.snapshot_tree(out), kit.snapshot_tree(self.fx.root)
        for dirpath, dirs, files in os.walk(out):
            for name in files:
                os.chmod(Path(dirpath) / name, stat.S_IRUSR)
            os.chmod(dirpath, stat.S_IRUSR | stat.S_IXUSR)
        result = self.run_tool("check", "--kit", str(out), "--json-out", str(self.base / "check report.json"))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(kit.snapshot_tree(out), before_kit)
        self.assertEqual(kit.snapshot_tree(self.fx.root), before_root)
        report = json.loads((self.base / "check report.json").read_text(encoding="utf-8"))
        self.assertFalse(report["network_used"])
        self.assertTrue(report["summary"]["ready"])

    def test_kit_without_decision_refuses_blocked_distribution(self):
        out = self.fx.out_parent / "no decision"
        result = self.run_tool(*self.fx.build_args(out))
        self.assertEqual(result.returncode, 1)
        self.assertIn("[BLOCKED] model:fake-phone", result.stdout)
        self.assertIn("--per-pc phone", result.stdout)
        self.assertIn("--accept-distribution fake-phone", result.stdout)
        self.assertFalse(out.exists(), "a refused build must not leave a kit behind")
        self.assertFalse(Path(str(out) + ".partial").exists())

    def test_per_pc_model_is_left_out_and_recorded(self):
        out = self.fx.out_parent / "per pc"
        result = self.run_tool(*self.fx.build_args(out, "--per-pc", "phone"))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((out / "models/phone/fake.onnx").exists())
        self.assertEqual(self.manifest(out)["per_pc"], ["phone"])
        self.assertIn("phone", (out / kit.README_NAME).read_text(encoding="utf-8"))
        check = self.run_tool("check", "--kit", str(out))
        self.assertEqual(check.returncode, 0, check.stdout)
        self.assertIn("[WARN] model:fake-phone", check.stdout)

    # ------------------------------------------------------------------------- missing / corrupt inputs

    def test_missing_model_is_named_with_preparation_command(self):
        (self.fx.models / "attention/face.task").unlink()
        out = self.fx.out_parent / "missing"
        result = self.run_tool(*self.fx.build_args(out, "--per-pc", "phone"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL] model:fake-face", result.stdout)
        self.assertIn("attention/face.task", result.stdout)
        self.assertIn("proctor.attention.model_tool fetch", result.stdout)
        self.assertFalse(out.exists())

    def test_corrupt_and_truncated_files_fail_build_and_check(self):
        out = self.build_ok()
        model = self.fx.models / "identity/emb.onnx"
        model.write_bytes(b"X" + self.fx.emb[1:])  # same size, different content
        bad = self.run_tool(*self.fx.build_args(self.fx.out_parent / "bad", "--per-pc", "phone", "--feature", "identity"))
        self.assertEqual(bad.returncode, 1)
        self.assertIn("[FAIL] model:fake-emb", bad.stdout)
        self.assertIn("повреждён или другая версия", bad.stdout)
        # corrupt the kit copy: one flipped byte, then truncation
        target = out / "models/attention/face.task"
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        target.write_bytes(b"Y" + self.fx.face[1:])
        report = self.base / "r1.json"
        flipped = self.run_tool("check", "--kit", str(out), "--json-out", str(report))
        self.assertEqual(flipped.returncode, 1)
        self.assertEqual(self.code_of(report, "model:fake-face"), "sha256_mismatch")
        target.write_bytes(self.fx.face[:10])
        truncated = self.run_tool("check", "--kit", str(out), "--json-out", str(report))
        self.assertEqual(truncated.returncode, 1)
        self.assertEqual(self.code_of(report, "model:fake-face"), "size_mismatch")
        self.assertIn("как исправить", truncated.stdout)

    def code_of(self, report: Path, artifact_id: str) -> str:
        rows = json.loads(report.read_text(encoding="utf-8"))["rows"]
        return next(r["code"] for r in rows if r["id"] == artifact_id)

    def test_missing_file_in_kit_and_extra_file(self):
        out = self.build_ok()
        os.remove(out / "models/identity/det.onnx")
        (out / "notes.txt").write_text("personal notes", encoding="utf-8")
        report = self.base / "r.json"
        result = self.run_tool("check", "--kit", str(out), "--json-out", str(report))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.code_of(report, "model:fake-det"), "missing")
        self.assertIn("models/identity/det.onnx", result.stdout)
        self.assertEqual(self.code_of(report, "kit:extra:notes.txt"), "extra_file")

    def test_audio_manifest_must_match_pinned_revision(self):
        (self.fx.models / "audio/manifest.json").write_text(json.dumps({"sha256": sha(self.fx.vad), "revision": "b" * 40}))
        result = self.run_tool(*self.fx.build_args(self.fx.out_parent / "audio", "--per-pc", "phone", "--feature", "audio"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("revision", result.stdout)

    def test_optional_feature_missing_is_warning_unless_requested(self):
        shutil.rmtree(self.fx.models / "identity")
        lenient = self.run_tool(*self.fx.build_args(self.fx.out_parent / "lenient", "--per-pc", "phone"))
        self.assertEqual(lenient.returncode, 0, lenient.stdout)
        self.assertIn("[WARN] model:fake-det", lenient.stdout)
        strict = self.run_tool(*self.fx.build_args(self.fx.out_parent / "strict", "--per-pc", "phone", "--feature", "identity"))
        self.assertEqual(strict.returncode, 1)
        self.assertIn("[FAIL] model:fake-det", strict.stdout)

    # -------------------------------------------------------------------------------- wrong platform

    def test_linux_wheel_is_reported_as_wrong_platform(self):
        os.remove(self.fx.wheels / "fakepkg-1.0-cp312-cp312-win_amd64.whl")
        shutil.copyfile(self.fx.linux_wheel, self.fx.wheels / self.fx.linux_wheel.name)
        result = self.run_tool(*self.fx.build_args(self.fx.out_parent / "linux", "--per-pc", "phone"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL] wheel:fakepkg", result.stdout)
        self.assertIn("manylinux_2_17_x86_64", result.stdout)
        self.assertIn("win_amd64", result.stdout)

    def test_linux_wheel_swapped_into_kit_is_wrong_platform(self):
        out = self.build_ok()
        wheel = out / "wheels/fakepkg-1.0-cp312-cp312-win_amd64.whl"
        os.remove(wheel)
        shutil.copyfile(self.fx.linux_wheel, out / "wheels" / self.fx.linux_wheel.name)
        report = self.base / "r.json"
        result = self.run_tool("check", "--kit", str(out), "--json-out", str(report))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.code_of(report, "wheel:fakepkg"), "wrong_platform")

    def test_electron_zip_for_wrong_architecture(self):
        for machine, name in ((0x14C, "x86"), (0xAA64, "arm64")):
            data = make_zip({"electron.exe": pe_header(machine), "version": Fixture.ELECTRON.encode()})
            self.fx.electron_zip.write_bytes(data)
            self.fx.set_checksums({self.fx.zip_name: sha(data)})  # even a "matching" checksum must not hide it
            result = self.run_tool(*self.fx.build_args(self.fx.out_parent / f"arch {name}", "--per-pc", "phone"))
            self.assertEqual(result.returncode, 1)
            self.assertIn(f"electron.exe={name}", result.stdout)
        elf = make_zip({"electron.exe": b"\x7fELF" + b"\0" * 60, "version": Fixture.ELECTRON.encode()})
        self.fx.electron_zip.write_bytes(elf)
        self.fx.set_checksums({self.fx.zip_name: sha(elf)})
        result = self.run_tool(*self.fx.build_args(self.fx.out_parent / "elf", "--per-pc", "phone"))
        self.assertIn("electron.exe=elf", result.stdout)

    def test_electron_zip_not_matching_locked_checksums(self):
        self.fx.electron_zip.write_bytes(self.fx.zip_bytes + b"tampered")
        result = self.run_tool(*self.fx.build_args(self.fx.out_parent / "tampered", "--per-pc", "phone"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL] electron:", result.stdout)

    def test_electron_without_trust_anchor_is_blocked_not_passed(self):
        shutil.rmtree(self.fx.npm_cache)
        args = [a for a in self.fx.build_args(self.fx.out_parent / "anchor", "--per-pc", "phone")]
        index = args.index("--npm-cache")
        del args[index:index + 2]
        result = self.run_tool(*args)
        self.assertEqual(result.returncode, 1)
        self.assertIn("[BLOCKED] electron:", result.stdout)
        self.assertIn("checksums.json", result.stdout)

    def test_kit_for_other_target_and_stale_kit(self):
        out = self.build_ok()
        manifest_path = out / kit.MANIFEST_NAME
        manifest = self.manifest(out)
        manifest["target"]["id"] = "linux-x64"
        os.chmod(manifest_path, stat.S_IRUSR | stat.S_IWUSR)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        report = self.base / "r.json"
        result = self.run_tool("check", "--kit", str(out), "--json-out", str(report))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.code_of(report, "kit:target"), "wrong_platform")
        out2 = self.build_ok("kit two")
        self.fx.write("backend/proctor/attention/models.manifest.json", json.dumps({
            "model_id": "fake-face", "file": "attention/face.task", "sha256": sha(b"new"), "size_bytes": 3,
            "source_url": "https://example.invalid/face.task", "license": "Apache-2.0"}))
        stale = self.run_tool("check", "--kit", str(out2), "--json-out", str(report))
        self.assertEqual(stale.returncode, 1)
        self.assertEqual(self.code_of(report, "kit:freshness"), "stale_kit")

    # --------------------------------------------------------------------------------- unsafe outputs

    def test_output_guards(self):
        cases = {
            "inside repository": self.fx.root / "kit",
            "inside an input": self.fx.models / "kit",
            "contains an input": self.fx.inputs,
        }
        for label, out in cases.items():
            result = self.run_tool(*self.fx.build_args(out, "--per-pc", "phone"))
            self.assertEqual(result.returncode, 2, label + result.stdout + result.stderr)
            self.assertIn("ОШИБКА ПАРАМЕТРОВ", result.stderr)
        busy = self.fx.out_parent / "busy"
        busy.mkdir()
        (busy / "keep.txt").write_text("x")
        self.assertEqual(self.run_tool(*self.fx.build_args(busy, "--per-pc", "phone")).returncode, 2)
        self.assertEqual((busy / "keep.txt").read_text(), "x")
        out = self.build_ok()
        result = self.run_tool("check", "--kit", str(out), "--json-out", str(out / "report.json"))
        self.assertEqual(result.returncode, 2)
        self.assertFalse((out / "report.json").exists())
        missing = self.run_tool("check", "--kit", str(self.base / "нет такого комплекта"))
        self.assertEqual(missing.returncode, 1)
        self.assertIn(kit.MANIFEST_NAME, missing.stdout)

    # ------------------------------------------------------------------------------ installed state

    def prepare_installed_desktop(self, machine: int = 0x8664, stale: bool = False) -> None:
        desktop = self.fx.root / "desktop"
        electron = desktop / "node_modules" / "electron"
        (electron / "dist").mkdir(parents=True)
        with zipfile.ZipFile(self.fx.electron_zip) as archive:  # what install.js would extract
            archive.extractall(electron / "dist")
        (electron / "dist" / "electron.exe").write_bytes(pe_header(machine))
        (electron / "path.txt").write_text("electron.exe")
        for relative in ("package.json", "native/qorgau_guard.py", "native/guard_core.py", "native/guard_win32.py",
                         "native/environment_check.py", "shared/class-lock.ts"):
            (desktop / relative).parent.mkdir(parents=True, exist_ok=True)
            (desktop / relative).write_text("# fixture")
        source = desktop / "main" / "src" / "main.ts"
        source.parent.mkdir(parents=True)
        source.write_text("// source")
        for relative in ("dist/main/main.cjs", "dist/preload/preload.cjs", "dist/renderer/index.html"):
            (desktop / relative).parent.mkdir(parents=True, exist_ok=True)
            (desktop / relative).write_text("built")
        old, new = 1_700_000_000, 1_800_000_000
        for path in desktop.rglob("*"):
            if path.is_file():
                os.utime(path, (old, old))
        if stale:
            os.utime(source, (new, new))
        for relative in ("backend/proctor/__main__.py", "contracts/python/proctor_contracts/v1.py", "demo/exams/demo_exam.json"):
            self.fx.write(relative, "{}")

    def install_report(self, *extra: str, env: dict | None = None) -> tuple[subprocess.CompletedProcess, dict]:
        report = self.base / "install.json"
        if report.exists():
            report.unlink()
        localappdata = self.base / "Local AppData Пользователь"
        audio = localappdata / "QorgauExam" / "models" / "audio"
        if not audio.exists():
            shutil.copytree(self.fx.models / "audio", audio)
        environment = {"LOCALAPPDATA": str(localappdata), **(env or {})}
        result = self.run_tool("check-install", "--scenario", "student", "--json-out", str(report), *extra, env=environment)
        rows = {r["id"]: r for r in json.loads(report.read_text(encoding="utf-8"))["rows"]}
        return result, rows

    def test_check_install_reports_models_electron_and_platform(self):
        self.prepare_installed_desktop()
        before = kit.snapshot_tree(self.fx.root)
        result, rows = self.install_report("--models-dir", str(self.fx.models), "--feature", "audio")
        self.assertEqual(kit.snapshot_tree(self.fx.root), before, "check-install must not write into the source tree")
        for model in ("model:fake-phone", "model:fake-face", "model:silero-vad-aaaaaaaaaaaa"):
            self.assertEqual(rows[model]["status"], "PASS", rows[model])
        self.assertEqual(rows[f"electron:installed:{Fixture.ELECTRON}"]["status"], "PASS")
        self.assertEqual(rows["desktop:build"]["status"], "PASS")
        self.assertEqual(rows["python-package:fakepkg"]["code"], "missing")
        self.assertNotIn("python-package:linuxonly", rows)
        on_target = sys.platform == "win32" and kit.sysconfig.get_platform() == "win-amd64"
        self.assertEqual(rows["python:platform"]["status"], "PASS" if on_target else "FAIL")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ADAL_OFFLINE_INSTALL NOT_READY", result.stdout)

    def test_check_install_negative_cases(self):
        self.prepare_installed_desktop(machine=0x14C, stale=True)
        os.remove(self.fx.models / "phone/fake.onnx")
        _result, rows = self.install_report("--models-dir", str(self.fx.models))
        self.assertEqual(rows["model:fake-phone"]["code"], "missing")
        self.assertIn("phone/fake.onnx", rows["model:fake-phone"]["message"])
        self.assertIn("proctor.phone.prepare", rows["model:fake-phone"]["fix"])
        self.assertEqual(rows[f"electron:installed:{Fixture.ELECTRON}"]["code"], "wrong_platform")
        self.assertIn("x86", rows[f"electron:installed:{Fixture.ELECTRON}"]["message"])
        self.assertEqual(rows["desktop:build"]["code"], "stale_or_missing")
        # Without QORGAU_MODELS_DIR the runtime default (proctoring/models) is checked and the ambiguity reported.
        _result, rows = self.install_report()
        self.assertEqual(rows["models:location"]["code"], "implicit_dir")
        self.assertEqual(rows["model:fake-face"]["code"], "missing")
        _result, rows = self.install_report(env={"QORGAU_MODELS_DIR": str(self.fx.models)})
        self.assertEqual(rows["model:fake-face"]["status"], "PASS")
        self.assertNotIn("models:location", rows)

    def test_check_install_electron_completeness_and_hidden_staleness(self):
        out = self.build_ok()
        self.prepare_installed_desktop()
        _result, rows = self.install_report("--models-dir", str(self.fx.models), "--kit", str(out))
        self.assertEqual(rows[f"electron:runtime-files:{Fixture.ELECTRON}"]["status"], "PASS")
        self.assertNotIn("desktop:build-unchecked-inputs", rows)
        os.remove(self.fx.root / "desktop/node_modules/electron/dist/LICENSES.chromium.html")
        shared = self.fx.root / "desktop/shared/class-lock.ts"
        os.utime(shared, (1_900_000_000, 1_900_000_000))
        _result, rows = self.install_report("--models-dir", str(self.fx.models), "--kit", str(out),
                                            env={"ELECTRON_MIRROR": "https://mirror.invalid/"})
        self.assertEqual(rows[f"electron:runtime-files:{Fixture.ELECTRON}"]["code"], "incomplete_runtime")
        self.assertIn("LICENSES.chromium.html", rows[f"electron:runtime-files:{Fixture.ELECTRON}"]["message"])
        self.assertEqual(rows["desktop:build"]["status"], "PASS", "the launcher rule itself still passes")
        self.assertEqual(rows["desktop:build-unchecked-inputs"]["code"], "stale_unchecked")
        self.assertEqual(rows["electron:download-env"]["code"], "download_override")
        os.remove(self.fx.root / "desktop/native/guard_win32.py")
        _result, rows = self.install_report("--models-dir", str(self.fx.models))
        self.assertEqual(rows["file:desktop/native/guard_win32.py"]["status"], "FAIL")

    def test_teacher_scenario_needs_no_models_or_electron(self):
        result = self.run_tool("inventory", "--scenario", "teacher", "--out", str(self.base / "inv.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        inventory = json.loads((self.base / "inv.json").read_text(encoding="utf-8"))
        kinds = {a["kind"] for a in inventory["artifacts"]}
        self.assertEqual(kinds, {"python-wheel"})
        self.assertEqual([a["id"] for a in inventory["artifacts"]], ["wheel:purepkg"])

    def test_plan_prints_online_stage_and_executes_nothing(self):
        result = self.run_tool("plan", "--scenario", "student")
        self.assertEqual(result.returncode, 0)
        self.assertIn("ничего не скачивает", result.stdout)
        self.assertIn("proctor.phone.prepare --download", result.stdout)
        self.assertIn("--feature identity", result.stdout)


class UnitTest(unittest.TestCase):
    def test_tool_has_no_network_or_process_imports(self):
        tree = ast.parse(TOOL.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported & FORBIDDEN_IMPORTS, set())
        source = TOOL.read_text(encoding="utf-8")
        for word in ("urlopen", "Popen", "os.system", "check_call", "check_output", "pip install", "npm ci\"", "fetch("):
            self.assertNotIn(word, source.replace("npm ci --", ""), word)

    def test_markers(self):
        env = kit.marker_environment()
        both = "(platform_machine == 'x86_64' and sys_platform == 'linux') or (platform_machine == 'AMD64' and sys_platform == 'win32')"
        self.assertTrue(kit.evaluate_marker(both, env))
        self.assertTrue(kit.evaluate_marker("platform_machine == 'AMD64' and sys_platform == 'win32'", env))
        self.assertFalse(kit.evaluate_marker("sys_platform == 'linux'", env))
        self.assertTrue(kit.evaluate_marker("python_version >= '3.10' and python_version < '3.13'", env))
        self.assertFalse(kit.evaluate_marker('sys_platform != "win32"', env))
        self.assertTrue(kit.evaluate_marker("'win' in sys_platform", env))
        with self.assertRaises(ValueError):
            kit.evaluate_marker("unknown_variable == '1'", env)
        with self.assertRaises(ValueError):
            kit.evaluate_marker("sys_platform == 'win32' xor", env)

    def test_wheel_tags(self):
        good = ["x-1-cp312-cp312-win_amd64.whl", "x-1-cp37-abi3-win_amd64.whl", "x-1-py3-none-any.whl",
                "x-1-py2.py3-none-any.whl", "x-1-py3-none-win_amd64.whl", "x-1-cp310-abi3-win_amd64.whl"]
        bad = ["x-1-cp311-cp311-win_amd64.whl", "x-1-cp312-cp312-win32.whl", "x-1-cp312-cp312-win_arm64.whl",
               "x-1-cp312-cp312-manylinux_2_17_x86_64.whl", "x-1-cp313-abi3-win_amd64.whl", "x-1-py2-none-any.whl",
               "x-1-cp312-cp312-macosx_11_0_arm64.whl"]
        for name in good:
            self.assertTrue(kit.wheel_platform_verdict(kit.parse_wheel_name(name)[2])[0], name)
        for name in bad:
            self.assertFalse(kit.wheel_platform_verdict(kit.parse_wheel_name(name)[2])[0], name)

    def test_pe_machine(self):
        self.assertEqual(kit.pe_machine(pe_header(0x8664)), "x64")
        self.assertEqual(kit.pe_machine(pe_header(0x14C)), "x86")
        self.assertEqual(kit.pe_machine(pe_header(0xAA64)), "arm64")
        self.assertEqual(kit.pe_machine(b"\x7fELF" + b"\0" * 60), "elf")
        self.assertEqual(kit.pe_machine(b"MZ"), "unknown")
        self.assertEqual(kit.pe_machine(b"not an executable at all" * 4), "unknown")

    def test_safe_relative_rejects_escapes(self):
        for bad in ("../x", "/abs", "C:/x", "a\\b", "a/../b", ""):
            with self.assertRaises(ValueError):
                kit.safe_relative(bad)
        self.assertEqual(str(kit.safe_relative("models/phone/x.onnx")), "models/phone/x.onnx")

    def test_requirements_parser_reads_hashes_and_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.txt"
            path.write_text("# c\nA_b==1.0 ; sys_platform == 'win32' \\\n    --hash=sha256:" + "1" * 64 +
                            " \\\n    --hash=sha256:" + "2" * 64 + "\n    # via x\nc==2\n", encoding="utf-8")
            rows = kit.parse_requirements(path)
        self.assertEqual([(r.name, r.version) for r in rows], [("a-b", "1.0"), ("c", "2")])
        self.assertEqual(rows[0].hashes, {"1" * 64, "2" * 64})
        self.assertEqual(rows[0].marker, "sys_platform == 'win32'")


class RealRepositoryTest(unittest.TestCase):
    """Reads the committed manifests/lockfiles of this checkout. No weights are needed or claimed."""

    def test_real_inventory_matches_committed_manifests(self):
        spec = kit.load_spec(REAL_SPEC)
        expected = kit.collect_expected(REAL_ROOT, spec, "student", {"identity", "audio"})
        models = {e.id: e for e in expected if e.kind == "model"}
        self.assertEqual(sorted(models), ["model:mediapipe-face-landmarker-f16-v1", "model:sface-2021dec",
                                          "model:silero-vad-be95df9152c0", "model:yolo11n-coco-onnx", "model:yunet-2023mar"])
        phone = json.loads((REAL_ROOT / "backend/proctor/phone/models.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(models["model:yolo11n-coco-onnx"].sha256, phone["sha256"])
        self.assertEqual(models["model:yolo11n-coco-onnx"].license["distribution"], "BLOCKED_PENDING_DECISION")
        self.assertEqual(models["model:yolo11n-coco-onnx"].level, "required")
        self.assertEqual(models["model:sface-2021dec"].level, "required")  # because --feature identity
        wheels = [e for e in expected if e.kind == "python-wheel"]
        requirements = kit.parse_requirements(REAL_ROOT / "requirements/full.txt")
        self.assertEqual(len(wheels), len(requirements), "every win-x64 requirement maps to exactly one wheel")
        self.assertEqual([e.id for e in wheels if e.blocked], [])
        for exp in wheels:
            ok, tag = kit.wheel_platform_verdict(kit.parse_wheel_name(exp.kit_path.rsplit("/", 1)[1])[2])
            self.assertTrue(ok, exp.kit_path)
        teacher = kit.collect_expected(REAL_ROOT, spec, "teacher", set())
        self.assertEqual({e.kind for e in teacher if e.level != "not-used"}, {"python-wheel"})
        electron = [e for e in expected if e.kind == "electron-zip"]
        self.assertEqual(len(electron), 1)
        if electron[0].blocked:
            self.assertIn("checksums.json", electron[0].blocked)

    def test_descriptors_exist(self):
        spec = kit.load_spec(REAL_SPEC)
        for relative in spec["descriptors"]:
            self.assertTrue((REAL_ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main(verbosity=2)
