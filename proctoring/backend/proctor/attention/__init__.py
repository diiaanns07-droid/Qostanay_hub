"""A04 attention module: faces, presence, primary face, head pose, approximate gaze, calibration.

Public entry point (OWNERSHIP.json): create_attention_analyzer(settings) -> AttentionAnalyzer.
Gaze here is an approximate estimate from head pose + eye landmarks, NOT eye tracking and NOT
evidence of cheating. See handoffs/A04/INTERFACE.md for value semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import MODULE_VERSION, AttentionConfig

if TYPE_CHECKING:  # pragma: no cover
    from proctor_contracts.interfaces import AttentionAnalyzer

    from proctor.settings import Settings

__all__ = ["create_attention_analyzer", "AttentionConfig", "MODULE_VERSION"]


def create_attention_analyzer(settings: "Settings") -> "AttentionAnalyzer":
    """Construct the analyzer. Cheap; the model is loaded (and verified) in load()."""
    from .analyzer import MediaPipeAttentionAnalyzer

    return MediaPipeAttentionAnalyzer(settings, AttentionConfig())
