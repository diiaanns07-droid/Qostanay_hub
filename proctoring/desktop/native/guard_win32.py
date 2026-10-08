"""ctypes Windows resources. Constructing this module does not install a hook."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import threading

from guard_core import MODIFIERS, KeyboardPolicy

LRESULT = C.c_ssize_t
WPARAM = C.c_size_t
LPARAM = C.c_ssize_t
WH_KEYBOARD_LL = 13
WM_QUIT = 0x12
KEYDOWN = (0x100, 0x104)
KEYUP = (0x101, 0x105)


class KBDLLHOOKSTRUCT(C.Structure):
    _fields_ = [("vkCode", W.DWORD), ("scanCode", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]


class Win32:
    def __init__(self):
        self.user = C.WinDLL("user32", use_last_error=True)
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.shell = C.WinDLL("shell32", use_last_error=True)
        self.HOOKPROC = C.WINFUNCTYPE(LRESULT, C.c_int, WPARAM, LPARAM)
        self.CTRLHANDLER = C.WINFUNCTYPE(W.BOOL, W.DWORD)
        self._sig(self.user.SetWindowsHookExW, [C.c_int, self.HOOKPROC, W.HINSTANCE, W.DWORD], W.HANDLE)
        self._sig(self.user.UnhookWindowsHookEx, [W.HANDLE], W.BOOL)
        self._sig(self.user.CallNextHookEx, [W.HANDLE, C.c_int, WPARAM, LPARAM], LRESULT)
        self._sig(self.user.GetMessageW, [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT], W.BOOL)
        self._sig(self.user.PeekMessageW, [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT], W.BOOL)
        self._sig(self.user.TranslateMessage, [C.POINTER(W.MSG)], W.BOOL)
        self._sig(self.user.DispatchMessageW, [C.POINTER(W.MSG)], LRESULT)
        self._sig(self.user.PostThreadMessageW, [W.DWORD, W.UINT, WPARAM, LPARAM], W.BOOL)
        self._sig(self.user.GetAsyncKeyState, [C.c_int], C.c_short)
        self._sig(self.user.GetForegroundWindow, [], W.HWND)
        self._sig(self.user.GetWindowThreadProcessId, [W.HWND, C.POINTER(W.DWORD)], W.DWORD)
        self._sig(self.kernel.GetCurrentThreadId, [], W.DWORD)
        self._sig(self.kernel.GetModuleHandleW, [W.LPCWSTR], W.HMODULE)
        self._sig(self.kernel.OpenProcess, [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
        self._sig(self.kernel.WaitForSingleObject, [W.HANDLE, W.DWORD], W.DWORD)
        self._sig(self.kernel.CloseHandle, [W.HANDLE], W.BOOL)
        self._sig(self.kernel.QueryFullProcessImageNameW, [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL)
        self._sig(self.kernel.SetConsoleCtrlHandler, [self.CTRLHANDLER, W.BOOL], W.BOOL)
        self._sig(self.shell.IsUserAnAdmin, [], W.BOOL)

    @staticmethod
    def _sig(fn, args, result):
        fn.argtypes, fn.restype = args, result

    def foreground(self, parent_pid):
        hwnd = self.user.GetForegroundWindow()
        pid = W.DWORD()
        if not hwnd or not self.user.GetWindowThreadProcessId(hwnd, C.byref(pid)):
            return False, None
        foreign = pid.value not in (parent_pid, os.getpid())
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return foreign, None
        try:
            size = W.DWORD(32768)
            name = C.create_unicode_buffer(size.value)
            return foreign, name.value if self.kernel.QueryFullProcessImageNameW(handle, 0, name, C.byref(size)) else None
        finally:
            self.kernel.CloseHandle(handle)


class ParentHandle:
    def __init__(self, api: Win32, pid: int):
        self.api = api
        self.handle = api.kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE, no termination right
        if not self.handle:
            raise OSError("parent_open_failed")

    def alive(self):
        result = self.api.kernel.WaitForSingleObject(self.handle, 0)
        if result == 0x102:  # WAIT_TIMEOUT, still running
            return True
        if result == 0:  # signalled, original process exited (PID reuse irrelevant)
            return False
        raise OSError("parent_wait_failed")

    def close(self):
        if self.handle:
            self.api.kernel.CloseHandle(self.handle)
            self.handle = None


class KeyboardHook:
    def __init__(self, api: Win32, stop, output, enforce=False):
        self.api, self.stop = api, stop
        self.policy = KeyboardPolicy(enforce, stop, output.emit)
        self.handle = None
        self.thread_id = None
        self.ready = threading.Event()
        self._lock = threading.Lock()
        self.callback = api.HOOKPROC(self._callback)  # strong reference until after unhook
        self.thread = threading.Thread(target=self._pump, name="guard-keyboard", daemon=True)

    def _callback(self, code, message, pointer):
        try:
            if code == 0 and message in KEYDOWN + KEYUP and not self.stop.event.is_set():
                data = C.cast(pointer, C.POINTER(KBDLLHOOKSTRUCT)).contents
                if self.policy.handle(data.vkCode, message in KEYDOWN, bool(data.flags & 0x20)):
                    return 1
        except BaseException:
            self.stop.request("hook_callback_failed", "hook_callback_failed")
        return self.api.user.CallNextHookEx(None, code, message, pointer)

    def start(self):
        self.thread.start()
        if not self.ready.wait(2.0):
            self.stop.request("hook_start_timeout", "hook_start_timeout")
        if not self.handle:
            self.stop.request("hook_install_failed", "hook_install_failed")

    def _pump(self):
        try:
            self.thread_id = self.api.kernel.GetCurrentThreadId()
            message = W.MSG()
            self.api.user.PeekMessageW(C.byref(message), None, 0, 0, 0)  # create this thread's message queue
            # Initial state only, outside the callback: the current event has not
            # yet updated GetAsyncKeyState when Windows invokes the hook.
            # Do not seed generic VK_CONTROL/SHIFT/MENU as well as their sided
            # keys: a later sided release would otherwise leave a stale alias.
            self.policy.modifiers = {k for k in MODIFIERS if k >= 0xA0 and self.api.user.GetAsyncKeyState(k) & 0x8000}
            if self.stop.event.is_set():
                return
            with self._lock:
                self.handle = self.api.user.SetWindowsHookExW(
                    WH_KEYBOARD_LL, self.callback, self.api.kernel.GetModuleHandleW(None), 0)
            if not self.handle:
                self.stop.request("hook_install_failed", "hook_install_failed")
                return
            self.ready.set()
            while not self.stop.event.is_set():
                result = self.api.user.GetMessageW(C.byref(message), None, 0, 0)
                if result == -1:
                    self.stop.request("hook_message_failed", "hook_message_failed")
                    break
                if result == 0:
                    self.stop.request("hook_stopped")
                    break
                self.api.user.TranslateMessage(C.byref(message))
                self.api.user.DispatchMessageW(C.byref(message))
        except BaseException:
            self.stop.request("hook_failed", "hook_failed")
        finally:
            self._unhook()
            self.ready.set()

    def _unhook(self):
        with self._lock:
            if self.handle:
                if not self.api.user.UnhookWindowsHookEx(self.handle):
                    self.stop.request("unhook_failed", "unhook_failed")
                self.handle = None

    def close(self):
        self.stop.event.set()
        if self.thread_id:
            self.api.user.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        if self.thread.ident is not None:
            self.thread.join(timeout=0.5)
        self._unhook()  # fallback if the message thread did not finish promptly
