"""Frame fan-out to consumers (owner: A02).

One worker thread per consumer with a size-1 *latest-frame mailbox*:

* the capture thread never waits for a consumer in ``realtime`` pacing: ``offer()`` only
  swaps a reference under a short lock; a pending frame that was not taken yet is replaced
  by the newer one and counted as ``frames_skipped``;
* a slow consumer (e.g. YOLO at 8 FPS) therefore never freezes the preview or other
  consumers and never accumulates seconds of old video: the frame it processes next is the
  newest one at the moment it becomes free (and its rate limit allows);
* ``max_fps`` is a rate limit measured at processing start on the monotonic clock; the
  frame is taken AFTER the limit elapsed, so the wait never ages the processed frame;
* in ``lockstep`` pacing (deterministic replay) the capture thread hands a frame only to
  consumers that want it (media-time decimation by ``max_fps``) and waits until each has
  processed it; nothing is skipped, so two runs over the same media are identical.

Ownership of arrays: FramePacket.image is a read-only view shared by every consumer and by
the ring buffer; nobody copies it, nobody may write it. A consumer that needs to modify
pixels copies first (``frame.image.copy()`` or any cv2 function that returns a new array).

Concurrency guarantee: a consumer callback is never invoked concurrently with itself, even
across a close()/open() restart where the previous worker is still finishing a slow
callback (``Consumer.callback_lock`` belongs to the registration, not to the worker).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from proctor_contracts.interfaces import FrameCallback, FramePacket

from .stats import RollingSeries, p50_p95, rate_per_s

log = logging.getLogger("proctor.capture")

_ERROR_LOG_FIRST = 3
_ERROR_LOG_EVERY = 100


@dataclass(eq=False)
class Consumer:
    """A registration. Survives close()/open(); workers are created per open()."""

    name: str
    callback: FrameCallback
    max_fps: float | None
    internal: bool = False  # internal consumers (preview) are excluded from e2e latency
    callback_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def min_interval_s(self) -> float:
        return 1.0 / self.max_fps if self.max_fps else 0.0


class ConsumerWorker:
    """Runs one consumer on its own thread for one capture run (one open())."""

    def __init__(
        self,
        consumer: Consumer,
        *,
        lockstep: bool,
        window_s: float,
        e2e: RollingSeries | None,
        thread_prefix: str = "capture-consumer",
    ):
        self.consumer = consumer
        self.lockstep = lockstep
        self._window_s = window_s
        self._e2e = e2e
        self._cond = threading.Condition()
        self._pending: FramePacket | None = None
        self._stop = False
        self._last_done_id = -1
        self._next_allowed = 0.0  # realtime rate limit (time.monotonic seconds)
        self._last_wanted_t_ms: float | None = None  # lockstep media-time decimation
        self.frames_processed = 0
        self.frames_skipped = 0
        self.errors = 0
        self.last_frame_id: int | None = None
        self._started_ns = time.monotonic_ns()
        self._done = RollingSeries(window_s)
        self._age_ms = RollingSeries(window_s)
        self._proc_ms = RollingSeries(window_s)
        self._thread = threading.Thread(
            target=self._run, name=f"{thread_prefix}-{consumer.name}", daemon=True
        )

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        self._started_ns = time.monotonic_ns()
        self._thread.start()

    def request_stop(self) -> None:
        with self._cond:
            self._stop = True
            self._pending = None
            self._cond.notify_all()

    def join(self, timeout: float) -> bool:
        """True when the worker thread has exited."""
        if self._thread.ident is None or self._thread is threading.current_thread():
            return True  # never started, or close() called from inside this consumer's callback
        self._thread.join(max(0.0, timeout))
        return not self._thread.is_alive()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    # ----------------------------------------------------------------- delivery
    def wants(self, packet: FramePacket) -> bool:
        """Lockstep only: media-time decimation by max_fps (deterministic)."""
        interval_ms = self.consumer.min_interval_s * 1000.0
        last = self._last_wanted_t_ms
        if interval_ms > 0 and last is not None and packet.t_session_ms - last < interval_ms - 1e-6:
            return False
        self._last_wanted_t_ms = packet.t_session_ms
        return True

    def offer(self, packet: FramePacket) -> None:
        """Non-blocking hand-off; replaces (and counts) a pending frame not taken yet."""
        with self._cond:
            if self._stop:
                return
            if self._pending is not None:
                self.frames_skipped += 1
            self._pending = packet
            self._cond.notify_all()

    def wait_processed(self, frame_id: int, stop: threading.Event) -> bool:
        """Lockstep: block the capture thread until ``frame_id`` was processed.

        Returns False when the worker or the run is stopping (the frame may be lost)."""
        with self._cond:
            while self._last_done_id < frame_id:
                if self._stop or stop.is_set():
                    return False
                self._cond.wait(0.1)
            return True

    # ------------------------------------------------------------------ metrics
    def snapshot(self, now_ns: int) -> dict:
        age50, age95 = p50_p95(self._age_ms.values(now_ns))
        pr50, pr95 = p50_p95(self._proc_ms.values(now_ns))
        return {
            "name": self.consumer.name,
            "processed_fps": round(rate_per_s(self._done.count(now_ns), now_ns, self._started_ns, self._window_s), 2),
            "frames_processed": self.frames_processed,
            "frames_skipped": self.frames_skipped,
            "errors": self.errors,
            "frame_age_ms_p50": age50,
            "frame_age_ms_p95": age95,
            "process_ms_p50": pr50,
            "process_ms_p95": pr95,
        }

    # ------------------------------------------------------------------- thread
    def _take(self) -> FramePacket | None:
        """Wait for a frame (and, in realtime, for the rate limit); None = stop."""
        min_interval = 0.0 if self.lockstep else self.consumer.min_interval_s
        with self._cond:
            while True:
                if self._stop:
                    return None
                if self._pending is not None:
                    wait = self._next_allowed - time.monotonic() if min_interval else 0.0
                    if wait <= 0:
                        packet, self._pending = self._pending, None
                        return packet
                    self._cond.wait(wait)
                else:
                    self._cond.wait(0.5)

    def _run(self) -> None:
        consumer = self.consumer
        while True:
            packet = self._take()
            if packet is None:
                return
            # A previous run's worker may still be inside a slow callback after a restart:
            # wait for it so the callback is never concurrent with itself.
            with consumer.callback_lock:
                with self._cond:
                    if self._stop:
                        return
                    if not self.lockstep and self._pending is not None:
                        # a newer frame arrived while we waited for the lock (restart overlap): take it
                        self.frames_skipped += 1
                        packet, self._pending = self._pending, None
                start_ns = time.monotonic_ns()
                self._next_allowed = start_ns / 1e9 + consumer.min_interval_s
                ok = True
                try:
                    consumer.callback(packet)
                except Exception:  # never stops capture; counted and logged (rate-limited)
                    ok = False
                    self.errors += 1
                    if self.errors <= _ERROR_LOG_FIRST or self.errors % _ERROR_LOG_EVERY == 0:
                        log.exception("consumer %r failed on frame %d (errors=%d)", consumer.name, packet.frame_id, self.errors)
                end_ns = time.monotonic_ns()
            capture_ns = packet.meta.t_capture_mono_ns
            self._age_ms.add(end_ns, (start_ns - capture_ns) / 1e6)
            self._proc_ms.add(end_ns, (end_ns - start_ns) / 1e6)
            if ok:
                self._done.add(end_ns)
                if self._e2e is not None and not consumer.internal:
                    self._e2e.add(end_ns, (end_ns - capture_ns) / 1e6)
            with self._cond:
                if ok:
                    self.frames_processed += 1
                self.last_frame_id = packet.frame_id
                self._last_done_id = packet.frame_id
                self._cond.notify_all()
