"""Interactive controlled test ONLY after captain says yes in chat. No acceptance auto-promotion."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import queue
import subprocess
import sys
import threading
import time
from qorgau_guard import RawInput


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('dry-run', 'enforce'), default='dry-run')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Windows only')
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'machine.json').write_text(json.dumps({
        'platform': sys.platform, 'os_release': platform.version(), 'python': platform.python_version(),
        'mode': args.mode, 'max_minutes': 2, 'started_at': datetime.now(timezone.utc).isoformat(),
        'acceptance': 'NOT_RECORDED: events alone do not prove suppression or restored keyboard',
    }, indent=2), encoding='utf-8')
    print('QORGAU CONTROLLED TEST / ' + args.mode, flush=True)
    print('EXIT: Ctrl+Alt+Shift+F12; close this console; Ctrl+Alt+Del -> Task Manager.', flush=True)
    print('Type stop + Enter to release. Automatic helper limit: 2 minutes.', flush=True)
    print('Test Win, Alt+Tab, PrtScn. No typed text, titles or clipboard contents are recorded.', flush=True)
    child = subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('qorgau_guard.py')),
        '--parent-pid', str(os.getpid()), '--mode', args.mode, '--max-minutes', '2'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    print(f'Helper PID {child.pid}; console parent PID {os.getpid()}', flush=True)
    commands = queue.Queue(maxsize=4)
    released = threading.Event()
    acknowledged = threading.Event()
    def read_commands():
        stream = RawInput()
        while True:
            line = stream.readline(64)
            if released.is_set():
                acknowledged.set()
                return
            if not line or line.strip().lower() == 'stop':
                try:
                    commands.put_nowait('stop')
                except queue.Full:
                    pass
                if not line:
                    acknowledged.set()
                    return
    def read_events():
        with (args.out / 'events.jsonl').open('w', encoding='utf-8') as log:
            for line in child.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                # Helper's fixed protocol only. Never record terminal input.
                if event.get('type') not in {'ready', 'key', 'foreground', 'error', 'bye'}:
                    continue
                line = json.dumps(event, ensure_ascii=True)
                log.write(line + '\n')
                log.flush()
                print(line, flush=True)
    reader = threading.Thread(target=read_events, daemon=True)
    reader.start()
    threading.Thread(target=read_commands, daemon=True).start()
    started = time.monotonic()
    try:
        while child.poll() is None:
            try:
                command = commands.get(timeout=1)
            except queue.Empty:
                command = 'hb'
            child.stdin.write(command + '\n')
            child.stdin.flush()
            if command == 'stop':
                child.stdin.close()
                break
            if time.monotonic() - started > 125:
                break
    except (KeyboardInterrupt, BrokenPipeError, OSError):
        pass
    finally:
        if not child.stdin.closed:
            child.stdin.close()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()  # only this harness's own helper
            child.wait(timeout=3)
        reader.join(timeout=1)
        child.stdout.close()
        child.stderr.close()
    print('HELPER EXITED. Now verify typing, Win/Alt+Tab, and focus in another application.', flush=True)
    print('Report what you observed in chat; VERIFICATION.json was NOT modified.', flush=True)
    released.set()
    # One input reader throughout; no competing buffered reads at shutdown.
    print('Press Enter or close this console. It will close after 2 minutes.', flush=True)
    try:
        acknowledged.wait(120)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
