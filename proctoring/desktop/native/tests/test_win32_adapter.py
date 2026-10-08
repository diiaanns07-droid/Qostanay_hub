"""Exercise real hook-thread/cleanup code with fake Win32 functions only."""
import ctypes as C
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guard_core import Output, StopState
from guard_win32 import KBDLLHOOKSTRUCT, KeyboardHook, ParentHandle, WM_QUIT


class FakeWin32:
    HOOKPROC = staticmethod(lambda function: function)
    def __init__(self, install=True, message_error=False):
        self.calls = []
        self.messages = queue.Queue()
        self.install = install
        self.message_error = message_error
        self.user = SimpleNamespace(
            SetWindowsHookExW=self.set_hook, UnhookWindowsHookEx=self.unhook,
            PeekMessageW=lambda *args: 0, GetMessageW=self.get_message,
            GetAsyncKeyState=lambda key: 0,
            PostThreadMessageW=self.post, TranslateMessage=lambda *args: None,
            DispatchMessageW=lambda *args: None, CallNextHookEx=lambda *args: 42)
        self.kernel = SimpleNamespace(GetCurrentThreadId=lambda: 17, GetModuleHandleW=lambda _: 123)
    def set_hook(self, kind, callback, module, tid):
        self.calls.append(('install', kind, module, tid, threading.current_thread().name))
        return 555 if self.install else None
    def unhook(self, handle):
        self.calls.append(('unhook', handle))
        return True
    def get_message(self, *args):
        if self.message_error:
            return -1
        assert self.messages.get(timeout=2) == WM_QUIT
        return 0
    def post(self, tid, message, *args):
        self.messages.put(message)
        return True


@pytest.mark.parametrize('install,message_error', [(True,False), (False,False), (True,True)])
def test_dedicated_thread_and_exactly_once_unhook(install, message_error):
    api, stop = FakeWin32(install, message_error), StopState()
    hook = KeyboardHook(api, stop, Output(lambda line: None, stop))
    try:
        hook.start()
    finally:
        hook.close()
        hook.close()
    assert api.calls[0] == ('install', 13, 123, 0, 'guard-keyboard')
    assert api.calls.count(('unhook', 555)) == int(install)
    assert not hook.thread.is_alive()
    if not install:
        assert stop.error == 'hook_install_failed'
    elif message_error:
        assert stop.error == 'hook_message_failed'


def test_callback_passes_negative_code_and_catches_policy_exception():
    api, stop = FakeWin32(), StopState()
    hook = KeyboardHook(api, stop, Output(lambda line: None, stop), enforce=True)
    # Never dereference an invalid pointer when nCode < 0.
    assert hook._callback(-1, 0x100, 0) == 42
    data = KBDLLHOOKSTRUCT(vkCode=0x5B)
    assert hook._callback(0, 0x100, C.addressof(data)) == 1
    assert hook._callback(0, 0x101, C.addressof(data)) == 1
    def fail(*args):
        raise RuntimeError('fake callback failure')
    hook.policy.handle = fail
    assert hook._callback(0, 0x100, C.addressof(data)) == 42
    assert stop.error == 'hook_callback_failed'
    assert hook._callback(0, 0x100, C.addressof(data)) == 42


def test_parent_open_failure_and_wait_failure_are_not_alive():
    api = SimpleNamespace(kernel=SimpleNamespace(OpenProcess=lambda *a: None))
    with pytest.raises(OSError, match='parent_open_failed'):
        ParentHandle(api, 12)
    api.kernel.OpenProcess = lambda *a: 99
    api.kernel.WaitForSingleObject = lambda *a: 0xFFFFFFFF
    api.kernel.CloseHandle = lambda *a: True
    parent = ParentHandle(api, 12)
    try:
        with pytest.raises(OSError, match='parent_wait_failed'):
            parent.alive()
    finally:
        parent.close()
