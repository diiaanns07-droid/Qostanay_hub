"""Compatibility wrapper for Start-Adal.ps1 -Role Standalone.

No historical SHA pin, bypass, downloads or separate process lifecycle. The canonical
launcher reports current SHA, validates an optional expected SHA and checks this build.
Console credentials are transient; this wrapper never writes a log or launch record.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def command(args: argparse.Namespace) -> list[str]:
    result = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
              str(ROOT / "Start-Adal.ps1"), "-Role", "Standalone", "-Python", args.python]
    for name, flag in (("expected_sha", "ExpectedSha"), ("data_dir", "DataDir"), ("models_dir", "ModelsDir")):
        if getattr(args, name):
            result.extend(["-" + flag, str(getattr(args, name))])
    for name, flag in (("check_only", "CheckOnly"), ("enforce", "Enforce"), ("demo_operator", "DemoOperator")):
        if getattr(args, name):
            result.append("-" + flag)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--expected-sha")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--models-dir", type=Path)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--demo-operator", action="store_true", help="Explicit one-time DEMO operator PIN in console")
    parser.add_argument("--diagnostic-no-selftest", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.diagnostic_no_selftest:
        parser.error("Historical --diagnostic-no-selftest bypass is retired; use --check-only and current readiness errors.")
    if sys.platform != "win32":
        parser.error("The Adal launcher requires Windows.")
    try:
        return subprocess.call(command(args), cwd=ROOT)
    except KeyboardInterrupt:
        # The delegated PowerShell owns a kill-on-close Windows Job for its child tree.
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
