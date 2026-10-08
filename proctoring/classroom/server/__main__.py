"""`python -m classroom.server` — run the Qorgau class server (owner: T01).

    python -m classroom.server                               # 0.0.0.0:8765, data in %LOCALAPPDATA%\\QorgauClassroom
    python -m classroom.server --port 0 --data-dir ./tmp     # tests: ephemeral port
Prints to STDOUT (one line each, nothing else):
    QORGAU_CLASS_READY {"port": 8765, "pid": 1234, "contract": "qorgau.classroom", ...}
    QORGAU_CLASS_PIN 123456          <- teacher PIN (v1 §2.6), valid until the server stops
Logs go to STDERR. Windows asks the user for a firewall permission on the first start (v1 §1); this program
never changes firewall rules.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
from dataclasses import replace
from pathlib import Path

from ..contracts.models import CONTRACT_ID, CONTRACT_VERSION, WIRE_PROTOCOL
from .config import SERVER_VERSION, ServerConfig


def _bind(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 0xFFFFFFFB), 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    sock.set_inheritable(False)
    return sock


def _lan_addresses() -> list[str]:
    out: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if not addr.startswith("127.") and addr not in out:
                out.append(addr)
    except OSError:
        pass
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m classroom.server", description="Qorgau class server (qorgau.class.v1)")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--features", default=None, help='comma list "module:factory" (default QORGAU_CLASS_FEATURES)')
    ap.add_argument("--exit-on-stdin-eof", action="store_true", help="stop when stdin closes (test harnesses)")
    args = ap.parse_args(argv)
    overrides = {k: v for k, v in (("host", args.host), ("port", args.port), ("data_dir", args.data_dir), ("features", args.features)) if v is not None}
    config = ServerConfig.from_env(**overrides)
    logging.basicConfig(level=config.log_level, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import uvicorn

    from .app import create_app

    try:
        sock = _bind(config.host, config.port)
    except OSError as exc:
        print(f"error: cannot listen on {config.host}:{config.port}: {exc}", file=sys.stderr)
        return 2
    port = sock.getsockname()[1]
    holder = {"port": port}
    try:
        app = create_app(replace(config, port=port), port_holder=holder)
    except Exception as exc:  # e.g. edited migration, unreadable data dir: fail visibly, never half-start
        logging.getLogger("classroom.server").exception("startup failed")
        print(f"error: startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    pin = app.state.teacher_auth.pin

    server = uvicorn.Server(uvicorn.Config(app, log_level=config.log_level.lower(), access_log=False, lifespan="on", log_config=None, ws_max_size=config.max_ws_message_bytes + 4096, timeout_graceful_shutdown=5))

    def announce() -> None:
        ready = {"port": port, "pid": os.getpid(), "contract": CONTRACT_ID, "contract_version": CONTRACT_VERSION, "wire_protocol": WIRE_PROTOCOL, "server_version": SERVER_VERSION}
        sys.stdout.write("QORGAU_CLASS_READY " + json.dumps(ready) + "\n")
        sys.stdout.write(f"QORGAU_CLASS_PIN {pin}\n")
        sys.stdout.flush()
        lan = ", ".join(f"{a}:{port}" for a in _lan_addresses()) or "(адрес в сети не определён)"
        print(f"Панель преподавателя: http://127.0.0.1:{port}/   PIN преподавателя: {pin}", file=sys.stderr)
        print(f"Адрес для студентов: {lan}", file=sys.stderr)

    original_startup = server.startup

    async def startup(sockets=None):  # type: ignore[no-untyped-def]
        await original_startup(sockets=sockets)
        announce()

    server.startup = startup  # type: ignore[method-assign]

    if args.exit_on_stdin_eof:
        def watchdog() -> None:
            for _ in sys.stdin:
                pass
            server.should_exit = True

        threading.Thread(target=watchdog, name="stdin-watchdog", daemon=True).start()

    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
