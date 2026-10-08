"""Server clock (UTC wall time). One clock per class server; tests use FakeClock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    """Deterministic clock for tests and the simulator scripts."""

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now += timedelta(seconds=seconds)
        return self._now


def iso(dt: datetime) -> str:
    """UTC ISO-8601 with milliseconds and a 'Z' suffix (protocol `sent_at` style)."""
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None
    return dt if dt.tzinfo is not None else None
