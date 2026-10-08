"""Offline operation: no runtime network requests/downloads after preparation (prompt item 8).

How it is checked (never by touching the machine's firewall/network):
  layer A (portable, Linux + Windows): CPython audit hook in the backend process records/blocks
          every non-loopback connect and every non-local DNS lookup (qorgau_qa.netguard);
  layer B (Linux only): the whole test process tree runs in a fresh network namespace that has
          only loopback (`unshare -rn` + qorgau_qa.netns) — catches native code as well.
On BOOTSTRAP this covers the synthetic pipeline only; the CV models (A03/A04) are re-checked on the
integration candidate.
"""

from __future__ import annotations

import json
import ast
import re
import subprocess
import sys
from collections import Counter

import pytest

from qorgau_qa.backend import PROCTORING_ROOT, QA_ROOT, backend_env
from qorgau_qa.netns import available as netns_available


def _guard(code: str, log, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "qorgau_qa.netguard", "--log", str(log), "--", "-c", code], capture_output=True, text=True, env=backend_env(), cwd=str(PROCTORING_ROOT), timeout=timeout)


# ------------------------------------------------------------------ harness self-check (layer A)


def test_netguard_blocks_and_records_external_connect(tmp_path):
    log = tmp_path / "g.jsonl"
    p = _guard("import socket; socket.create_connection(('203.0.113.7', 80), timeout=1)", log)
    assert p.returncode != 0 and "PermissionError" in p.stderr
    entries = [json.loads(l) for l in log.read_text().splitlines()]
    assert entries and entries[0]["host"] == "203.0.113.7" and entries[0]["event"] in ("socket.getaddrinfo", "socket.connect")
    log.write_text("")
    p = _guard("import socket; s=socket.socket(); s.settimeout(1); s.connect(('203.0.113.7', 80))", log)
    assert p.returncode != 0
    assert [json.loads(l)["event"] for l in log.read_text().splitlines()] == ["socket.connect"]


def test_netguard_blocks_dns_and_urllib(tmp_path):
    log = tmp_path / "g.jsonl"
    p = _guard("import urllib.request; urllib.request.urlopen('https://pypi.org/simple/', timeout=2)", log)
    assert p.returncode != 0
    hosts = {json.loads(l)["host"] for l in log.read_text().splitlines()}
    assert "pypi.org" in hosts


def test_netguard_allows_loopback(tmp_path):
    log = tmp_path / "g.jsonl"
    code = "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); c=socket.create_connection(s.getsockname()); socket.getaddrinfo('localhost', 80); print('ok')"
    p = _guard(code, log)
    assert p.returncode == 0 and p.stdout.strip() == "ok", p.stderr
    assert log.read_text() == ""


# ------------------------------------------------------------------ product offline runs


def _offline_run(tmp_path, prefix: list[str], expect_isolated: bool) -> dict:
    cmd = [*prefix, sys.executable, "-m", "qorgau_qa.offline_check", "--data-dir", str(tmp_path / "data"), "--guard-log", str(tmp_path / "guard.jsonl")]
    if expect_isolated:
        cmd.append("--expect-isolated")
    p = subprocess.run(cmd, capture_output=True, text=True, env=backend_env(), cwd=str(PROCTORING_ROOT), timeout=300)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def _assert_offline_ok(result: dict) -> None:
    failed = [r for r in result["rows"] if r["status"] == "FAIL"]
    assert not failed, failed
    assert result["guard_entries"] == [], f"backend attempted network access: {result['guard_entries']}"
    assert result["exit_code"] == 0 and result["token_leaks"] == 0


def test_full_flow_offline_audit_guard(tmp_path, record_property):
    result = _offline_run(tmp_path, [], expect_isolated=False)
    record_property("layer", "A: audit hook in backend process")
    _assert_offline_ok(result)


def test_full_flow_offline_network_namespace(tmp_path, record_property):
    ok, why = netns_available()
    if not ok:
        pytest.skip(f"layer B not available here: {why}")
    result = _offline_run(tmp_path, ["unshare", "-rn", sys.executable, "-m", "qorgau_qa.netns", "--"], expect_isolated=True)
    record_property("isolation", json.dumps(result["isolation"]))
    iso = result["isolation"]
    assert all(not v.startswith(("REACHABLE", "RESOLVED")) for v in iso.values()), f"namespace is NOT isolated: {iso}"
    _assert_offline_ok(result)


# ------------------------------------------------------------------ static gate


DOWNLOAD_PATTERNS = re.compile(
    r"\b(urllib\.request|urlopen|requests\.(get|post)|http\.client|hf_hub_download|snapshot_download|torch\.hub|"
    r"YOLO\(|ultralytics|download_url|wget|curl|pip install|attempt_download)\b"
)

