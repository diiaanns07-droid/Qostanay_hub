"""Evidence storage, human review and teacher reports (owner: A08).

Public entry point (OWNERSHIP.json): create_evidence_store(settings) -> EvidenceStore.
SQLite database and media live under settings.data_dir (outside the source tree).
"""

from __future__ import annotations

from proctor.settings import Settings

from .config import EvidenceConfig
from .store import SqliteEvidenceStore


def create_evidence_store(settings: Settings, config: EvidenceConfig | None = None) -> SqliteEvidenceStore:
    """A01 calls this once at startup, then store.open() and store.create_router(context)."""
    return SqliteEvidenceStore(settings, config)


__all__ = ["EvidenceConfig", "SqliteEvidenceStore", "create_evidence_store"]
