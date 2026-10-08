"""Replay source and manifest format ``qorgau.replay.v1`` (owner: A02; demo content: A10).

A replay is selected by an opaque ``SourceConfig.replay_id`` (never a path). The manifest
lives at ``settings.replay_dir/<replay_id>.json`` and references media INSIDE
``settings.replay_dir`` (typically ``media/``, git-ignored: recordings of people are never
committed). Example::

    {
      "format": "qorgau.replay.v1",
      "replay_id": "phone_raise_01",
      "title": "Phone raised in front of the screen",
      "media": {"kind": "video", "path": "media/phone_raise_01.mp4",
                "sha256": "<hex of the file>", "timestamps": "container"},
      "pacing": "realtime",
      "loop": false,
      "labels": [{"label": "phone_raised", "t_start_ms": 3000, "t_end_ms": 5200}],
      "provenance": {"recorded_with": "proctor.capture.record", "consent": "team member, written"}
    }

Timeline (CONTRACTS.md §Time): ``t_session_ms = replay_start_t_ms + media_pts_ms`` where
``replay_start_t_ms`` is the session time at which the first replay frame was emitted and
``media_pts_ms`` is the recording timestamp relative to ``start_ms`` (plus the loop period
for looped playback). Timestamps come from the recording, not from the replay machine:

* ``timestamps: "container"`` — the decoder's presentation timestamp (cv2 CAP_PROP_POS_MSEC);
* ``timestamps: "fps"`` — ``index * 1000 / fps`` (``media.fps`` or the container FPS);
* ``timestamps: "sidecar"`` — ``media.timestamps_path`` JSON ``{"pts_ms": [...]}``, one
  non-decreasing value per frame (written by ``proctor.capture.record``); equal neighbours
  (one 15.6 ms clock tick on Windows/Python 3.12) are moved by +1 ms and counted.

Non-increasing timestamps are repaired to ``previous + nominal interval`` and counted.

Pacing: ``realtime`` (default) emits frames when their timestamp is due (``speed``
multiplier) and drops frames when the machine falls behind (counted as frames_dropped,
never silently); ``lockstep`` emits as fast as consumers allow and waits until every
consumer processed each frame it wants — a deterministic run for tests/evaluation.
``frame_id`` of a replay frame is its media index (+ frames of previous loops), so it is
the same in every run regardless of drops.

Every replay frame carries ``source_mode="replay"`` and ``source_id="replay:<replay_id>"``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from proctor_contracts.interfaces import CaptureError
from proctor_contracts.v1 import ErrorCode, SourceMode

from .sources import FactDict, RawFrame, SourceEnded, normalize_bgr, require_cv2

log = logging.getLogger("proctor.capture")

REPLAY_FORMAT = "qorgau.replay.v1"
REPLAY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_REL_PATH = r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$"  # no '..', no leading '/', no '\'
MAX_MANIFEST_BYTES = 256_000
MAX_SIDECAR_BYTES = 20_000_000
MAX_SEQUENCE_FILES = 100_000
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp")

RelPath = Annotated[str, Field(pattern=_REL_PATH, max_length=200)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReplayMedia(_Model):
    kind: Literal["video", "image_sequence"]
    path: RelPath  # relative to replay_dir: a video file or a directory of images
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None  # verified at open when set
    fps: Annotated[float, Field(gt=0, le=240)] | None = None
    timestamps: Literal["container", "fps", "sidecar"] = "container"
    timestamps_path: RelPath | None = None
    mirrored: bool = False  # True: media is horizontally mirrored -> flipped back on read

    @model_validator(mode="after")
    def _check(self) -> "ReplayMedia":
        if self.timestamps == "sidecar" and self.timestamps_path is None:
            raise ValueError("timestamps 'sidecar' requires media.timestamps_path")
        if self.kind == "image_sequence" and self.timestamps == "container":
            raise ValueError("image_sequence has no container timestamps: use 'fps' or 'sidecar'")
        if self.kind == "image_sequence" and self.timestamps == "fps" and self.fps is None:
            raise ValueError("image_sequence with timestamps 'fps' requires media.fps")
        return self


class ReplayLabel(_Model):
    """Ground-truth interval for evaluation (A09/A10). Capture never reads it."""

    label: Annotated[str, Field(pattern=r"^[a-z0-9_.]{1,64}$")]
    t_start_ms: Annotated[float, Field(ge=0)]
    t_end_ms: Annotated[float, Field(ge=0)]
    note: Annotated[str, Field(max_length=300)] = ""


class ReplayManifest(_Model):
    format: Literal["qorgau.replay.v1"]
    replay_id: str
    title: Annotated[str, Field(max_length=200)] = ""
    media: ReplayMedia
    pacing: Literal["realtime", "lockstep"] = "realtime"
    speed: Annotated[float, Field(gt=0, le=16)] = 1.0  # realtime only
    loop: bool = False
    start_ms: Annotated[float, Field(ge=0)] = 0.0
    end_ms: Annotated[float, Field(gt=0)] | None = None
    labels: list[ReplayLabel] = Field(default_factory=list, max_length=1000)
    provenance: dict[str, Annotated[str, Field(max_length=300)] | int | float | bool] = Field(default_factory=dict, max_length=32)
    notes: Annotated[str, Field(max_length=2000)] = ""

    @model_validator(mode="after")
    def _check(self) -> "ReplayManifest":
        if not REPLAY_ID_RE.match(self.replay_id):
            raise ValueError("replay_id must match " + REPLAY_ID_RE.pattern)
        if self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


def _invalid(message: str, reason: str, **details: Any) -> CaptureError:
    return CaptureError(ErrorCode.REPLAY_INVALID, message, reason=reason, **details)


def _inside(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root`` and refuse anything that escapes it (incl. symlinks)."""
    root_r = root.resolve()
    path = (root_r / rel).resolve()
    if not path.is_relative_to(root_r):
        raise _invalid("media path escapes the replay directory", "path_outside_replay_dir")
    return path


