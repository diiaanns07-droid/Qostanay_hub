"""Long-run measurement of the capture pipeline (owner: A02).

    python -m proctor.capture soak --source synthetic --minutes 2
    python -m proctor.capture soak --source replay:<replay_id> --minutes 20
    python -m proctor.capture soak --source live:0 --minutes 20 --width 640 --height 480 --fps 30

Runs the REAL FrameCaptureService with two SIMULATED analysis consumers (default "phone-like"
8 FPS x 60 ms and "attention-like" 15 FPS x 25 ms of CPU work via cv2 filters, which release
the GIL like onnxruntime/MediaPipe do). The harness measures latency itself, independently of
RuntimeMetrics, at these points (process-local monotonic clock):

* frame_age_ms = consumer callback start - FramePacketMeta.t_capture_mono_ns
* e2e_ms       = consumer callback end   - t_capture_mono_ns   (= "observation published")
* capture FPS  = frames_captured / wall duration; per-second samples from RuntimeMetrics

Memory = process RSS (Linux /proc, Windows GetProcessMemoryInfo, macOS ru_maxrss as peak).
The simulated consumers are NOT computer vision; the numbers describe the frame pipeline on
this machine only. Targets are printed separately from measurements.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from proctor_contracts.interfaces import FramePacket, SessionClock
from proctor_contracts.v1 import SourceConfig, SourceMode

from .service import CAPTURE_VERSION, FrameCaptureService
from .sources import cv2

#: working targets of A02 (NOT measurements): what the demo needs to feel live
TARGETS = {
    "capture_fps_min_ratio": 0.8,  # of the requested fps
    "preview_fps_min": 10.0,
    "e2e_ms_p95_max": 250.0,  # with the default simulated consumers
    "rss_growth_mb_max_per_10min": 30.0,
}


def rss_bytes() -> int | None:
    """Current resident set size of this process, or None if unknown."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/status", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) * 1024
        elif sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                return int(pmc.WorkingSetSize)
        else:
            import resource

            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)  # macOS: bytes, peak
    except Exception:
        return None
    return None


def hardware_info() -> dict[str, Any]:
    cpu = platform.processor() or ""
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("model name"):
                        cpu = line.split(":", 1)[1].strip()
                        break
        except OSError:
            pass
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "python": platform.python_version(),
        "opencv": getattr(cv2, "__version__", "missing") if cv2 is not None else "missing",
        "numpy": np.__version__,
        "capture_version": CAPTURE_VERSION,
    }


def parse_source(spec: str, width: int, height: int, fps: int) -> SourceConfig:
    """'synthetic' | 'replay:<id>' | 'live' | 'live:<index>'."""
    if spec == "synthetic":
        return SourceConfig(mode=SourceMode.SYNTHETIC, width=width, height=height, fps=fps)
    if spec.startswith("replay:"):
        return SourceConfig(mode=SourceMode.REPLAY, replay_id=spec.split(":", 1)[1], width=width, height=height, fps=fps)
    if spec == "live" or spec.startswith("live:"):
        index = int(spec.split(":", 1)[1]) if ":" in spec else 0
        return SourceConfig(mode=SourceMode.LIVE, camera_index=index, width=width, height=height, fps=fps)
    raise ValueError("source must be synthetic, replay:<id>, live or live:<index>")


@dataclass
class SimulatedConsumer:
    """CPU-bound stand-in for an analyzer: repeats a cv2 blur until ``work_ms`` elapsed."""

    name: str
    max_fps: float | None
    work_ms: float
    mode: str = "cpu"  # "cpu" (cv2 filter loop, releases the GIL) | "sleep"
    ages: list[float] = field(default_factory=list)
    e2e: list[float] = field(default_factory=list)
    ids: list[int] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, frame: FramePacket) -> None:
        start = time.monotonic_ns()
        age = (start - frame.meta.t_capture_mono_ns) / 1e6
        if self.work_ms > 0:
            deadline = start + int(self.work_ms * 1e6)
            if self.mode == "sleep" or cv2 is None:
                time.sleep(self.work_ms / 1000.0)
            else:
                small = cv2.resize(frame.image, (320, 240))  # new array: the shared frame is never written
                while time.monotonic_ns() < deadline:
                    small = cv2.GaussianBlur(small, (5, 5), 0)
        end = time.monotonic_ns()
        with self.lock:
            self.ages.append(age)
            self.e2e.append((end - frame.meta.t_capture_mono_ns) / 1e6)
            self.ids.append(frame.frame_id)


