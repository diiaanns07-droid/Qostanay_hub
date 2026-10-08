"""One microphone owner; bounded in-memory PCM queue, scalar observations only."""
import os
import queue
import threading
import time
from uuid import uuid4

from proctor_contracts.v1 import (AudioObservation, Component, Health, HealthObservation,
                                 HealthStatus, ObservationStatus, Producer, SignalState)

from .vad import AudioDetector, BLOCK, RATE

_MICROPHONE = threading.Lock()


class MicrophoneLease:
    """Process lock plus Windows named mutex across backend processes."""
    def __enter__(self):
        self.handle = None
        if not _MICROPHONE.acquire(blocking=False):
            raise RuntimeError("microphone_owned")
        try:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                self.api = ctypes.WinDLL("kernel32", use_last_error=True)
                self.api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
                self.api.CreateMutexW.restype = wintypes.HANDLE
                self.api.CloseHandle.argtypes = [wintypes.HANDLE]
                self.api.ReleaseMutex.argtypes = [wintypes.HANDLE]
                self.handle = self.api.CreateMutexW(None, True, "Local\\QorgauExam.AudioMicrophone")
                error = ctypes.get_last_error()
                if not self.handle:
                    raise OSError(error, "audio mutex unavailable")
                if error == 183:
                    self.api.CloseHandle(self.handle)
                    self.handle = None
                    raise RuntimeError("microphone_owned")
            return self
        except Exception:
            _MICROPHONE.release()
            raise

    def __exit__(self, *args):
        if self.handle:
            self.api.ReleaseMutex(self.handle)
            self.api.CloseHandle(self.handle)
        _MICROPHONE.release()


class AudioMonitor:
    def __init__(self, session_id, source_mode, clock, publish, *, stream_factory=None, detector_factory=AudioDetector):
        self.session_id, self.mode, self.clock, self.publish = session_id, source_mode, clock, publish
        self.stream_factory, self.detector_factory = stream_factory, detector_factory
        self._thread = None
        self._stop = threading.Event()
        self._lifecycle = threading.Lock()

    def start(self):
        with self._lifecycle:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="audio-monitor", daemon=True)
            self._thread.start()

    def stop(self):
        with self._lifecycle:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(3)
                # A stuck owner retains the lease; a second capture must never be opened.
                if not self._thread.is_alive():
                    self._thread = None

    def _base(self, t=None):
        t = self.clock.now_ms() if t is None else t
        return dict(observation_id=f"audio-{uuid4().hex}", session_id=self.session_id, frame_id=None,
                    t_session_ms=t, wall_time=self.clock.wall_at(t), source_mode=self.mode,
                    producer=Producer(module="audio", version="a14-1.0.0"))

    def _health(self, code, status):
        if not self._stop.is_set():
            self.publish(HealthObservation(**self._base(),
                         status=ObservationStatus.OK if status == HealthStatus.OK else ObservationStatus.DEGRADED,
                         health=Health(component=Component.AUDIO, status=status, code=code)))

    def _unknown(self, code):
        if not self._stop.is_set():
            self.publish(AudioObservation(**self._base(), status=ObservationStatus.DEGRADED,
                                          voice_like=SignalState.UNKNOWN, reasons=[code]))

    def _run(self):
        pcm = queue.Queue(maxsize=16)  # <=512 ms; never written or published
        overflow = threading.Event()

        def callback(indata, frames, timing, status):
            if self._stop.is_set():
                return
            if status or frames != BLOCK:
                overflow.set()
                return
            try:
                pcm.put_nowait((time.monotonic(), self.clock.now_ms(), indata[:, 0].copy()))
            except queue.Full:
                overflow.set()

        try:
            with MicrophoneLease():
                detector = self.detector_factory()
                factory = self.stream_factory
                if factory is None:
                    import sounddevice
                    factory = sounddevice.InputStream
                with factory(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK, callback=callback):
                    self._health("noise_calibration", HealthStatus.DEGRADED)
                    previous = "noise_calibration"
                    last_block = last_fault = time.monotonic()
                    while not self._stop.is_set():
                        try:
                            captured, t, block = pcm.get(timeout=0.1)
                        except queue.Empty:
                            now = time.monotonic()
                            if now - last_block > 0.6 and now - last_fault >= 0.5:
                                self._health("audio_stream_stalled", HealthStatus.DEGRADED)
                                self._unknown("audio_stream_stalled")
                                detector.reset()
                                previous = "audio_stream_stalled"
                                last_fault = now
                            continue
                        last_block = time.monotonic()
                        if overflow.is_set() or last_block - captured > 0.6:
                            overflow.clear()
                            while not pcm.empty():
                                try:
                                    pcm.get_nowait()
                                except queue.Empty:
                                    break
                            detector.reset()
                            self._health("audio_discontinuity", HealthStatus.DEGRADED)
                            self._unknown("audio_discontinuity")
                            previous = "audio_discontinuity"
                            continue
                        summary = detector.feed(block)
                        if summary is None or self._stop.is_set():
                            continue
                        code = "noise_calibration" if summary.voice_like == "unknown" else (
                            "energy_fallback" if detector.vad is None else "audio_ok")
                        if code != previous:
                            self._health(code, HealthStatus.OK if code == "audio_ok" else HealthStatus.DEGRADED)
                            previous = code
                        self.publish(AudioObservation(**self._base(t),
                            status=ObservationStatus.DEGRADED if code != "audio_ok" else ObservationStatus.OK,
                            voice_like=SignalState(summary.voice_like), voice_probability=summary.probability,
                            rms_dbfs=summary.rms_dbfs, noise_floor_dbfs=summary.noise_floor_dbfs,
                            reasons=summary.reasons))
        except Exception:
            self._health("audio_unavailable", HealthStatus.DEGRADED)
            self._unknown("audio_unavailable")
        finally:
            while not pcm.empty():
                try:
                    pcm.get_nowait()
                except queue.Empty:
                    break
