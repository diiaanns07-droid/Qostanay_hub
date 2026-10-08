"""Backend preflight adapter check; no device/desktop access."""
from types import SimpleNamespace
import pytest
from proctor.session import SessionRuntime
from proctor_contracts.v1 import EnvironmentCapabilities, SourceMode


@pytest.mark.parametrize("state", ["detected", "unknown", "not_detected"])
@pytest.mark.parametrize("hard_failure", [None, "electron.display_count.multiple", "native.remote_check.anydesk", "unsupported"])
def test_vm_advisory_never_masks_environment_failure(state, hard_failure):
    runtime = SessionRuntime.__new__(SessionRuntime)
    runtime.mode = SourceMode.LIVE
    items = [dict(action="shortcut_ctrl_c", status="blocked", mechanism="electron.before_input_event"),
             dict(action="exam_mode_engaged", status="unverified" if state == "unknown" else "detected_only",
                  mechanism="native.vm_check." + state, note_ru="VM result " + state)]
    if hard_failure and hard_failure != "unsupported":
        items.append(dict(action="display_changed", status="detected_only", mechanism=hard_failure))
    caps = EnvironmentCapabilities(platform="test", shell_version="test", reported_at="2026-10-08T00:00:00Z",
                                   exam_mode_supported=not hard_failure, items=items)
    runtime._provider = SimpleNamespace(environment_capabilities=lambda: caps)
    result = runtime._environment_check()
    if hard_failure:
        assert result.status == "fail" and result.required
    elif state == "not_detected":
        assert result.status == "pass" and result.required
    else:
        assert result.status == "warn" and not result.required
        assert result.details["vm_state"] == state
        assert result.message_ru == "VM result " + state


def test_second_monitor_is_required_environment_failure():
    runtime = SessionRuntime.__new__(SessionRuntime)
    runtime.mode = SourceMode.LIVE
    caps = EnvironmentCapabilities(platform="test", shell_version="test", reported_at="2026-10-08T00:00:00Z",
        exam_mode_supported=False, items=[dict(action="display_changed", status="detected_only",
            mechanism="electron.display_count.multiple", note_ru="Отключите второй монитор, чтобы начать")])
    runtime._provider = SimpleNamespace(environment_capabilities=lambda: caps)
    result = runtime._environment_check()
    assert result.status == "fail" and result.required
    assert result.message_ru == "Отключите второй монитор, чтобы начать"


def test_remote_process_preflight_failure():
    runtime = SessionRuntime.__new__(SessionRuntime)
    runtime.mode = SourceMode.LIVE
    caps = EnvironmentCapabilities(platform="test", shell_version="test", reported_at="2026-10-08T00:00:00Z",
        exam_mode_supported=False, items=[dict(action="foreign_window_foreground", status="detected_only",
            mechanism="native.remote_check.anydesk", note_ru="Закройте anydesk.exe, чтобы начать")])
    runtime._provider = SimpleNamespace(environment_capabilities=lambda: caps)
    assert runtime._environment_check().status == "fail"
    assert runtime._environment_check().message_ru == "Закройте anydesk.exe, чтобы начать"
