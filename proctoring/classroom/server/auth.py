"""Access control (owner: T01).

Two kinds of clients, never interchangeable:
* TEACHER — only from this computer (loopback address AND loopback Host header, so DNS rebinding fails), with
  an Origin equal to the server's own origin (or the configured dev origin) whenever the browser sends one,
  and an HttpOnly SameSite=Strict session cookie obtained with the one-time PIN printed at start (v1 §2.6).
* STUDENT — from the LAN, authenticated by its own resume token (32 random bytes). A student token never
  grants teacher access and only ever acts for the student it was issued to. Join code / token are never
  accepted in a URL.
Failed join/PIN attempts are rate limited per IP (v1 §2.5).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from dataclasses import dataclass

TEACHER_COOKIE = "qorgau_teacher"
LOOPBACK_HOSTNAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


def new_token() -> str:
    return secrets.token_hex(32)


def new_join_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def is_loopback_ip(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def host_header_ok(host_header: str | None) -> bool:
    if not host_header:
        return False
    host = host_header.strip().lower()
    if host.startswith("["):
        host = host[: host.find("]") + 1] if "]" in host else host
    else:
        host = host.rsplit(":", 1)[0]
    return host in LOOPBACK_HOSTNAMES


def origin_ok(origin: str | None, port: int, dev_origin: str) -> bool:
    """No Origin (curl, server-to-server, same-origin GET in some browsers) is fine: the cookie is SameSite=Strict."""
    if origin is None:
        return True
    allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    if dev_origin:
        allowed.add(dev_origin.rstrip("/"))
    return origin.rstrip("/") in allowed


class RateLimiter:
    """N failures per key -> blocked for block_s. Success does not reset a running block."""

    def __init__(self, limit: int, block_s: float, clock=time.monotonic):
        self.limit = limit
        self.block_s = block_s
        self._clock = clock
        self._lock = threading.Lock()
        self._fails: dict[str, int] = {}
        self._blocked_until: dict[str, float] = {}

    def blocked(self, key: str) -> float:
        """Seconds left in the block (0 = not blocked)."""
        with self._lock:
            until = self._blocked_until.get(key, 0.0)
            left = until - self._clock()
            if left <= 0 and key in self._blocked_until:
                del self._blocked_until[key]
                self._fails.pop(key, None)
            return max(0.0, left)

    def fail(self, key: str) -> float:
        with self._lock:
            n = self._fails.get(key, 0) + 1
            self._fails[key] = n
            if n >= self.limit:
                self._blocked_until[key] = self._clock() + self.block_s
                return self.block_s
            return 0.0

    def succeed(self, key: str) -> None:
        with self._lock:
            if key not in self._blocked_until:
                self._fails.pop(key, None)


@dataclass(frozen=True)
class TeacherPrincipal:
    teacher_id: str = "teacher"  # v1 has a single PIN teacher (T04 R4: maps to one principal)
    role: str = "teacher"


class TeacherAuth:
    def __init__(self, pin: str, limiter: RateLimiter):
        self.pin = pin or f"{secrets.randbelow(1_000_000):06d}"
        self.limiter = limiter
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}  # sha256(cookie) -> created (monotonic)

    def login(self, pin: str, key: str = "loopback") -> tuple[str | None, float]:
        """Returns (cookie value, 0) on success, (None, seconds blocked) otherwise."""
        left = self.limiter.blocked(key)
        if left:
            return None, left
        if not hmac.compare_digest(pin.encode(), self.pin.encode()):
            return None, self.limiter.fail(key)
        self.limiter.succeed(key)
        cookie = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token_hash(cookie)] = time.monotonic()
        return cookie, 0.0

    def check(self, cookie: str | None) -> TeacherPrincipal | None:
        if not cookie or len(cookie) > 200:
            return None
        with self._lock:
            ok = token_hash(cookie) in self._sessions
        return TeacherPrincipal() if ok else None

    def logout(self, cookie: str | None) -> None:
        if cookie:
            with self._lock:
                self._sessions.pop(token_hash(cookie), None)
