"""Backend preflight adapter check; no device/desktop access."""
from types import SimpleNamespace
from proctor.session import SessionRuntime
from proctor_contracts.v1 import EnvironmentCapabilities, SourceMode


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
