"""Bounded launcher regression: copied scripts and stub process owner; no app/devices/hooks/network.

The actual shared environment/root functions execute. Only probe/process creation/wait/cleanup
are replaced inside the temporary fixture library; production scripts have no test bypass.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
PS = os.environ.get("QORGAU_TEST_POWERSHELL", "powershell.exe")
PYTHON = str(Path(sys.executable).resolve())
LIBRARY_STUB = r"""
if ($Library) {
    function Test-QorgauPython([string]$PythonExe, [string]$Root, [hashtable]$Environment, [switch]$Student, [switch]$DesktopAssets) {
        Write-Host ('STUB_PROBE ' + $Root)
        if ($DesktopAssets) {
            Write-Host 'STUB_ASSETS'
            if ($env:ADAL_TEST_FAIL_ASSETS -eq '1') { throw 'fixture: missing model' }
        }
    }
    function Start-QorgauProcess([string]$PythonExe, [string]$Root, [hashtable]$Environment, [hashtable]$Payload) {
        if ($Payload.kind -ne 'desktop') { throw 'fixture forbids non-desktop process' }
        $safe = @{kind=$Payload.kind; root=$Root; python=$PythonExe; enforce=$Environment.QORGAU_SHELL_NATIVE_ENFORCE;
            path=$Environment.PYTHONPATH; selftest=$Environment.QORGAU_SHELL_SELFTEST;
            emergency=$Environment.QORGAU_SHELL_EMERGENCY_ACCELERATOR; demo=$Environment.QORGAU_SHELL_DEMO_OPERATOR;
            server=$Environment.QORGAU_CLASS_SERVER; node=$Environment.ELECTRON_RUN_AS_NODE;
            dev=$Environment.QORGAU_SHELL_DEV_RENDERER_URL; models=$Environment.QORGAU_MODELS_DIR}
        Write-Host ('STUB_DESKTOP ' + ($safe | ConvertTo-Json -Compress))
        return @{}
    }
    function Wait-QorgauProcess { Write-Host 'STUB_WAIT' }
    function Stop-QorgauProcess { Write-Host 'STUB_CLEANUP' }
    return
}
"""


def run(script: Path, *args, env=None):
    return subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                           "-Python", PYTHON, *map(str, args)], cwd=tempfile.gettempdir(),
                          env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)


@unittest.skipUnless(sys.platform == "win32", "Windows PowerShell scripts")
class UnifiedLauncher(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="adal-launch-stub-")
        self.root = Path(self.temp.name) / "selected checkout" / "proctoring"
        self.root.mkdir(parents=True)
        for relative in ("Start-Adal.ps1", "packaging/launch-windows.ps1", "acceptance/classroom/Start-Student.ps1",
                         "acceptance/classroom/Start-Teacher.ps1"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = (ROOT / relative).read_text(encoding="utf-8-sig")
            if relative.endswith("Start-Teacher.ps1"):
                self.assertIn("if ($Library) { return }", source)
                source = source.replace("if ($Library) { return }", LIBRARY_STUB)
            target.write_text(source, encoding="utf-8-sig")
        desktop = self.root / "desktop"
        for directory in ("main/src", "preload/src", "renderer/src", "shared", "../contracts/ts", "../class-audio/web"):
            (desktop / directory).mkdir(parents=True, exist_ok=True)
        inputs = ("package.json", "package-lock.json", "vite.config.ts", "renderer/index.html", "tsconfig.json",
                  "tsconfig.main.json", "tsconfig.renderer.json", "scripts/build-electron.mjs", "main/src/main.ts",
                  "shared/class-lock.ts", "../class-audio/web/shared/student-endpoint.js", "../class-audio/web/shared/media-errors.js")
        for relative in inputs:
            file = desktop / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("fixture source", encoding="utf-8")
            os.utime(file, (1000, 1000))
        for relative in ("dist/main/main.cjs", "dist/preload/preload.cjs", "dist/renderer/index.html",
                         "node_modules/electron/dist/electron.exe", "native/qorgau_guard.py"):
            file = desktop / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("DO NOT EXECUTE: process creation is stubbed", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("QORGAU_", "ADAL_TEST_"))}

    def tearDown(self):
        self.assertEqual(Path(self.temp.name).resolve().parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(Path(self.temp.name).name.startswith("adal-launch-stub-"))
        self.temp.cleanup()

    def invoke(self, *args, relative="Start-Adal.ps1", success=True):
        result = run(self.root / relative, *args, env=self.env)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("004201", result.stdout + result.stderr, "join code must not leak")
        return result.stdout

    def desktop(self, output):
        rows = [line.partition("STUB_DESKTOP ")[2] for line in output.splitlines() if "STUB_DESKTOP " in line]
        self.assertEqual(len(rows), 1, output)
        return json.loads(rows[0])

    def student_args(self):
        return ("-Role", "Student", "-Server", "127.0.0.1:8765", "-JoinCode", "004201", "-Label", "fixture")

    def test_explicit_enforce_reaches_desktop_and_default_overrides_inherited_enforce(self):
        self.env.update(QORGAU_SHELL_NATIVE_ENFORCE="1", ELECTRON_RUN_AS_NODE="1",
                        QORGAU_SHELL_DEV_RENDERER_URL="http://localhost:9999/")
        off = self.desktop(self.invoke(*self.student_args()))
        on = self.desktop(self.invoke(*self.student_args(), "-Enforce"))
        self.assertEqual((off["enforce"], on["enforce"]), ("0", "1"))
        self.assertEqual(on["selftest"], "1")
        self.assertEqual(on["emergency"], "CommandOrControl+Alt+Shift+F12")
        self.assertIsNone(on["node"])
        self.assertIsNone(on["dev"])

    def test_standalone_uses_shared_owner_current_source_and_no_inherited_class(self):
        self.env.update(PYTHONPATH="Z:/old-editable", QORGAU_CLASS_SERVER="old:1234", QORGAU_CLASS_CODE="004201")
        output = self.invoke("-Role", "Standalone", "-Enforce", "-ModelsDir", self.root / "models", "-DemoOperator")
        data = self.desktop(output)
        self.assertEqual(data["root"], str(self.root))
        self.assertEqual(data["python"], PYTHON)
        self.assertEqual(data["path"].split(";"), [str(self.root), str(self.root / "backend"), str(self.root / "contracts/python")])
        self.assertEqual(data["enforce"], "1")
        self.assertEqual(data["demo"], "1")
        self.assertIsNone(data["server"])
        self.assertIn("STUB_ASSETS", output)
        self.assertIn("STUB_CLEANUP", output)

    def test_check_only_enforce_does_not_start_desktop_or_create_data(self):
        data = self.root / "never-created"
        for args in (self.student_args(), ("-Role", "Standalone")):
            output = self.invoke(*args, "-Enforce", "-CheckOnly", "-DataDir", data)
            self.assertNotIn("STUB_DESKTOP", output)
        self.assertFalse(data.exists())

    def test_backend_only_enforce_and_wrong_role_options_rejected_before_probe(self):
        for args in ((*self.student_args(), "-BackendOnly", "-Enforce"),
                     ("-Role", "Standalone", "-BackendOnly", "-Enforce"),
                     ("-Role", "Teacher", "-Enforce"), ("-Role", "Standalone", "-Lan"),
                     ("-Role", "Standalone", "-Server", "127.0.0.1:8765")):
            output = self.invoke(*args, "-CheckOnly", success=False)
            self.assertNotIn("STUB_PROBE", output)
            self.assertNotIn("STUB_DESKTOP", output)

    def test_compatibility_powershell_routes_to_same_desktop(self):
        output = self.invoke("-Enforce", relative="packaging/launch-windows.ps1")
        self.assertEqual(self.desktop(output)["enforce"], "1")
        self.assertIn("STUB_ASSETS", output)

    def test_stale_build_never_uses_external_checkout(self):
        file = self.root / "desktop/main/src/main.ts"
        os.utime(file, (time.time() + 30, time.time() + 30))
        output = self.invoke(*self.student_args(), "-CheckOnly", success=False)
        self.assertIn("npm run build", output)
        self.assertNotIn("STUB_DESKTOP", output)
        self.invoke("-Role", "Standalone", "-CheckOnly", success=False)

    def test_shared_and_transitive_audio_changes_require_rebuild(self):
        self.invoke(*self.student_args(), "-CheckOnly") # positive control: fixture build is initially fresh
        for relative in ("shared/class-lock.ts", "../class-audio/web/shared/student-endpoint.js",
                         "../class-audio/web/shared/media-errors.js"):
            with self.subTest(dependency=relative):
                file = self.root / "desktop" / relative
                os.utime(file, (time.time() + 30, time.time() + 30))
                try:
                    for args in (self.student_args(), ("-Role", "Standalone")):
                        output = self.invoke(*args, "-CheckOnly", success=False)
                        self.assertIn("npm run build", output)
                        self.assertNotIn("STUB_DESKTOP", output)
                finally:
                    os.utime(file, (1000, 1000)) # each dependency must independently invalidate the old build
        self.invoke(*self.student_args(), "-CheckOnly")

    def test_missing_models_block_standalone_without_app(self):
        self.env["ADAL_TEST_FAIL_ASSETS"] = "1"
        output = self.invoke("-Role", "Standalone", "-CheckOnly", success=False)
        self.assertIn("missing model", output)
        self.assertNotIn("STUB_DESKTOP", output)

    def test_missing_native_helper_refuses_enforce_before_app(self):
        (self.root / "desktop/native/qorgau_guard.py").unlink()
        self.invoke(*self.student_args(), "-Enforce", "-CheckOnly", success=False)

    def test_expected_sha_fails_closed_without_git_metadata(self):
        output = self.invoke("-Role", "Teacher", "-ExpectedSha", "a" * 40, "-CheckOnly", success=False)
        self.assertNotIn("STUB_PROBE", output)

    def test_ps51_bom_and_parse_all_entrypoints(self):
        for relative in ("Start-Adal.ps1", "packaging/launch-windows.ps1", "acceptance/classroom/Start-Teacher.ps1",
                         "acceptance/classroom/Start-Student.ps1"):
            file = ROOT / relative
            self.assertTrue(file.read_bytes().startswith(b"\xef\xbb\xbf"), relative)
            escaped = str(file).replace("'", "''")
            code = f"$t=$null;$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('{escaped}',[ref]$t,[ref]$e);if($e.Count){{exit 1}}"
            result = subprocess.run([PS, "-NoProfile", "-Command", code], capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, relative)


class LegacyWrapper(unittest.TestCase):
    def test_python_compatibility_only_delegates_flags(self):
        spec = importlib.util.spec_from_file_location("adal_legacy", ROOT / "packaging/launch-live-tonight.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        args = argparse.Namespace(python=PYTHON, expected_sha="a" * 40, data_dir=None, models_dir=Path("C:/models"),
                                  check_only=True, enforce=True, demo_operator=False)
        cmd = module.command(args)
        self.assertIn(str(ROOT / "Start-Adal.ps1"), cmd)
        for flag in ("-Enforce", "-CheckOnly", "-ExpectedSha", "-ModelsDir"):
            self.assertIn(flag, cmd)
        self.assertNotIn("-DemoOperator", cmd)
        self.assertNotIn("de7290509bf558d6488be84d2e0730b2b9ab104a", " ".join(cmd))


if __name__ == "__main__":
    unittest.main(verbosity=2)
