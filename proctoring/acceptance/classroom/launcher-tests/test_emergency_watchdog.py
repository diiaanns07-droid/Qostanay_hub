"""Pure watchdog tests: injected key reader/process, never Win32 key APIs or Electron."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / "emergency_watchdog.py"
spec = importlib.util.spec_from_file_location("adal_emergency_watchdog", FILE)
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)


class Child:
    def __init__(self, *, normal_exit=None, graceful=None, terminate_exits=True):
        self.code = normal_exit
        self.graceful = graceful
        self.terminate_exits = terminate_exits
        self.terminated = []
        self.created = time.monotonic()

    def poll(self):
        return self.code

    def wait(self, timeout):
        if self.code is None and self.graceful and time.monotonic() - self.created >= self.graceful:
            self.code = 0
        if self.code is None:
            time.sleep(min(timeout, 0.002))
            raise subprocess.TimeoutExpired("owned fixture", timeout)
        return self.code

    def terminate(self):
        self.terminated.append(time.monotonic())
        if self.terminate_exits:
            self.code = 1


class Watchdog(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"QORGAU_SHELL_NATIVE_ENFORCE": "0"})
        self.env.start()
        self.cfg = {"electron": "fixture.exe", "desktop": "fixture-dir", "emergency_watchdog": True}

    def tearDown(self):
        self.env.stop()
        self.assertFalse([t for t in threading.enumerate() if t.name == "adal-emergency-watchdog"])

    def run_child(self, child, reader, **cfg):
        calls, logs = [], []
        def spawn(args, **kwargs):
            calls.append((args, kwargs))
            return child
        result = wd.run_desktop({**self.cfg, **cfg}, popen=spawn, poll_factory=lambda: reader,
                                grace_seconds=0.025, interval=0.002, notice=logs.append)
        self.assertEqual(calls, [(["fixture.exe", "fixture-dir"], {"cwd": "fixture-dir"})])
        self.assertEqual(logs, [wd.NOTICE_READY], "no post-launch logging may block emergency cleanup")
        return result

    def test_reads_only_four_fixed_keys_and_ignores_low_transition_bit(self):
        reads = []
        values = {key: 1 for key in wd.KEYS}
        def get(key):
            reads.append(key)
            return values[key]
        reader = wd.chord_reader(get)
        self.assertFalse(reader())
        self.assertEqual(reads, list(wd.KEYS))
        for key in wd.KEYS:
            values[key] = -32768
        self.assertTrue(reader())
        for key in wd.KEYS:
            values[key] = 0
            self.assertFalse(reader(), "every modifier and F12 must be held")
            values[key] = 0x8000

    def test_normal_exit_joins_thread_and_does_not_terminate(self):
        child = Child(normal_exit=7)
        self.assertEqual(self.run_child(child, lambda: False), 7)
        self.assertEqual(child.terminated, [])

    def test_chord_rising_edge_waits_grace_and_only_terminates_owned_child(self):
        owned, unrelated = Child(), Child()
        first, triggered = True, []
        def reader():
            nonlocal first
            if first:
                first = False
                return False
            if not triggered:
                triggered.append(time.monotonic())
            return True
        self.assertEqual(self.run_child(owned, reader), 0)
        self.assertEqual(len(owned.terminated), 1)
        self.assertGreaterEqual(owned.terminated[0] - triggered[0], 0.024)
        self.assertEqual(unrelated.terminated, [])
        self.assertIsNone(unrelated.poll())

    def test_electron_can_exit_gracefully_before_fallback(self):
        child = Child(graceful=0.012)
        calls = 0
        def reader():
            nonlocal calls
            calls += 1
            return calls > 1
        self.assertEqual(self.run_child(child, reader), 0)
        self.assertFalse(child.terminated)

    def test_deadline_stops_without_any_chord(self):
        child = Child()
        self.assertEqual(self.run_child(child, lambda: False, max_duration_seconds=0.015), 0)
        self.assertGreaterEqual(child.terminated[0] - child.created, 0.039)

    def test_failure_after_start_stops_owned_child(self):
        calls = 0
        def reader():
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError("fixture API failure")
            return False
        child = Child()
        self.assertEqual(self.run_child(child, reader), 0)
        self.assertEqual(len(child.terminated), 1)

    def test_factory_failure_or_initial_poll_failure_or_held_chord_refuses_before_spawn(self):
        def broken():
            raise OSError("fixture")
        for factory in (broken, lambda: broken, lambda: lambda: True):
            calls = []
            with self.subTest(factory=factory), self.assertRaises(wd.WatchdogUnavailable):
                wd.run_desktop(self.cfg, poll_factory=factory, popen=lambda *a, **k: calls.append(a), notice=lambda _: None)
            self.assertEqual(calls, [])

    def test_enforce_requires_watchdog_even_if_environment_requests_it(self):
        for cfg, env in (({"enforce": True}, "0"), ({}, "1")):
            with patch.dict(os.environ, {"QORGAU_SHELL_NATIVE_ENFORCE": env}):
                with self.assertRaises(wd.WatchdogUnavailable):
                    wd.run_desktop({**self.cfg, **cfg, "emergency_watchdog": False},
                                   popen=lambda *a, **k: self.fail("must not spawn"))

    def test_invalid_trial_deadlines_refuse_before_spawn(self):
        for duration in (-1, 3601, float("nan"), float("inf"), True, "120"):
            with self.subTest(duration=duration), self.assertRaises(wd.WatchdogUnavailable):
                wd.run_desktop({**self.cfg, "max_duration_seconds": duration},
                               popen=lambda *a, **k: self.fail("must not spawn"))

    def test_failed_child_termination_returns_to_job_owner_without_infinite_wait(self):
        child = Child(terminate_exits=False)
        start = time.monotonic()
        self.assertEqual(self.run_child(child, lambda: False, max_duration_seconds=0.01), 0)
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertGreaterEqual(len(child.terminated), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
