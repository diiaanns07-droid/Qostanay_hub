"""No native hooks/media: lock protocol tests and authenticated local-API checks."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from proctor.uplink.client import Uplink
from proctor.uplink.lock import LockCoordinator, LockReceipt, PendingLock
from proctor.uplink.outbox import Outbox
from proctor.uplink.tests.test_uplink import FakeView, make_cfg


def receipt(request, **changes):
    return LockReceipt(**{k: v for k, v in request.items() if k not in ("expires_at", "recovery")}, applied=True, **changes)


def command(cid="cmd-1", kind="lock", **extra):
    return dict(command_id=cid, kind=kind, payload={"reason_ru": "Телефон на столе"} if kind == "lock" else {}, **extra)


@pytest.fixture
def gate(tmp_path):
    outbox = Outbox(tmp_path / "lock.sqlite")
    outbox.set_meta("server", "test:8765")
    gate = LockCoordinator(outbox, lambda: None, timeout_s=0.1)
    gate.bind("student-1", "class-1", "local-1")
    yield gate
    gate.stop()
    outbox.close()


class Socket:
    def __init__(self): self.sent = []
    async def send(self, data): self.sent.append(json.loads(data))


def test_backend_without_electron_never_reports_lock_success(tmp_path):
    events = []
    up = Uplink(make_cfg(tmp_path, "127.0.0.1:8765"), FakeView(tmp_path), events.append)
    up.outbox.set_meta("student_id", "student-1")
    up.lock_control.bind(up.student_id, "class-1", None)
    up.lock_control.timeout_s = 0.04
    async def run():
        up._send_lock, up._flush_lock = asyncio.Lock(), asyncio.Lock()
        socket = Socket()
        await up._command(socket, command())
        ack = socket.sent[-1]
        assert ack["type"] == "ack" and ack["ok"] is False
        assert ack["code"] == "expired" and ack["result"]["locked"] is False
        assert any(e.lock_state == "requested" for e in events)
        assert not any(e.locked for e in events)
    try: asyncio.run(run())
    finally: up.stop()


def test_only_effective_ui_receipt_completes_lock_and_unlock(gate):
    pending = gate.begin(command())
    assert isinstance(pending, PendingLock)
    assert gate.snapshot()["locked"] is False and pending.result is None
    assert pending.request["reason_ru"] == "Телефон на столе"
    assert gate.confirm(receipt(pending.request)) == {"accepted": True}
    assert gate.wait(pending)["ok"] is True and gate.snapshot()["locked"] is True
    unlock = gate.begin(command("cmd-2", "unlock"))
    assert gate.snapshot()["locked"] is True
    gate.confirm(receipt(unlock.request))
    assert gate.wait(unlock)["ok"] is True and gate.snapshot()["locked"] is False


@pytest.mark.parametrize("key", ["command_id", "student_id", "class_session_id", "source_session_id", "backend_instance_id", "request_token"])
def test_wrong_receipt_scope_is_rejected_without_completing_request(gate, key):
    pending = gate.begin(command())
    body = receipt(pending.request).model_copy(update={key: "wrong"})
    assert gate.confirm(body)["accepted"] is False
    assert pending.result is None and not gate.snapshot()["locked"]
    gate.confirm(receipt(pending.request))
    assert gate.wait(pending)["ok"] is True


def test_failed_render_or_wrong_reason_does_not_lock(gate):
    pending = gate.begin(command())
    assert gate.confirm(receipt(pending.request).model_copy(update={"reason_ru": "Подмена"}))["accepted"] is True
    assert gate.wait(pending)["ok"] is False and not gate.snapshot()["locked"]
    pending = gate.begin(command("cmd-2"))
    gate.confirm(receipt(pending.request).model_copy(update={"applied": False}))
    assert gate.wait(pending)["ok"] is False


def test_expired_replay_superseded_and_changed_session(gate):
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert gate.begin(command(expires_at=expired))["code"] == "expired"
    assert gate.begin(command(ttl_ms=0))["code"] == "expired"
    first = gate.begin(command())
    second = gate.begin(command("cmd-2", "unlock"))
    assert gate.wait(first)["ok"] is False
    assert not gate.confirm(receipt(first.request))["accepted"]
    gate.bind("student-2", "class-2", "local-2")
    assert gate.wait(second)["ok"] is False
    assert not gate.confirm(receipt(second.request))["accepted"]
    assert gate.begin(command(student_id="wrong"))["code"] == "invalid"


def test_restart_does_not_replay_success_and_requires_fresh_receipt(gate):
    pending = gate.begin(command())
    old_receipt = receipt(pending.request)
    gate.confirm(old_receipt)
    restarted = LockCoordinator(gate.outbox, lambda: None)
    restarted.bind("student-1", "class-1", "local-1")
    assert restarted.snapshot()["locked"] is False
    assert restarted.snapshot()["lock_request"]["recovery"] is True
    assert not restarted.confirm(old_receipt)["accepted"]
    assert restarted.begin(command())["code"] == "duplicate"
    fresh = restarted.snapshot()["lock_request"]
    assert restarted.confirm(receipt(fresh))["accepted"]
    assert restarted.snapshot()["locked"] is True
    restarted.stop()


def test_timeout_preserves_previous_effective_state_and_rejects_late_receipt(gate):
    pending = gate.begin(command())
    gate.confirm(receipt(pending.request))
    unlock = gate.begin(command("cmd-2", "unlock", ttl_ms=1))
    assert gate.wait(unlock)["code"] == "expired"
    assert gate.snapshot()["locked"] is True
    assert not gate.confirm(receipt(unlock.request))["accepted"]


def test_local_ack_route_requires_auth_and_strict_scope(tmp_path, monkeypatch):
    from proctor.uplink.tests.test_uplink_app import _client
    monkeypatch.delenv("QORGAU_CLASS_SERVER", raising=False)
    monkeypatch.delenv("QORGAU_CLASS_CODE", raising=False)
    with _client(tmp_path) as client:
        response = client.post("/v1/class/lock/ack", json={}, headers={"Authorization": "Bearer invalid"})
        assert response.status_code == 401
        assert client.post("/v1/class/lock/ack", json={"locked": True}).status_code == 422
