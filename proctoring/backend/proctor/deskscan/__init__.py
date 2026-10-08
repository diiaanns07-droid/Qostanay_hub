"""Desk (workplace) scan before the exam (owner: A15, contract 1.2).

Public entry points (wired by A01 in proctor.app):
    install_desk_scan_routes(api, manager_getter, registry) -> DeskScanService
        GET  /v1/sessions/{session_id}/desk-scan                       -> DeskScanResult
        POST /v1/sessions/{session_id}/desk-scan?mode=laptop|usb       (DeskScanRequest) -> DeskScanResult (recording)
        POST /v1/sessions/{session_id}/desk-scan/skip  {"reason": ...} -> DeskScanResult (skipped; PIN in the shell)
    preflight_check(result) -> PreflightCheck (DESK_SCAN, required=false)
The scan helps the teacher; it is not evidence of a violation.
"""

from __future__ import annotations

from .routes import DeskScanSkipRequest, install_desk_scan_routes
from .service import FIXED_CAMERA_REASON, DeskScanAggregator, DeskScanService, preflight_check

__all__ = [
    "FIXED_CAMERA_REASON",
    "DeskScanAggregator",
    "DeskScanService",
    "DeskScanSkipRequest",
    "install_desk_scan_routes",
    "preflight_check",
]
