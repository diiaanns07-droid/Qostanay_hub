"""SIMULATED student clients for the class server (owner: T01). TEST DATA ONLY.

    python -m classroom.simulator --server 127.0.0.1:8765 --code 123456 --students 30 --duration 120

Every simulated client speaks the real qorgau.class.v1 wire over a real WebSocket, but everything it reports
is invented and labelled as such: hello.simulated=true, app_version "qorgau-class-simulator/…",
student_label "SIM-07 (симуляция)", computer_name "SIM-PC-07", explanations and zone reasons start with
"СИМУЛЯЦИЯ", previews are drawn pictures with a "SIMULATED" watermark. The server marks these students
origin="simulated" everywhere. N simulated clients prove the server/network path for N connections — they
prove nothing about N real cameras, CV or Windows.

Behaviour per client: status every 2 s, preview every 2 s, an occasional episode (open -> close), answers
ping, acks commands (lock/unlock change `locked`, audio_start/stop change `mic_active`), optional random
disconnects (--chaos) with reconnect by resume token; incidents produced while offline are queued and sent
again after reconnect (the server deduplicates them).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import random
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

APP_VERSION = "qorgau-class-simulator/0.1"
FALLBACK_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxOQERXRTc4UG1RV19iZ2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2P/wAARCAAYACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwAooooAKKKKACiiigAooooA/9k="
)
RULES = [("phone_visible", "phone", "medium"), ("gaze_prolonged_down", "attention", "low"), ("multiple_faces", "presence", "high"), ("face_missing", "presence", "medium")]


def now() -> datetime:
    return datetime.now(timezone.utc)


def preview_jpeg(n: int, tick: int) -> bytes:
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415

        img = np.full((240, 320, 3), 60 + (n * 37) % 120, np.uint8)
        cv2.rectangle(img, (0, 0), (319, 239), (0, 165, 255), 6)
        cv2.putText(img, "SIMULATED", (60, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)
        cv2.putText(img, f"SIM-{n:02d}  t={tick}", (80, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 55])
        if ok and len(buf) <= 30 * 1024:
            return buf.tobytes()
    except Exception:
        pass
    return FALLBACK_JPEG


@dataclass
class SimStudent:
    n: int
    args: argparse.Namespace
    rng: random.Random
    token: str | None = None
    student_id: str | None = None
    run_id: str = field(default_factory=lambda: f"run-{secrets.token_hex(6)}")
    seq: int = 0
    outbox: list[dict[str, Any]] = field(default_factory=list)  # incidents/acks not yet confirmed as sent
    locked: bool = False
    mic: bool = False
    zone: str = "green"
    reasons: list[str] = field(default_factory=list)
    open_incident: dict[str, Any] | None = None
    stats: dict[str, int] = field(default_factory=lambda: {"connects": 0, "incidents": 0, "acks": 0, "previews": 0, "drops": 0})

    def env(self, type_: str, **fields: Any) -> dict[str, Any]:
        return {"type": type_, "v": 1, "msg_id": secrets.token_hex(8), "sent_at": now().isoformat(), **fields}

    async def run(self, deadline: float) -> None:
        backoff = 0.5
        while time.monotonic() < deadline:
            try:
                await self._session(deadline)
                backoff = 0.5
            except (OSError, ConnectionClosed, InvalidStatus, asyncio.TimeoutError) as exc:
                self.stats["drops"] += 1
                if self.args.verbose:
                    print(f"SIM-{self.n:02d}: connection lost ({type(exc).__name__}), retry in {backoff:.1f}s", file=sys.stderr)
            except RuntimeError as exc:  # rejected hello
                print(f"SIM-{self.n:02d}: {exc}", file=sys.stderr)
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 8.0)

    async def _session(self, deadline: float) -> None:
        async with connect(f"ws://{self.args.server}/ws/student", open_timeout=10, max_size=2**20) as ws:
            cred = {"resume_token": self.token} if self.token else {"join_code": self.args.code}
            await ws.send(json.dumps(self.env(
                "hello", protocol="qorgau.class.v1", computer_name=f"SIM-PC-{self.n:02d}", student_label=f"SIM-{self.n:02d} (симуляция)",
                app_version=APP_VERSION, simulated=True, client_run_id=self.run_id,
                capabilities={"commands": [], "command_progress": True, "command_expiry": True}, **cred,
            )))
            first = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if first.get("type") != "welcome":
                raise RuntimeError(f"hello rejected: {first.get('code')} {first.get('message_ru')}")
            self.token, self.student_id = first["resume_token"], first["student_id"]
            self.stats["connects"] += 1
            for msg in list(self.outbox):  # offline queue: re-send in seq order, the server drops duplicates
                await ws.send(json.dumps(msg))
            self.outbox.clear()
            await self._send_status(ws)
            reader = asyncio.create_task(self._reader(ws))
            try:
                tick = 0
                while time.monotonic() < deadline:
                    await asyncio.sleep(self.args.period)
                    tick += 1
                    self._evolve()
                    await self._send_status(ws)
                    if not self.args.no_previews:
                        await ws.send(json.dumps(self.env("preview", jpeg_b64=base64.b64encode(preview_jpeg(self.n, tick)).decode(), frame_wall=now().isoformat())))
                        self.stats["previews"] += 1
                    await self._maybe_incident(ws)
                    if self.args.chaos and self.rng.random() < self.args.chaos:
                        await ws.close()  # simulated network drop
                        return
                    if reader.done():
                        return
            finally:
                reader.cancel()

    async def _reader(self, ws: Any) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "ping":
                await ws.send(json.dumps(self.env("pong")))
            elif msg.get("type") == "command":
                await self._on_command(ws, msg)

    async def _on_command(self, ws: Any, msg: dict[str, Any]) -> None:
        cid, kind = msg["command_id"], msg["kind"]
        expires = msg.get("expires_at")
        await ws.send(json.dumps(self.env("command_progress", command_id=cid, state="received")))
        await asyncio.sleep(self.rng.uniform(0.05, 0.3))
        if expires and datetime.fromisoformat(expires) < now():
            ack = self.env("ack", command_id=cid, ok=False, code="expired", error_ru="СИМУЛЯЦИЯ: срок команды истёк")
        elif self.rng.random() < self.args.ack_failure_rate:
            ack = self.env("ack", command_id=cid, ok=False, code="failed", error_ru="СИМУЛЯЦИЯ: имитация сбоя выполнения")
        else:
            if kind == "lock":
                self.locked = True
            elif kind == "unlock":
                self.locked = False
            elif kind == "audio_start":
                self.mic = True
            elif kind == "audio_stop":
                self.mic = False
            ack = self.env("ack", command_id=cid, ok=True, executed_at=now().isoformat(), result={"locked": self.locked, "simulated": True})
        try:
            await ws.send(json.dumps(ack))
            self.stats["acks"] += 1
        except ConnectionClosed:
            self.outbox.append(ack)
        await self._send_status(ws)

    def _evolve(self) -> None:
        r = self.rng.random()
        if r < 0.05:
            self.zone, self.reasons = "red", ["СИМУЛЯЦИЯ: два эпизода высокого приоритета"]
        elif r < 0.15:
            self.zone, self.reasons = "yellow", ["СИМУЛЯЦИЯ: телефон виден"]
        elif r < 0.5:
            self.zone, self.reasons = "green", []

    async def _send_status(self, ws: Any) -> None:
        await ws.send(json.dumps(self.env(
            "status", exam_state="running", camera="ok", monitoring="ok", zone=self.zone, zone_reasons_ru=self.reasons[:3],
            incidents_total=self.stats["incidents"], incidents_by_priority={"low": 0, "medium": self.stats["incidents"], "high": 0},
            locked=self.locked, mic_active=self.mic,
        )))

    async def _maybe_incident(self, ws: Any) -> None:
        msg = None
        if self.open_incident is not None and self.rng.random() < 0.5:
            inc = self.open_incident
            self.seq += 1
            dur = (now() - datetime.fromisoformat(inc["t_start_wall"])).total_seconds() * 1000
            msg = self.env("incident", **{**inc, "seq": self.seq, "state": "closed", "duration_ms": max(0.0, dur), "event_id": f"{inc['incident_id']}:closed"})
            self.open_incident = None
        elif self.open_incident is None and self.rng.random() < self.args.incident_rate:
            rule, cat, prio = self.rng.choice(RULES)
            self.seq += 1
            inc = {"incident_id": f"sim-{self.n:02d}-{self.run_id[-6:]}-{self.seq}", "rule_id": rule, "category": cat, "priority": prio,
                   "t_start_wall": (now() - timedelta(seconds=1)).isoformat(), "explanation_ru": f"СИМУЛЯЦИЯ: сценарный эпизод {rule}", "clip_available": False}
            self.open_incident = inc
            msg = self.env("incident", **{**inc, "seq": self.seq, "state": "open", "duration_ms": 1000.0, "event_id": f"{inc['incident_id']}:open"})
        if msg is not None:
            self.stats["incidents"] += 1
            self.outbox.append(msg)  # kept until the socket accepted it; re-sent after reconnect otherwise
            await ws.send(json.dumps(msg))
            self.outbox.remove(msg)


async def amain(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    sims = [SimStudent(n=i + 1, args=args, rng=random.Random(rng.random())) for i in range(args.students)]
    deadline = time.monotonic() + (args.duration if args.duration > 0 else 10**9)
    await asyncio.gather(*(s.run(deadline) for s in sims))
    total = {k: sum(s.stats[k] for s in sims) for k in sims[0].stats} if sims else {}
    print(json.dumps({"simulated_students": len(sims), "connected_once": sum(1 for s in sims if s.stats["connects"]), **total}, ensure_ascii=False))
    return 0 if all(s.stats["connects"] for s in sims) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m classroom.simulator", description="SIMULATED students for the class server (test data only)")
    ap.add_argument("--server", default="127.0.0.1:8765", help="host:port of the class server")
    ap.add_argument("--code", required=True, help="6-digit join code shown in the teacher console")
    ap.add_argument("--students", type=int, default=5)
    ap.add_argument("--duration", type=float, default=60.0, help="seconds (0 = until Ctrl+C)")
    ap.add_argument("--period", type=float, default=2.0, help="seconds between status/preview (v1: 2 s)")
    ap.add_argument("--incident-rate", type=float, default=0.1, help="chance per period to open an episode")
    ap.add_argument("--ack-failure-rate", type=float, default=0.0)
    ap.add_argument("--chaos", type=float, default=0.0, help="chance per period to drop the connection")
    ap.add_argument("--no-previews", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if not (1 <= args.students <= 500):
        ap.error("--students must be 1..500")
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
