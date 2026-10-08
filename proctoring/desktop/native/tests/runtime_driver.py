"""Subprocess fixture: actual pipes/threads, FAKE hook and FAKE parent, no Win32."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guard_core import Output, Runtime, StopState
from qorgau_guard import RawInput, write_line

case = sys.argv[1]
started = time.monotonic()
class FakeHook:
    closed = False
    def start(self):
        pass
    def close(self):
        self.closed = True
class FakeParent:
    closed = False
    def alive(self):
        return case != 'parent' or time.monotonic() - started < 0.15
    def close(self):
        self.closed = True

stop, hook, parent = StopState(), FakeHook(), FakeParent()
output = Output(write_line, stop)
clock = (lambda: time.monotonic() * 40) if case == 'heartbeat' else time.monotonic
runtime = Runtime(hook, parent, lambda: (False, None), output, stop,
                  max_minutes=0.003 if case == 'limit' else 120, clock=clock)
runtime.run(RawInput())
assert hook.closed and parent.closed
