"""Composition root and HTTP/WebSocket API v1 (owner: A01).

Only this file wires modules together. Modules are discovered by their agreed factories
(see proctor_contracts.interfaces). A missing module is reported as UNAVAILABLE with code
"module_not_integrated"; bootstrap substitutes exist ONLY for synthetic sessions and are
labelled "bootstrap" everywhere. LIVE/REPLAY never fall back to synthetic parts.
"""

from __future__ import annotations

import asyncio
import hmac
import importlib
import json
import logging
import re
import struct
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import HTTPConnection

from proctor_contracts.interfaces import ProctorError
from proctor_contracts.v1 import (
    AbortRequest,
    ApiError,
    ApiErrorBody,
    CalibrationSkipRequest,
    CalibrationState,
    CalibrationTargetRequest,
    Component,
    CONTRACT_ID,
    EnvironmentCapabilities,
    EnvironmentEventAck,
    EnvironmentEventBatch,
    ErrorCode,
    ExamDefinition,
    Health,
    HealthMsg,
    HealthReport,
    HealthStatus,
    HelloMsg,
    PauseRequest,
    PreflightReport,
    RuntimeMetrics,
    SessionCreate,
    SessionInfo,
    SourceMode,
    utc_now,
)

from .bootstrap.engine import BootstrapIncidentEngine
from .bootstrap.memory_store import MemoryEvidenceStore
from .bootstrap.synthetic import ScriptedAttentionAnalyzer, ScriptedPhoneAnalyzer, SyntheticCaptureService
from .session import Pipeline, PipelinePart, SessionManager
from .settings import BACKEND_VERSION, PROCTORING_ROOT, Settings
from .uplink import start_uplink  # C2: class-mode uplink (disabled unless QORGAU_CLASS_SERVER/CODE are set)

log = logging.getLogger("proctor.app")

MODULES: dict[str, tuple[str, str, Component]] = {
    "capture": ("proctor.capture", "create_capture_service", Component.CAPTURE),
    "phone": ("proctor.phone", "create_phone_analyzer", Component.PHONE),
    "attention": ("proctor.attention", "create_attention_analyzer", Component.ATTENTION),
    "identity": ("proctor.identity", "create_identity_analyzer", Component.IDENTITY),  # A13, contract 1.1
    "fusion": ("proctor.fusion", "create_incident_engine", Component.FUSION),
    "evidence": ("proctor.evidence", "create_evidence_store", Component.EVIDENCE),
}
FALLBACK_EXAM = PROCTORING_ROOT / "contracts" / "fixtures" / "v1" / "ExamDefinition.demo_min.json"


# ---------------------------------------------------------------------------
# Module registry (composition)
# ---------------------------------------------------------------------------


@dataclass
class _Loaded:
    impl: Any | None
    health: Health