# Explicitly reviewed network operations; not whole-file or whole-directory exemptions.
# Each scope/name/count must still match. Any additional call (even in these files) needs review.
# Preparation downloads are operator-invoked before the exam, with manifest/hash verification.
# Classroom calls implement the user's optional LAN transport; they are not model downloads.
# Neither category bypasses netguard: public network attempts are still blocked during QA.
REVIEWED_NETWORK_USES = {
    "phone/prepare.py": {("", "import urllib.request"): 1, ("_iter_url", "urllib.request.urlopen"): 1},
    "attention/model_tool.py": {("", "import urllib.request"): 1, ("fetch", "urllib.request.urlopen"): 1},
    "audio/prepare.py": {("", "import urllib.request"): 1, ("download", "urllib.request.urlopen"): 2},
    "identity/prepare.py": {("", "import urllib.request"): 1, ("_iter_url", "urllib.request.urlopen"): 1},
    "uplink/client.py": {("", "import urllib.request"): 1, ("http_post_file", "urllib.request.Request"): 1,
                         ("http_post_file", "urllib.request.urlopen"): 1},
    "uplink/demo_teacher.py": {("", "import urllib.request"): 1,
        ("Teacher.__init__", "urllib.request.build_opener"): 1,
        ("Teacher.__init__", "urllib.request.HTTPCookieProcessor"): 1,
        ("Teacher.call", "urllib.request.Request"): 1},
}


def _unreviewed_network_uses(source: str, rel: str) -> list[str]:
    reviewed = REVIEWED_NETWORK_USES.get(rel, {})
    counts = Counter()
    reviewed_lines = set()

    class Uses(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def scoped(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_FunctionDef = scoped
        visit_AsyncFunctionDef = scoped
        visit_ClassDef = scoped

        def note(self, node, name):
            key = (".".join(self.scope), name)
            if DOWNLOAD_PATTERNS.search(name):
                counts[key] += 1
                if key in reviewed:
                    reviewed_lines.update(range(node.lineno, node.end_lineno + 1))

        def visit_Import(self, node):
            for alias in node.names:
                self.note(node, f"import {alias.name}")

        def visit_Call(self, node):
            self.note(node, ast.unparse(node.func))
            self.generic_visit(node)

    Uses().visit(ast.parse(source))
    hits = [f"{rel}: network operation {scope or '<module>'}:{name} expected {reviewed.get((scope, name), 0)}, got {count}"
            for (scope, name), count in counts.items() if count != reviewed.get((scope, name), 0)]
    hits.extend(f"{rel}: reviewed operation removed/moved: {scope}:{name}"
                for (scope, name), count in reviewed.items() if counts[(scope, name)] < count)
    for n, line in enumerate(source.splitlines(), 1):
        if n not in reviewed_lines and not line.lstrip().startswith("#") and DOWNLOAD_PATTERNS.search(line):
            hits.append(f"{rel}:{n}: {line.strip()[:120]}")
    return hits


def test_no_download_calls_in_backend_runtime_source():
    """Heuristic gate: only explicitly reviewed preparation/classroom calls are allowed.

    Scans proctoring/backend/proctor (excluding tests and the A01 smoke client, which talks to
    127.0.0.1 only). A hit is not automatically a bug; it must be justified or removed.
    """
    hits = []
    root = PROCTORING_ROOT / "backend" / "proctor"
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if "/tests/" in f"/{rel}" or rel.endswith("bootstrap/smoke.py"):
            continue
        hits.extend(_unreviewed_network_uses(path.read_text(encoding="utf-8"), rel))
    assert not hits, "\n".join(hits)


def test_reviewed_network_scopes_cannot_hide_new_runtime_calls():
    root = PROCTORING_ROOT / "backend" / "proctor"
    for rel in REVIEWED_NETWORK_USES:
        source = (root / rel).read_text(encoding="utf-8")
        assert not _unreviewed_network_uses(source, rel)
        changed = source + "\ndef unintended_runtime_download():\n    urllib.request.urlopen('https://example.invalid')\n"
        assert _unreviewed_network_uses(changed, rel), rel
    source = "import urllib.request\ndef runtime():\n    urllib.request.urlopen('https://example.invalid')\n"
    assert _unreviewed_network_uses(source, "unreviewed_module.py")
    # A second download in an already reviewed function is also a change, not an exemption.
    source = "import urllib.request\ndef _iter_url(url):\n    urllib.request.urlopen(url)\n    urllib.request.urlopen(url)\n"
    assert _unreviewed_network_uses(source, "phone/prepare.py")


def test_reviewed_classroom_uploader_still_cannot_access_public_network(tmp_path):
    clip = tmp_path / "test-payload.bin"
    clip.write_bytes(b"SYNTHETIC QA NETWORK GUARD CHECK")
    log = tmp_path / "uplink-guard.jsonl"
    code = ("from pathlib import Path; from proctor.uplink.client import http_post_file; "
            f"ok, reason = http_post_file('http://203.0.113.7:8765/api/student/clips/test', Path({str(clip)!r}), "
            "'qa-fixture-token', 'video/mp4', timeout_s=1); assert not ok; print('public upload blocked')")
    result = _guard(code, log)
    assert result.returncode == 0 and result.stdout.strip() == "public upload blocked", result.stderr
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert entries and all(entry["host"] == "203.0.113.7" for entry in entries)


def test_qa_harness_itself_is_offline():
    """The harness must not fetch anything either (keeps the offline verdict honest)."""
    hits = []
    for path in QA_ROOT.rglob("*.py"):
        if path.name == "test_offline.py" or path.name == "netguard.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"https?://(?!127\.0\.0\.1|localhost)", line) and "evil.example" not in line and "qorgau.local" not in line:
                hits.append(f"{path.name}:{n}: {line.strip()[:100]}")
    assert not hits, hits
