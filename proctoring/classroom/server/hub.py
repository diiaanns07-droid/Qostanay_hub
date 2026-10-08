"""Teacher stream fan-out (owner: T01).

publish() is thread-safe and never blocks: each /ws/teacher client has a bounded queue; when it overflows the
client gets ONE `resync_required` message (and loses the overflowing messages) instead of silently missing
updates. `seq` is per connection and contiguous for the messages that were delivered.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..contracts.models import utc_now


class _Client:
    def __init__(self, maxsize: int, inline_previews: bool):
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self.inline_previews = inline_previews
        self.seq = 0
        self.overflowed = False

    def offer(self, payload: dict[str, Any]) -> None:
        if payload.get("type") == "preview" and not self.inline_previews:
            payload = {k: v for k, v in payload.items() if k != "jpeg_b64"}
        if self.overflowed:
            if self.queue.qsize() < self.queue.maxsize // 2:
                self.overflowed = False
                self._put({"type": "resync_required", "reason": "queue_overflow"})
            else:
                return
        if self.queue.full():
            self.overflowed = True
            return
        self._put(payload)

    def _put(self, payload: dict[str, Any]) -> None:
        self.seq += 1
        self.queue.put_nowait({**payload, "seq": self.seq, "sent_at": payload.get("sent_at") or utc_now().isoformat()})


class TeacherHub:
    def __init__(self, maxsize: int = 2000):
        self.maxsize = maxsize
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[_Client] = set()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def clients(self) -> int:
        return len(self._clients)

    def publish(self, payload: dict[str, Any]) -> None:
        """payload: a TeacherStreamMessage as JSON-able dict WITHOUT seq/sent_at (added per client)."""
        loop = self._loop
        if loop is None or not self._clients:
            return
        try:
            loop.call_soon_threadsafe(self._fanout, payload)
        except RuntimeError:
            pass  # loop closed during shutdown

    def _fanout(self, payload: dict[str, Any]) -> None:
        for client in list(self._clients):
            client.offer(dict(payload))

    def add(self, inline_previews: bool) -> _Client:
        client = _Client(self.maxsize, inline_previews)
        self._clients.add(client)
        return client

    def remove(self, client: _Client) -> None:
        self._clients.discard(client)


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