class ModuleRegistry:
    def __init__(self, settings: Settings, overrides: dict[str, Callable[..., Any]] | None = None):
        self.settings = settings
        self._overrides = overrides or {}
        self.factories: dict[str, Callable[..., Any]] = {}
        self.loaded: dict[str, _Loaded] = {}
        self.import_health: dict[str, Health] = {}
        self._env_caps: EnvironmentCapabilities | None = None
        self._env_lock = threading.Lock()
        # bootstrap (synthetic-only) parts
        self.synthetic_capture = SyntheticCaptureService(
            fps=settings.synthetic_fps, preview_fps=settings.preview_fps, ring_seconds=settings.frame_ring_seconds
        )
        self.scripted_phone = ScriptedPhoneAnalyzer()
        self.scripted_attention = ScriptedAttentionAnalyzer()
        self.memory_store = MemoryEvidenceStore()

    def load(self) -> None:
        for key, (module_name, factory_name, component) in MODULES.items():
            if key in self._overrides and self._overrides[key] is None:
                self.import_health[key] = self._unavailable(
                    component, "module_not_integrated", f"{module_name} hidden by module_overrides"
                )
                continue
            factory = self._overrides.get(key)
            if factory is None:
                try:
                    module = importlib.import_module(module_name)
                    factory = getattr(module, factory_name)
                except ModuleNotFoundError as exc:
                    if exc.name != module_name:
                        log.exception("import of %s failed", module_name)
                        self.import_health[key] = self._unavailable(component, "import_error", f"{type(exc).__name__}: {exc}")
                    else:
                        self.import_health[key] = self._unavailable(
                            component, "module_not_integrated", f"{module_name} is not integrated in this build"
                        )
                    continue
                except Exception as exc:  # broken module must not take the backend down
                    log.exception("import of %s failed", module_name)
                    self.import_health[key] = self._unavailable(component, "import_error", f"{type(exc).__name__}: {exc}")
                    continue
            self.factories[key] = factory
        for key in ("capture", "phone", "attention", "identity", "evidence"):
            factory = self.factories.get(key)
            if factory is None:
                continue
            component = MODULES[key][2]
            try:
                impl = factory(self.settings)
                if key in ("phone", "attention", "identity"):
                    health = impl.load()
                elif key == "evidence":
                    health = impl.open()
                else:
                    health = impl.health()
                self.loaded[key] = _Loaded(impl, health)
            except Exception as exc:
                log.exception("factory for %s failed", key)
                self.loaded[key] = _Loaded(None, self._unavailable(component, "init_error", f"{type(exc).__name__}: {exc}"))

    @staticmethod
    def _unavailable(component: Component, code: str, message: str) -> Health:
        return Health(component=component, status=HealthStatus.UNAVAILABLE, code=code, message=message[:500])

    def _part(self, key: str) -> _Loaded:
        if key in self.loaded:
            return self.loaded[key]
        health = self.import_health.get(key) or self._unavailable(MODULES[key][2], "module_not_integrated", "not loaded")
        return _Loaded(None, health)

    def pipeline_for(self, mode: SourceMode) -> Pipeline:
        synthetic = mode == SourceMode.SYNTHETIC

        def module_or_bootstrap(key: str, bootstrap: Any) -> PipelinePart:
            part = self._part(key)
            if part.impl is not None:
                return PipelinePart(part.impl, "module", part.health)
            if synthetic:
                return PipelinePart(bootstrap, "bootstrap", bootstrap.health())
            return PipelinePart(None, "missing", part.health)

        capture = module_or_bootstrap("capture", self.synthetic_capture)
        if synthetic:  # synthetic frames carry no real content: analysis is scripted and labelled
            phone = PipelinePart(self.scripted_phone, "bootstrap", self.scripted_phone.health())
            attention = PipelinePart(self.scripted_attention, "bootstrap", self.scripted_attention.health())
        else:
            phone = module_or_bootstrap("phone", None)
            attention = module_or_bootstrap("attention", None)
        # A13: real frames only (synthetic frames have no faces); absent module => no identity consumer
        identity = None if synthetic else module_or_bootstrap("identity", None)
        store = module_or_bootstrap("evidence", self.memory_store)

        fusion_factory = self.factories.get("fusion")
        if fusion_factory is not None:
            settings = self.settings
            engine_factory = lambda sid, m: fusion_factory(sid, m, settings)  # noqa: E731
            engine_label = "module"
            engine_health = Health(component=Component.FUSION, status=HealthStatus.OK, code="ok")
        elif synthetic:
            engine_factory = lambda sid, m: BootstrapIncidentEngine(sid, m)  # noqa: E731
            engine_label = "bootstrap"
            engine_health = Health(
                component=Component.FUSION,
                status=HealthStatus.DEGRADED,
                code="bootstrap_engine",
                message="Bootstrap engine: phone_visible + environment only (synthetic)",
            )
        else:
            engine_factory, engine_label = None, "missing"
            engine_health = self.import_health.get("fusion") or self._unavailable(Component.FUSION, "module_not_integrated", "")
        return Pipeline(capture, phone, attention, store, engine_factory, engine_label, engine_health, identity=identity)

    def router_store(self) -> Any:
        part = self._part("evidence")
        return part.impl if part.impl is not None else self.memory_store

    # environment capabilities reported by the shell (A06)
    def set_environment_capabilities(self, caps: EnvironmentCapabilities) -> None:
        with self._env_lock:
            self._env_caps = caps

    def environment_capabilities(self) -> EnvironmentCapabilities | None:
        with self._env_lock:
            return self._env_caps

    def health_components(self) -> list[Health]:
        result = [Health(component=Component.BACKEND, status=HealthStatus.OK, code="ok", message=f"backend {BACKEND_VERSION}")]
        for key, (_, _, component) in MODULES.items():
            part = self._part(key)
            if key == "fusion":
                result.append(
                    Health(component=component, status=HealthStatus.OK, code="ok")
                    if key in self.factories
                    else part.health
                )
                continue
            impl = part.impl
            try:
                result.append(impl.health() if impl is not None else part.health)
            except Exception as exc:
                result.append(self._unavailable(component, "health_error", str(exc)))
        caps = self.environment_capabilities()
        result.append(
            Health(component=Component.ENVIRONMENT, status=HealthStatus.OK, code="capabilities_reported", message=caps.platform)
            if caps is not None
            else Health(component=Component.ENVIRONMENT, status=HealthStatus.UNAVAILABLE, code="shell_not_reported")
        )
        return result

    def close(self) -> None:
        for key, part in self.loaded.items():
            if part.impl is None:
                continue
            try:
                if key == "capture":
                    part.impl.close()
                elif hasattr(part.impl, "close"):
                    part.impl.close()
            except Exception:
                log.exception("close of %s failed", key)
        self.synthetic_capture.close()


