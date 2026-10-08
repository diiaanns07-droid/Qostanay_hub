"""Uplink configuration from the environment (owner: C2). No Settings field is added (A01 owns Settings).

    QORGAU_CLASS_SERVER   host:port of the class server (qorgau.class.v1), e.g. 192.168.1.10:8765
    QORGAU_CLASS_CODE     6-digit join code from the teacher
    QORGAU_CLASS_LABEL    optional student label shown to the teacher (default: computer name)
    QORGAU_CLASS_PREVIEW_FPS  0.5–5 (default 0.5); the teacher's preview rate limit also applies

Both SERVER and CODE must be set, otherwise the uplink is disabled and the backend works locally.
"""

from __future__ import annotations

import math
import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path

PROTOCOL = "qorgau.class.v1"
_HOSTPORT = re.compile(r"^[A-Za-z0-9.\-]{1,253}:\d{1,5}$|^\[[0-9A-Fa-f:]+\]:\d{1,5}$")
_CODE = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class UplinkConfig:
    server: str  # host:port
    join_code: str
    student_label: str
    computer_name: str
    state_dir: Path  # resume token + outbox (inside settings.data_dir, outside Git)
    status_interval_s: float = 2.0
    preview_interval_s: float = 2.0
    poll_interval_s: float = 0.5
    backoff_max_s: float = 15.0
    welcome_timeout_s: float = 10.0
    outbox_max: int = 1000
    clip_before_s: float = 5.0
    clip_after_s: float = 5.0

    @property
    def ws_url(self) -> str:
        return f"ws://{self.server}/ws/student"

    def clip_url(self, incident_id: str) -> str:
        return f"http://{self.server}/api/student/clips/{incident_id}"


def config_from_env(data_dir: Path, environ: dict[str, str] | None = None) -> UplinkConfig | None:
    """None when the uplink is not configured. Raises ValueError for a malformed server/code."""
    env = os.environ if environ is None else environ
    server = (env.get("QORGAU_CLASS_SERVER") or "").strip()
    code = (env.get("QORGAU_CLASS_CODE") or "").strip()
    if not server or not code:
        return None
    if server.startswith(("ws://", "http://")):
        server = server.split("://", 1)[1].rstrip("/")
    if not _HOSTPORT.match(server) or not 0 < int(server.rsplit(":", 1)[1]) < 65536:
        raise ValueError("QORGAU_CLASS_SERVER must be host:port")
    if not _CODE.match(code):
        raise ValueError("QORGAU_CLASS_CODE must be 6 digits")
    computer = (platform.node() or "student-pc")[:64]
    label = (env.get("QORGAU_CLASS_LABEL") or computer).strip()[:64]
    try:
        preview_fps = float(env.get("QORGAU_CLASS_PREVIEW_FPS", "0.5"))
    except (TypeError, ValueError):
        raise ValueError("QORGAU_CLASS_PREVIEW_FPS must be a number from 0.5 to 5") from None
    if not math.isfinite(preview_fps) or not 0.5 <= preview_fps <= 5.0:
        raise ValueError("QORGAU_CLASS_PREVIEW_FPS must be a number from 0.5 to 5")
    return UplinkConfig(server=server, join_code=code, student_label=label, computer_name=computer,
                        state_dir=Path(data_dir) / "class_uplink", preview_interval_s=1.0 / preview_fps)
