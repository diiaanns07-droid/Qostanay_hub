"""Record a consented clip into the replay format (owner: A02).

    python -m proctor.capture record --replay-id phone_raise_01 --seconds 20 \\
        --consent "team member A, written consent 2026-10-09" --title "Phone raised"

Frames come from the SAME FrameCaptureService as the product (one camera owner); a consumer
writes them to ``<replay_dir>/media/<replay_id>.avi`` (MJPG) and their capture timestamps to
``<replay_id>.ts.json`` (``timestamps: "sidecar"``), so replay reproduces the real timing,
including frames the writer could not keep up with. The manifest gets the file's sha256 and a
provenance block. Recordings of people stay OUTSIDE Git (*.avi is git-ignored); only the
manifest may be committed by A10, and only if the people recorded agreed.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from proctor_contracts.interfaces import FramePacket, SessionClock
from proctor_contracts.v1 import SourceConfig

from .replay import REPLAY_FORMAT, REPLAY_ID_RE, SIDECAR_TIE_STEP_MS, ReplayManifest, sha256_file
from .service import CAPTURE_VERSION, FrameCaptureService
from .sources import cv2


class _Writer:
    def __init__(self, path: Path, fps: float):
        self.path = path
        self.fps = fps
        self.writer: Any = None
        self.pts: list[float] = []
        self.ties = 0
        self.first_t: float | None = None
        self.size: tuple[int, int] | None = None
        self.lock = threading.Lock()
        self.error: str | None = None

    def __call__(self, frame: FramePacket) -> None:  # runs on the capture consumer thread only
        with self.lock:
            if self.error:
                return
            if self.writer is None:
                h, w = frame.image.shape[:2]
                self.size = (w, h)
                self.writer = cv2.VideoWriter(str(self.path), cv2.VideoWriter_fourcc(*"MJPG"), float(self.fps), (w, h))
                if not self.writer.isOpened():
                    self.error = "cv2.VideoWriter could not open (MJPG/AVI)"
                    return
                self.first_t = frame.t_session_ms
            if (frame.image.shape[1], frame.image.shape[0]) != self.size:
                return  # resolution changed after a reconnect: keep the clip consistent
            self.writer.write(frame.image)
            t = round(frame.t_session_ms - (self.first_t or 0.0), 3)
            if self.pts and t <= self.pts[-1]:  # same clock tick (15.6 ms on Windows/Python 3.12)
                t = round(self.pts[-1] + SIDECAR_TIE_STEP_MS, 3)
                self.ties += 1
            self.pts.append(t)

    def close(self) -> None:
        with self.lock:
            if self.writer is not None:
                self.writer.release()


def record(
    settings: Any,
    source: SourceConfig,
    replay_id: str,
    seconds: float,
    *,
    consent: str,
    title: str = "",
    overwrite: bool = False,
    countdown_s: int = 0,
    tick: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """``countdown_s``: the camera is opened first, then the countdown runs, then writing starts,
    so t = 0 of the clip is the moment "REC" is shown (the person in front of the camera can
    follow a timed script). ``tick`` gets the countdown and one line per recorded second."""
    if cv2 is None:
        raise SystemExit("OpenCV is required for recording")
    if not REPLAY_ID_RE.match(replay_id):
        raise SystemExit(f"replay id must match {REPLAY_ID_RE.pattern}")
    if not consent.strip():
        raise SystemExit("--consent is required: who was recorded and how they agreed")
    root = Path(settings.replay_dir)
    media_dir = root / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = root / f"{replay_id}.json"
    video = media_dir / f"{replay_id}.avi"
    sidecar = media_dir / f"{replay_id}.ts.json"
    if not overwrite and (manifest_path.exists() or video.exists()):
        raise SystemExit(f"{manifest_path.name} or its media already exists (use --overwrite)")
    writer = _Writer(video, fps=float(source.fps))
    say = tick or (lambda _msg: None)
    svc = FrameCaptureService(settings)
    svc.open(f"record-{replay_id}", source, SessionClock())
    try:
        for n in range(max(0, int(countdown_s)), 0, -1):
            say(f"recording starts in {n} s ...")
            time.sleep(1.0)
        svc.add_consumer("recorder", writer)
        started = time.monotonic()
        say(f"REC t = 0 s (of {seconds:g} s)")
        shown = 0
        while time.monotonic() - started < seconds and writer.error is None:
            time.sleep(0.1)
            elapsed = int(time.monotonic() - started)
            if elapsed > shown:
                shown = elapsed
                say(f"REC t = {elapsed} s")
        health = svc.health()
        metrics = svc.metrics()
    finally:
        svc.close()
        svc.remove_consumer("recorder")
        writer.close()
    if writer.error:
        raise SystemExit(writer.error)
    if not writer.pts:
        raise SystemExit("no frames were recorded")
    sidecar.write_text(json.dumps({"pts_ms": writer.pts}), encoding="utf-8")
    rec = next((c for c in metrics.consumers if c.name == "recorder"), None)
    manifest = {
        "format": REPLAY_FORMAT,
        "replay_id": replay_id,
        "title": title,
        "media": {
            "kind": "video",
            "path": f"media/{video.name}",
            "sha256": sha256_file(video),
            "fps": float(source.fps),
            "timestamps": "sidecar",
            "timestamps_path": f"media/{sidecar.name}",
            "mirrored": False,
        },
        "pacing": "realtime",
        "loop": False,
        "labels": [],
        "provenance": {
            "recorded_with": f"proctor.capture.record {CAPTURE_VERSION}",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "source": source.mode.value,
            "device": str(health.details.get("source_id", "")),
            "backend": str(health.details.get("backend", "")),
            "frames": len(writer.pts),
            "skipped_by_writer": int(rec.frames_skipped) if rec else 0,
            "timestamp_ties_plus_1ms": writer.ties,
            "consent": consent.strip()[:300],
        },
    }
    ReplayManifest.model_validate(manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"manifest": str(manifest_path), "media": str(video), "frames": len(writer.pts), "duration_ms": writer.pts[-1], "size": writer.size}