# ---------------------------------------------------------------------------
# Event stream hub
# ---------------------------------------------------------------------------


class _StreamClient:
    def __init__(self, session_filter: str | None):
        self.session_filter = session_filter
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=512)
        self.seq = 0

    def offer(self, session_id: str | None, payload: dict) -> None:
        if self.session_filter and session_id and session_id != self.session_filter:
            return
        self.seq += 1  # a dropped message leaves a visible gap in seq
        envelope = {
            "contract": CONTRACT_ID,
            "seq": self.seq,
            "sent_at": utc_now().isoformat(),
            "session_id": session_id,
            "message": payload,
        }
        try:
            self.queue.put_nowait(envelope)
        except asyncio.QueueFull:
            pass


class StreamHub:
    """Thread-safe publish from any thread; delivery on the server event loop."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[_StreamClient] = set()
        # C2 can connect before Electron subscribes, or change state while the
        # renderer refreshes. Retain only this global state, not session events.
        # Written/read on the hub loop so snapshot + subscription are ordered.
        self._class_state: dict | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, message: Any, session_id: str | None) -> None:
        loop = self._loop
        if loop is None:
            return
        if not self._clients and not (session_id is None and getattr(message, "type", None) == "class_state"):
            return
        payload = message.model_dump(mode="json")
        try:
            loop.call_soon_threadsafe(self._fanout, session_id, payload)
        except RuntimeError:
            pass  # loop closed during shutdown

    def _fanout(self, session_id: str | None, payload: dict) -> None:
        if session_id is None and payload.get("type") == "class_state":
            self._class_state = payload
        for client in list(self._clients):
            client.offer(session_id, payload)

    async def serve(self, ws: WebSocket, session_filter: str | None) -> None:
        client = _StreamClient(session_filter)
        self._clients.add(client)
        disconnected = asyncio.create_task(_wait_disconnect(ws))
        try:
            client.offer(None, HelloMsg(backend_version=BACKEND_VERSION, server_time=utc_now()).model_dump(mode="json"))
            if self._class_state is not None:
                client.offer(None, self._class_state)
            while True:
                getter = asyncio.create_task(client.queue.get())
                done, _ = await asyncio.wait({getter, disconnected}, return_when=asyncio.FIRST_COMPLETED)
                if disconnected in done:
                    getter.cancel()
                    return
                await ws.send_text(json.dumps(getter.result(), ensure_ascii=False))
        finally:
            disconnected.cancel()
            self._clients.discard(client)


async def _wait_disconnect(ws: WebSocket) -> None:
    """Consume client frames until disconnect (clients are not expected to send anything)."""
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return


# ---------------------------------------------------------------------------
# Security: loopback Host, Origin allow-list, bearer token, body size
# ---------------------------------------------------------------------------

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
WS_SUBPROTOCOL = "qorgau.v1"
WS_TOKEN_PREFIX = "qorgau.bearer."


# One table for every ErrorCode (CONTRACTS.md §1). Authoritative over ProctorError.http_status (QA-BUG-003).
HTTP_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN_ORIGIN: 403,
    ErrorCode.INVALID_ARGUMENT: 422,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.SESSION_NOT_FOUND: 404,
    ErrorCode.SESSION_ACTIVE: 409,
    ErrorCode.SESSION_MISMATCH: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.PREFLIGHT_FAILED: 409,
    ErrorCode.CALIBRATION_FAILED: 409,
    ErrorCode.CAMERA_UNAVAILABLE: 503,
    ErrorCode.CAMERA_BUSY: 503,
    ErrorCode.CAMERA_DENIED: 503,
    ErrorCode.REPLAY_INVALID: 422,
    ErrorCode.MODEL_MISSING: 503,
    ErrorCode.MODEL_INVALID: 503,
    ErrorCode.MODULE_NOT_INTEGRATED: 503,
    ErrorCode.STORAGE_ERROR: 503,
    ErrorCode.NOT_IMPLEMENTED: 501,
    ErrorCode.INTERNAL: 500,
}
assert set(HTTP_STATUS_BY_CODE) == set(ErrorCode), "every ErrorCode needs an HTTP status"
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _clip(value: Any, limit: int) -> Any:
    return value[:limit] + "…" if isinstance(value, str) and len(value) > limit else value


def _api_error(code: ErrorCode, message: str, status: int | None = None, retryable: bool = False, **details: Any) -> JSONResponse:
    """Never fails: oversized messages/details are clipped so the error handler cannot raise (QA-BUG-002)."""
    body = ApiError(
        error=ApiErrorBody(
            code=code,
            message=_clip(str(message), 900),
            retryable=retryable,
            details={str(k)[:64]: _clip(v, 200) for k, v in details.items() if isinstance(v, (bool, int, float, str))},
        )
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status or HTTP_STATUS_BY_CODE[code])


def validate_path_ids(request: HTTPConnection) -> None:
    """Router dependency: every path parameter must be a contract Id (422 instead of 500/connection reset)."""
    for name, value in request.path_params.items():
        if not isinstance(value, str) or not ID_RE.match(value):
            raise ProctorError(
                ErrorCode.INVALID_ARGUMENT,
                f"path parameter {name!r} is not a valid id",
                parameter=name,
                length=len(value) if isinstance(value, str) else 0,
            )


class SecurityMiddleware:
    def __init__(self, app: Any, token: str, allow_origin: str, max_body: int):
        if len(token) < 32:
            raise ValueError("API token must be at least 32 characters")
        self.app = app
        self._token = token.encode()
        self._allow_origin = allow_origin
        self._max_body = max_body

    def _token_ok(self, candidate: str | None) -> bool:
        return candidate is not None and hmac.compare_digest(candidate.encode(), self._token)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "").rsplit(":", 1)[0].strip("[]")
        origin = headers.get("origin")
        problem: tuple[ErrorCode, str, int] | None = None
        if host not in ALLOWED_HOSTS:
            problem = (ErrorCode.FORBIDDEN_ORIGIN, "Host header must be a loopback name", 403)
        elif origin is not None and (not self._allow_origin or origin != self._allow_origin):
            problem = (ErrorCode.FORBIDDEN_ORIGIN, "Origin not allowed", 403)
        elif scope["type"] == "http" and scope.get("method") == "OPTIONS" and origin is not None:
            return await self.app(scope, receive, send)  # CORS preflight for the dev origin
        else:
            auth = headers.get("authorization", "")
            bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else None
            if scope["type"] == "websocket" and bearer is None:
                for proto in scope.get("subprotocols", []):
                    if proto.startswith(WS_TOKEN_PREFIX):
                        bearer = proto[len(WS_TOKEN_PREFIX) :]
            if not self._token_ok(bearer):
                problem = (ErrorCode.UNAUTHORIZED, "Missing or invalid bearer token", 401)
            elif scope["type"] == "http":
                length = headers.get("content-length")
                if "chunked" in headers.get("transfer-encoding", "").lower():
                    problem = (ErrorCode.PAYLOAD_TOO_LARGE, "Chunked request bodies are not accepted", 411)
                elif length is not None and (not length.isdigit() or int(length) > self._max_body):
                    problem = (ErrorCode.PAYLOAD_TOO_LARGE, f"Body larger than {self._max_body} bytes", 413)
        if problem is None:
            return await self.app(scope, receive, send)
        code, message, status = problem
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 4401 if status == 401 else 4403, "reason": code.value})
            return
        response = _api_error(code, message, status)
        await response(scope, receive, send)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def load_exam(settings: Settings) -> ExamDefinition:
    path = settings.exam_path if settings.exam_path.is_file() else FALLBACK_EXAM
    exam = ExamDefinition.model_validate_json(path.read_text(encoding="utf-8"))
    if path == FALLBACK_EXAM:
        log.warning("exam file %s not found; using contract fixture exam (demo)", settings.exam_path)
    return exam


def create_app(
    settings: Settings,
    token: str,
    *,
    module_overrides: dict[str, Callable[..., Any]] | None = None,
    on_ready: Callable[[], None] | None = None,
) -> FastAPI:
    if len(token) < 32:
        raise ValueError("API token must be at least 32 characters")
    registry = ModuleRegistry(settings, module_overrides)
    hub = StreamHub()
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.bind_loop(asyncio.get_running_loop())
        await asyncio.to_thread(registry.load)
        state["manager"] = SessionManager(settings, registry, hub, load_exam(settings))
        store = registry.router_store()
        state["manager"].health_reporter = health
        n_before = len(app.router.routes)
        app.include_router(
            store.create_router(_Context(state["manager"], registry)),
            prefix="/v1",
            dependencies=[Depends(validate_path_ids)],
        )
        # Store routes are added at startup, after the API router. Move them first so static
        # paths such as /v1/sessions/overview are not captured by /v1/sessions/{session_id}.
        store_routes = app.router.routes[n_before:]
        del app.router.routes[n_before:]
        app.router.routes[0:0] = store_routes
        state["calibration_task"] = asyncio.create_task(_calibration_progress(state, hub))
        state["uplink"] = start_uplink(settings, lambda: state.get("manager"), hub)  # C2; None when not configured
        log.info("backend ready: %s", {h.component.value: h.code for h in registry.health_components()})
        if on_ready is not None:
            on_ready()
        try:
            yield
        finally:
            state["calibration_task"].cancel()
            if state.get("uplink") is not None:  # C2
                await asyncio.to_thread(state["uplink"].stop)
            await asyncio.to_thread(state["manager"].shutdown)
            await asyncio.to_thread(registry.close)

    app = FastAPI(
        title="Qorgau Exam local API",
        version=BACKEND_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/v1/openapi.json",
    )
    if settings.dev_allow_origin:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(SecurityMiddleware, token=token, allow_origin=settings.dev_allow_origin, max_body=settings.max_body_bytes)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[settings.dev_allow_origin],
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )
    else:
        app.add_middleware(SecurityMiddleware, token=token, allow_origin="", max_body=settings.max_body_bytes)

    @app.exception_handler(ProctorError)
    async def proctor_error(_: Request, exc: ProctorError) -> JSONResponse:
        return _api_error(exc.code, exc.message, None, exc.retryable, **exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        return _api_error(
            ErrorCode.INVALID_ARGUMENT,
            "Request does not match contract qorgau.v1",
            422,
            location=".".join(str(p) for p in first.get("loc", [])),
            problem=str(first.get("msg", ""))[:200],
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.INVALID_ARGUMENT
        return _api_error(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def internal_error(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _api_error(ErrorCode.INTERNAL, "Internal error (see backend log)", 500)

    def manager() -> SessionManager:
        return state["manager"]

    api = APIRouter(prefix="/v1", dependencies=[Depends(validate_path_ids)])

    @api.get("/health", response_model=HealthReport)
    def health() -> HealthReport:
        components = registry.health_components()
        mgr = state.get("manager")
        rt = mgr.active_runtime() if mgr else None
        if rt is not None:  # QA-BUG-005: pipeline errors of the running session are not "ok"
            faults = {h.component: h for h in rt.faults.health()}
            components = [faults.pop(c.component, c) if c.status == HealthStatus.OK else c for c in components]
            components += list(faults.values())
        statuses = {c.status for c in components}
        overall = (
            HealthStatus.ERROR
            if HealthStatus.ERROR in statuses
            else HealthStatus.DEGRADED
            if statuses & {HealthStatus.DEGRADED, HealthStatus.UNAVAILABLE}
            else HealthStatus.OK
        )
        return HealthReport(
            backend_version=BACKEND_VERSION,
            overall=overall,
            components=components,
            active_session_id=mgr.active_session_id() if mgr else None,
            server_time=utc_now(),
        )

    @api.post("/sessions", response_model=SessionInfo, status_code=201)
    def create_session(body: SessionCreate, mgr: SessionManager = Depends(manager)) -> SessionInfo:
        return mgr.create(body)

    @api.get("/sessions/{session_id}", response_model=SessionInfo)
    def get_session(session_id: str, mgr: SessionManager = Depends(manager)) -> SessionInfo:
        return mgr.runtime(session_id).info

    @api.post("/sessions/{session_id}/preflight", response_model=PreflightReport)
    def preflight(session_id: str, mgr: SessionManager = Depends(manager)) -> PreflightReport:
        return mgr.runtime(session_id).preflight()

    @api.post("/sessions/{session_id}/calibration/start", response_model=CalibrationState)
    def calibration_start(session_id: str, mgr: SessionManager = Depends(manager)) -> CalibrationState:
        return mgr.runtime(session_id).calibration_start()

    @api.post("/sessions/{session_id}/calibration/target", response_model=CalibrationState)
    def calibration_target(session_id: str, body: CalibrationTargetRequest, mgr: SessionManager = Depends(manager)) -> CalibrationState:
        return mgr.runtime(session_id).calibration_target(body.target)

    @api.get("/sessions/{session_id}/calibration", response_model=CalibrationState)
    def calibration_state(session_id: str, mgr: SessionManager = Depends(manager)) -> CalibrationState:
        return mgr.runtime(session_id).calibration_state()

    @api.post("/sessions/{session_id}/calibration/finish", response_model=CalibrationState)
    def calibration_finish(session_id: str, mgr: SessionManager = Depends(manager)) -> CalibrationState:
        return mgr.runtime(session_id).calibration_finish()

    @api.post("/sessions/{session_id}/calibration/cancel", response_model=CalibrationState)
    def calibration_cancel(session_id: str, mgr: SessionManager = Depends(manager)) -> CalibrationState:
        return mgr.runtime(session_id).calibration_cancel()

    @api.post("/sessions/{session_id}/calibration/skip", response_model=CalibrationState)
    def calibration_skip(session_id: str, body: CalibrationSkipRequest, mgr: SessionManager = Depends(manager)) -> CalibrationState:
        return mgr.runtime(session_id).calibration_skip(body.reason)

    @api.post("/sessions/{session_id}/start", response_model=SessionInfo)
    def start(session_id: str, mgr: SessionManager = Depends(manager)) -> SessionInfo:
        return mgr.runtime(session_id).start()

    @api.post("/sessions/{session_id}/pause", response_model=SessionInfo)
    def pause(session_id: str, body: PauseRequest, mgr: SessionManager = Depends(manager)) -> SessionInfo:
        return mgr.runtime(session_id).pause(body)

    @api.post("/sessions/{session_id}/resume", response_model=SessionInfo)
    def resume(session_id: str, mgr: SessionManager = Depends(manager)) -> SessionInfo:
        return mgr.runtime(session_id).resume()

    @api.post("/sessions/{session_id}/finish", response_model=SessionInfo)
    def finish(session_id: str, mgr: SessionManager = Depends(manager)) -> SessionInfo:
        return mgr.runtime(session_id).finish()

    @api.post("/sessions/{session_id}/abort", response_model=SessionInfo)
    def abort(session_id: str, body: AbortRequest, mgr: SessionManager = Depends(manager)) -> SessionInfo:
        return mgr.runtime(session_id).abort(body)

    @api.get("/sessions/{session_id}/exam", response_model=ExamDefinition)
    def exam(session_id: str, mgr: SessionManager = Depends(manager)) -> ExamDefinition:
        mgr.runtime(session_id)
        return mgr.exam

    @api.get("/sessions/{session_id}/metrics", response_model=RuntimeMetrics)
    def metrics(session_id: str, mgr: SessionManager = Depends(manager)) -> RuntimeMetrics:
        return mgr.runtime(session_id).metrics()

    @api.get("/sessions/{session_id}/preview.jpg")
    def preview_jpg(session_id: str, mgr: SessionManager = Depends(manager)) -> Response:
        latest = mgr.runtime(session_id).preview()
        if latest is None:
            return Response(status_code=204)
        meta, data = latest
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={"X-Qorgau-Preview-Meta": meta.model_dump_json(), "Cache-Control": "no-store"},
        )

    @api.put("/environment/capabilities", response_model=EnvironmentCapabilities)
    def put_capabilities(body: EnvironmentCapabilities) -> EnvironmentCapabilities:
        registry.set_environment_capabilities(body)
        hub.publish(HealthMsg(report=health()), None)
        return body

    @api.get("/environment/capabilities", response_model=EnvironmentCapabilities | None)
    def get_capabilities() -> EnvironmentCapabilities | None:
        return registry.environment_capabilities()

    @api.post("/sessions/{session_id}/environment/events", response_model=EnvironmentEventAck)
    def environment_events(session_id: str, body: EnvironmentEventBatch, mgr: SessionManager = Depends(manager)) -> EnvironmentEventAck:
        return mgr.runtime(session_id).environment_events(body)

    @api.websocket("/stream")
    async def stream(ws: WebSocket, session_id: str | None = None) -> None:
        await ws.accept(subprotocol=WS_SUBPROTOCOL if WS_SUBPROTOCOL in ws.scope.get("subprotocols", []) else None)
        try:
            await hub.serve(ws, session_id)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @api.websocket("/preview")
    async def preview_ws(ws: WebSocket, session_id: str | None = None) -> None:
        """Binary frames: uint32 big-endian header length | PreviewFrameMeta JSON (UTF-8) | JPEG bytes."""
        await ws.accept(subprotocol=WS_SUBPROTOCOL if WS_SUBPROTOCOL in ws.scope.get("subprotocols", []) else None)
        interval = 1.0 / max(settings.preview_fps, 1.0)
        last: tuple[str, int] | None = None
        disconnected = asyncio.create_task(_wait_disconnect(ws))
        try:
            while not disconnected.done():
                rt = state["manager"].active_runtime()
                latest = rt.preview() if rt is not None and (session_id is None or rt.session_id == session_id) else None
                if latest is not None and (latest[0].session_id, latest[0].frame_id) != last:
                    meta, data = latest
                    header = meta.model_dump_json().encode("utf-8")
                    await ws.send_bytes(struct.pack(">I", len(header)) + header + data)
                    last = (meta.session_id, meta.frame_id)
                await asyncio.wait({disconnected}, timeout=interval)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            disconnected.cancel()

    app.include_router(api)
    app.state.registry = registry
    app.state.hub = hub
    app.state.proctor = state
    return app


class _Context:
    """BackendContext handed to the A08 router."""

    def __init__(self, manager: SessionManager, registry: ModuleRegistry):
        self._manager = manager
        self._registry = registry

    def get_session(self, session_id: str) -> SessionInfo | None:
        return self._manager.get(session_id)

    def active_session_id(self) -> str | None:
        return self._manager.active_session_id()

    def capture(self):
        part = self._registry.loaded.get("capture")
        return part.impl if part is not None else None

    def forget_session(self, session_id: str) -> None:
        """Called by the A08 router after a successful DELETE: drop the terminal in-memory runtime."""
        self._manager.forget(session_id)


async def _calibration_progress(state: dict[str, Any], hub: StreamHub) -> None:
    """While a session is calibrating, push CalibrationMsg when A04's state changes (A04 R2): no UI polling needed."""
    from proctor_contracts.v1 import CalibrationMsg, SessionState

    last: tuple[str, Any] | None = None
    while True:
        await asyncio.sleep(0.25)
        try:
            rt = state["manager"].active_runtime()
            if rt is None or rt.state != SessionState.CALIBRATING:
                continue
            cal = await asyncio.to_thread(rt.calibration_state)
            key = (rt.session_id, cal.updated_at)
            if key != last:
                last = key
                hub.publish(CalibrationMsg(session_id=rt.session_id, calibration=cal), rt.session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("calibration progress publisher failed")
