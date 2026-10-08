"""Launch the pinned A01 candidate on Windows, with explicit recovery information.

No models/dependencies are downloaded here. Native enforcement is always disabled.
Console shows the temporary operator PIN; the on-disk log redacts it and API tokens.
"""
from __future__ import annotations

import datetime as dt
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from preflight import inspect

EXPECTED_SHA = "de7290509bf558d6488be84d2e0730b2b9ab104a"
ROOT = Path(__file__).resolve().parents[1]


def redact(line: str) -> str:
    line = re.sub(r"(operator PIN for this run:\s*)\d+", r"\1[REDACTED]", line)
    return re.sub(r"\b[0-9a-fA-F]{64}\b", "[REDACTED_HEX64]", line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic-no-selftest", action="store_true",
                        help="Open the UI only: work around QA-WIN-007; LIVE preflight remains blocked")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("This launcher is for the Windows demo laptop.")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout.strip()
    if sha != EXPECTED_SHA:
        raise SystemExit(f"Wrong candidate: {sha}; expected {EXPECTED_SHA}")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", ".", ":!qa", ":!packaging", ":!handoffs/A09"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    if dirty:
        raise SystemExit("Product files differ from the pinned candidate:\n" + dirty)
    readiness = inspect(ROOT, "desktop", ROOT / "models")
    if not readiness["ready_for_launch"]:
        raise SystemExit(json.dumps(readiness, ensure_ascii=False, indent=2))

    # This checkout lives at <workspace>/worktrees/candidate/proctoring. Keep all
    # exam metadata and redacted diagnostics outside the source checkout.
    workspace = ROOT.parents[2]
    run_root = workspace / "tmp" / "live-tonight"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = run_root / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("QORGAU_", "ELECTRON_"))
           and k.upper() not in {"NODE_OPTIONS", "NODE_INSPECT_RESUME_ON_START", "PYTHONPATH"}}
    env.update(
        QORGAU_PROCTORING_ROOT=str(ROOT),
        QORGAU_PYTHON=str(ROOT / ".venv" / "Scripts" / "python.exe"),
        QORGAU_MODELS_DIR=str(ROOT / "models"),
        QORGAU_DATA_DIR=str(run_dir / "data"),
        QORGAU_SHELL_NATIVE_ENFORCE="0",
        QORGAU_SHELL_SELFTEST="0" if args.diagnostic_no_selftest else "1",
        QORGAU_SHELL_ALLOW_DEVTOOLS="0",
        QORGAU_SHELL_DEMO_OPERATOR="1",
        QORGAU_SHELL_EMERGENCY_ACCELERATOR="CommandOrControl+Alt+Shift+F12",
        PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
    )
    exe = ROOT / "desktop" / "node_modules" / "electron" / "dist" / "electron.exe"
    # A stable, dedicated Electron profile preserves single-instance protection.
    command = [str(exe), ".", f"--user-data-dir={run_root / 'shell-profile'}"]
    label = "DIAGNOSTIC" if args.diagnostic_no_selftest else "LIVE test"
    print(f"Qorgau {label} | native enforcement OFF | candidate {sha}", flush=True)
    if args.diagnostic_no_selftest:
        print("DIAGNOSTIC ONLY: самопроверка отключена из-за QA-WIN-007.", flush=True)
        print("LIVE preflight заблокирован: защита остаётся unverified. Не начинать экзамен.", flush=True)
    print("ВЫХОД: Ctrl+Alt+Shift+F12 (при необходимости вместе с Fn).", flush=True)
    print("Если приложение зависло: Ctrl+Alt+Del → Диспетчер задач → Подробности.", flush=True)
    print("Завершить только electron.exe с PID Qorgau, указанным ниже. Другие процессы не трогать.", flush=True)
    print("При входе/выходе из экзамена приложение очищает буфер обмена.", flush=True)
    print("Сначала проверьте аварийный выход в короткой тестовой сессии.", flush=True)
    proc = subprocess.Popen(command, cwd=ROOT / "desktop", env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    record = {"candidate_sha": sha, "pid": proc.pid, "exe": str(exe),
              "started_at": stamp, "native_enforcement": False,
              "diagnostic_no_selftest": args.diagnostic_no_selftest,
              "log": str(run_dir / "shell-redacted.log"), "run_dir": str(run_dir)}
    for path in (run_dir / "launch.json", run_root / "latest.json"):
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"QORGAU PID: {proc.pid}\nLog: {record['log']}", flush=True)
    try:
        with (run_dir / "shell-redacted.log").open("w", encoding="utf-8") as log:
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(redact(line))
                log.flush()
                print(line, end="", flush=True)
        rc = proc.wait()
    except KeyboardInterrupt:
        # The live Popen handle identifies only the process this launcher owns.
        # No name-based kill, no foreign process tree, no Windows settings.
        if proc.poll() is None:
            proc.terminate()
        rc = proc.wait(timeout=15)
    finally:
        if proc.stdout:
            proc.stdout.close()
    record.update(exit_code=rc, finished_at=dt.datetime.now(dt.timezone.utc).isoformat())
    (run_dir / "launch.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    latest = run_root / "latest.json"
    if json.loads(latest.read_text(encoding="utf-8")).get("pid") == proc.pid:
        latest.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"Qorgau exited: {rc}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
