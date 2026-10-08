"""Linux-only: run a command in a fresh network namespace that has ONLY loopback (owner: A09).

    unshare -rn <python> -m qorgau_qa.netns -- <command...>

`unshare -rn` (user + network namespace, no root on the host needed) gives the child an empty
network stack; this helper brings `lo` up (ioctl SIOCSIFFLAGS) and execs the command. Only the
test process tree is isolated; the host network/firewall is not changed.
"""

from __future__ import annotations

import os
import shutil
import socket
import struct
import subprocess
import sys

SIOCGIFFLAGS = 0x8913
SIOCSIFFLAGS = 0x8914
IFF_UP = 0x1


def bring_loopback_up() -> None:
    import fcntl  # Linux only; imported lazily so Windows can import available()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        ifr = struct.pack("16sh", b"lo", 0)
        flags = struct.unpack("16sh", fcntl.ioctl(s, SIOCGIFFLAGS, ifr))[1]
        fcntl.ioctl(s, SIOCSIFFLAGS, struct.pack("16sh", b"lo", flags | IFF_UP))


def available() -> tuple[bool, str]:
    """Probe whether `unshare -rn` works here (containers may forbid user namespaces)."""
    if not sys.platform.startswith("linux"):
        return False, "not Linux"
    exe = shutil.which("unshare")
    if exe is None:
        return False, "unshare not installed"
    try:
        proc = subprocess.run([exe, "-rn", sys.executable, "-c", "print('ok')"], capture_output=True, text=True, timeout=20)
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0 or proc.stdout.strip() != "ok":
        return False, f"unshare -rn failed: {proc.stderr.strip()[:200]}"
    return True, exe


def main(argv: list[str]) -> int:
    if argv[:1] != ["--"] or len(argv) < 2:
        print("usage: unshare -rn python -m qorgau_qa.netns -- command...", file=sys.stderr)
        return 2
    bring_loopback_up()
    cmd = argv[1:]
    os.execvp(cmd[0], cmd)
    return 127  # not reached


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
