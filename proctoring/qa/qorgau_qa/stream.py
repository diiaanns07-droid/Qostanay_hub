"""WebSocket clients for /v1/stream (JSON envelopes) and /v1/preview (binary frames) (owner: A09)."""

from __future__ import annotations

import json
import struct
import threading
import time
from typing import Any, Callable

from websockets.sync.client import connect


class StreamRecorder:
    """Background reader of WS /v1/stream. Keeps every raw envelope in arrival order."""

    def __init__(self, url: str, headers: dict[str, str] | None = None, subprotocols: list[str] | None = None):
        kwargs: dict[str, Any] = {"open_timeout": 10}
        if headers:
            kwargs["additional_headers"] = headers
        if subprotocols:
            kwargs["subprotocols"] = subprotocols
        self._cm = connect(url, **kwargs)  # websockets>=16: the sync client is a context manager
        self.ws = self._cm.__enter__()
        self.messages: list[dict[str, Any]] = []
        self.closed_reason: str | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.ws.recv(timeout=0.25)
            except TimeoutError:
                continue
            except Exception as exc:  # connection closed
                self.closed_reason = f"{type(exc).__name__}: {exc}"
                return
            with self._lock:
                self.messages.append(json.loads(raw))

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.messages)

    def of_type(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.snapshot() if m.get("message", {}).get("type") == kind]

    def wait(self, predicate: Callable[[list[dict[str, Any]]], Any], timeout: float) -> Any:
        deadline = time.monotonic() + timeout
        while True:
            value = predicate(self.snapshot())
            if value or time.monotonic() >= deadline:
                return value
            time.sleep(0.05)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(2.0)
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass

    def __enter__(self) -> "StreamRecorder":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def incidents(messages: list[dict[str, Any]], rule: str | None = None) -> list[dict[str, Any]]:
    """IncidentChange payloads from stream envelopes, optionally filtered by rule_id."""
    out = []
    for m in messages:
        msg = m.get("message", {})
        if msg.get("type") == "incident" and (rule is None or msg["change"]["incident"]["rule_id"] == rule):
            out.append(msg["change"])
    return out


def states(messages: list[dict[str, Any]], session_id: str) -> list[str]:
    seq: list[str] = []
    for m in messages:
        msg = m.get("message", {})
        if msg.get("type") == "session_state" and msg["session"]["session_id"] == session_id:
            state = msg["session"]["state"]
            if not seq or seq[-1] != state:
                seq.append(state)
    return seq


def parse_preview_frame(data: bytes) -> tuple[dict[str, Any], bytes]:
    """WS /v1/preview frame: uint32 BE header_len | PreviewFrameMeta JSON (UTF-8) | JPEG bytes."""
    assert len(data) >= 4, "preview frame shorter than its length prefix"
    (n,) = struct.unpack(">I", data[:4])
    assert 0 < n <= len(data) - 4, f"header length {n} out of range for {len(data)} bytes"
    meta = json.loads(data[4 : 4 + n].decode("utf-8"))
    return meta, data[4 + n :]
