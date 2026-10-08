"""Validate wire payloads against the frozen contract twice: JSON Schema `$defs` AND Pydantic (owner: A09).

Two independent validators so that a bug in one (e.g. a schema generator drift) does not hide a
contract break. Only public contract artifacts are used: contracts/schema/v1 + proctor_contracts.v1.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import httpx
import jsonschema
from pydantic import TypeAdapter

from proctor_contracts import v1

from .backend import PROCTORING_ROOT

SCHEMA_PATH = PROCTORING_ROOT / "contracts" / "schema" / "v1" / "qorgau.v1.schema.json"
STACK_TRACE_MARKERS = ("Traceback (most recent call last)", 'File "', ".py\", line ", "site-packages")


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _validator(name: str) -> jsonschema.Draft202012Validator:
    s = schema()
    if name not in s["$defs"]:
        raise KeyError(f"{name} is not a contract type in $defs")
    return jsonschema.Draft202012Validator({"$schema": s["$schema"], "$defs": s["$defs"], "$ref": f"#/$defs/{name}"})


def models() -> dict[str, Any]:
    return {m.__name__: m for m in v1.WIRE_MODELS}


def validate(name: str, data: Any) -> Any:
    """Validate one payload as contract type `name` (e.g. "SessionInfo"); returns the parsed model."""
    _validator(name).validate(data)
    model = models().get(name)
    if model is not None:
        return model.model_validate(data)
    return TypeAdapter(getattr(v1, name)).validate_python(data)


def validate_list(name: str, data: Any) -> list[Any]:
    assert isinstance(data, list), f"expected a JSON array of {name}, got {type(data).__name__}"
    return [validate(name, item) for item in data]


def ok(resp: httpx.Response, name: str | None, status: int = 200) -> Any:
    """Assert a success status + contract body; returns the parsed model (or None for bodies without a type)."""
    assert resp.status_code == status, f"{resp.request.method} {resp.request.url.path}: {resp.status_code} {resp.text[:400]}"
    if name is None:
        return None
    return validate(name, resp.json())


def api_error(resp: httpx.Response, status: int | set[int], code: str | set[str] | None = None) -> dict[str, Any]:
    """Assert a contract ApiError: status, error.code, JSON body valid, no stack trace / internal paths."""
    statuses = {status} if isinstance(status, int) else status
    assert resp.status_code in statuses, f"{resp.request.method} {resp.request.url.path}: expected {statuses}, got {resp.status_code} {resp.text[:400]}"
    assert resp.headers.get("content-type", "").startswith("application/json"), resp.headers.get("content-type")
    body = resp.json()
    validate("ApiError", body)
    if code is not None:
        codes = {code} if isinstance(code, str) else code
        assert body["error"]["code"] in codes, body
    text = resp.text
    for marker in STACK_TRACE_MARKERS:
        assert marker not in text, f"error body leaks internals ({marker!r}): {text[:300]}"
    return body
