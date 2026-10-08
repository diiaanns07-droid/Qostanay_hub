"""Identity continuity (owner: A13): control that the person at the computer is the same person as at the
start of the exam. Local, in memory, no biometric database; not identification, not anti-spoofing.

Public entry point (OWNERSHIP.json): ``create_identity_analyzer(settings) -> FrameAnalyzer`` (name="identity").
OpenCV Zoo YuNet (face boxes, MIT) + SFace (128-d feature, Apache-2.0) through cv2.FaceDetectorYN /
cv2.FaceRecognizerSF, verified against ``models.manifest.json``; never downloads at runtime (prepare with
``python -m proctor.identity.prepare --download``). Emits IdentityObservation; incidents belong to A05.
"""

from __future__ import annotations

from typing import Any

from .analyzer import IDENTITY_MODULE_VERSION, IdentityAnalyzer
from .config import IdentityConfig

__all__ = ["IDENTITY_MODULE_VERSION", "IdentityAnalyzer", "IdentityConfig", "create_identity_analyzer"]


def create_identity_analyzer(settings: Any) -> IdentityAnalyzer:
    """Build the analyzer (cheap, no I/O). Models are verified/loaded in ``load()``.

    Configuration: IdentityConfig defaults overridden by QORGAU_IDENTITY_<FIELD>. An invalid override does
    not raise here: ``load()`` then reports UNAVAILABLE ``config_invalid``.
    """
    try:
        config = IdentityConfig.from_env()
    except ValueError as exc:
        return IdentityAnalyzer(settings, IdentityConfig(), config_error=str(exc))
    return IdentityAnalyzer(settings, config)
