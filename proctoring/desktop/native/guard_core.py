"""Bounded protocol and fail-open lifecycle. No Windows APIs and no keyboard logging."""
from __future__ import annotations

import json
import math
import ntpath
import queue
import re
import threading
import time

VERSION = "python-prototype-1"
HEARTBEAT_SECONDS = 5.0
PROCESS_RE = re.compile(r"[A-Za-z0-9 ._()-]{1,64}\Z")
MODIFIERS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
CTRL = {0x11, 0xA2, 0xA3}
ALT = {0x12, 0xA4, 0xA5}
SHIFT = {0x10, 0xA0, 0xA1}


def process_basename(path: str | None) -> str | None:
    name = ntpath.basename(path) if path else ""
    return name if PROCESS_RE.fullmatch(name) else None


class StopState:
    def __init__(self):
        self.event = threading.Event()
        self.reason = "normal_exit"
        self.error = None
        self._lock = threading.Lock()

    def request(self, reason: str, error: str | None = None):
        with self._lock:
            if error and self.error is None:
                self.error = error
            if not self.event.is_set():
                self.reason = reason
                self.event.set()


class Output:
    """Only the writer calls write_line. A blocked stdout cannot delay unhooking.

    The CLI writer uses os.write (not Python buffered stdout), so a blocked daemon
    cannot hold a Python stdio lock during interpreter shutdown.
    """
    def __init__(self, write_line, stop: StopState, capacity=256):
        if capacity < 4:
            raise ValueError("output capacity must be at least four")
        self.queue = queue.Queue(maxsize=capacity)
        self.write_line = write_line
        self.stop = stop
        self.thread = threading.Thread(target=self._run, name="guard-output", daemon=True)

    def start(self):
        self.thread.start()

    def emit(self, event: dict) -> bool:
        try:
            self.queue.put_nowait(event)
            return True
        except queue.Full:
            self.stop.request("queue_overflow", "queue_overflow")
            return False

    def finish(self):
        # Cleanup is already complete. Discard stale events if necessary to leave
        # room for the terminal records; never wait on the stdout reader.
        terminal = []
        if self.stop.error:
            terminal.append({"type": "error", "code": self.stop.error})
        terminal.extend([{"type": "bye", "reason": self.stop.reason}, None])
        while self.queue.qsize() > self.queue.maxsize - len(terminal):
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        for event in terminal:
            while True:
                try:
                    self.queue.put_nowait(event)
                    break
                except queue.Full:
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        pass
        self.thread.join(timeout=0.3)

    def _run(self):
        try:
            while True:
                event = self.queue.get()
                if event is None:
                    return
                self.write_line(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
        except Exception:
            self.stop.request("output_failed", "output_failed")


class KeyboardPolicy:
    """Hook-thread-only state: modifiers and our swallowed target keys, never text.

    Key-up is swallowed only if this helper swallowed that key's down. An emergency
    chord passes through and requests release even without a responsive shell.
    """
    def __init__(self, enforce: bool, stop: StopState, emit, initial_modifiers=()):
        self.enforce, self.stop, self.emit = enforce, stop, emit
        self.modifiers = set(initial_modifiers) & MODIFIERS
        self.swallowed = {}

    def handle(self, vk: int, down: bool, alt_flag=False) -> bool:
        if self.stop.event.is_set():
            return False
        if vk in MODIFIERS:
            if down:
                self.modifiers.add(vk)
            else:
                self.modifiers.discard(vk)
                if vk in (0x10, 0x11, 0x12):
                    self.modifiers.difference_update({0x10: SHIFT, 0x11: CTRL, 0x12: ALT}[vk])
            return False
        if not down:
            return self.swallowed.pop(vk, None) is not None
        alt = alt_flag or bool(self.modifiers & ALT)
        ctrl = bool(self.modifiers & CTRL)
        if vk == 0x7B and ctrl and alt and self.modifiers & SHIFT:  # F12
            self.stop.request("emergency_hotkey")
            return False
        key = self.swallowed.get(vk)
        if key is None:
            if vk in (0x5B, 0x5C):
                key = "win"
            elif vk == 0x2C:
                key = "print_screen"
            elif vk == 0x09 and alt:
                key = "alt_tab"
            elif vk == 0x1B and alt:
                key = "alt_esc"
            elif vk == 0x1B and ctrl:
                key = "ctrl_esc"
        if key is None:
            return False
        swallow = self.enforce
        if not self.emit({"type": "key", "key": key, "swallowed": swallow}):
            # Queue failure is a release condition, never silent suppression.
            return False
        if swallow:
            self.swallowed[vk] = key
        return swallow


class Runtime:
    """Interfaces are injected for tests. hook.start/close own all native resources."""
    def __init__(self, hook, parent, foreground, output: Output, stop: StopState,
                 *, mode="dry-run", max_minutes=120, clock=time.monotonic):
        if mode not in ("dry-run", "enforce") or not math.isfinite(max_minutes) or not 0 < max_minutes <= 240:
            raise ValueError("invalid runtime limits")
        self.hook, self.parent, self.foreground = hook, parent, foreground
        self.output, self.stop, self.mode = output, stop, mode
        self.clock, self.max_seconds = clock, max_minutes * 60
        self.started = self.last_hb = clock()
        self.last_foreground = object()

    def input_line(self, line: str):
        if line == "":
            self.stop.request("stdin_eof")
        elif line.strip() == "stop":
            self.stop.request("stop_requested")
        elif line.strip() == "hb":
            self.last_hb = self.clock()
        else:
            self.stop.request("invalid_command", "invalid_command")

    def read_input(self, stream):
        try:
            while not self.stop.event.is_set():
                self.input_line(stream.readline(64))
        except Exception:
            self.stop.request("stdin_failed", "stdin_failed")

    def poll(self):
        now = self.clock()
        if now - self.last_hb >= HEARTBEAT_SECONDS:
            self.stop.request("heartbeat_lost")
        elif now - self.started >= self.max_seconds:
            self.stop.request("max_minutes")
        elif not self.parent.alive():
            self.stop.request("parent_exited")

    def run(self, stream):
        self.output.start()
        try:
            # Check the retained parent handle before installing anything.
            self.poll()
            if self.stop.event.is_set():
                return
            self.hook.start()
            if self.stop.event.is_set():
                return
            self.output.emit({"type": "ready", "version": VERSION,
                              "mode": "enforce" if self.mode == "enforce" else "dry_run",
                              "hook": True, "foreground_watch": True})
            threading.Thread(target=self.read_input, args=(stream,), name="guard-input", daemon=True).start()
            next_foreground = 0.0
            while not self.stop.event.wait(0.05):
                self.poll()
                if not self.stop.event.is_set() and self.clock() >= next_foreground:
                    foreign, path = self.foreground()
                    item = (bool(foreign), process_basename(path))
                    if item != self.last_foreground:
                        self.output.emit({"type": "foreground", "foreign": item[0], "process": item[1]})
                        self.last_foreground = item
                    next_foreground = self.clock() + 0.2
        except Exception:
            self.stop.request("runtime_failed", "runtime_failed")
        finally:
            # Disable suppression before any potentially slow cleanup or stdout.
            self.stop.event.set()
            try:
                self.hook.close()
            finally:
                try:
                    self.parent.close()
                finally:
                    self.output.finish()