def _pct(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "p50": None, "p95": None, "p99": None, "max": None}
    arr = np.asarray(values, dtype=np.float64)
    p50, p95, p99 = np.percentile(arr, [50, 95, 99])
    return {"n": int(arr.size), "p50": round(float(p50), 2), "p95": round(float(p95), 2), "p99": round(float(p99), 2), "max": round(float(arr.max()), 2)}


def run_soak(
    settings: Any,
    source: SourceConfig,
    seconds: float,
    *,
    consumers: list[SimulatedConsumer] | None = None,
    sample_every_s: float = 1.0,
    service: FrameCaptureService | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    svc = service or FrameCaptureService(settings)
    consumers = consumers if consumers is not None else [
        SimulatedConsumer("phone_sim", 8.0, 60.0),
        SimulatedConsumer("attention_sim", 15.0, 25.0),
    ]
    health_events: list[dict[str, Any]] = []
    svc.set_health_listener(lambda h: health_events.append({"t_session_ms": h.since_t_session_ms, "status": h.status.value, "code": h.code}))
    for c in consumers:
        svc.add_consumer(c.name, c, max_fps=c.max_fps)
    threads_before = threading.active_count()
    rss_before = rss_bytes()
    clock = SessionClock()
    t_open = time.monotonic()
    svc.open("soak-session", source, clock)
    open_ms = (time.monotonic() - t_open) * 1000.0
    samples: list[dict[str, Any]] = []
    rss_series: list[int] = []
    started = time.monotonic()
    next_sample = started + sample_every_s
    try:
        while True:
            now = time.monotonic()
            if now - started >= seconds:
                break
            time.sleep(max(0.0, min(next_sample, started + seconds) - now))
            m = svc.metrics()
            rss = rss_bytes()
            if rss is not None:
                rss_series.append(rss)
            samples.append(
                {
                    "t_s": round(time.monotonic() - started, 2),
                    "capture_fps": m.capture_fps,
                    "frames_captured": m.frames_captured,
                    "frames_dropped": m.frames_dropped,
                    "consumers": {c.name: {"fps": c.processed_fps, "skipped": c.frames_skipped, "errors": c.errors} for c in m.consumers},
                    "e2e_p95": m.e2e_latency_ms_p95,
                    "rss_mb": round(rss / 2**20, 1) if rss is not None else None,
                    "health": svc.health().code,
                }
            )
            if progress is not None:
                progress(samples[-1])
            next_sample += sample_every_s
            if svc.health().code in ("replay_ended",):
                break
    finally:
        duration = time.monotonic() - started
        final = svc.metrics()
        svc.close()
        for c in consumers:
            svc.remove_consumer(c.name)
        svc.set_health_listener(None)
    time.sleep(0.2)
    rss_after = rss_bytes()
    threads_after = threading.active_count()
    frames = final.frames_captured
    report: dict[str, Any] = {
        "kind": "qorgau.a02.soak.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": f"{source.mode.value.upper()} source, SIMULATED consumers (not CV)",
        "hardware": hardware_info(),
        "source": source.model_dump(mode="json"),
        "requested_duration_s": seconds,
        "measured_duration_s": round(duration, 2),
        "open_ms": round(open_ms, 1),
        "frames_captured": frames,
        "frames_dropped": final.frames_dropped,
        "capture_fps_mean": round(frames / duration, 2) if duration > 0 else None,
        "capture_fps_samples": _pct([s["capture_fps"] for s in samples[2:]] or [s["capture_fps"] for s in samples]),
        "consumers": {},
        "memory": {
            "rss_before_mb": round(rss_before / 2**20, 1) if rss_before else None,
            "rss_after_mb": round(rss_after / 2**20, 1) if rss_after else None,
            "rss_max_mb": round(max(rss_series) / 2**20, 1) if rss_series else None,
            # growth after warm-up: last minute vs first sample after 10 s
            "rss_growth_after_warmup_mb": _growth(rss_series, sample_every_s),
        },
        "threads": {"before": threads_before, "after": threads_after},
        "leaked_runs": svc.leaked_runs(),
        "health_events": health_events,
        "service_metrics_final": final.model_dump(mode="json"),
        "samples": samples,
        "targets": TARGETS,
        "notes": [
            "frame_age/e2e measured by the harness at callback start/end vs t_capture_mono_ns (read() return time)",
            "driver/sensor latency before cv2 read() returns is not observable and not included",
        ],
    }
    for c in consumers:
        with c.lock:
            ids = list(c.ids)
            report["consumers"][c.name] = {
                "max_fps": c.max_fps,
                "simulated_work_ms": c.work_ms,
                "work_mode": c.mode,
                "processed": len(ids),
                "processed_fps_mean": round(len(ids) / duration, 2) if duration > 0 else None,
                "ids_strictly_increasing": all(b > a for a, b in zip(ids, ids[1:])),
                "frame_age_ms": _pct(c.ages),
                "e2e_ms": _pct(c.e2e),
            }
    report["evaluation"] = evaluate(report, source)
    return report


def _growth(series: list[int], step_s: float) -> float | None:
    warm = int(10 / step_s)
    if len(series) <= warm + 2:
        return None
    tail = series[-max(1, int(60 / step_s)) :]
    return round((float(np.median(tail)) - series[warm]) / 2**20, 1) + 0.0  # no "-0.0"


def evaluate(report: dict[str, Any], source: SourceConfig) -> dict[str, str]:
    """Compare MEASURED values with TARGETS (PASS/FAIL/NOT_RUN); never upgrades a synthetic run to LIVE."""
    out: dict[str, str] = {}
    fps = report.get("capture_fps_mean")
    want = source.fps if source.mode != SourceMode.REPLAY else None
    if fps is not None and want:
        out["capture_fps"] = "PASS" if fps >= TARGETS["capture_fps_min_ratio"] * want else "FAIL"
    prev = report["service_metrics_final"]
    preview = next((c for c in prev.get("consumers", []) if c["name"] == "preview"), None)
    out["preview_fps"] = (
        "NOT_RUN" if preview is None else ("PASS" if preview["processed_fps"] >= TARGETS["preview_fps_min"] else "FAIL")
    )
    worst = max((c["e2e_ms"]["p95"] or 0.0) for c in report["consumers"].values()) if report["consumers"] else None
    if worst is not None:
        out["e2e_p95"] = "PASS" if worst <= TARGETS["e2e_ms_p95_max"] else "FAIL"
    growth = report["memory"]["rss_growth_after_warmup_mb"]
    minutes = report["measured_duration_s"] / 60.0
    if growth is None or minutes < 1.0:
        out["memory_growth"] = "NOT_RUN (run >= 1 min)"
    else:
        allowed = TARGETS["rss_growth_mb_max_per_10min"] * max(1.0, minutes / 10.0)
        out["memory_growth"] = "PASS" if growth <= allowed else "FAIL"
    out["threads_released"] = "PASS" if report["threads"]["after"] <= report["threads"]["before"] and report["leaked_runs"] == 0 else "FAIL"
    out["ids_monotonic"] = "PASS" if all(c["ids_strictly_increasing"] for c in report["consumers"].values()) else "FAIL"
    out["mode"] = source.mode.value.upper() + (" (not a camera)" if source.mode != SourceMode.LIVE else "")
    return out


def summarize(report: dict[str, Any]) -> str:
    hw = report["hardware"]
    src = report["source"]
    lines = [
        f"A02 soak — {report['label']}",
        f"hardware: {hw['cpu'] or hw['machine']} x{hw['logical_cpus']} | {hw['platform']} | python {hw['python']} | opencv {hw['opencv']}",
        f"source: {src['mode']} {src.get('replay_id') or ''} requested {src['width']}x{src['height']}@{src['fps']}",
        f"duration: {report['measured_duration_s']} s (requested {report['requested_duration_s']} s), open {report['open_ms']} ms",
        f"frames: {report['frames_captured']} captured, {report['frames_dropped']} dropped, mean {report['capture_fps_mean']} FPS",
    ]
    for name, c in report["consumers"].items():
        a, e = c["frame_age_ms"], c["e2e_ms"]
        lines.append(
            f"  {name}: {c['processed']} frames ({c['processed_fps_mean']} FPS, limit {c['max_fps']}, work {c['simulated_work_ms']} ms) "
            f"age p50/p95 {a['p50']}/{a['p95']} ms, e2e p50/p95/max {e['p50']}/{e['p95']}/{e['max']} ms"
        )
    mem = report["memory"]
    lines.append(f"memory RSS: {mem['rss_before_mb']} -> {mem['rss_after_mb']} MB (max {mem['rss_max_mb']}, growth after warm-up {mem['rss_growth_after_warmup_mb']} MB)")
    lines.append(f"threads: {report['threads']['before']} -> {report['threads']['after']}, leaked runs {report['leaked_runs']}")
    lines.append("evaluation vs A02 targets: " + ", ".join(f"{k}={v}" for k, v in report["evaluation"].items()))
    return "\n".join(lines)


def write_report(report: dict[str, Any], out: Path | None) -> Path | None:
    if out is None:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
