"""Phone detection and phone signals (owner: A03).

Public entry point (OWNERSHIP.json): ``create_phone_analyzer(settings) -> FrameAnalyzer`` (name="phone").
Local YOLO11n ONNX on onnxruntime CPU, verified against ``models.manifest.json``; never downloads at
runtime (weights are prepared with ``python -m proctor.phone.prepare``). Emits PhoneObservation with raw
detections + signals phone_visible / phone_raised / possible_screen_capture; incidents belong to A05.
A phone in the frame is NOT evidence that anything was photographed.
"""

from __future__ import annotations

from typing import Any

from .analyzer import PHONE_MODULE_VERSION, PhoneAnalyzer
from .config import PhoneConfig

__all__ = ["PHONE_MODULE_VERSION", "PhoneAnalyzer", "PhoneConfig", "create_phone_analyzer"]


def create_phone_analyzer(settings: Any) -> PhoneAnalyzer:
    """Build the analyzer (cheap, no I/O). Weights are verified/loaded in ``load()``.

    Configuration: PhoneConfig defaults overridden by QORGAU_PHONE_<FIELD> environment variables. An
    invalid override does not raise here: ``load()`` then reports UNAVAILABLE ``config_invalid``.
    """
    try:
        config = PhoneConfig.from_env()
    except ValueError as exc:
        return PhoneAnalyzer(settings, PhoneConfig(), config_error=str(exc))
    return PhoneAnalyzer(settings, config)
