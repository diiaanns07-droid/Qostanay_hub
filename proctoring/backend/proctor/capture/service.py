"""FrameCaptureService — the single owner of the frame source in the product (owner: A02).

Implements ``proctor_contracts.interfaces.CaptureService`` for all three source modes:

=========  ===============================  ==========================================
mode       source                           timeline (t_session_ms)
=========  ===============================  ==========================================
live       CameraSource (cv2.VideoCapture)  SessionClock.mono_to_session_ms(read time)
replay     ReplaySource (qorgau.replay.v1)  replay_start_t_ms + media_pts_ms
synthetic  SyntheticSource (test frames)    SessionClock.mono_to_session_ms(gen time)
=========  ===============================  ==========================================

Threads per open(): one capture thread (owns the source), one watchdog (frame stalls),
one worker per consumer + one internal preview encoder (latest-frame mailboxes, see
fanout.py). Nothing here runs on the asyncio loop; ``latest_preview()`` is an attribute
read and ``metrics()`` only sorts a few thousand floats.

Health state machine (component ``capture``)::

    STOPPED/idle --open()--> STARTING/opening --source ok--> OK/running
    OK/running --no frame > stall timeout--> DEGRADED/frame_stall --frame--> OK/running
    OK|DEGRADED --live read failures--> UNAVAILABLE/camera_disconnected --reopen ok--> OK/running
    OK --replay end (no loop)--> STOPPED/replay_ended
    STARTING --open fails--> UNAVAILABLE/<reason>  (open() raises CaptureError)
    any --close()--> STOPPED/closed ;  unexpected exception --> ERROR/capture_error

Missing frames are a capture HEALTH problem (``frame_stall`` / ``camera_disconnected``),
never ``face_missing``. Every state change is delivered to the health listener in order.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Any

import numpy as np

from proctor_contracts.interfaces import CaptureError, FrameCallback, FramePacket, HealthListener, SessionClock
from proctor_contracts.v1 import (
    Component,
    ConsumerMetrics,
    ErrorCode,
    FramePacketMeta,
    Health,
    HealthStatus,
    PreviewFrameMeta,
    RuntimeMetrics,
    SourceConfig,
    SourceMode,
)

from .fanout import Consumer, ConsumerWorker
from .replay import ReplaySource, load_replay
from .sources import CameraSource, FactDict, FrameSource, RawFrame, SourceDisconnected, SourceEnded, SyntheticSource, cv2
from .stats import RollingSeries, p50_p95, rate_per_s

log = logging.getLogger("proctor.capture")

CAPTURE_VERSION = "0.1.0"
PREVIEW_CONSUMER = "preview"  # reserved name of the internal JPEG preview encoder
PREVIEW_MAX_WIDTH = 960  # preview is downscaled above this width (normalized overlays unaffected)
METRICS_WINDOW_S = 5.0
OPEN_TIMEOUT_S = 6.0  # device open + first frame probe (CaptureService.open "< ~5 s")
STALL_MIN_S = 1.0  # no frame for max(STALL_MIN_S, 4 frame intervals) -> frame_stall
WATCHDOG_TICK_S = 0.1
RECONNECT_FIRST_DELAY_S = 0.25
RECONNECT_MAX_DELAY_S = 5.0
RING_MAX_BYTES = 256 * 1024 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class _Ring:
    """Bounded frame_id -> FramePacket buffer: last ``seconds`` of the timeline, <= max_bytes."""

    def __init__(self, seconds: float, max_bytes: int = RING_MAX_BYTES):
        self._span_ms = max(0.0, seconds) * 1000.0
        self._max_bytes = max_bytes
        self._items: OrderedDict[int, FramePacket] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def add(self, packet: FramePacket) -> None:
        with self._lock:
            self._items[packet.frame_id] = packet
            self._bytes += packet.image.nbytes
            newest = packet.t_session_ms
            while len(self._items) > 1:
                oldest = next(iter(self._items.values()))
                if newest - oldest.t_session_ms <= self._span_ms and self._bytes <= self._max_bytes:
                    break
                self._items.popitem(last=False)
                self._bytes -= oldest.image.nbytes

    def get(self, frame_id: int) -> FramePacket | None:
        with self._lock:
            return self._items.get(frame_id)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class _Run:
    """Everything that belongs to one open() .. close(). Discarded afterwards (clean restart)."""

    def __init__(self, session_id: str, source: FrameSource, clock: SessionClock, *, lockstep: bool, nominal_fps: float, ring_seconds: float, window_s: float):
        self.session_id = session_id
        self.source = source
        self.mode = source.mode
        self.clock = clock
        self.lockstep = lockstep
        self.stop = threading.Event()
        self.opened = threading.Event()
        self.go = threading.Event()
        self.open_error: CaptureError | None = None
        self.capture_thread: threading.Thread | None = None
        self.watchdog_thread: threading.Thread | None = None
        self.workers: dict[str, ConsumerWorker] = {}
        self.workers_lock = threading.Lock()
        self.ring = _Ring(ring_seconds)
        self.preview: tuple[PreviewFrameMeta, bytes] | None = None
        self.started_ns = time.monotonic_ns()
        self.frames_captured = 0
        self.frames_rejected = 0
        self.next_frame_id = 0
        self.first_media_index: int | None = None
        self.last_frame_id: int | None = None
        self.last_t_ms: float | None = None
        self.last_frame_mono_ns: int | None = None
        self.replay_start_t_ms: float | None = None
        self.reconnects = 0
        self.reconnect_attempts = 0
        self.stall_s = max(STALL_MIN_S, 4.0 / max(nominal_fps, 0.1))
        self.capture_series = RollingSeries(window_s)
        self.e2e = RollingSeries(window_s)

    def worker_list(self) -> list[ConsumerWorker]:
        with self.workers_lock:
            return list(self.workers.values())


class FrameCaptureService:
    """See module docstring. Construct via ``proctor.capture.create_capture_service``."""

    def __init__(
        self,
        settings: Any,
        *,
        camera_kwargs: dict[str, Any] | None = None,
        open_timeout_s: float = OPEN_TIMEOUT_S,
        metrics_window_s: float = METRICS_WINDOW_S,
        replay_pacing: str | None = None,
    ):
        self._settings = settings
        # test/diagnostic hooks for CameraSource (opener, backends, platform, probes, probe_timeout_s)
        self._camera_kwargs: dict[str, Any] = dict(camera_kwargs or {})
        self._open_timeout_s = open_timeout_s
        self._window_s = metrics_window_s
        if replay_pacing not in (None, "realtime", "lockstep"):
            raise ValueError("replay_pacing must be None, 'realtime' or 'lockstep'")
        self._replay_pacing = replay_pacing
        self._lock = threading.RLock()
        self._consumers: dict[str, Consumer] = {}
        self._run: _Run | None = None
        self._zombies: list[_Run] = []
        self._listener: HealthListener | None = None
        self._notify_lock = threading.Lock()
        self._pending_health: deque[Health] = deque(maxlen=256)
        self._status = HealthStatus.STOPPED
        self._code = "idle"
        self._message = "Capture is idle (no session)"
        self._since_ms: float | None = None
        self._details: FactDict = {}
        self.sessions_opened = 0

    # =================================================================== API
    def open(self, session_id: str, source: SourceConfig, clock: SessionClock) -> None:
        if not isinstance(session_id, str) or not _ID_RE.match(session_id):
            raise CaptureError(ErrorCode.INVALID_ARGUMENT, "session_id is not a valid Id")
        with self._lock:
            if self._run is not None:
                raise CaptureError(
                    ErrorCode.SESSION_ACTIVE, "capture is already open for another session", open_session_id=self._run.session_id
                )
            self._prune_zombies()
            if source.mode == SourceMode.LIVE and any(z.mode == SourceMode.LIVE for z in self._zombies):
                raise CaptureError(
                    ErrorCode.CAMERA_BUSY,
                    "the previous camera capture is still releasing the device",
                    retryable=True,
                    reason="previous_capture_releasing",
                )
        try:
            src, lockstep, nominal_fps = self._make_source(source)  # replay manifest is validated here (REPLAY_INVALID)
        except CaptureError as err:
            self._record_open_failure(err, clock)
            raise
        run = _Run(
            session_id,
            src,
            clock,
            lockstep=lockstep,
            nominal_fps=nominal_fps,
            ring_seconds=float(self._settings.frame_ring_seconds),
            window_s=self._window_s,
        )
        run.capture_thread = threading.Thread(target=self._capture_main, args=(run,), name=f"capture-{src.mode.value}", daemon=True)
        run.watchdog_thread = threading.Thread(target=self._watchdog_main, args=(run,), name="capture-watchdog", daemon=True)
        with self._lock:
            if self._run is not None:  # lost a race with a concurrent open()
                raise CaptureError(ErrorCode.SESSION_ACTIVE, "capture is already open for another session")
            self._run = run
            self.sessions_opened += 1
            self._details = {}
            self._set_health_locked(run, HealthStatus.STARTING, "opening", f"Opening {src.source_id}")
            for consumer in self._consumers.values():
                self._start_worker_locked(run, consumer)
            preview = self._preview_consumer(run)
            if preview is not None:
                self._start_worker_locked(run, preview)
        self._flush_health()
        run.capture_thread.start()
        if not run.opened.wait(self._open_timeout_s):
            err = CaptureError(
                ErrorCode.CAMERA_UNAVAILABLE if run.mode == SourceMode.LIVE else ErrorCode.REPLAY_INVALID,
                f"opening {src.source_id} timed out after {self._open_timeout_s:.0f} s",
                retryable=True,
                reason="open_timeout",
            )
            self._abort_open(run, err)
            raise err
        if run.open_error is not None:
            self._abort_open(run, run.open_error)
            raise run.open_error
        with self._lock:
            self._set_health_locked(run, HealthStatus.OK, "running", f"{src.source_id} delivers frames")
        self._flush_health()
        run.watchdog_thread.start()
        run.go.set()

    def close(self, timeout_s: float = 3.0) -> None:
        try:
            with self._lock:
                run, self._run = self._run, None
            if run is None:
                return
            deadline = time.monotonic() + max(0.1, timeout_s)
            leaked = self._stop_run(run, deadline)
            with self._lock:
                self._details = {"leaked_threads": leaked} if leaked else {}
                self._set_health_locked(None, HealthStatus.STOPPED, "closed", "Capture closed", clock=run.clock)
            self._flush_health()
        except Exception:  # close() never raises (CaptureService contract)
            log.exception("capture close failed")

    def add_consumer(self, name: str, callback: FrameCallback, *, max_fps: float | None = None) -> None:
        if not isinstance(name, str) or not 0 < len(name) <= 64:
            raise ValueError("consumer name must be a non-empty string of at most 64 characters")
        if name == PREVIEW_CONSUMER:
            raise ValueError(f"consumer name {PREVIEW_CONSUMER!r} is reserved")
        if not callable(callback):
            raise ValueError("callback must be callable")
        if max_fps is not None and not (isinstance(max_fps, (int, float)) and np.isfinite(max_fps) and max_fps > 0):
            raise ValueError("max_fps must be None or a positive number")
        with self._lock:
            if name in self._consumers:
                raise ValueError(f"consumer {name!r} already registered")
            consumer = Consumer(name=name, callback=callback, max_fps=float(max_fps) if max_fps else None)
            self._consumers[name] = consumer
            if self._run is not None:
                self._start_worker_locked(self._run, consumer)

    def remove_consumer(self, name: str) -> None:
        with self._lock:
            self._consumers.pop(name, None)
            run = self._run
            worker = None
            if run is not None:
                with run.workers_lock:
                    worker = run.workers.pop(name, None)
        if worker is not None:
            worker.request_stop()
            if not worker.join(3.0):
                log.warning("consumer %r still inside its callback after remove_consumer()", name)

    def latest_preview(self) -> tuple[PreviewFrameMeta, bytes] | None:
        run = self._run
        return run.preview if run is not None else None

    def get_frame(self, frame_id: int) -> FramePacket | None:
        run = self._run
        return run.ring.get(frame_id) if run is not None else None

    def set_health_listener(self, listener: HealthListener | None) -> None:
        with self._lock:
            self._listener = listener

    def health(self) -> Health:
        with self._lock:
            return self._health_locked()

    def metrics(self) -> RuntimeMetrics:
        run = self._run
        now = time.monotonic_ns()
        if run is None:
            return RuntimeMetrics(window_s=self._window_s, capture_fps=0.0, frames_captured=0, frames_dropped=0)
        effective_window = min(self._window_s, max(1e-3, (now - run.started_ns) / 1e9))
        e2e50, e2e95 = p50_p95(run.e2e.values(now))
        return RuntimeMetrics(
            session_id=run.session_id,
            t_session_ms=run.clock.now_ms(),
            window_s=round(effective_window, 3),
            capture_fps=round(rate_per_s(run.capture_series.count(now), now, run.started_ns, self._window_s), 2),
            frames_captured=run.frames_captured,
            frames_dropped=int(run.source.dropped) + run.frames_rejected,
            consumers=[ConsumerMetrics(**w.snapshot(now)) for w in run.worker_list()],
            e2e_latency_ms_p50=e2e50,
            e2e_latency_ms_p95=e2e95,
        )

    # ------------------------------------------------- A02 extras (not in the Protocol)
    @property
    def is_open(self) -> bool:
        return self._run is not None

    def replay_start_t_ms(self) -> float | None:
        """Session time of the first replay frame (t_session_ms = this + media_pts_ms)."""
        run = self._run
        return run.replay_start_t_ms if run is not None else None

    def leaked_runs(self) -> int:
        with self._lock:
            self._prune_zombies()
            return len(self._zombies)

    # ============================================================ internals
    def _make_source(self, source: SourceConfig) -> tuple[FrameSource, bool, float]:
        s = self._settings
        given = source.model_fields_set
        width = source.width if "width" in given else int(s.capture_width)
        height = source.height if "height" in given else int(s.capture_height)
        fps = source.fps if "fps" in given else int(s.capture_fps)
        if source.mode == SourceMode.LIVE:
            if self._camera_kwargs.get("opener") is None and cv2 is None:
                raise CaptureError(ErrorCode.CAMERA_UNAVAILABLE, "OpenCV (cv2) is not installed", reason="opencv_missing")
            index = source.camera_index if "camera_index" in given else int(s.camera_index)
            cam = CameraSource(index, width, height, fps, **self._camera_kwargs)
            return cam, False, float(fps)
        if source.mode == SourceMode.REPLAY:
            replay = ReplaySource(s.replay_dir, source.replay_id, max_width=width, max_height=height, pacing=self._replay_pacing)
            replay.prepare()
            fps_hint = replay.manifest.media.fps or 30.0
            return replay, replay.pacing == "lockstep", float(fps_hint)
        if source.mode == SourceMode.SYNTHETIC:
            sfps = float(source.fps) if "fps" in given else float(s.synthetic_fps)
            return SyntheticSource(width, height, sfps), False, sfps
        raise CaptureError(ErrorCode.INVALID_ARGUMENT, f"unsupported source mode {source.mode!r}")

    def _preview_consumer(self, run: _Run) -> Consumer | None:
        fps = float(self._settings.preview_fps)
        if cv2 is None or fps <= 0:
            return None
        quality = int(min(95, max(10, int(self._settings.preview_jpeg_quality))))

        def encode(packet: FramePacket) -> None:
            self._encode_preview(run, packet, quality)

        return Consumer(name=PREVIEW_CONSUMER, callback=encode, max_fps=fps, internal=True)

    @staticmethod
    def _encode_preview(run: _Run, packet: FramePacket, quality: int) -> None:
        img = packet.image
        h, w = img.shape[:2]
        if w > PREVIEW_MAX_WIDTH:
            scale = PREVIEW_MAX_WIDTH / w
            img = cv2.resize(img, (PREVIEW_MAX_WIDTH, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        data = buf.tobytes()
        meta = packet.meta
        run.preview = (
            PreviewFrameMeta(
                session_id=meta.session_id,
                frame_id=meta.frame_id,
                t_session_ms=meta.t_session_ms,
                wall_time=meta.wall_time,
                width=int(img.shape[1]),
                height=int(img.shape[0]),
                source_mode=meta.source_mode,
                byte_length=len(data),
            ),
            data,
        )

    def _start_worker_locked(self, run: _Run, consumer: Consumer) -> None:
        worker = ConsumerWorker(consumer, lockstep=run.lockstep, window_s=self._window_s, e2e=run.e2e)
        with run.workers_lock:
            run.workers[consumer.name] = worker
        worker.start()

    def _stop_run(self, run: _Run, deadline: float) -> int:
        """Stop threads of ``run``; returns the number of threads still alive at the deadline."""
        run.stop.set()
        with run.workers_lock:
            workers = list(run.workers.values())
            run.workers.clear()
        for w in workers:
            w.request_stop()
        alive = 0
        for t in (run.capture_thread, run.watchdog_thread):
            if t is not None and t.ident is not None:
                t.join(max(0.0, deadline - time.monotonic()))
                alive += t.is_alive()
        for w in workers:
            alive += not w.join(max(0.0, deadline - time.monotonic()))
        run.ring.clear()
        run.preview = None
        if alive:
            log.warning("capture run for %s: %d thread(s) still alive after close timeout", run.session_id, alive)
            with self._lock:
                self._zombies.append(run)
        return alive

    def _abort_open(self, run: _Run, err: CaptureError) -> None:
        self._stop_run(run, time.monotonic() + 1.0)
        with self._lock:
            if self._run is run:
                self._run = None
        self._record_open_failure(err, run.clock)

    def _record_open_failure(self, err: CaptureError, clock: SessionClock) -> None:
        """Health after a failed open(): UNAVAILABLE/<reason> until the next open()/close()."""
        with self._lock:
            if self._run is not None:
                return
            reason = str(err.details.get("reason") or err.code.value.lower())
            self._details = {k: v for k, v in err.details.items() if k != "reason"}
            self._details["error_code"] = err.code.value
            code = reason if re.match(r"^[a-z0-9_.]{1,64}$", reason) else "open_failed"
            self._set_health_locked(None, HealthStatus.UNAVAILABLE, code, err.message, clock=clock)
        self._flush_health()

    def _prune_zombies(self) -> None:
        def alive(r: _Run) -> bool:
            threads = [r.capture_thread, r.watchdog_thread]
            return any(t is not None and t.is_alive() for t in threads)

        self._zombies = [z for z in self._zombies if alive(z)]

    # ------------------------------------------------------------ health
    def _set_health_locked(
        self, run: _Run | None, status: HealthStatus, code: str, message: str, *, clock: SessionClock | None = None, **details: Any
    ) -> None:
        """Change state; queue a listener notification when (status, code) changed. Hold self._lock."""
        if run is not None and self._run is not run:
            return  # a stale run must never change the current state
        changed = (status, code) != (self._status, self._code)
        self._status, self._code, self._message = status, code, message
        if clock is None:
            clock = run.clock if run is not None else (self._run.clock if self._run is not None else None)
        if changed:
            self._since_ms = clock.now_ms() if clock is not None else None
        for k, v in details.items():
            if isinstance(v, (bool, int, float, str)):
                self._details[k] = v
        if changed:
            self._pending_health.append(self._health_locked())

    def _health_locked(self) -> Health:
        details: FactDict = dict(self._details)
        details["opencv"] = getattr(cv2, "__version__", "missing") if cv2 is not None else "missing"
        run = self._run
        if run is not None:
            try:
                details.update(run.source.describe())
            except Exception:
                pass
            details.update(
                source_mode=run.mode.value,
                source_id=run.source.source_id,
                pacing="lockstep" if run.lockstep else "realtime",
                frames=run.frames_captured,
                reconnects=run.reconnects,
            )
            if run.last_frame_id is not None:
                details["last_frame_id"] = run.last_frame_id
            if run.replay_start_t_ms is not None:
                details["replay_start_t_ms"] = round(run.replay_start_t_ms, 3)
        return Health(
            component=Component.CAPTURE,
            status=self._status,
            code=self._code,
            message=self._message[:500],
            since_t_session_ms=self._since_ms,
            details={k: v for k, v in details.items() if isinstance(v, (bool, int, float, str))},
        )

    def _flush_health(self) -> None:
        """Deliver queued notifications in order. Never call while holding self._lock."""
        with self._notify_lock:
            while True:
                with self._lock:
                    if not self._pending_health:
                        return
                    health = self._pending_health.popleft()
                    listener = self._listener
                if listener is None:
                    continue
                try:
                    listener(health)
                except Exception:
                    log.exception("capture health listener failed")

    def _set_health(self, run: _Run, status: HealthStatus, code: str, message: str, **details: Any) -> None:
        with self._lock:
            self._set_health_locked(run, status, code, message, **details)
        self._flush_health()

    # ----------------------------------------------------------- threads
    def _capture_main(self, run: _Run) -> None:
        src = run.source
        try:
            try:
                src.open(run.stop, time.monotonic() + self._open_timeout_s)
            except CaptureError as exc:
                run.open_error = exc
                return
            except Exception as exc:
                log.exception("opening %s failed", src.source_id)
                code = ErrorCode.CAMERA_UNAVAILABLE if run.mode == SourceMode.LIVE else ErrorCode.REPLAY_INVALID
                run.open_error = CaptureError(code, f"opening the source failed: {type(exc).__name__}", reason="open_error")
                return
            finally:
                run.opened.set()
            while not run.go.wait(0.1):
                if run.stop.is_set():
                    return
            self._loop(run)
        except Exception as exc:
            log.exception("capture thread failed")
            self._set_health(run, HealthStatus.ERROR, "capture_error", f"capture failed: {type(exc).__name__}")
        finally:
            try:
                src.close()
            except Exception:
                log.exception("closing %s failed", src.source_id)

    def _loop(self, run: _Run) -> None:
        src = run.source
        while not run.stop.is_set():
            try:
                raw = src.read()
            except SourceEnded:
                if not run.stop.is_set():
                    self._set_health(run, HealthStatus.STOPPED, "replay_ended", "Replay reached the end of the recording")
                return
            except SourceDisconnected as exc:
                if run.stop.is_set():
                    return
                if not src.supports_reconnect:
                    self._set_health(run, HealthStatus.ERROR, "source_lost", str(exc)[:300])
                    return
                if not self._reconnect(run):
                    return
                continue
            if raw is None or run.stop.is_set():
                continue
            self._emit(run, raw)

    def _reconnect(self, run: _Run) -> bool:
        src = run.source
        self._set_health(
            run,
            HealthStatus.UNAVAILABLE,
            "camera_disconnected",
            "Camera stopped delivering frames; reconnecting",
            reconnecting=True,
        )
        try:
            src.close()
        except Exception:
            log.exception("closing the disconnected camera failed")
        delay = RECONNECT_FIRST_DELAY_S
        while not run.stop.is_set():
            if run.stop.wait(delay):
                return False
            run.reconnect_attempts += 1
            try:
                src.open(run.stop, time.monotonic() + self._open_timeout_s)
            except CaptureError as exc:
                with self._lock:
                    if self._run is run:
                        self._details.update(reconnect_attempts=run.reconnect_attempts, last_open_error=str(exc.details.get("reason", exc.code.value)))
                delay = min(delay * 2.0, RECONNECT_MAX_DELAY_S)
                continue
            except Exception:
                log.exception("camera reopen failed")
                delay = min(delay * 2.0, RECONNECT_MAX_DELAY_S)
                continue
            if run.stop.is_set():
                return False
            run.reconnects += 1
            self._set_health(run, HealthStatus.OK, "running", "Camera reconnected", reconnecting=False, reconnect_attempts=run.reconnect_attempts)
            return True
        return False

    def _emit(self, run: _Run, raw: RawFrame) -> None:
        image = raw.image
        if run.mode == SourceMode.REPLAY and raw.media_pts_ms is not None and raw.media_index is not None:
            if run.replay_start_t_ms is None:
                run.replay_start_t_ms = max(0.0, run.clock.mono_to_session_ms(raw.mono_ns) - raw.media_pts_ms)
                run.first_media_index = raw.media_index
            t_ms = run.replay_start_t_ms + raw.media_pts_ms
            frame_id = raw.media_index - (run.first_media_index or 0)
        else:
            t_ms = run.clock.mono_to_session_ms(raw.mono_ns)
            frame_id = run.next_frame_id
        if (run.last_frame_id is not None and frame_id <= run.last_frame_id) or (run.last_t_ms is not None and t_ms < run.last_t_ms):
            run.frames_rejected += 1  # never deliver out-of-order frames
            return
        image.flags.writeable = False
        view = image.view()  # a view of a read-only base cannot be made writeable again
        h, w = view.shape[:2]
        meta = FramePacketMeta(
            session_id=run.session_id,
            frame_id=frame_id,
            t_session_ms=t_ms,
            wall_time=run.clock.wall_at(t_ms),
            t_capture_mono_ns=raw.mono_ns,
            width=w,
            height=h,
            source_mode=run.mode,
            source_id=run.source.source_id,
        )
        packet = FramePacket(meta=meta, image=view)
        run.ring.add(packet)
        if run.preview is None and cv2 is not None and float(self._settings.preview_fps) > 0:
            try:  # first preview synchronously: preflight sees a preview as soon as frames flow
                self._encode_preview(run, packet, int(min(95, max(10, int(self._settings.preview_jpeg_quality)))))
            except Exception:
                log.exception("first preview encode failed")
        run.next_frame_id = frame_id + 1
        run.last_frame_id = frame_id
        run.last_t_ms = t_ms
        run.last_frame_mono_ns = raw.mono_ns
        run.frames_captured += 1
        run.capture_series.add(raw.mono_ns)
        if self._status == HealthStatus.DEGRADED and self._code == "frame_stall":
            self._set_health(run, HealthStatus.OK, "running", "Frames flow again")
        workers = run.worker_list()
        if run.lockstep:
            wanted = [w for w in workers if w.wants(packet)]
            for w in wanted:
                w.offer(packet)
            for w in wanted:
                w.wait_processed(frame_id, run.stop)
        else:
            for w in workers:
                w.offer(packet)

    def _watchdog_main(self, run: _Run) -> None:
        while not run.stop.wait(WATCHDOG_TICK_S):
            last = run.last_frame_mono_ns or run.started_ns
            idle_s = (time.monotonic_ns() - last) / 1e9
            if idle_s <= run.stall_s:
                continue
            with self._lock:
                if self._run is run and self._status == HealthStatus.OK:
                    self._set_health_locked(
                        run, HealthStatus.DEGRADED, "frame_stall", f"No frames for {idle_s:.1f} s", stall_threshold_s=round(run.stall_s, 3)
                    )
            self._flush_health()