_SHA_CACHE: dict[tuple[str, int, int], str] = {}
_SHA_LOCK = threading.Lock()


def sha256_file(path: Path) -> str:
    """sha256 of a file; cached by (resolved path, size, mtime_ns) so a large replay is not
    re-hashed on every session (any change of the file changes size or mtime)."""
    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    with _SHA_LOCK:
        hit = _SHA_CACHE.get(key)
    if hit is not None:
        return hit
    digest = _sha256_file_uncached(path)
    with _SHA_LOCK:
        if len(_SHA_CACHE) > 256:
            _SHA_CACHE.clear()
        _SHA_CACHE[key] = digest
    return digest


def _sha256_file_uncached(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_sequence(directory: Path) -> list[Path]:
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if len(files) > MAX_SEQUENCE_FILES:
        raise _invalid(f"image sequence has more than {MAX_SEQUENCE_FILES} files", "sequence_too_long")
    return files


def sha256_sequence(files: list[Path]) -> str:
    """Digest of an image sequence: sha256 over lines '<file name> <sha256 of file>\\n' in order."""
    h = hashlib.sha256()
    for p in files:
        h.update(f"{p.name} {sha256_file(p)}\n".encode("utf-8"))
    return h.hexdigest()


class ResolvedReplay:
    def __init__(self, manifest: ReplayManifest, media_path: Path, sidecar: list[float] | None, files: list[Path] | None):
        self.manifest = manifest
        self.media_path = media_path
        self.sidecar = sidecar
        self.files = files
        self.sidecar_ties = 0  # equal sidecar timestamps moved by +1 ms (counted as timestamp repairs)


def load_replay(replay_dir: Path, replay_id: str | None, *, verify_sha256: bool = True) -> ResolvedReplay:
    """Validate the manifest and media of ``replay_id``; raise CaptureError(REPLAY_INVALID).

    OS-level problems (permission denied, a file locked by another process, an online-only
    cloud placeholder, a symlink loop) are reported as REPLAY_INVALID/media_unreadable."""
    try:
        return _load_replay(replay_dir, replay_id, verify_sha256=verify_sha256)
    except CaptureError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise _invalid(f"replay files cannot be read: {type(exc).__name__}", "media_unreadable", replay_id=str(replay_id or "")[:64]) from None


def _load_replay(replay_dir: Path, replay_id: str | None, *, verify_sha256: bool = True) -> ResolvedReplay:
    if not replay_id:
        raise _invalid("replay source requires replay_id", "replay_id_missing")
    if not REPLAY_ID_RE.match(replay_id):
        raise _invalid("replay_id is not a valid replay identifier", "replay_id_invalid")
    root = Path(replay_dir)
    if not root.is_dir():
        raise _invalid("replay directory does not exist", "replay_dir_missing", replay_id=replay_id)
    manifest_path = _inside(root, f"{replay_id}.json")
    if not manifest_path.is_file():
        raise _invalid(f"replay '{replay_id}' not found", "manifest_missing", replay_id=replay_id)
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise _invalid("replay manifest is too large", "manifest_too_large", replay_id=replay_id)
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid(f"replay manifest is not valid JSON: {type(exc).__name__}", "manifest_not_json", replay_id=replay_id) from None
    try:
        manifest = ReplayManifest.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {"loc": (), "msg": "invalid"}
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise _invalid(f"replay manifest invalid at '{where}': {first.get('msg')}"[:400], "manifest_invalid", replay_id=replay_id) from None
    if manifest.replay_id != replay_id:
        raise _invalid("manifest replay_id does not match its file name", "replay_id_mismatch", replay_id=replay_id)

    media = manifest.media
    media_path = _inside(root, media.path)
    files: list[Path] | None = None
    if media.kind == "video":
        if not media_path.is_file():
            raise _invalid("replay media file is missing", "media_missing", replay_id=replay_id)
        if media_path.stat().st_size == 0:
            raise _invalid("replay media file is empty", "media_empty", replay_id=replay_id)
        if verify_sha256 and media.sha256 and sha256_file(media_path) != media.sha256:
            raise _invalid("replay media sha256 does not match the manifest", "media_sha256_mismatch", replay_id=replay_id)
    else:
        if not media_path.is_dir():
            raise _invalid("replay image directory is missing", "media_missing", replay_id=replay_id)
        files = list_sequence(media_path)
        if not files:
            raise _invalid("replay image directory has no images", "media_empty", replay_id=replay_id)
        for f in files:  # every file must stay inside the replay dir (symlinks)
            if not f.resolve().is_relative_to(root.resolve()):
                raise _invalid("image sequence file escapes the replay directory", "path_outside_replay_dir")
        if verify_sha256 and media.sha256 and sha256_sequence(files) != media.sha256:
            raise _invalid("replay image sequence sha256 does not match the manifest", "media_sha256_mismatch", replay_id=replay_id)

    sidecar: list[float] | None = None
    if media.timestamps == "sidecar":
        assert media.timestamps_path is not None
        side_path = _inside(root, media.timestamps_path)
        if not side_path.is_file() or side_path.stat().st_size > MAX_SIDECAR_BYTES:
            raise _invalid("timestamps sidecar is missing or too large", "sidecar_missing", replay_id=replay_id)
        try:
            data = json.loads(side_path.read_text(encoding="utf-8"))
            pts = [float(v) for v in data["pts_ms"]]
        except Exception:
            raise _invalid("timestamps sidecar must be JSON {\"pts_ms\": [numbers]}", "sidecar_invalid", replay_id=replay_id) from None
        if not pts or any(not np.isfinite(v) or v < 0 for v in pts) or any(b < a for a, b in zip(pts, pts[1:])):
            raise _invalid("sidecar pts_ms must be non-empty, finite, >= 0 and non-decreasing", "sidecar_invalid", replay_id=replay_id)
        sidecar, sidecar_ties = _untie(pts)
    resolved = ResolvedReplay(manifest, media_path, sidecar, files)
    resolved.sidecar_ties = sidecar_ties if sidecar is not None else 0
    return resolved


#: tie-break step for equal sidecar timestamps (ms)
SIDECAR_TIE_STEP_MS = 1.0


def _untie(pts: list[float]) -> tuple[list[float], int]:
    """Equal neighbours -> previous + 1 ms (counted). On Windows with Python 3.12 time.monotonic has a
    15.6 ms tick (GetTickCount64), so two frames read within one tick get the same timestamp
    (measured on the demo laptop). +1 ms keeps them in order without shifting the timeline."""
    out: list[float] = []
    ties = 0
    for v in pts:
        if out and v <= out[-1]:
            v = out[-1] + SIDECAR_TIE_STEP_MS
            ties += 1
        out.append(v)
    return out, ties


# ---------------------------------------------------------------------------
# Readers (one decoding pass over the media; restartable for loops)
# ---------------------------------------------------------------------------


class _VideoReader:
    def __init__(self, path: Path):
        self._cv2 = require_cv2(ErrorCode.REPLAY_INVALID)
        self._path = path
        self._cap: Any = None
        self.container_fps: float | None = None
        self.frame_count: int | None = None

    def open(self) -> None:
        cv2 = self._cv2
        cap = cv2.VideoCapture(str(self._path), cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            raise _invalid("replay media cannot be decoded", "media_unreadable")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self.container_fps = fps if 0 < fps <= 240 else None
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.frame_count = count if count > 0 else None
        self._cap = cap

    def grab(self) -> tuple[bool, float | None]:
        """Advance to the next frame without decoding pixels: (ok, container pts ms)."""
        cap = self._cap
        if cap is None or not cap.grab():
            return False, None
        return True, float(cap.get(self._cv2.CAP_PROP_POS_MSEC))

    def retrieve(self) -> np.ndarray | None:
        cap = self._cap
        if cap is None:
            return None
        ok, img = cap.retrieve()
        return img if ok else None

    def close(self) -> None:
        cap, self._cap = self._cap, None
        if cap is not None:
            cap.release()


class _SequenceReader:
    def __init__(self, files: list[Path]):
        self._cv2 = require_cv2(ErrorCode.REPLAY_INVALID)
        self._files = files
        self._i = 0
        self.container_fps: float | None = None
        self.frame_count: int | None = len(files)

    def open(self) -> None:
        self._i = 0

    def grab(self) -> tuple[bool, float | None]:
        if self._i >= len(self._files):
            return False, None
        self._i += 1
        return True, None

    def retrieve(self) -> np.ndarray | None:
        return self._cv2.imread(str(self._files[self._i - 1]), self._cv2.IMREAD_COLOR)

    def close(self) -> None:
        self._i = len(self._files)


class _Stopped(Exception):
    """close() was requested while the source was decoding/skipping."""


class ReplaySource:
    """Plays a validated replay; owned by the capture thread."""

    mode = SourceMode.REPLAY
    supports_reconnect = False

    #: realtime: frames later than this are dropped (but at least one frame per MIN_EMIT_MS of media)
    LATE_DROP_MS = 100.0
    MIN_EMIT_MS = 250.0

    def __init__(self, replay_dir: Path, replay_id: str | None, *, max_width: int, max_height: int, pacing: str | None = None):
        self._replay_dir = Path(replay_dir)
        self.replay_id = replay_id or ""
        self.source_id = f"replay:{self.replay_id}"
        self._max_w, self._max_h = max_width, max_height
        self._pacing_override = pacing
        self.dropped = 0
        self.timestamp_repairs = 0
        self.frames_read = 0
        self.loops = 0
        self._stop = threading.Event()
        self._resolved: ResolvedReplay | None = None
        self._reader: _VideoReader | _SequenceReader | None = None
        self._index = 0  # media index within the current loop
        self._index_base = 0  # frames of previous loops
        self._pts_base_ms = 0.0  # media time of previous loops
        self._last_pts_rel: float | None = None  # within current loop
        self._last_emit_rel: float | None = None  # global rel ms of last emitted frame
        self._origin_mono: float | None = None
        self._ended = False
        self._out_size: tuple[int, int] | None = None
        self._media_size: tuple[int, int] | None = None
        self._pending_first: tuple[np.ndarray | None, float, int] | None = None
        self._nominal_ms = 1000.0 / 30.0
        self._skip_deadline: float | None = None  # open(): the trim skip must fit the open budget

    @property
    def manifest(self) -> ReplayManifest:
        assert self._resolved is not None
        return self._resolved.manifest

    @property
    def pacing(self) -> str:
        return self._pacing_override or (self._resolved.manifest.pacing if self._resolved else "realtime")

    def prepare(self) -> None:
        """Validate manifest + media in the caller's thread, before any capture thread starts."""
        self._resolved = load_replay(self._replay_dir, self.replay_id or None)

    def open(self, stop: threading.Event, deadline: float) -> None:
        self._stop = stop
        if self._resolved is None:
            self.prepare()
        r = self._resolved
        assert r is not None
        self.timestamp_repairs = r.sidecar_ties
        self._reader = _VideoReader(r.media_path) if r.manifest.media.kind == "video" else _SequenceReader(r.files or [])
        self._reader.open()
        self._nominal_ms = self._nominal_interval_ms()
        self._index = 0
        self._index_base = 0
        self._pts_base_ms = 0.0
        self._last_pts_rel = None
        self._last_emit_rel = None
        self._origin_mono = None
        self._ended = False
        # validate the first frame now (bad media fails preflight, not mid-exam)
        self._skip_deadline = deadline
        try:
            first = self._next_in_loop()
        except _Stopped:
            self._reader.close()
            raise _invalid("replay open was cancelled", "open_cancelled", replay_id=self.replay_id) from None
        except CaptureError:
            self._reader.close()
            raise
        finally:
            self._skip_deadline = None
        if first is None:
            self._reader.close()
            raise _invalid("replay media has no decodable frames in the selected range", "media_no_frames", replay_id=self.replay_id)
        self._pending_first = first

    def _nominal_interval_ms(self) -> float:
        m = self.manifest.media
        fps = m.fps or (self._reader.container_fps if self._reader else None) or 30.0
        return 1000.0 / fps

    def _pts_for(self, index: int, container_pts: float | None) -> float:
        m = self.manifest.media
        if m.timestamps == "sidecar":
            side = self._resolved.sidecar if self._resolved else None
            assert side is not None
            if index < len(side):
                return side[index]
            self.timestamp_repairs += 1  # more frames than timestamps: extrapolate, counted
            return side[-1] + (index - len(side) + 1) * self._nominal_ms
        if m.timestamps == "fps":
            return index * self._nominal_ms
        return container_pts if container_pts is not None and np.isfinite(container_pts) else index * self._nominal_ms

    def _next_in_loop(self, decode: bool = True) -> tuple[np.ndarray | None, float, int] | None:
        """Next frame of the current pass within [start_ms, end_ms]: (image, rel_pts_ms, media_index).

        Frames before ``start_ms`` (and late frames when ``decode`` is False) are only grabbed,
        not decoded; the trim is exact and deterministic. image is None when not decoded and
        an empty array when decoding failed (the caller drops it and counts it)."""
        man = self.manifest
        reader = self._reader
        assert reader is not None
        skipped = 0
        while True:
            if self._stop.is_set():
                raise _Stopped()
            ok, container_pts = reader.grab()
            if not ok:
                return None
            index = self._index
            self._index += 1
            pts = self._pts_for(index, container_pts)
            if pts < man.start_ms:
                skipped += 1
                if self._skip_deadline is not None and skipped % 16 == 0 and time.monotonic() > self._skip_deadline:
                    raise _invalid(
                        "skipping to start_ms takes too long: trim the media file instead", "trim_too_slow", replay_id=self.replay_id
                    )
                continue
            if man.end_ms is not None and pts > man.end_ms:
                return None
            rel = pts - man.start_ms
            if self._last_pts_rel is not None and rel <= self._last_pts_rel:
                rel = self._last_pts_rel + self._nominal_ms
                self.timestamp_repairs += 1
            self._last_pts_rel = rel
            if not decode:
                return None, rel, index
            img = reader.retrieve()
            return (img if img is not None else np.zeros((0,), np.uint8)), rel, index

    def _restart_loop(self) -> bool:
        if not self.manifest.loop or self._last_pts_rel is None:
            return False
        reader = self._reader
        assert reader is not None
        reader.close()
        reader.open()
        self._index_base += self._index
        self._pts_base_ms += self._last_pts_rel + self._nominal_ms
        self._index = 0
        self._last_pts_rel = None
        self.loops += 1
        return True

    def _fit(self, img: np.ndarray) -> np.ndarray:
        if self.manifest.media.mirrored:
            img = img[:, ::-1]
        h, w = img.shape[:2]
        self._media_size = (w, h)
        scale = min(self._max_w / w, self._max_h / h, 1.0)
        if scale < 1.0:
            cv2 = require_cv2(ErrorCode.REPLAY_INVALID)
            size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
            img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
        img = np.ascontiguousarray(img)
        self._out_size = (img.shape[1], img.shape[0])
        return img

    def read(self) -> RawFrame | None:
        if self._ended:
            raise SourceEnded(self.replay_id)
        realtime = self.pacing == "realtime"
        speed = self.manifest.speed
        while not self._stop.is_set():
            item = self._pending_first
            if item is not None:
                self._pending_first = None
            else:
                late = False
                if realtime and self._origin_mono is not None and self._last_emit_rel is not None:
                    # decide BEFORE decoding whether the next frame is already too late
                    expected_rel = self._pts_base_ms + (self._last_pts_rel or 0.0) + self._nominal_ms
                    lateness = (time.monotonic() - self._origin_mono) * 1000.0 * speed - expected_rel
                    late = lateness > self.LATE_DROP_MS and expected_rel - self._last_emit_rel < self.MIN_EMIT_MS
                try:
                    item = self._next_in_loop(decode=not late)
                except _Stopped:
                    return None
                if item is None:
                    if self._restart_loop():
                        continue
                    self._ended = True
                    raise SourceEnded(self.replay_id)
                if late:
                    self.dropped += 1
                    continue
            img, rel, index = item
            global_rel = self._pts_base_ms + rel
            if realtime:
                if self._origin_mono is None:
                    self._origin_mono = time.monotonic() - global_rel / 1000.0 / speed
                due = self._origin_mono + global_rel / 1000.0 / speed
                wait = due - time.monotonic()
                if wait > 0 and self._stop.wait(wait):
                    return None
            frame = normalize_bgr(img)
            if frame is None:
                self.dropped += 1
                continue
            frame = self._fit(frame)
            self.frames_read += 1
            self._last_emit_rel = global_rel
            return RawFrame(image=frame, mono_ns=time.monotonic_ns(), media_pts_ms=global_rel, media_index=self._index_base + index)
        return None

    def close(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            try:
                reader.close()
            except Exception:
                log.info("replay reader close raised", exc_info=True)

    def describe(self) -> FactDict:
        d: FactDict = {"replay_id": self.replay_id, "pacing": self.pacing}
        if self._resolved is not None:
            m = self._resolved.manifest
            d.update(media_kind=m.media.kind, timestamps=m.media.timestamps, loop=m.loop, speed=m.speed)
        if self._media_size:
            d["media_size"] = f"{self._media_size[0]}x{self._media_size[1]}"
        if self._out_size:
            d["output_size"] = f"{self._out_size[0]}x{self._out_size[1]}"
        d.update(frames_read=self.frames_read, timestamp_repairs=self.timestamp_repairs, loops=self.loops)
        return d
