"""Class server configuration (owner: T01). Defaults <- environment QORGAU_CLASS_<FIELD> <- explicit overrides."""

from __future__ import annotations

import os
import sys
from dataclasses import MISSING, dataclass, field, fields, replace
from pathlib import Path

SERVER_VERSION = "0.1.0"
CLASSROOM_ROOT = Path(__file__).resolve().parents[1]  # proctoring/classroom
CLASS_PANEL_DIR = CLASSROOM_ROOT.parent / "class-panel"  # T02 (vanilla panel), served as-is with the REAL adapter


def _default_data_dir() -> Path:
    """Outside the repository (classroom data must never land in the source tree)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "QorgauClassroom"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "qorgau-classroom"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"  # students connect over LAN; teacher routes are loopback-only regardless
    port: int = 8765  # protocol v1 §1; 0 = ephemeral (tests)
    data_dir: Path = field(default_factory=_default_data_dir)
    ui_dir: Path = CLASSROOM_ROOT / "teacher-ui" / "dist"
    ui: str = "auto"  # auto = teacher-ui/dist if built, else the T02 class panel; "teacher-ui" | "class-panel" | "none" | <dir>
    teacher_pin: str = ""  # empty = random 6-digit PIN printed to the console at start (v1 §2.6)
    dev_origin: str = ""  # extra allowed Origin for the teacher UI dev server, e.g. http://127.0.0.1:5173
    # audio (T05 bridge) + T03 history/clips/decisions + T04 exams/commands (INTERFACES.md §3); a missing module
    # shows as "not_installed" in /api/teacher/info and the core keeps its 501 fallbacks
    features: str = "classroom.server.audio_feature:create_classroom_feature,classreview.classroom_feature:create,proctor_classctl.classroom_feature:create"

    ping_interval_s: float = 5.0  # v1 §3.2
    pong_timeout_s: float = 15.0  # v1 §3.2: no pong for 15 s -> "нет связи"
    hello_timeout_s: float = 10.0
    status_stale_s: float = 10.0  # v1 §4: no status for > 10 s -> grey
    ack_window_s: float = 10.0  # v1 §3.2: no ack for 10 s -> "команда не подтверждена" (still pending)
    command_ttl_s: float = 120.0  # default expiry of a command; never delivered after it
    tick_s: float = 0.5

    max_ws_message_bytes: int = 256 * 1024  # v1 §3
    max_clip_bytes: int = 8 * 1024 * 1024  # v1 §5
    preview_min_interval_s: float = 1.0
    join_fail_limit: int = 5  # v1 §2.5: 5 failed attempts from one IP -> 30 s pause
    join_block_s: float = 30.0
    pin_fail_limit: int = 5
    pin_block_s: float = 60.0
    teacher_queue: int = 2000  # per teacher-stream client; overflow -> resync_required
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None, **overrides: object) -> "ServerConfig":
        env = os.environ if environ is None else environ
        values: dict[str, object] = {}
        for f in fields(cls):
            raw = env.get(f"QORGAU_CLASS_{f.name.upper()}")
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


def resolve_ui(config: ServerConfig) -> tuple[str, Path | None]:
    """-> (kind, directory). kind: "teacher-ui" (T01 React shell), "class-panel" (T02 panel), "custom", "none"."""
    named = {"teacher-ui": config.ui_dir, "class-panel": CLASS_PANEL_DIR}
    order = ["teacher-ui", "class-panel"] if config.ui == "auto" else [config.ui] if config.ui in named else []
    for kind in order:
        if (named[kind] / "index.html").is_file():
            return kind, named[kind]
    if config.ui not in ("auto", "none", *named) and (Path(config.ui) / "index.html").is_file():
        directory = Path(config.ui).resolve()
        return ("class-panel" if (directory / "src" / "adapters" / "real.js").is_file() else "custom"), directory
    return "none", None
