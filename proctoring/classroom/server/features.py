"""Feature plug-ins of the class server (owner: T01). How T03/T04/T05 (and later others) attach server code.

A feature is loaded from QORGAU_CLASS_FEATURES="pkg.module:factory,..." and built with factory(ctx) -> Feature.
Everything a feature may use is on FeatureContext; everything it may provide is an optional attribute/method:

    name: str                                  # id, e.g. "history"
    owner: str                                 # "T03" | "T04" | "T05"
    migrations() -> list[Migration]            # own tables, prefixed "<owner>_" (e.g. t03_reviews)
    router -> fastapi.APIRouter | None         # routes MUST start with a prefix reserved for the owner below
    on_student_connected(student: Student, hello: dict) -> dict | None   # may return a welcome.exam override
    on_student_message(student_id: str, message: dict) -> None           # every validated student message
    on_student_disconnected(student_id: str) -> None
    on_command_update(command: Command) -> None
    incidents_unreviewed(student_id: str) -> int | None                 # T03: fills StudentCard.incidents_unreviewed
    tick() -> None                             # ~every 0.5 s on the server loop; must not block
    close() -> None

Authentication is NOT the feature's job: the server's gate rejects every /api/teacher/* request that is not
a logged-in teacher on this computer and every /api/student/* request without a valid student token before a
feature route runs. A feature reads the caller with ctx.teacher(request) / ctx.student(request).
Exceptions in hooks are caught, logged and shown as feature status "degraded" in /api/teacher/info.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.routing import APIRoute, APIWebSocketRoute

from ..contracts.models import Command, CommandKind, FeatureInfo, Student
from .db import Database, Migration

log = logging.getLogger("classroom.features")

# Route prefixes reserved per owner. A feature's router may only add routes under its owner's prefixes.
ROUTE_RESERVATIONS: dict[str, tuple[str, ...]] = {
    "T01": ("/api/teacher/info", "/api/teacher/login", "/api/teacher/logout", "/api/teacher/session", "/api/teacher/students",
            "/api/teacher/commands", "/api/teacher/events", "/ws/teacher", "/ws/student", "/api/student/ping"),
    "T03": ("/api/teacher/history/", "/api/teacher/clips/", "/api/student/clips/"),
    "T04": ("/api/teacher/control/",),
    "T05": ("/api/teacher/audio/",),
}
# v1 §5 paths that belong to T03 although they sit under the T01 /api/teacher/students tree:
V1_DELEGATED: dict[str, tuple[str, ...]] = {
    "T03": ("/api/teacher/students/{student_id}/incidents", "/api/teacher/students/{student_id}/decision"),
}


class FeatureError(RuntimeError):
    pass


@dataclass
class FeatureContext:
    data_dir: Path
    db: Database
    publish: Callable[[str, dict[str, Any]], None]
    submit_command: Callable[..., Command]
    send_raw_to_student: Callable[[str, dict[str, Any]], bool]
    students: Callable[[], list[Student]]
    student: Callable[[str], Student | None]
    current_session_id: Callable[[], str | None]
    teacher: Callable[[Request], Any]
    student_from_request: Callable[[Request], Student | None]
    student_by_token: Callable[[str | None], Student | None] = lambda token: None  # raw resume_token -> its student (T03 StudentResolver)
    config: Any = None
    command_kinds: tuple[str, ...] = tuple(k.value for k in CommandKind)

    def feature_dir(self, name: str) -> Path:
        path = self.data_dir / "features" / name
        path.mkdir(parents=True, exist_ok=True)
        return path


@dataclass
class _Loaded:
    spec: str
    feature: Any = None
    info: FeatureInfo | None = None
    errors: int = 0
    last_error: str = ""


@dataclass
class FeatureManager:
    loaded: list[_Loaded] = field(default_factory=list)

    @classmethod
    def load(cls, specs: str, ctx: FeatureContext, extra: list[Any] | None = None) -> "FeatureManager":
        mgr = cls()
        for spec in [s.strip() for s in specs.split(",") if s.strip()]:
            item = _Loaded(spec)
            try:
                module_name, _, factory_name = spec.partition(":")
                module = importlib.import_module(module_name)
                factory = getattr(module, factory_name or "create_classroom_feature")
                item.feature = factory(ctx)
                item.info = FeatureInfo(name=_name(item.feature), owner=_owner(item.feature), status="mounted")
            except ModuleNotFoundError as exc:
                item.info = FeatureInfo(name=_safe(spec), owner="?", status="not_installed", detail=f"{exc.name} is not installed in this build")
            except Exception as exc:  # a broken feature must not take the server down
                log.exception("feature %s failed to load", spec)
                item.info = FeatureInfo(name=_safe(spec), owner="?", status="failed", detail=f"{type(exc).__name__}: {exc}"[:300])
            mgr.loaded.append(item)
        for feature in extra or []:  # in-process features (tests, embedded)
            mgr.loaded.append(_Loaded(f"<inline:{_name(feature)}>", feature, FeatureInfo(name=_name(feature), owner=_owner(feature), status="mounted")))
        return mgr

    @property
    def active(self) -> list[Any]:
        return [i.feature for i in self.loaded if i.feature is not None and i.info is not None and i.info.status == "mounted"]

    def migrations(self) -> list[Migration]:
        out: list[Migration] = []
        for item in self.loaded:
            fn = getattr(item.feature, "migrations", None)
            if item.feature is None or not callable(fn):
                continue
            try:
                migs = list(fn())
                for mig in migs:
                    if mig.owner != _owner(item.feature):
                        raise FeatureError(f"migration {mig.id} has owner {mig.owner}, feature owner is {_owner(item.feature)}")
                out.extend(migs)
            except Exception as exc:
                self._fail(item, exc)
        return out

    def mount(self, app: FastAPI, taken: set[tuple[str, str]]) -> None:
        for item in self.loaded:
            router = getattr(item.feature, "router", None) if item.feature is not None else None
            if router is None:
                continue
            owner = _owner(item.feature)
            try:
                check_routes(owner, router.routes, taken)
                app.include_router(router)
            except Exception as exc:
                self._fail(item, exc)

    def infos(self) -> list[FeatureInfo]:
        return [i.info for i in self.loaded if i.info is not None]

    # ------------------------------------------------------------------------------------- hook calls
    def _call(self, hook: str, *args: Any) -> list[Any]:
        results = []
        for item in self.loaded:
            if item.feature is None or item.info is None or item.info.status not in ("mounted",):
                continue
            fn = getattr(item.feature, hook, None)
            if not callable(fn):
                continue
            try:
                results.append(fn(*args))
            except Exception as exc:
                item.errors += 1
                item.last_error = f"{hook}: {type(exc).__name__}: {exc}"[:300]
                log.exception("feature %s hook %s failed", _name(item.feature), hook)
        return results

    def student_connected(self, student: Student, hello: dict[str, Any]) -> dict[str, Any] | None:
        overrides = [r for r in self._call("on_student_connected", student, hello) if isinstance(r, dict)]
        return overrides[-1] if overrides else None

    def student_message(self, student_id: str, message: dict[str, Any]) -> None:
        self._call("on_student_message", student_id, message)

    def student_disconnected(self, student_id: str) -> None:
        self._call("on_student_disconnected", student_id)

    def command_update(self, command: Command) -> None:
        self._call("on_command_update", command)

    def incidents_unreviewed(self, student_id: str) -> int | None:
        values = [v for v in self._call("incidents_unreviewed", student_id) if isinstance(v, int)]
        return values[0] if values else None

    def tick(self) -> None:
        self._call("tick")

    def close(self) -> None:
        self._call("close")

    def status(self) -> list[FeatureInfo]:
        out = []
        for item in self.loaded:
            info = item.info
            if info is None:
                continue
            if info.status == "mounted" and item.errors:
                info = info.model_copy(update={"detail": f"{item.errors} hook error(s); last: {item.last_error}"[:300]})
            out.append(info)
        return out

    def _fail(self, item: _Loaded, exc: Exception) -> None:
        log.error("feature %s disabled: %s", item.spec, exc)
        name = _name(item.feature) if item.feature is not None else _safe(item.spec)
        item.info = FeatureInfo(name=name, owner=_owner(item.feature) if item.feature is not None else "?", status="failed", detail=str(exc)[:300])


def check_routes(owner: str, routes: list[Any], taken: set[tuple[str, str]]) -> None:
    """Every route must sit under a prefix reserved for `owner` and must not collide with an existing route."""
    allowed = ROUTE_RESERVATIONS.get(owner, ()) + V1_DELEGATED.get(owner, ())
    for route in routes:
        if not isinstance(route, (APIRoute, APIWebSocketRoute)):
            continue
        path = route.path
        if not any(under(path, p) for p in allowed):
            raise FeatureError(f"{owner} route {path} is outside its reserved prefixes {allowed}")
        methods = sorted(getattr(route, "methods", None) or {"WS"})
        for method in methods:
            key = (method, path)
            if key in taken:
                raise FeatureError(f"{owner} route {method} {path} is already registered")
            taken.add(key)


def under(path: str, prefix: str) -> bool:
    base = prefix.rstrip("/")
    return path == base or path.startswith(base + "/")


def _name(feature: Any) -> str:
    return _safe(str(getattr(feature, "name", type(feature).__name__)))


def _owner(feature: Any) -> str:
    owner = str(getattr(feature, "owner", "?"))
    return owner if owner in ROUTE_RESERVATIONS else "?"


def _safe(text: str) -> str:
    import re

    cleaned = re.sub(r"[^A-Za-z0-9._:-]", "_", text)[:128]
    return cleaned or "feature"
