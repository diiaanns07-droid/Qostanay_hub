"""Capture module (owner: A02): the single source of frames for the whole product.

Public entry point (OWNERSHIP.json ``provides``)::

    from proctor.capture import create_capture_service
    capture = create_capture_service(settings)   # -> proctor_contracts.interfaces.CaptureService

Only this package opens ``cv2.VideoCapture``. Phone (A03) and attention (A04) receive
``FramePacket`` objects through ``CaptureService.add_consumer`` and never open a camera; the
renderer (A07) gets the preview over the backend's ``/v1/preview`` transport.
Replay manifests: ``proctor.capture.replay`` (format ``qorgau.replay.v1``).
Class-mode incident clips: ``FrameCaptureService.export_clip`` (``proctor.capture.clips``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .clips import ClipError, ClipResult
from .service import CAPTURE_VERSION, CLIPS_CONSUMER, PREVIEW_CONSUMER, FrameCaptureService

if TYPE_CHECKING:  # pragma: no cover
    from proctor.settings import Settings

__all__ = ["CAPTURE_VERSION", "CLIPS_CONSUMER", "PREVIEW_CONSUMER", "ClipError", "ClipResult", "FrameCaptureService", "create_capture_service"]


def create_capture_service(settings: "Settings") -> FrameCaptureService:
    """Factory used by the composition root (proctor.app.ModuleRegistry)."""
    return FrameCaptureService(settings)
