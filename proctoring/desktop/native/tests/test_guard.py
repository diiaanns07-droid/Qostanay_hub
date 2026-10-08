"""Protocol/lifecycle tests. All hook, foreground and parent resources are fakes.
No test in this file calls SetWindowsHookEx or generates system keyboard input.
"""
import io
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guard_core import KeyboardPolicy, Output, Runtime, StopState, process_basename
from guard_win32 import ParentHandle


class Clock:
    now = 0.0
    def __call__(self):
        return self.now


class Parent:
    live = True
    closed = False
    def alive(self):
        return self.live
    def close(self):
        self.closed = True


class Hook:
    def __init__(self, start=lambda: None):
        self.started = False
        self.closed = False
        self.action = start
    def start(self):
        self.started = True
        self.action()
    def close(self):
        self.closed = True


class WaitingInput:
    def __init__(self, stop):
        self.stop = stop
    def readline(self, _limit):
        self.stop.event.wait(1)
        return ""


def setup_runtime(start=lambda: None, **kwargs):
    stop, lines, parent, hook = StopState(), [], Parent(), Hook(start)
    output = Output(lines.append, stop)
    runtime = Runtime(hook, parent, lambda: (True, r'C:\Windows\notepad.exe'), output, stop, **kwargs)
    return runtime, stop, lines, parent, hook


@pytest.mark.parametrize('commands,reason', [('stop\n', 'stop_requested'), ('', 'stdin_eof'),
                                          ('unexpected\n', 'invalid_command'), ('x' * 1000, 'invalid_command')])
def test_commands_release_and_end_with_bye(commands, reason):
    runtime, stop, lines, parent, hook = setup_runtime()
    runtime.run(io.StringIO(commands))
    assert hook.started and hook.closed and parent.closed
    events = list(map(json.loads, lines))
    assert events[0]['type'] == 'ready' and events[0]['mode'] == 'dry_run'
    assert events[-1] == {'type': 'bye', 'reason': reason}


@pytest.mark.parametrize('cause,reason', [('heartbeat', 'heartbeat_lost'), ('parent', 'parent_exited'),
                                       ('limit', 'max_minutes'), ('exception', 'runtime_failed')])
def test_watchdogs_and_exception_always_close_resources(cause, reason):
    clock = Clock()
    runtime, stop, lines, parent, hook = setup_runtime(clock=clock, max_minutes=0.01 if cause == 'limit' else 120)
    def after_start():
        if cause == 'heartbeat':
            clock.now = 5.0
        elif cause == 'limit':
            clock.now = 0.6
        elif cause == 'parent':
            parent.live = False
        else:
            raise RuntimeError('fake hook failure')
    hook.action = after_start
    runtime.run(WaitingInput(stop))
    assert stop.reason == reason and hook.closed and parent.closed
    assert json.loads(lines[-1]) == {'type': 'bye', 'reason': reason}


def test_heartbeat_refreshes_and_parent_is_checked_before_hook():
    clock = Clock()
    runtime, stop, lines, parent, hook = setup_runtime(clock=clock)
    clock.now = 4.9
    runtime.input_line('hb\r\n')
    clock.now = 9.8
    runtime.poll()
    assert not stop.event.is_set()
    parent.live = False
    runtime.run(WaitingInput(stop))
    assert not hook.started and hook.closed and parent.closed
    assert stop.reason == 'parent_exited'


def test_queue_overflow_is_fail_open_and_cleanup_does_not_wait_for_stdout():
    stop = StopState()
    gate = threading.Event()
    entered = threading.Event()
    def blocked_writer(line):
        entered.set()
        gate.wait(5)
    output = Output(blocked_writer, stop, capacity=4)
    policy = KeyboardPolicy(True, stop, output.emit)
    output.start()
    output.emit({'type': 'ready'})
    assert entered.wait(1)
    try:
        for _ in range(4):
            assert policy.handle(0x5B, True)
        assert not policy.handle(0x5B, True)
        assert stop.reason == 'queue_overflow'
        assert not policy.handle(0x2C, True)
        started = time.monotonic()
        output.finish()
        assert time.monotonic() - started < 1
    finally:
        gate.set()
        output.thread.join(1)


def test_broken_pipe_releases_hook_before_return():
    runtime, stop, lines, parent, hook = setup_runtime()
    def broken(_line):
        raise BrokenPipeError()
    runtime.output.write_line = broken
    runtime.run(WaitingInput(stop))
    assert stop.reason == 'output_failed' and hook.closed and parent.closed


@pytest.mark.parametrize('vk,mods,key', [(0x5B, [], 'win'), (0x5C, [], 'win'),
    (0x09, [0xA4], 'alt_tab'), (0x1B, [0xA5], 'alt_esc'),
    (0x1B, [0xA2], 'ctrl_esc'), (0x2C, [], 'print_screen')])
@pytest.mark.parametrize('enforce', [False, True])
def test_only_requested_keys_and_matching_keyup(vk, mods, key, enforce):
    events, stop = [], StopState()
    policy = KeyboardPolicy(enforce, stop, lambda e: events.append(e) or True)
    for mod in mods:
        assert not policy.handle(mod, True)
    assert policy.handle(vk, True) == enforce
    for mod in mods:  # release modifiers first: matching key-up still swallowed
        assert not policy.handle(mod, False)
    assert policy.handle(vk, False) == enforce
    assert events == [{'type': 'key', 'key': key, 'swallowed': enforce}]
    assert not policy.handle(0x41, True) and not policy.handle(0x41, False)
    assert len(events) == 1


def test_emergency_chord_and_all_following_keys_pass():
    stop, events = StopState(), []
    policy = KeyboardPolicy(True, stop, lambda e: events.append(e) or True)
    for vk in (0xA3, 0xA5, 0xA1, 0x7B):
        assert not policy.handle(vk, True)
    assert stop.reason == 'emergency_hotkey' and events == []
    assert not policy.handle(0x5B, True)


def test_alt_flag_and_preexisting_keyup():
    stop, events = StopState(), []
    policy = KeyboardPolicy(True, stop, lambda e: events.append(e) or True, initial_modifiers=[0xA2])
    assert not policy.handle(0x5B, False)  # key-down happened before our hook
    policy.handle(0xA2, False)
    assert not policy.handle(0x1B, True)
    assert policy.handle(0x09, True, alt_flag=True)


@pytest.mark.parametrize('path,expected', [(r'C:\Apps\notepad.exe','notepad.exe'),
    (r'C:\Apps\window-title!.exe',None), ('x'*65,None), (None,None)])
def test_foreground_never_transmits_paths_or_non_allowlisted_text(path, expected):
    assert process_basename(path) == expected


def test_parent_handle_opened_once_and_closed_after_exit():
    calls = []
    def open_process(access, inherit, pid):
        calls.append(('open', access, inherit, pid))
        return 1234
    results = iter([0x102, 0])
    api = SimpleNamespace(kernel=SimpleNamespace(OpenProcess=open_process,
        WaitForSingleObject=lambda handle, ms: next(results), CloseHandle=lambda handle: calls.append(('close', handle))))
    parent = ParentHandle(api, 99)
    assert parent.alive() and not parent.alive()
    parent.close()
    parent.close()
    assert calls == [('open', 0x100000, False, 99), ('close', 1234)]


def test_cleanup_failure_is_visible_after_normal_stop():
    stop = StopState()
    stop.request('stop_requested')
    stop.request('unhook_failed', 'unhook_failed')
    assert stop.reason == 'stop_requested' and stop.error == 'unhook_failed'
