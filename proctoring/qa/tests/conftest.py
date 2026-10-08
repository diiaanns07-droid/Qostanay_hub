"""A09 QA suite fixtures. Run from proctoring/:  python -m pytest qa/tests -q

Every test talks to a REAL backend process over loopback HTTP/WS (no in-process TestClient,
no backend internals). Synthetic sessions only on BOOTSTRAP: results are wiring/contract/negative
checks, not CV accuracy and not Windows environment protection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

QA_ROOT = Path(__file__).resolve().parents[1]
if str(QA_ROOT) not in sys.path:
    sys.path.insert(0, str(QA_ROOT))

from qorgau_qa.backend import Api, BackendProcess  # noqa: E402


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    """One backend process per test module; every test leaves no active session behind."""
    be = BackendProcess.start(tmp_path_factory.mktemp("data"))
    yield be
    rc = be.stop()
    leaks = be.token_leaks()
    assert rc == 0, f"backend exit code {rc} after stdin EOF; stderr tail: {''.join(be.stderr_lines[-10:])}"
    assert not leaks, f"API token appeared in backend stdout/stderr: {leaks}"


@pytest.fixture()
def api(backend):
    a = Api(backend.http)
    a.abort_active()
    yield a
    a.abort_active()


@pytest.fixture()
def fresh_backend(tmp_path):
    """Factory for tests that need their own process (crash, shutdown, env overrides)."""
    started: list[BackendProcess] = []

    def make(**kwargs) -> BackendProcess:
        be = BackendProcess.start(kwargs.pop("data_dir", tmp_path / f"data{len(started)}"), **kwargs)
        started.append(be)
        return be

    yield make
    for be in started:
        if be.exit_code is None:
            be.stop()
