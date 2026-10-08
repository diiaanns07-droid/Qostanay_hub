from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vm_check


def fields(manufacturer="LENOVO", model="ThinkPad", bios="LENOVO", version="1.2"):
    return dict(manufacturer=manufacturer, model=model, bios_manufacturer=bios,
                bios_version=version, bios_legacy_version="BIOS 1.2")


@pytest.mark.parametrize("data,platform", [
    (fields("VMware, Inc.", "VMware Virtual Platform"), "VMware"),
    (fields("innotek GmbH", "VirtualBox"), "VirtualBox"),
    (fields("Oracle Corporation", "VirtualBox"), "VirtualBox"),
    (fields("Microsoft Corporation", "Virtual Machine"), "Hyper-V"),
    (fields("QEMU", "Standard PC (Q35 + ICH9, 2009)"), "QEMU/KVM"),
    (fields("Red Hat", "KVM"), "QEMU/KVM"),
    (fields("Parallels Software International Inc.", "Parallels Virtual Platform"), "Parallels"),
    (fields("Xen", "HVM domU"), "Xen"),
    (fields(bios="VMware, Inc."), "VMware"),
    (fields(version="VirtualBox"), "VirtualBox"),
])
def test_platforms(data, platform):
    assert vm_check.snapshot(lambda: data) == dict(type="vm", state="detected", platform=platform)


@pytest.mark.parametrize("data", [fields(), fields("Microsoft Corporation", "Surface Pro"),
                                  fields("Dell Inc.", "Precision", "SeaBIOS")])
def test_physical_hardware_and_hyperv_host_not_guest(data):
    assert vm_check.classify(data) == dict(type="vm", state="not_detected", platform=None)


@pytest.mark.parametrize("error", [PermissionError(), OSError(), subprocess.TimeoutExpired("WMI", 8), ValueError()])
def test_read_failure_is_unknown(error):
    def reader():
        raise error
    assert vm_check.snapshot(reader) == vm_check.unknown()


@pytest.mark.parametrize("data", [None, {}, fields(model=""), fields(bios="", version="") | {"bios_legacy_version": ""}])
def test_incomplete_wmi_is_unknown(data):
    assert vm_check.classify(data) == vm_check.unknown()


def test_wmi_command_is_bounded_read_only_and_selects_no_identifiers(monkeypatch):
    seen = []
    def run(args, **kwargs):
        seen.append((args, kwargs))
        return SimpleNamespace(stdout=b'{"manufacturer":"Dell"}')
    monkeypatch.setattr(vm_check.subprocess, "run", run)
    vm_check.read_wmi()
    args, kwargs = seen[0]
    assert kwargs["timeout"] == 8 and kwargs["check"] and kwargs["capture_output"]
    assert "-NonInteractive" in args and "-NoProfile" in args
    assert "Get-CimInstance Win32_ComputerSystem" in args[-1]
    assert "Get-CimInstance Win32_BIOS" in args[-1]
    for forbidden in ("SerialNumber", "UUID", "Set-CimInstance", "Invoke-CimMethod"):
        assert forbidden not in args[-1]
