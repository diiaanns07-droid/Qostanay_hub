"""HTTP routes of the desk scan (owner: A15). Registered by A01 in proctor.app (one call)."""

from __future__ import annotations

from typing import Annotated, Any, Callable, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from proctor_contracts.v1 import DeskScanRequest, DeskScanResult

from .clip import write_desk_scan_clip
from .service import DeskScanService, preflight_check


class DeskScanSkipRequest(BaseModel):
    """Local request body (not a contract change): same shape as CalibrationSkipRequest.
    reason "fixed_camera_teacher_check" = variant 3 (fixed camera, the teacher checks the desk)."""

    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, Field(min_length=1, max_length=200)]


def _phone_factory(registry: Any) -> Callable[[], Any] | None:
    factory = getattr(registry, "factories", {}).get("phone")  # A03 public create_phone_analyzer
    if factory is None:
        return None
    settings = registry.settings
    return lambda: factory(settings)


def install_desk_scan_routes(api: APIRouter, manager: Callable[[], Any], registry: Any, service: DeskScanService | None = None) -> DeskScanService:
    """``manager()`` returns the SessionManager (A01); the factory is looked up lazily (after registry.load)."""

    def make_analyzer() -> Any:
        factory = _phone_factory(registry)
        return None if factory is None else factory()

    if service is None:
        service = DeskScanService(make_analyzer, clip_writer=write_desk_scan_clip)

    def store() -> Any:
        try:
            return registry.router_store()
        except Exception:
            return None

    @api.get("/sessions/{session_id}/desk-scan", response_model=DeskScanResult)
    def get_desk_scan(session_id: str) -> DeskScanResult:
        known = service.known_result(session_id)
        if known is not None:
            return known
        mgr = manager()
        if mgr.get(session_id) is None:  # e.g. after a backend restart: the stored result, else 404
            res = service.result(session_id, store())
            if res.scan_id is None:
                mgr.runtime(session_id)  # raises SESSION_NOT_FOUND
            return res
        service.warm_up()  # the student opened the step: load the model before "Начать осмотр"
        return service.result(session_id, store())

    @api.post("/sessions/{session_id}/desk-scan", response_model=DeskScanResult)
    def start_desk_scan(session_id: str, body: DeskScanRequest, mode: Literal["laptop", "usb"] = "laptop") -> DeskScanResult:
        rt = manager().runtime(session_id)
        return service.start(rt, body, mode, store())

    @api.post("/sessions/{session_id}/desk-scan/skip", response_model=DeskScanResult)
    def skip_desk_scan(session_id: str, body: DeskScanSkipRequest) -> DeskScanResult:
        rt = manager().runtime(session_id)
        return service.skip(rt, body.reason, store())

    return service


__all__ = ["DeskScanSkipRequest", "install_desk_scan_routes", "preflight_check"]
