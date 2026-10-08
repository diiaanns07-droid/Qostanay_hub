"""A08 storage knobs (owner: A08).

Defaults are deliberately minimal: metadata only, media only when a session has
retain_media=true, short media TTL, bounded buffers. Environment overrides use the prefix
QORGAU_EVIDENCE_<FIELD> (e.g. QORGAU_EVIDENCE_MEDIA_TTL_S=86400). Shared backend settings
(data_dir, ...) come from proctor.settings.Settings and are read-only here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class EvidenceConfig:
    db_file_name: str = "qorgau-evidence.sqlite3"
    media_dir_name: str = "evidence-media"

    # In-memory buffer of recent CV observations. Only observations referenced by an incident
    # (plus environment/health observations) are written to SQLite.
    observation_buffer_ms: float = 120_000.0
    observation_buffer_max: int = 5_000
    max_observation_json_bytes: int = 64_000

    # Media (only when SessionInfo.retain_media is true)
    jpeg_quality: int = 85
    max_snapshot_bytes: int = 2_000_000
    max_snapshots_per_incident: int = 3
    max_media_items_per_session: int = 200
    max_media_bytes_per_session: int = 50_000_000
    media_ttl_s: float = 7 * 24 * 3600.0  # media older than this is purged on open/next session
    min_free_disk_bytes: int = 100_000_000  # refuse new media below this free space

    # Coverage (observed monitoring time)
    coverage_gap_ms: float = 2_000.0  # a hole longer than this between observations is a gap
    coverage_flush_ms: float = 2_000.0  # session-time interval between coverage writes
    max_coverage_segments: int = 20_000  # per session and component
    max_gaps_reported: int = 200

    # Reviews: an identical review repeated within this window is treated as a double submit
    review_dedup_window_s: float = 5.0

    # Report/export bounds
    report_max_incidents: int = 2_000
    report_max_embedded_images: int = 60
    report_max_embedded_bytes: int = 20_000_000
    report_max_observations: int = 5_000

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None, **overrides: object) -> "EvidenceConfig":
        env = os.environ if environ is None else environ
        values: dict[str, object] = {}
        base = cls()
        for f in fields(cls):
            raw = env.get(f"QORGAU_EVIDENCE_{f.name.upper()}")
            if raw is None:
                continue
            default = getattr(base, f.name)
            if isinstance(default, int):
                values[f.name] = int(raw)
            elif isinstance(default, float):
                values[f.name] = float(raw)
            else:
                values[f.name] = raw
        return replace(base, **{**values, **overrides})
