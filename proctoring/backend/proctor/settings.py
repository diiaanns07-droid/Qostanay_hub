"""Backend settings (owner: A01). Read-only for every other module.

Values come from defaults, then environment variables QORGAU_<NAME> (upper case).
Nothing here is a secret: the API token is passed separately (stdin or a named env var)
and is never stored in Settings.
"""

from __future__ import annotations

import os
import sys
from dataclasses import MISSING, dataclass, field, fields, replace
from pathlib import Path

BACKEND_VERSION = "0.1.0"
PROCTORING_ROOT = Path(__file__).resolve().parents[2]  # .../proctoring


def _default_data_dir() -> Path:
    """User data dir OUTSIDE the repository (evidence must not land in the source tree)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "QorgauExam"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "qorgau-exam"


@dataclass(frozen=True)
class Settings:
    # --- network (loopback only) ---
    host: str = "127.0.0.1"
    port: int = 0  # 0 = ephemeral; the actual port is printed in the READY line
    max_body_bytes: int = 1_000_000
    # Extra Origin allowed for browser-based renderer development (e.g. http://127.0.0.1:5173).
    # Empty = requests carrying an Origin header are rejected (Electron main sends none).
    dev_allow_origin: str = ""

    # --- locations ---
    data_dir: Path = field(default_factory=_default_data_dir)  # SQLite + evidence (A08)
    models_dir: Path = PROCTORING_ROOT / "models"  # weights, git-ignored; manifests live in modules
    replay_dir: Path = PROCTORING_ROOT / "demo" / "replay"  # replay manifests (A02/A10); media outside Git
    exam_path: Path = PROCTORING_ROOT / "demo" / "exams" / "demo_exam.json"  # A10 content

    # --- capture (A02) ---
    camera_index: int = 0
    capture_width: int = 640
    capture_height: int = 480
    capture_fps: int = 30
    preview_fps: float = 15.0
    preview_jpeg_quality: int = 70
    frame_ring_seconds: float = 3.0

    # --- analysis rates (targets for scheduling, not measurements) ---
    phone_max_fps: float = 8.0
    attention_max_fps: float = 15.0
    fusion_tick_ms: float = 250.0

    # --- privacy ---
    retain_media_default: bool = False

    # --- bootstrap / synthetic ---
    synthetic_fps: float = 15.0

    log_level: str = "INFO"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None, **overrides) -> "Settings":
        env = os.environ if environ is None else environ
        values: dict[str, object] = {}
        for f in fields(cls):
            raw = env.get(f"QORGAU_{f.name.upper()}")
            if raw is None:
                continue
            default = f.default_factory() if f.default_factory is not MISSING else f.default  # type: ignore[misc]
            values[f.name] = _coerce(raw, default)
        return replace(cls(), **{**values, **overrides})


def _coerce(raw: str, default: object) -> object:
    if isinstance(default, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, Path):
        return Path(raw).expanduser()
    return raw
