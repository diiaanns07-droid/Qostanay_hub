import ctypes as C
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from environment_check import REMOTE_BASENAMES, remote_processes, snapshot
from guard_win32 import Win32, PROCESSENTRY32W


def test_exact_case_insensitive_remote_basenames_only():
    assert remote_processes([r"C:\Apps\AnyDesk.EXE", "anydesk.exe", "chrome.exe", "anydesk.exe.bak", "notrustdesk.exe"]) == ["anydesk.exe"]
    assert len(remote_processes([n.upper() + ".EXE" for n in REMOTE_BASENAMES])) == 11


def test_snapshot_filters_before_output_and_reads_rdp():
    api = SimpleNamespace(process_names=lambda: ["chrome.exe", "rustdesk.exe"], remote_session=lambda: True)
    assert snapshot(api) == dict(type="environment", processes=["rustdesk.exe"], remote_session=True)
    seen = []
    api = Win32.__new__(Win32)
    api.user = SimpleNamespace(GetSystemMetrics=lambda metric: seen.append(metric) or 1)
    assert api.remote_session() is True
    assert seen == [0x1000]


def test_toolhelp_snapshot_is_read_only_and_closes_handle(monkeypatch):
    api = Win32.__new__(Win32)
    calls = []
    def first(handle, pointer):
        entry = C.cast(pointer, C.POINTER(PROCESSENTRY32W)).contents
        assert entry.dwSize == C.sizeof(PROCESSENTRY32W)
        entry.szExeFile = "TeamViewer.exe"
        return True
    api.kernel = SimpleNamespace(
        CreateToolhelp32Snapshot=lambda flags, pid: calls.append((flags, pid)) or 123,
        Process32FirstW=first, Process32NextW=lambda *a: False,
        CloseHandle=lambda handle: calls.append(("close", handle)))
    monkeypatch.setattr(C, "get_last_error", lambda: 18, raising=False)
    assert api.process_names() == ["TeamViewer.exe"]
    assert calls == [(2, 0), ("close", 123)]
    monkeypatch.setattr(C, "get_last_error", lambda: 5, raising=False)
    with pytest.raises(OSError, match="enumeration"):
        api.process_names()
    assert calls[-1] == ("close", 123)


def test_snapshot_failure_does_not_claim_no_remote_processes():
    api = Win32.__new__(Win32)
    api.kernel = SimpleNamespace(CreateToolhelp32Snapshot=lambda *a: C.c_void_p(-1).value)
    with pytest.raises(OSError, match="snapshot"):
        api.process_names()
