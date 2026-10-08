"""Rolling-window measurement helpers (owner: A02).

Everything here records MEASURED values on the process-local monotonic clock
(time.monotonic_ns). Nothing in this module is a target or a default "nice" number:
an empty window yields None, never a made-up FPS or latency.
"""

from __future__ import annotations

import threading
from collections import deque

import numpy as np


class RollingSeries:
    """Thread-safe (t_mono_ns, value) samples kept for the last ``window_s`` seconds.

    Bounded twice: by time (``window_s``) and by count (``maxlen``) so a burst can never
    grow memory without limit. Samples older than the window are discarded on read.
    """

    __slots__ = ("_window_ns", "_items", "_lock")

    def __init__(self, window_s: float, maxlen: int = 8192):
        if window_s <= 0:
            raise ValueError("window_s must be > 0")
        self._window_ns = int(window_s * 1e9)
        self._items: deque[tuple[int, float]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, t_ns: int, value: float = 0.0) -> None:
        with self._lock:
            self._items.append((t_ns, value))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _prune(self, now_ns: int) -> None:
        cutoff = now_ns - self._window_ns
        items = self._items
        while items and items[0][0] < cutoff:
            items.popleft()

    def values(self, now_ns: int) -> list[float]:
        with self._lock:
            self._prune(now_ns)
            return [v for _, v in self._items]

    def count(self, now_ns: int) -> int:
        with self._lock:
            self._prune(now_ns)
            return len(self._items)


def rate_per_s(count: int, now_ns: int, since_ns: int, window_s: float) -> float:
    """Events per second over the effective window min(window_s, now - since).

    ``since_ns`` is when counting started (open of the source); before ~50 ms of
    history the rate is reported as 0.0 instead of an extrapolated spike.
    """
    effective_s = min(window_s, max(0.0, (now_ns - since_ns) / 1e9))
    if effective_s < 0.05:
        return 0.0
    return count / effective_s


def p50_p95(values: list[float]) -> tuple[float | None, float | None]:
    """Median and 95th percentile (numpy 'linear' interpolation); (None, None) when empty."""
    if not values:
        return None, None
    p50, p95 = np.percentile(np.asarray(values, dtype=np.float64), [50.0, 95.0])
    return round(float(p50), 3), round(float(p95), 3)
