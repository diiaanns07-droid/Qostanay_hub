"""A05 fusion: observations (A03/A04/A06/A02 health) -> explainable incidents (to A08/A07).

Public entry point (OWNERSHIP.json):
    proctor.fusion.create_incident_engine(session_id, source_mode, settings) -> IncidentEngine
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from proctor_contracts.v1 import SourceMode

from .config import CONFIG_NAME, RULE_VERSION, FusionConfig, IntervalParams
from .engine import FusionEngine

if TYPE_CHECKING:  # pragma: no cover
    from proctor.settings import Settings

__all__ = [
    "CONFIG_NAME",
    "RULE_VERSION",
    "FusionConfig",
    "FusionEngine",
    "IntervalParams",
    "create_incident_engine",
]


def create_incident_engine(
    session_id: str,
    source_mode: SourceMode,
    settings: "Settings | None" = None,
    *,
    config: FusionConfig | dict[str, Any] | None = None,
) -> FusionEngine:
    """One engine per session (created by A01 at ``start``). ``settings`` is accepted for the contract
    signature; thresholds come from the versioned FusionConfig (``config`` overrides are for tests,
    replay tuning and a future Settings field — see handoffs/A05/DEPENDENCIES.txt)."""
    if isinstance(config, dict):
        config = FusionConfig.from_dict(config)
    return FusionEngine(session_id, source_mode, config)
