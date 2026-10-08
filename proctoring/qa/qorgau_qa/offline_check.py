"""Offline run: the full scenario with the backend under the network guard (owner: A09).

    python -m qorgau_qa.offline_check --data-dir D --guard-log G.jsonl [--expect-isolated]
    unshare -rn python -m qorgau_qa.netns -- python -m qorgau_qa.offline_check ... --expect-isolated

Prints one JSON document: isolation probe, scenario rows, guard entries, backend exit code.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

from .backend import BackendProcess
from .scenario import run_full_flow


def probe_isolation() -> dict[str, str]:
    """Is the outside world unreachable from THIS process tree? (TEST-NET-3 + a public resolver + DNS)."""
    result = {}
    for name, addr in (("tcp_test_net", ("203.0.113.7", 80)), ("tcp_public_dns", ("1.1.1.1", 53))):
        try:
            socket.create_connection(addr, timeout=2).close()
            result[name] = "REACHABLE"
        except OSError as exc:
            result[name] = f"unreachable ({exc.__class__.__name__}: {exc.strerror or exc})"
    try:
        socket.getaddrinfo("pypi.org", 443)
        result["dns"] = "RESOLVED"
    except OSError as exc:
        result["dns"] = f"no resolution ({exc})"
    return result


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--guard-log", required=True)
    ap.add_argument("--expect-isolated", action="store_true")
    args = ap.parse_args(argv)
    guard_log = Path(args.guard_log)
    guard_log.write_text("", encoding="utf-8")
    out: dict = {"isolation": probe_isolation() if args.expect_isolated else "not requested"}
    be = BackendProcess.start(Path(args.data_dir), argv_prefix=[sys.executable, "-m", "qorgau_qa.netguard", "--log", str(guard_log), "--"])
    try:
        rows = run_full_flow(be)
        out["rows"] = rows.as_json()
    finally:
        out["exit_code"] = be.stop()
    out["token_leaks"] = len(be.token_leaks())
    out["guard_entries"] = [json.loads(l) for l in guard_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
