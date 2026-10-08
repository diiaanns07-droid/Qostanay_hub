"""Backend entry point (owner: A01).

    python -m proctor serve --token-stdin [--port 0]     # how Electron main (A06) starts it
    python -m proctor serve --token-env QORGAU_DEV_TOKEN  # manual development
    python -m proctor smoke                               # synthetic end-to-end self-check

Readiness handshake: after startup exactly one line is printed to STDOUT:
    QORGAU_READY {"contract": "qorgau.v1", "contract_version": "1.0.0", "port": 53123, ...}
All logs go to STDERR. The token is never printed, logged, or put on the command line.
With --token-stdin the process exits when its stdin reaches EOF (parent died).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import sys
import threading

from proctor_contracts.v1 import CONTRACT_ID, CONTRACT_VERSION

from .settings import BACKEND_VERSION, Settings

READY_PREFIX = "QORGAU_READY "
_QUERY_RE = re.compile(r"\?[^\s\"']*")


class StripQueryFilter(logging.Filter):
    """QA-BUG-001: uvicorn logs WebSocket handshakes as 'WebSocket /path?query'. Tokens never belong in a URL,
    but if a client misplaces one it must not reach the log: drop every query string from the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(_QUERY_RE.sub("", a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str) and "?" in record.msg:
            record.msg = _QUERY_RE.sub("", record.msg)
        return True


def install_log_filters() -> None:
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "websockets", "websockets.server"):
        logging.getLogger(name).addFilter(StripQueryFilter())


def _bind(host: str, port: int) -> socket.socket:
    if host not in ("127.0.0.1", "localhost"):
        raise SystemExit("refusing to bind a non-loopback address")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 0xFFFFFFFB), 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(64)
    sock.set_inheritable(False)
    return sock


def serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app

    settings = Settings.from_env(**({"port": args.port} if args.port is not None else {}))
    logging.basicConfig(
        level=settings.log_level, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if args.token_stdin:
        token = sys.stdin.readline().strip()
    elif args.token_env:
        token = os.environ.get(args.token_env, "").strip()
    else:
        print("error: --token-stdin or --token-env VAR is required (no unauthenticated mode)", file=sys.stderr)
        return 2
    if len(token) < 32:
        print("error: API token must be at least 32 characters", file=sys.stderr)
        return 2

    sock = _bind(settings.host, settings.port)
    port = sock.getsockname()[1]

    def on_ready() -> None:
        line = {
            "contract": CONTRACT_ID,
            "contract_version": CONTRACT_VERSION,
            "backend_version": BACKEND_VERSION,
            "port": port,
            "pid": os.getpid(),
        }
        sys.stdout.write(READY_PREFIX + json.dumps(line) + "\n")
        sys.stdout.flush()

    app = create_app(settings, token, on_ready=on_ready)
    config = uvicorn.Config(app, log_level=settings.log_level.lower(), access_log=False, lifespan="on", log_config=None, timeout_graceful_shutdown=5)
    server = uvicorn.Server(config)
    install_log_filters()

    if args.token_stdin or args.exit_on_stdin_eof:

        def watchdog() -> None:
            for line in sys.stdin:
                if line.strip() == "shutdown":
                    break
            logging.getLogger("proctor").warning("stdin closed or shutdown requested: stopping backend")
            server.should_exit = True

        threading.Thread(target=watchdog, name="stdin-watchdog", daemon=True).start()

    server.run(sockets=[sock])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m proctor", description="Qorgau Exam local backend")
    sub = parser.add_subparsers(dest="command", required=True)
    p_serve = sub.add_parser("serve", help="run the loopback API server")
    p_serve.add_argument("--port", type=int, default=None, help="port (default QORGAU_PORT or 0 = ephemeral)")
    group = p_serve.add_mutually_exclusive_group()
    group.add_argument("--token-stdin", action="store_true", help="read the bearer token from the first stdin line")
    group.add_argument("--token-env", metavar="VAR", help="read the bearer token from this environment variable")
    p_serve.add_argument("--exit-on-stdin-eof", action="store_true", help="exit when stdin closes")
    p_smoke = sub.add_parser("smoke", help="synthetic end-to-end self-check through a real server")
    p_smoke.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(args)
    from .bootstrap.smoke import run_smoke

    return run_smoke(as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
