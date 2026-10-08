"""Security review of the loopback API (CONTRACTS.md §3): bearer token, Host, Origin/CORS,
body limits, token leakage, error hygiene. Real process, raw sockets/httpx only.

Scope: only this local product. These checks do not cover the Electron shell (A06) — see
qa/scenarios/WINDOWS.md for IPC/navigation/XSS checks that need the desktop build.
"""

from __future__ import annotations

import socket

import httpx
import pytest
from websockets.exceptions import InvalidStatus, ConnectionClosed
from websockets.sync.client import connect

from qorgau_qa import contract
from qorgau_qa.backend import session_create_body


def _raw_request(port: int, request: bytes, timeout: float = 5.0) -> bytes:
    """Send bytes exactly as given (httpx normalises headers; this does not)."""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall(request)
        chunks = []
        while True:
            try:
                data = s.recv(65536)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


# ------------------------------------------------------------------ authentication


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Token {token}"},
        {"Authorization": "Bearer {token}x"},
        {"Authorization": "Bearer {token_prefix}"},
        {"Authorization": "Bearer {token_upper}"},
        {"X-Api-Key": "{token}"},
        {"Cookie": "token={token}"},
    ],
    ids=["none", "empty", "bearer-only", "basic", "token-scheme", "token+suffix", "token-prefix", "token-uppercase", "x-api-key", "cookie"],
)
def test_every_route_family_rejects_bad_credentials(backend, headers):
    t = backend.token
    hdrs = {k: v.format(token=t, token_prefix=t[:-1], token_upper=t.upper()) for k, v in headers.items()}
    with backend.raw() as c:
        for method, path in [("GET", "/health"), ("POST", "/sessions"), ("GET", "/sessions"), ("GET", "/sessions/x/incidents"), ("PUT", "/environment/capabilities"), ("GET", "/openapi.json")]:
            r = c.request(method, path, headers=hdrs, json={} if method in ("POST", "PUT") else None)
            contract.api_error(r, 401, "UNAUTHORIZED")


def test_token_in_query_string_is_not_accepted(backend):
    with backend.raw() as c:
        for q in (f"token={backend.token}", f"access_token={backend.token}", f"bearer={backend.token}"):
            contract.api_error(c.get(f"/health?{q}"), 401, "UNAUTHORIZED")


@pytest.mark.parametrize("value", ["Bearer ", "Bearer\t", "Bearer {token} extra", "Bearer\x00{token}"])
def test_raw_malformed_authorization_values_rejected(backend, value):
    value = value.format(token=backend.token)
    req = f"GET /v1/health HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: {value}\r\nConnection: close\r\n\r\n".encode()
    resp = _raw_request(backend.port, req)
    assert resp.split(b" ", 2)[1] in (b"400", b"401"), resp[:200]


def test_lowercase_bearer_scheme_is_accepted(backend):
    """RFC 7235: the auth scheme is case-insensitive."""
    with backend.raw() as c:
        assert c.get("/health", headers={"Authorization": f"bearer {backend.token}"}).status_code == 200


def test_unknown_route_does_not_bypass_auth_or_reveal_routes(backend):
    with backend.raw() as c:
        contract.api_error(c.get("/nope"), 401)
        contract.api_error(c.get("/docs"), 401)
    contract.api_error(backend.http.get("/docs"), 404, "NOT_FOUND")  # docs UI disabled
    contract.api_error(backend.http.get("/redoc"), 404, "NOT_FOUND")


# ------------------------------------------------------------------ Host (DNS rebinding)


@pytest.mark.parametrize(
    "host",
    ["evil.example", "127.0.0.1.evil.example", "localhost.evil.example", "0.0.0.0", "[::1]", "10.0.0.5", "127.0.0.2", ""],
)
def test_non_loopback_host_header_rejected(backend, host):
    req = f"GET /v1/health HTTP/1.1\r\nHost: {host}\r\nAuthorization: Bearer {backend.token}\r\nConnection: close\r\n\r\n".encode()
    resp = _raw_request(backend.port, req)
    status = resp.split(b" ", 2)[1]
    assert status in (b"403", b"400"), resp[:200]


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "localhost", pytest.param("LOCALHOST", marks=pytest.mark.xfail(strict=True, reason="QA-OBS-006 (A01, cosmetic): Host comparison is case-sensitive; browsers send lowercase, so no impact"))],
)
def test_loopback_host_header_accepted(backend, host):
    req = f"GET /v1/health HTTP/1.1\r\nHost: {host}:{backend.port}\r\nAuthorization: Bearer {backend.token}\r\nConnection: close\r\n\r\n".encode()
    resp = _raw_request(backend.port, req)
    assert resp.split(b" ", 2)[1] == b"200", resp[:200]


