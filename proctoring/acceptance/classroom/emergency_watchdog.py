"""Independent emergency stop for the launcher-owned desktop process.

No hook/shortcut registration, key history, process discovery or global termination.
Only Ctrl, Alt, Shift and F12 are sampled, using the current-down high bit. Electron
gets a two-second grace period to handle the same chord; then only the retained
Popen handle is terminated. The PowerShell owner closes its job and descendants.
"""
from __future__ import annotations

import ctypes
import math
import os
import subprocess
import sys
import threading
import time
from typing import Callable

KEYS = (0x11, 0x12, 0x10, 0x7B)  # Ctrl, Alt, Shift, F12; no other keys are read.
NOTICE_READY = "ADAL_EMERGENCY_WATCHDOG_READY Ctrl+Alt+Shift+F12"
NOTICE_UNAVAILABLE = "ADAL_EMERGENCY_WATCHDOG_UNAVAILABLE desktop launch refused"


class WatchdogUnavailable(RuntimeError):
    pass


def chord_reader(get_key_state: Callable[[int], int]) -> Callable[[], bool]:
    def down() -> bool:
        states = tuple(get_key_state(key) for key in KEYS)
        return all(state & 0x8000 for state in states)
    return down


def windows_chord() -> Callable[[], bool]:
    if sys.platform != "win32":
        raise WatchdogUnavailable(NOTICE_UNAVAILABLE)
    api = ctypes.WinDLL("user32", use_last_error=True).GetAsyncKeyState
    api.argtypes = [ctypes.c_int]
    api.restype = ctypes.c_short
    return chord_reader(api)


class EmergencyWatchdog:
    def __init__(self, key_down: Callable[[], bool], *, max_duration: float = 0,
                 interval: float = 0.05, clock: Callable[[], float] = time.monotonic):
        self.key_down, self.max_duration, self.interval, self.clock = key_down, max_duration, interval, clock
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.requested = threading.Event()
        self.requested_at: float | None = None
        self.initial_error = False
        self.thread = threading.Thread(target=self._watch, name="adal-emergency-watchdog", daemon=True)

    def start(self) -> None:
        self.thread.start()
        if not self.ready.wait(2.0) or self.initial_error:
            self.close()
            raise WatchdogUnavailable(NOTICE_UNAVAILABLE)

    def _request(self) -> None:
        self.requested_at = self.clock()
        self.requested.set()

    def _watch(self) -> None:
        try:
            # Refuse launch if the emergency chord is already held: there would be no rising edge.
            if self.key_down():
                self.initial_error = True
                return
            started = self.clock()
            self.ready.set()
            previous = False
            while not self.stopped.wait(self.interval):
                current = self.key_down()
                if (current and not previous) or (self.max_duration and self.clock() - started >= self.max_duration):
                    self._request()
                    return
                previous = current
        except Exception:
            if self.ready.is_set():
                self._request()  # Losing the independent safety monitor also stops the owned app.
            else:
                self.initial_error = True
        finally:
            self.ready.set()

    def close(self) -> None:
        self.stopped.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=1.0)


def run_desktop(cfg: dict, *, popen=subprocess.Popen, poll_factory=windows_chord,
                clock=time.monotonic, grace_seconds: float = 2.0, interval: float = 0.05,
                notice: Callable[[str], None] = lambda text: print(text, flush=True)) -> int:
    """Spawn only after watchdog preflight; all termination uses the returned Popen handle.

    cfg: electron, desktop, emergency_watchdog (bool), enforce (bool),
    max_duration_seconds (0 disables the optional, at-most-one-hour trial deadline).
    Injectable seams are for pure tests, never read from launcher configuration.
    """
    enabled = cfg.get("emergency_watchdog") is True
    enforce = cfg.get("enforce") is True or os.environ.get("QORGAU_SHELL_NATIVE_ENFORCE") == "1"
    duration = cfg.get("max_duration_seconds", 0)
    if (enforce and not enabled) or isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise WatchdogUnavailable(NOTICE_UNAVAILABLE)
    if not math.isfinite(duration) or not 0 <= duration <= 3600:
        raise WatchdogUnavailable(NOTICE_UNAVAILABLE)
    try:
        reader = poll_factory() if enabled else lambda: False
        watcher = EmergencyWatchdog(reader, max_duration=duration, interval=interval, clock=clock)
        watcher.start()
    except Exception:
        raise WatchdogUnavailable(NOTICE_UNAVAILABLE) from None
    child = None
    emergency = False
    try:
        if watcher.requested.is_set():
            raise WatchdogUnavailable(NOTICE_UNAVAILABLE)
        if enabled:
            notice(NOTICE_READY)
        child = popen([cfg["electron"], cfg["desktop"]], cwd=cfg["desktop"])
        terminated = False
        while True:
            if watcher.requested.is_set():
                emergency = True
                if clock() - watcher.requested_at >= grace_seconds:
                    if not terminated and child.poll() is None:
                        child.terminate()
                        terminated = True
                    # Never wait forever for a failed terminate: wrapper exit closes the owned job.
                    try:
                        child.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        return 0
                    return 0
            try:
                result = child.wait(timeout=interval)
                return 0 if emergency else result
            except subprocess.TimeoutExpired:
                pass
    finally:
        watcher.close()
        # A supervision exception must return control to the job owner, not strand Electron.
        if child is not None and child.poll() is None:
            try:
                child.terminate()
            except OSError:
                pass
        # No synchronous log after launch: a full/unread pipe must never delay job-owner cleanup.
