"""Process-level network guard for offline checks (owner: A09). Portable: Linux and Windows.

    python -m qorgau_qa.netguard --log guard.jsonl -- -m proctor serve --token-stdin --port 0
    python -m qorgau_qa.netguard --log guard.jsonl -- -c "import socket; socket.create_connection(('203.0.113.7', 80))"

Installs a CPython audit hook (PEP 578) in THIS process only, then runs the target module/code.
Every attempt to resolve a non-local name or to connect/send to a non-loopback address is
appended to the JSONL log and aborted with PermissionError. Loopback, AF_UNIX and binding are allowed.
The global firewall / network of the machine is never touched.

Limits (stated in qa/RESULTS.md): covers only Python-level sockets of the guarded process and its
in-process libraries; native code that opens sockets without going through the CPython socket
module is not seen — on Linux the netns layer (qorgau_qa.netns) covers that, on Windows use an
isolated VM / Windows Sandbox with networking disabled.
"""

from __future__ import annotations

import ipaddress
import json
import os
import runpy
import sys
import time

LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", ""}
_EVENTS = {"socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex", "socket.gethostbyaddr", "urllib.Request"}


def _is_local_host(host: object) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    h = host.strip("[]").lower()
    if h in LOCAL_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def _address_host(address: object) -> object:
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return None
    if isinstance(address, tuple) and address:
        return address[0]
    return "<unknown-address>"


def install(log_path: str) -> None:
    log = open(log_path, "a", encoding="utf-8", buffering=1)

    def hook(event: str, args: tuple) -> None:
        if event not in _EVENTS:
            return
        if event in ("socket.connect", "socket.sendto"):
            host = _address_host(args[1]) if len(args) > 1 else "<unknown-address>"
        elif event == "socket.sendmsg":
            host = _address_host(args[1]) if len(args) > 1 and args[1] is not None else None
        elif event == "urllib.Request":
            host = args[0] if args else "<unknown-url>"
            host = str(host).split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0]
        else:
            host = args[0] if args else None
        if _is_local_host(host):
            return
        log.write(json.dumps({"t": time.time(), "pid": os.getpid(), "event": event, "host": str(host)}) + "\n")
        raise PermissionError(f"qa netguard: blocked {event} to {host!r} (offline check)")

    sys.addaudithook(hook)


def main(argv: list[str]) -> int:
    if "--" not in argv or argv[:1] != ["--log"]:
        print("usage: python -m qorgau_qa.netguard --log FILE -- (-m module | -c code) [args...]", file=sys.stderr)
        return 2
    log_path = argv[1]
    rest = argv[argv.index("--") + 1 :]
    install(log_path)
    if rest[:1] == ["-m"] and len(rest) >= 2:
        sys.argv = [rest[1], *rest[2:]]
        runpy.run_module(rest[1], run_name="__main__", alter_sys=True)
        return 0
    if rest[:1] == ["-c"] and len(rest) >= 2:
        sys.argv = ["-c", *rest[2:]]
        exec(compile(rest[1], "<netguard -c>", "exec"), {"__name__": "__main__"})
        return 0
    print("netguard: expected -m module or -c code after --", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
