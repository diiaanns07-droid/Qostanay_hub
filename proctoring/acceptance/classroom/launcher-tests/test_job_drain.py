"""Real Windows Job cleanup using sleeping Python fixtures; no app, devices or keys."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

LIBRARY = Path(__file__).resolve().parents[1] / "Start-Teacher.ps1"
PS = os.environ.get("QORGAU_TEST_POWERSHELL", "powershell.exe")


def quoted(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(sys.platform == "win32", "Windows Job Object")
class JobDrain(unittest.TestCase):
    def test_desktop_worker_honors_trial_deadline_without_any_keyboard_api(self):
        with tempfile.TemporaryDirectory(prefix="adal-watchdog-worker-") as directory:
            root = Path(directory)
            desktop = root / "fixture desktop"
            desktop.mkdir()
            (desktop / "__main__.py").write_text("import time; time.sleep(30)\n", encoding="utf-8")
            script = root / "check.ps1"
            source_root = LIBRARY.parents[2]
            script.write_text(
                f". {quoted(LIBRARY)} -Library\n"
                "$ErrorActionPreference='Stop'\n$child=$null\ntry {\n"
                f"  $root={quoted(source_root)}\n"
                "  $environment=Get-QorgauEnvironment $root\n"
                "  $environment.QORGAU_SHELL_NATIVE_ENFORCE='0'\n"
                f"  $child=Start-QorgauProcess {quoted(sys.executable)} $root $environment "
                f"@{{kind='desktop';electron={quoted(sys.executable)};desktop={quoted(desktop)};"
                "emergency_watchdog=$false;max_duration_seconds=0.05}\n"
                "  Wait-QorgauProcess $child 'desktop' 5 0\n"
                "  Write-Host 'FIXTURE_TIMER_COMPLETED'\n"
                "} finally { Stop-QorgauProcess $child }\n", encoding="utf-8-sig")
            started = time.monotonic()
            result = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("FIXTURE_TIMER_COMPLETED", result.stdout)
            self.assertNotIn("ADAL_EMERGENCY_WATCHDOG_READY", result.stdout, "no real keyboard API in fixture")
            self.assertLess(time.monotonic() - started, 12)

    def test_wrapper_exit_closes_job_before_waiting_for_inherited_pipe_eof(self):
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(40)"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with tempfile.TemporaryDirectory(prefix="adal-job-drain-") as directory:
                root = Path(directory)
                (root / "fixture_parent.py").write_text(
                    "import subprocess, sys\n"
                    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                    "print('FIXTURE_WRAPPER_EXIT', flush=True)\n", encoding="utf-8")
                script = root / "check.ps1"
                script.write_text(
                    f". {quoted(LIBRARY)} -Library\n"
                    "$ErrorActionPreference='Stop'\n$child=$null\ntry {\n"
                    f"  $root={quoted(root)}\n"
                    "  $environment=Get-QorgauEnvironment $root\n"
                    f"  $child=Start-QorgauProcess {quoted(sys.executable)} $root $environment "
                    "@{kind='fixture';module='fixture_parent';args=@()}\n"
                    "  Wait-QorgauProcess $child 'desktop' 5 0\n"
                    "} finally { Stop-QorgauProcess $child }\n", encoding="utf-8-sig")
                started = time.monotonic()
                result = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("FIXTURE_WRAPPER_EXIT", result.stdout)
                self.assertLess(time.monotonic() - started, 12, "must not wait 30s for orphan stdout")
                self.assertIsNone(unrelated.poll(), "job cleanup must not affect another process")
        finally:
            if unrelated.poll() is None:
                unrelated.terminate()  # our own retained Popen only
            unrelated.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