def test_listens_on_loopback_only(backend):
    """The READY port must not be reachable on a non-loopback interface address of this host."""
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    external = [a for a in addrs if not a.startswith("127.")]
    if not external:
        pytest.skip("no non-loopback IPv4 address on this host to probe")
    for addr in external:
        with pytest.raises(OSError):
            socket.create_connection((addr, backend.port), timeout=2).close()


# ------------------------------------------------------------------ Origin / CORS


@pytest.mark.parametrize("origin", ["http://evil.example", "null", "file://", "http://127.0.0.1:5173", "http://localhost", "app://qorgau", "chrome-extension://abc"])
def test_requests_with_origin_rejected_without_dev_allowlist(backend, origin):
    r = backend.http.get("/health", headers={"Origin": origin})
    contract.api_error(r, 403, "FORBIDDEN_ORIGIN")
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_cors_preflight_from_foreign_origin_gets_no_allow_headers(backend):
    with backend.raw() as c:
        r = c.options("/sessions", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 403
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_simple_cross_site_post_cannot_create_session(backend):
    """A web page can send a 'simple' text/plain POST without preflight; it must fail (no token, Origin)."""
    with backend.raw() as c:
        r = c.post("/sessions", content=b'{"x":1}', headers={"Content-Type": "text/plain", "Origin": "http://evil.example"})
    assert r.status_code in (401, 403)


def test_dev_origin_allowlist_exact_match_only(fresh_backend):
    be = fresh_backend(env={"QORGAU_DEV_ALLOW_ORIGIN": "http://127.0.0.1:5173"})
    try:
        ok = be.http.get("/health", headers={"Origin": "http://127.0.0.1:5173"})
        assert ok.status_code == 200 and ok.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
        for bad in ("http://127.0.0.1:5174", "http://127.0.0.1:5173.evil.example", "https://127.0.0.1:5173", "http://localhost:5173"):
            contract.api_error(be.http.get("/health", headers={"Origin": bad}), 403, "FORBIDDEN_ORIGIN")
        # the allow-listed origin still needs the token
        with be.raw() as c:
            contract.api_error(c.get("/health", headers={"Origin": "http://127.0.0.1:5173"}), 401, "UNAUTHORIZED")
    finally:
        assert be.stop() == 0


# ------------------------------------------------------------------ body limits


def test_body_over_limit_rejected_before_parsing(backend):
    r = backend.http.post("/sessions", content=b"{" + b" " * 1_000_001 + b"}", headers={"Content-Type": "application/json"})
    contract.api_error(r, 413, "PAYLOAD_TOO_LARGE")


def test_body_exactly_at_limit_is_parsed(backend):
    body = b'{"source":{"mode":"synthetic"}' + b" " * (1_000_000 - 31) + b"}"
    assert len(body) == 1_000_000
    r = backend.http.post("/sessions", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 422  # reached validation (missing fields), i.e. not rejected by size


def test_chunked_body_rejected(backend):
    def gen():
        yield b'{"source":'
        yield b'{"mode":"synthetic"}}'

    r = backend.http.post("/sessions", content=gen(), headers={"Content-Type": "application/json"})
    contract.api_error(r, 411, "PAYLOAD_TOO_LARGE")


@pytest.mark.parametrize("length", ["-1", "abc", "1e3", "99999999999999999999"])
def test_malformed_content_length_rejected(backend, length):
    req = (
        f"POST /v1/sessions HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {backend.token}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {length}\r\nConnection: close\r\n\r\n{{}}"
    ).encode()
    resp = _raw_request(backend.port, req)
    status = resp.split(b" ", 2)[1]
    assert status in (b"400", b"411", b"413"), resp[:200]


# ------------------------------------------------------------------ WebSocket auth


def _ws_open(url: str, **kwargs):
    ws = connect(url, open_timeout=5, **kwargs)
    return ws.__enter__()


def _assert_ws_rejected(url: str, close_code: int, **kwargs) -> str:
    """Rejected either at the HTTP handshake (uvicorn turns a pre-accept close into HTTP 403)
    or right after accept with the 44xx close code. Never a delivered message."""
    try:
        ws = _ws_open(url, **kwargs)
    except InvalidStatus as exc:
        assert exc.response.status_code in (401, 403), exc.response.status_code
        return f"handshake HTTP {exc.response.status_code}"
    with pytest.raises(ConnectionClosed) as closed:
        ws.recv(timeout=5)
    assert closed.value.rcvd is not None and closed.value.rcvd.code == close_code, closed.value
    return f"close {close_code}"


@pytest.mark.parametrize("path", ["/stream", "/preview"])
def test_websocket_requires_token(backend, path):
    _assert_ws_rejected(backend.ws_url(path), 4401)


@pytest.mark.parametrize("path", ["/stream", "/preview"])
def test_websocket_token_in_query_rejected(fresh_backend, path):
    """Own process: the misuse below must not contaminate the shared backend's log scan."""
    be = fresh_backend()
    _assert_ws_rejected(be.ws_url(path, f"token={be.token}"), 4401)
    be.stop()


@pytest.mark.xfail(strict=True, reason="QA-BUG-001 (A01, low): uvicorn.error logs the full WS URL incl. query, so a token a client wrongly puts in ?token= is written to backend stderr")
def test_token_misplaced_in_ws_query_is_not_logged(fresh_backend):
    be = fresh_backend()
    _assert_ws_rejected(be.ws_url("/stream", f"token={be.token}"), 4401)
    be.stop()
    assert not be.token_leaks(), be.token_leaks()


def test_websocket_foreign_origin_rejected(backend):
    _assert_ws_rejected(backend.ws_url("/stream"), 4403, additional_headers={**backend.auth, "Origin": "http://evil.example"})


def test_websocket_wrong_subprotocol_token_rejected(backend):
    _assert_ws_rejected(backend.ws_url("/stream"), 4401, subprotocols=["qorgau.v1", "qorgau.bearer." + "0" * 64])


def test_websocket_subprotocol_token_accepted_for_browser_dev(backend):
    ws = _ws_open(backend.ws_url("/stream"), subprotocols=["qorgau.v1", f"qorgau.bearer.{backend.token}"])
    try:
        import json

        hello = json.loads(ws.recv(timeout=5))
        assert hello["message"]["type"] == "hello"
        assert ws.subprotocol == "qorgau.v1", "server must not echo the token-carrying subprotocol"
    finally:
        ws.close()


# ------------------------------------------------------------------ leakage / hygiene


def test_token_never_in_responses_or_logs(backend, api):
    sid = api.running_session()
    bodies = [backend.http.get(p).text for p in ("/health", f"/sessions/{sid}", "/sessions", "/openapi.json", f"/sessions/{sid}/metrics")]
    r = backend.http.get(f"/sessions/{sid}/preview.jpg")
    bodies.append(r.headers.get("X-Qorgau-Preview-Meta", ""))
    assert not any(backend.token in b for b in bodies)
    backend.http.post(f"/sessions/{sid}/finish")
    assert not backend.token_leaks(), backend.token_leaks()


def test_errors_have_no_stack_traces_or_paths(backend, api):
    probes = [
        backend.http.get("/sessions/..%2F..%2Fetc%2Fpasswd"),
        backend.http.post("/sessions", content=b"{not json", headers={"Content-Type": "application/json"}),
        backend.http.post("/sessions", json=session_create_body(fps=0)),
        backend.http.get("/sessions/" + "A" * 5000),
        backend.http.post("/sessions/x/start"),
    ]
    for r in probes:
        assert r.status_code >= 400
        contract.api_error(r, r.status_code)


def test_responses_are_not_cacheable_for_preview(backend, api):
    sid = api.running_session()
    from qorgau_qa.backend import wait_for

    assert wait_for(lambda: backend.http.get(f"/sessions/{sid}/preview.jpg").status_code == 200, 5)
    r = backend.http.get(f"/sessions/{sid}/preview.jpg")
    assert "no-store" in r.headers.get("cache-control", "")
    backend.http.post(f"/sessions/{sid}/finish")


def test_openapi_requires_auth_and_lists_only_v1(backend):
    spec = backend.http.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    assert all(p.startswith("/v1/") for p in paths), [p for p in paths if not p.startswith("/v1/")]
    for forbidden in ("shell", "exec", "file", "fs", "eval", "debug"):
        assert not any(f"/{forbidden}" in p for p in paths), forbidden


def test_http_client_cannot_switch_to_absolute_form_proxy_request(backend):
    """Absolute-form request lines (proxy style) must not reach another host or bypass Host checks."""
    req = f"GET http://evil.example/v1/health HTTP/1.1\r\nHost: evil.example\r\nAuthorization: Bearer {backend.token}\r\nConnection: close\r\n\r\n".encode()
    resp = _raw_request(backend.port, req)
    assert resp.split(b" ", 2)[1] in (b"400", b"403"), resp[:200]


def test_httpx_default_client_without_token_cannot_read_history(backend):
    with httpx.Client(timeout=5) as c:
        r = c.get(f"{backend.base}/sessions")
    contract.api_error(r, 401)
