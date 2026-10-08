"""Qorgau per-exam Windows guard. Dry-run by default. No third-party packages."""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import threading

from guard_core import Output, Runtime, StopState, VERSION


def write_line(line):
    data = line.encode("utf-8")
    while data:
        data = data[os.write(sys.stdout.fileno(), data):]


class RawInput:
    """Avoid a daemon holding Python's buffered stdin lock at interpreter exit."""
    def readline(self, limit):
        data = bytearray()
        while len(data) < limit:
            char = os.read(sys.stdin.fileno(), 1)
            if not char:
                break
            data.extend(char)
            if char == b'\n':
                break
        return data.decode('ascii', errors='replace')


def minutes(value):
    n = float(value)
    if not math.isfinite(n) or not 0 < n <= 240:
        raise argparse.ArgumentTypeError("max-minutes must be greater than 0 and at most 240")
    return n


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--mode", choices=("dry-run", "enforce"), default="dry-run")
    parser.add_argument("--max-minutes", type=minutes, default=120)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        write_line('{"type":"error","code":"unsupported_platform"}\n')
        return 2
    from guard_win32 import KeyboardHook, ParentHandle, Win32
    api = Win32()
    if args.self_check:
        version = sys.getwindowsversion()
        write_line(json.dumps({"type": "selfcheck", "version": VERSION,
                               "os": f"{version.major}.{version.minor}.{version.build}",
                               "elevated": bool(api.shell.IsUserAnAdmin())}) + "\n")
        return 0  # no hook installed by self-check
    if not args.parent_pid or args.parent_pid <= 0 or args.parent_pid == os.getpid():
        parser.error("a distinct positive --parent-pid is required")
    stop = StopState()
    output = Output(write_line, stop)
    parent = None
    hook = None
    finished = threading.Event()

    def on_signal(_signum, _frame):
        # Python signal handlers can interrupt Event.set/Lock.acquire on the main
        # thread. Defer all locking to Runtime.poll instead of re-entering a lock.
        stop.pending_signal = "console_signal"

    @api.CTRLHANDLER
    def console_handler(kind):
        if kind not in (0, 1, 2, 5, 6):
            return False
        stop.request("console_closed" if kind == 2 else "console_signal")
        # Windows terminates the process after CTRL_CLOSE handler returns.
        # Give the main thread a bounded chance to explicitly unhook first.
        finished.wait(1.0)
        return True

    old_signals = {}
    console_registered = False
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):
            old_signals[sig] = signal.signal(sig, on_signal)
        console_registered = bool(api.kernel.SetConsoleCtrlHandler(console_handler, True))
        if not console_registered:
            raise OSError("console_handler_failed")
        parent = ParentHandle(api, args.parent_pid)
        hook = KeyboardHook(api, stop, output, enforce=args.mode == "enforce")
        Runtime(hook, parent, lambda: api.foreground(args.parent_pid), output, stop,
                mode=args.mode, max_minutes=args.max_minutes).run(RawInput())
    except BaseException:
        stop.request("startup_failed", "startup_failed")
        if output.thread.ident is None:
            output.start()
            output.finish()
    finally:
        stop.event.set()
        try:
            if hook is not None:
                hook.close()
        finally:
            if parent is not None:
                parent.close()
            finished.set()
            if console_registered:
                api.kernel.SetConsoleCtrlHandler(console_handler, False)
            for sig, handler in old_signals.items():
                signal.signal(sig, handler)
    return 2 if stop.error else 0


if __name__ == "__main__":
    raise SystemExit(main())
