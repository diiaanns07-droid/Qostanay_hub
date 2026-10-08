"""Message helpers for qorgau.class.audio.v1 (additive to qorgau.class.v1 §6). No third-party imports."""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

PROTOCOL = "qorgau.class.v1"
AUDIO_PROTOCOL = "qorgau.class.audio.v1"
MAX_MESSAGE_BYTES = 256 * 1024  # v1 §3
MAX_SDP_CHARS = 64 * 1024

ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SESSION_ID_RE = re.compile(r"^as-[0-9a-f]{32}$")

TEACHER_TYPES = {"audio_request", "audio_update", "audio_signal", "audio_media", "audio_stop"}
STUDENT_AUDIO_TYPES = {"audio_signal", "audio_media"}

ERROR_RU = {
    "not_authorized": "Нет доступа преподавателя.",
    "bad_request": "Некорректный запрос аудиосвязи.",
    "student_unknown": "Такого студента нет в классе.",
    "student_offline": "Студент сейчас не на связи.",
    "teacher_busy": "Уже идёт аудиосвязь с другим студентом. Сначала завершите её.",
    "student_busy": "У этого студента уже идёт аудиосвязь.",
    "session_mismatch": "Сообщение относится не к текущей аудиосвязи — отклонено.",
    "session_ended": "Аудиосвязь уже завершена.",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def envelope(type_: str, **fields: Any) -> dict[str, Any]:
    """v1 envelope: {type, v, msg_id, sent_at, ...fields}."""
    return {"type": type_, "v": 1, "msg_id": str(uuid.uuid4()), "sent_at": utc_now(), **fields}


def new_session_id() -> str:
    return "as-" + secrets.token_hex(16)


def new_command_id() -> str:
    return str(uuid.uuid4())


def direction(listen: bool, talk: bool) -> str:
    """v1 `audio_start.payload.direction` from the teacher's point of view."""
    return "both" if listen and talk else "listen" if listen else "talk"


class BadMessage(ValueError):
    pass


def req_bool(msg: dict[str, Any], key: str) -> bool:
    v = msg.get(key)
    if not isinstance(v, bool):
        raise BadMessage(f"{key} must be a boolean")
    return v


def req_id(msg: dict[str, Any], key: str, pattern: re.Pattern[str] = ID_RE) -> str:
    v = msg.get(key)
    if not isinstance(v, str) or not pattern.match(v):
        raise BadMessage(f"{key} is missing or malformed")
    return v


def clean_signal(msg: dict[str, Any], allowed_kinds: set[str]) -> dict[str, Any]:
    """Validate an audio_signal body; returns only the relayable fields (never the sender's extras)."""
    kind = msg.get("kind")
    if kind not in allowed_kinds:
        raise BadMessage(f"kind must be one of {sorted(allowed_kinds)}")
    out: dict[str, Any] = {"kind": kind}
    if kind in ("offer", "answer"):
        sdp = msg.get("sdp")
        if not isinstance(sdp, str) or not sdp.startswith("v=0") or len(sdp) > MAX_SDP_CHARS:
            raise BadMessage("sdp is missing or malformed")
        out["sdp"] = sdp
        out["ice_restart"] = bool(msg.get("ice_restart", False))
    else:
        ice = msg.get("ice")
        if ice is None:
            out["ice"] = None  # end of candidates
        elif isinstance(ice, dict):
            cand = ice.get("candidate")
            mid = ice.get("sdpMid")
            idx = ice.get("sdpMLineIndex")
            if not isinstance(cand, str) or len(cand) > 2048:
                raise BadMessage("ice.candidate malformed")
            if mid is not None and (not isinstance(mid, str) or len(mid) > 64):
                raise BadMessage("ice.sdpMid malformed")
            if idx is not None and (not isinstance(idx, int) or not 0 <= idx < 64):
                raise BadMessage("ice.sdpMLineIndex malformed")
            out["ice"] = {"candidate": cand, "sdpMid": mid, "sdpMLineIndex": idx}
        else:
            raise BadMessage("ice must be an object or null")
    return out
