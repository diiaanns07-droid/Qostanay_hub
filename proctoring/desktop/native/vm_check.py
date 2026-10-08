"""Read-only WMI VM hints. No identifiers, hooks, settings or network access."""
from __future__ import annotations

import json
import os
import re
import subprocess

WMI_SCRIPT = r"""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$cs = Get-CimInstance Win32_ComputerSystem -Property Manufacturer,Model -ErrorAction Stop
$bios = Get-CimInstance Win32_BIOS -Property Manufacturer,SMBIOSBIOSVersion,Version -ErrorAction Stop
@{manufacturer=[string]$cs.Manufacturer; model=[string]$cs.Model;
  bios_manufacturer=[string]$bios.Manufacturer; bios_version=[string]$bios.SMBIOSBIOSVersion;
  bios_legacy_version=[string]$bios.Version} | ConvertTo-Json -Compress
"""


def unknown():
    return {"type": "vm", "state": "unknown", "platform": None}


def classify(fields):
    keys = ("manufacturer", "model", "bios_manufacturer", "bios_version", "bios_legacy_version")
    if not isinstance(fields, dict) or any(not isinstance(fields.get(k), str) for k in keys):
        return unknown()
    values = {k: fields[k].strip().lower() for k in keys}
    if not values["manufacturer"] or not values["model"] or not any(values[k] for k in keys[2:]):
        return unknown()
    text = " ".join(values.values())
    platform = None
    if "vmware" in text:
        platform = "VMware"
    elif "virtualbox" in text or "innotek gmbh" in text:
        platform = "VirtualBox"
    elif values["manufacturer"] == "microsoft corporation" and values["model"] == "virtual machine":
        platform = "Hyper-V"
    elif re.search(r"\b(qemu|kvm)\b", text):
        platform = "QEMU/KVM"
    elif "parallels" in text:
        platform = "Parallels"
    elif re.search(r"\bxen\b", text):
        platform = "Xen"
    return {"type": "vm", "state": "detected" if platform else "not_detected", "platform": platform}


def read_wmi():
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                             "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", WMI_SCRIPT],
                            capture_output=True, timeout=8, check=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if len(result.stdout) > 8192:
        raise ValueError("oversized WMI response")
    return json.loads(result.stdout.decode("utf-8-sig"))


def snapshot(reader=read_wmi):
    try:
        return classify(reader())
    except Exception:
        return unknown()  # access denied, missing provider, timeout and malformed response
