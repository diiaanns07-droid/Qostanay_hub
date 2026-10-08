"""T03 settings (episodes, clips, teacher decisions on the class server).

Values: defaults, then environment variables. Data lives OUTSIDE the source tree:
  QORGAU_CLASS_DATA_DIR                  base directory (default: %LOCALAPPDATA%/QorgauClass or ~/.local/share/qorgau-class)
  QORGAU_CLASS_REVIEW_CLIP_TIMEOUT_S     seconds a requested clip may take before it is shown as "недоступен" (default 120)
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

#: qorgau.class.v1 §5: body video/mp4 or video/x-msvideo, at most 8 MB.
MAX_CLIP_BYTES = 8 * 1024 * 1024
CLIP_MEDIA_TYPES = {"video/mp4": "mp4", "video/x-msvideo": "avi"}
#: §3.1: snapshot_jpeg_b64 ≤ 40 KB of JPEG.
MAX_SNAPSHOT_BYTES = 40 * 1024
MAX_SNAPSHOT_B64 = math.ceil(MAX_SNAPSHOT_BYTES * 4 / 3) + 8


def default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "QorgauClass"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "qorgau-class"


@dataclass(frozen=True)
class ReviewConfig:
    data_dir: Path = field(default_factory=default_data_dir)
    db_file_name: str = "class-review.sqlite3"
    max_clip_bytes: int = MAX_CLIP_BYTES
    clip_request_timeout_s: float = 120.0
    max_explanation_chars: int = 1000
    max_note_chars: int = 1000
    max_duration_ms: float = 24 * 3600 * 1000.0
    decision_dedup_window_s: float = 5.0

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None, **overrides: object) -> "ReviewConfig":
        env = os.environ if environ is None else environ
        values: dict[str, object] = {}
        if env.get("QORGAU_CLASS_DATA_DIR"):
            values["data_dir"] = Path(env["QORGAU_CLASS_DATA_DIR"]).expanduser()
        if env.get("QORGAU_CLASS_REVIEW_CLIP_TIMEOUT_S"):
            values["clip_request_timeout_s"] = float(env["QORGAU_CLASS_REVIEW_CLIP_TIMEOUT_S"])
        return replace(cls(), **{**values, **overrides})
