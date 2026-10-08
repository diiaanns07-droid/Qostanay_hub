"""Windows-capable C1 -> TWO full student backends -> C2 acceptance check.

All sources and students are SYNTHETIC. No webcam, microphone, Electron or native
enforcement is started. A successful run proves process/API/uplink integration on
loopback, not real CV, LAN capacity or exam lockdown. No test doubles are used.

python check_chain.py --python /path/to/venv/python --output /path/to/result.json
Only the selected interpreter needs the project's backend dependencies installed.
PYTHONPATH is set explicitly so an older editable install cannot select old code.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import datetime, timezone
import http.cookiejar
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request


PROCTORING = Path(__file__).resolve().parents[2]
CHECKS = (
    "source_checkout", "three_processes_ready", "two_distinct_students",
    "synthetic_sessions_preflight", "targeted_start_isolation", "student_a_incident_and_preview",
    "student_b_incident_and_preview", "targeted_finish_isolation", "incident_identity_and_counts",
    "teacher_stream", "synthetic_provenance", "server_crash_resume",
    "durable_events_and_commands", "student_restart_identity", "cleanup",
)


class CheckFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise CheckFailure(message)


def wait_until(fn, *, timeout=20.0, description="condition"):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last_error = type(exc).__name__
        time.sleep(0.15)
    raise CheckFailure(f"Timed out after {timeout:g}s: {description}" + (f" ({last_error})" if last_error else ""))


class Redactor:
    def __init__(self):
        self.values = set()

    def add(self, value):
        if value:
            self.values.add(str(value))
        return value

    def clean(self, text):
        text = str(text)
        for value in sorted(self.values, key=len, reverse=True):
            text = text.replace(value, "[REDACTED]")
        text = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)
        text = re.sub(r"(?i)((?:resume_token|join_code|qorgau_teacher)[\"'\s:=]+)[^,\s}\"']+", r"\1[REDACTED]", text)
        return text


def child_env(data_dir):
    # Do not inherit user Qorgau settings: a stored camera/config must not affect this run.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("QORGAU_")}
    private_temp = data_dir / "temp"
    private_temp.mkdir(parents=True, exist_ok=True)
    env.update(
        PYTHONPATH=os.pathsep.join(map(str, (PROCTORING / "backend", PROCTORING / "contracts/python", PROCTORING))),
        PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1",
        QORGAU_DATA_DIR=str(data_dir), QORGAU_MODELS_DIR=str(data_dir / "empty-models"),
        QORGAU_REPLAY_DIR=str(data_dir / "empty-replay"), QORGAU_EXAM_PATH=str(data_dir / "absent-exam.json"),
        QORGAU_HOST="127.0.0.1", QORGAU_PORT="0", QORGAU_LOG_LEVEL="WARNING",
        TEMP=str(private_temp), TMP=str(private_temp), TMPDIR=str(private_temp),
    )
    return env


class Process:
    def __init__(self, name, command, env, redactor):
        self.name, self.command, self.env, self.redactor = name, command, env, redactor
        self.proc = None
        self.ready = None
        self.lines = deque(maxlen=30)
        self.threads = []

    def start(self, prefix, token=None):
        self.proc = subprocess.Popen(
            self.command, cwd=PROCTORING, env=self.env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        event = threading.Event()

        def pump(stream):
            for line in stream:
                if line.startswith(prefix):
                    self.ready = json.loads(line[len(prefix):])
                    event.set()
                # Raw PIN lines never reach the retained log, even if server formatting changes.
                if not line.startswith("QORGAU_CLASS_PIN "):
                    self.lines.append(self.redactor.clean(line.rstrip()))

        for stream in (self.proc.stdout, self.proc.stderr):
            thread = threading.Thread(target=pump, args=(stream,), daemon=True)
            thread.start()
            self.threads.append(thread)
        if token:
            self.proc.stdin.write(token + "\n")
            self.proc.stdin.flush()
        deadline = time.monotonic() + 35
        while not event.wait(0.1):
            if self.proc.poll() is not None or time.monotonic() >= deadline:
                raise CheckFailure(f"{self.name} did not become ready: " + "\n".join(self.lines))
        # On Windows the venv python.exe redirector creates a child interpreter.
        require(self.ready["pid"] == self.proc.pid or self.ready["pid"] in self._windows_descendants(),
                f"{self.name}: readiness PID does not belong to launched process")
        self.port = self.ready["port"]
        return self

    def _windows_descendants(self):
        if sys.platform != "win32":
            return set()
        import ctypes
        from ctypes import wintypes

        class Entry(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                        ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                        ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                        ("exe", wintypes.WCHAR * 260)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateToolhelp32Snapshot(2, 0)
        if handle == wintypes.HANDLE(-1).value:
            raise CheckFailure("Cannot inspect launched Windows process descendants")
        try:
            entry = Entry()
            entry.size = ctypes.sizeof(entry)
            parents = {}
            found = kernel.Process32FirstW(handle, ctypes.byref(entry))
            while found:
                parents[entry.pid] = entry.parent
                found = kernel.Process32NextW(handle, ctypes.byref(entry))
            descendants = set()
            for candidate in parents:
                pid = candidate
                for _ in range(16):
                    pid = parents.get(pid)
                    if pid == self.proc.pid:
                        descendants.add(candidate)
                        break
                    if pid is None:
                        break
            return descendants
        finally:
            kernel.CloseHandle(handle)

    def _kill(self):
        if sys.platform == "win32" and self.proc.poll() is None:
            # Include children even when startup failed before a readiness line.
            # Verify ancestry while our original launcher is alive. Never kill by image name.
            for pid in self._windows_descendants():
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if self.proc.poll() is None:
            self.proc.kill()

    def stop(self, *, crash=False):
        if self.proc is None:
            return None
        if self.proc.poll() is None:
            if crash:
                self._kill()
            else:
                try:
                    self.proc.stdin.close()
                except OSError:
                    pass
            try:
                self.proc.wait(timeout=12)
            except subprocess.TimeoutExpired:
                self._kill()
                self.proc.wait(timeout=5)
        for thread in self.threads:
            thread.join(timeout=1)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream and not stream.closed:
                stream.close()
        return self.proc.returncode


class Api:
    def __init__(self, port, token=None):
        self.base = f"http://127.0.0.1:{port}"
        self.token = token
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.cookies))

    def call(self, path, body=None, *, method=None, expected=200, raw=False):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                         headers=headers, method=method or ("POST" if body is not None else "GET"))
        try:
            with self.opener.open(request, timeout=8) as response:
                payload = response.read()
                require(response.status == expected, f"{path}: expected HTTP {expected}, got {response.status}")
                return (payload, dict(response.headers)) if raw else json.loads(payload)
        except urllib.error.HTTPError as exc:
            # Do not print response bodies: welcome/session/auth bodies can contain credentials.
            raise CheckFailure(f"{path}: expected HTTP {expected}, got {exc.code}") from None


class Stream:
    def __init__(self, url, headers):
        from websockets.sync.client import connect
        self.context = connect(url, additional_headers=headers, open_timeout=8, close_timeout=2, max_size=2**20, proxy=None)
        self.ws = self.context.__enter__()
        self.messages = deque(maxlen=20000)
        self.error = None
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        while not self.stopping.is_set():
            try:
                payload = json.loads(self.ws.recv(timeout=0.25))
                self.messages.append(payload)
            except TimeoutError:
                continue
            except Exception as exc:
                if not self.stopping.is_set():
                    self.error = type(exc).__name__
                break

    def snapshot(self):
        return list(self.messages)

    def close(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        self.context.__exit__(None, None, None)
        self.thread.join(timeout=3)


def execute(args):
    redactor = Redactor()
    results = {}
    processes, streams = [], []
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix="qorgau-synthetic-chain-"))
    pin = redactor.add(f"{secrets.randbelow(1000000):06d}")
    report = {
        "schema": "qorgau.acceptance.chain.v1", "source_mode": "synthetic",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(), "python_version": platform.python_version(),
        "limitations": ["Loopback only; no LAN/Wi-Fi load proof", "Scripted synthetic observations; no CV accuracy proof",
                        "No Electron, native enforcement, webcam or microphone", "Core C1/C2 only; optional T03/T04/T05 features are not mounted"],
    }

    def record(name, fn, *, fatal=True):
        before = time.monotonic()
        try:
            evidence = fn()
            results[name] = {"status": "pass", "evidence": evidence or {}}
        except Exception as exc:
            results[name] = {"status": "fail", "error": redactor.clean(f"{type(exc).__name__}: {exc}")}
            if fatal:
                raise
        finally:
            if name in results:
                results[name]["duration_s"] = round(time.monotonic() - before, 3)
                print(f"SYNTHETIC {name}: {results[name]['status']}", flush=True)

    def spawn(name, command, env, prefix, token=None):
        process = Process(name, command, env, redactor)
        processes.append(process)  # registered before start, including startup failure
        return process.start(prefix, token)

    def start_c1(port=0):
        env = child_env(work / "c1")
        env["QORGAU_CLASS_TEACHER_PIN"] = pin
        return spawn("class-server", [args.python, "-m", "classroom.server", "--host", "127.0.0.1", "--port", str(port),
                                     "--data-dir", str(work / "c1"), "--features", "", "--ui", "none", "--exit-on-stdin-eof"],
                     env, "QORGAU_CLASS_READY ")

    def login(c1):
        teacher = Api(c1.port)
        teacher.call("/api/teacher/login", {"pin": pin})
        for cookie in teacher.cookies:
            redactor.add(cookie.value)
        return teacher

    try:
        def source_checkout():
            code = "import json,proctor,classroom,proctor_contracts; print(json.dumps([proctor.__file__,classroom.__file__,proctor_contracts.__file__]))"
            completed = subprocess.run([args.python, "-c", code], cwd=PROCTORING, env=child_env(work), capture_output=True,
                                       text=True, encoding="utf-8", timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            require(completed.returncode == 0, "Selected Python could not resolve project packages")
            paths = [Path(p).resolve() for p in json.loads(completed.stdout)]
            require(all(p.is_relative_to(PROCTORING) for p in paths), "Editable install resolved code outside this checkout")
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROCTORING, capture_output=True, text=True, timeout=5)
            report["source_commit"] = sha.stdout.strip() if sha.returncode == 0 else "unknown"
            return {"modules": [str(p.relative_to(PROCTORING)) for p in paths], "source_commit": report["source_commit"]}
        record("source_checkout", source_checkout)
        c1 = start_c1()
        teacher = login(c1)
        classroom = teacher.call("/api/teacher/session", {"title": "SYNTHETIC Windows two-backend acceptance", "mode": "url",
                                                        "allowed_urls": ["https://example.invalid/*"]}, expected=201)
        code = redactor.add(classroom["join_code"])
        students = []
        for index in (1, 2):
            token = redactor.add(secrets.token_urlsafe(36))
            env = child_env(work / f"student-{index}")
            env.update(QORGAU_CLASS_SERVER=f"127.0.0.1:{c1.port}", QORGAU_CLASS_CODE=code,
                       QORGAU_CLASS_LABEL=f"SYNTHETIC acceptance student {index}")
            proc = spawn(f"student-{index}", [args.python, "-m", "proctor", "serve", "--token-stdin", "--port", "0"],
                         env, "QORGAU_READY ", token)
            api = Api(proc.port, token)
            stream = Stream(f"ws://127.0.0.1:{proc.port}/v1/stream", {"Authorization": f"Bearer {token}"})
            streams.append(stream)
            students.append({"label": env["QORGAU_CLASS_LABEL"], "api": api, "process": proc, "stream": stream, "token": token})
        record("three_processes_ready", lambda: {"launcher_pids": [p.proc.pid for p in processes],
                                                 "backend_pids": [p.ready["pid"] for p in processes],
                                                 "distinct": len({p.ready["pid"] for p in processes}) == 3})

        def roster():
            return teacher.call("/api/teacher/students")

        def pairing():
            cards = wait_until(lambda: (cards if len(cards := roster()) == 2 and all(c["connected"] for c in cards) else None),
                               description="two full C2 backends join C1")
            require(len({c["student_id"] for c in cards}) == 2, "Student IDs collide")
            for s in students:
                matches = [c for c in cards if c["student_label"] == s["label"]]
                require(len(matches) == 1, "Labels are not isolated")
                s["student_id"] = matches[0]["student_id"]
            return {"student_ids": [s["student_id"] for s in students], "same_computer_name": cards[0]["computer_name"] == cards[1]["computer_name"]}
        record("two_distinct_students", pairing)
        cookie = "; ".join(f"{c.name}={c.value}" for c in teacher.cookies)
        teacher_stream = Stream(f"ws://127.0.0.1:{c1.port}/ws/teacher?inline_previews=0", {"Cookie": cookie})
        streams.append(teacher_stream)

        def prepare():
            for s in students:
                info = s["api"].call("/v1/sessions", {"source": {"mode": "synthetic"}, "exam_id": "demo-exam-1", "student_label": s["label"],
                                                     "consent": {"accepted": True, "text_version": "consent-ru-1", "accepted_at": datetime.now(timezone.utc).isoformat()}}, expected=201)
                s["session_id"] = info["session_id"]
                preflight = s["api"].call(f"/v1/sessions/{s['session_id']}/preflight", method="POST")
                require(preflight["ready"] and preflight["source_mode"] == "synthetic", "Synthetic preflight did not pass")
                s["api"].call(f"/v1/sessions/{s['session_id']}/calibration/skip", {"reason": "SYNTHETIC acceptance; no human calibration"})
            require(students[0]["session_id"] != students[1]["session_id"], "Local session IDs collide")
            return {"local_session_ids": [s["session_id"] for s in students], "source_modes": ["synthetic", "synthetic"]}
        record("synthetic_sessions_preflight", prepare)

        def info(s):
            return s["api"].call(f"/v1/sessions/{s['session_id']}")

        def local_incidents(s):
            return s["api"].call(f"/v1/sessions/{s['session_id']}/incidents")

        def server_incidents(s):
            return teacher.call(f"/api/teacher/students/{s['student_id']}/incidents")

        commands = []

        def command(s, kind):
            issued = teacher.call(f"/api/teacher/students/{s['student_id']}/commands", {"kind": kind, "payload": {}}, expected=202)
            cid = issued["command_id"]
            done = wait_until(lambda: (cmd if (cmd := teacher.call(f"/api/teacher/commands/{cid}"))["status"] in ("succeeded", "failed", "expired") else None),
                              description=f"{kind} acknowledged by addressed backend")
            require(done["status"] == "succeeded" and done.get("ack", {}).get("ok"), f"{kind} was not successfully executed")
            commands.append(done)
            return done

        a, b = students

        def start_a():
            command(a, "start_exam")
            require(info(a)["state"] == "running" and info(b)["state"] == "ready", "start_exam reached the wrong backend")
            require(local_incidents(b) == [] and server_incidents(b) == [], "Inactive student B received A's incident")
            return {"student_a": "running", "student_b": "ready", "student_b_incidents": 0}
        record("targeted_start_isolation", start_a)

        def incident_and_preview(s):
            wait_until(lambda: any(i["rule_id"] == "phone_visible" for i in server_incidents(s)), timeout=30,
                       description="scripted phone -> real fusion/evidence -> real C2 -> C1")
            wait_until(lambda: any(m.get("type") == "preview" and m.get("student_id") == s["student_id"] for m in teacher_stream.snapshot()),
                       description="C2 preview in teacher stream")
            data, headers = teacher.call(f"/api/teacher/students/{s['student_id']}/preview.jpg", raw=True)
            require(data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9") and 0 < len(data) <= 30000, "Invalid uplink JPEG")
            s["preview_origin"] = headers.get("X-Qorgau-Origin") or headers.get("x-qorgau-origin")
            return {"student_id": s["student_id"], "incident_count": len(server_incidents(s)), "jpeg_bytes": len(data)}
        record("student_a_incident_and_preview", lambda: incident_and_preview(a))
        command(b, "start_exam")
        record("student_b_incident_and_preview", lambda: incident_and_preview(b))

        def finish_a():
            command(a, "finish_exam")
            require(info(a)["state"] == "finished" and info(b)["state"] == "running", "finish_exam reached the wrong backend")
            return {"student_a": "finished", "student_b": "running"}
        record("targeted_finish_isolation", finish_a)
        command(b, "finish_exam")

        def incident_counts():
            def caught_up():
                for s in students:
                    local, remote = local_incidents(s), server_incidents(s)
                    card = teacher.call(f"/api/teacher/students/{s['student_id']}")
                    if not local or {i["incident_id"] for i in local} != {i["incident_id"] for i in remote}:
                        return False
                    if card["exam_state"] != "finished" or card["incidents_total"] != len(local):
                        return False
                    if any(i["state"] != "closed" for i in remote):
                        return False
                return True
            wait_until(caught_up, description="finished incident IDs/states/counts catch up")
            identities = []
            evidence = []
            for s in students:
                local, remote = local_incidents(s), server_incidents(s)
                events = teacher.call(f"/api/teacher/students/{s['student_id']}/events?limit=1000")
                card = teacher.call(f"/api/teacher/students/{s['student_id']}")
                ids = {i["incident_id"] for i in local}
                identities.append(ids)
                require(all(i["source_mode"] == "synthetic" for i in local), "Local non-synthetic incident")
                require(all(i["student_id"] == s["student_id"] for i in remote + events), "Cross-student event leak")
                require({e["payload"]["incident_id"] for e in events} == ids, "Event IDs differ from local incident IDs")
                require(len({e["event_id"] for e in events}) == len(events), "Duplicate stored event ID")
                require(all(e["event_time"] and e["received_at"] for e in events), "Missing event provenance timestamps")
                priorities = {p: sum(i["priority"] == p for i in local) for p in ("low", "medium", "high")}
                require(card["incidents_by_priority"] == priorities, "Per-priority totals disagree with evidence store")
                s["baseline_events"] = events
                s["baseline_incidents"] = remote
                evidence.append({"student_id": s["student_id"], "incidents": len(local), "events": len(events), "priorities": priorities})
            require(identities[0].isdisjoint(identities[1]), "Students share incident identifiers")
            return evidence
        record("incident_identity_and_counts", incident_counts)

        def stream_check():
            messages = teacher_stream.snapshot()
            seqs = [m["seq"] for m in messages]
            require(seqs == list(range(1, len(seqs) + 1)), "Teacher stream sequence gap")
            require({m.get("student_id") for m in messages if m.get("type") == "incident"} == {s["student_id"] for s in students}, "Teacher stream did not see both students")
            for s in students:
                states = [m.get("message", {}) for m in s["stream"].snapshot() if m.get("message", {}).get("type") == "class_state"]
                require(any(m.get("student_id") == s["student_id"] and m.get("last_command", {}).get("kind") == "finish_exam" for m in states if m.get("last_command")),
                        "Student stream missed acknowledged classroom finish")
                require(all(m.get("student_id") in (None, s["student_id"]) for m in states), "Student stream contains another identity")
            return {"teacher_messages": len(messages), "types": dict(Counter(m["type"] for m in messages)), "contiguous_seq": True}
        record("teacher_stream", stream_check, fatal=False)

        def provenance():
            observations = []
            for s in students:
                card = teacher.call(f"/api/teacher/students/{s['student_id']}")
                origins = {card["origin"], s["preview_origin"], *(i["origin"] for i in s["baseline_incidents"]), *(e["origin"] for e in s["baseline_events"])}
                observations.append({"student_id": s["student_id"], "actual_origins": sorted(str(o) for o in origins)})
            require(all(x["actual_origins"] == ["simulated"] for x in observations), "SYNTHETIC data labelled incorrectly by C1/C2: " + json.dumps(observations))
            return observations
        record("synthetic_provenance", provenance, fatal=False)

        teacher_stream.close()
        port = c1.port
        c1.stop(crash=True)
        before_restart = time.monotonic()
        c1 = start_c1(port)
        teacher = login(c1)

        def resumed():
            cards = wait_until(lambda: (cards if len(cards := roster()) == 2 and all(c["connected"] for c in cards) else None), timeout=35,
                               description="both C2 clients resume after C1 process kill")
            require({c["student_id"] for c in cards} == {s["student_id"] for s in students}, "Reconnect created duplicate/new student identities")
            require(teacher.call("/api/teacher/session")["session_id"] == classroom["session_id"], "Class session changed after restart")
            require(all(info(s)["state"] == "finished" for s in students), "C1 crash changed local session state")
            return {"students": 2, "same_ids": True, "reconnect_s": round(time.monotonic() - before_restart, 3)}
        record("server_crash_resume", resumed)

        def persistence():
            preserved = late_clips = 0
            for s in students:
                events = teacher.call(f"/api/teacher/students/{s['student_id']}/events?limit=1000")
                keyed = {e["event_id"]: e for e in events}
                baseline = {e["event_id"]: e for e in s["baseline_events"]}
                require(len(keyed) == len(events), "Duplicate event after C1 crash")
                require(all(keyed.get(k) == event for k, event in baseline.items()), "Previously stored event lost/changed after C1 crash")
                # Clip encoding can finish after the exam ends: C2 legitimately sends
                # another closed/clip_available event. It must not count as a new episode.
                incident_ids = {i["incident_id"] for i in s["baseline_incidents"]}
                late = [e for e in events if e["event_id"] not in baseline]
                require(all(e["payload"]["incident_id"] in incident_ids and e["payload"]["state"] == "closed" and
                            e["payload"]["clip_available"] for e in late), "Unexpected new event after finished-session reconnect")
                current = server_incidents(s)
                require({i["incident_id"] for i in current} == incident_ids, "Durable incident identity changed after C1 crash")
                for incident in s["baseline_incidents"]:
                    restored = next(i for i in current if i["incident_id"] == incident["incident_id"])
                    require(all(restored[k] == incident[k] for k in incident if k not in ("clip_available", "last_received_at", "events")),
                            "Durable incident content changed after C1 crash")
                    require(not incident["clip_available"] or restored["clip_available"], "Clip availability regressed after C1 crash")
                preserved += len(baseline)
                late_clips += len(late)
                s["baseline_events"] = events
            for cmd in commands:
                stored = teacher.call(f"/api/teacher/commands/{cmd['command_id']}")
                require(stored["status"] == "succeeded" and stored["ack"] == cmd["ack"], "Acknowledged command lost after C1 crash")
            return {"stored_events_preserved": preserved, "late_clip_ready_events": late_clips, "commands_succeeded": len(commands)}
        record("durable_events_and_commands", persistence)

        def student_restart():
            a["stream"].close()
            original = a["process"]
            require(original.stop() == 0, "Student A did not shut down cleanly")
            wait_until(lambda: not teacher.call(f"/api/teacher/students/{a['student_id']}")["connected"], description="Student A offline")
            replacement = spawn("student-1-restarted", original.command, original.env, "QORGAU_READY ", a["token"])
            wait_until(lambda: teacher.call(f"/api/teacher/students/{a['student_id']}")["connected"], description="Student A resumes persisted identity")
            require({c["student_id"] for c in roster()} == {s["student_id"] for s in students}, "Backend restart created a duplicate roster entry")
            require(teacher.call(f"/api/teacher/students/{a['student_id']}/events?limit=1000") == a["baseline_events"], "Backend restart duplicated old events")
            return {"same_student_id": True, "new_pid": replacement.proc.pid, "roster_size": 2}
        record("student_restart_identity", student_restart)
    except Exception as exc:
        report["fatal_error"] = redactor.clean(f"{type(exc).__name__}: {exc}")
    finally:
        cleanup_errors = []
        for stream in reversed(streams):
            try:
                stream.close()
            except Exception as exc:
                cleanup_errors.append(type(exc).__name__)
        exits = []
        for process in reversed(processes):
            try:
                exits.append({"name": process.name, "pid": process.proc.pid if process.proc else None, "exit_code": process.stop()})
            except Exception as exc:
                cleanup_errors.append(f"{process.name}: {type(exc).__name__}")
        try:
            # Only this mkdtemp-created directory is removed; no user-provided path is deleted.
            shutil.rmtree(work)
        except OSError as exc:
            cleanup_errors.append(f"temporary directory: {type(exc).__name__}")
        results["cleanup"] = {"status": "fail" if cleanup_errors else "pass", "processes": exits, "temporary_data_removed": not work.exists()}
        if cleanup_errors:
            results["cleanup"]["errors"] = cleanup_errors
        for name in CHECKS:
            results.setdefault(name, {"status": "skipped", "reason": "A prerequisite failed"})
        report["checks"] = {name: results[name] for name in CHECKS}
        report["duration_s"] = round(time.monotonic() - started, 3)
        report["passed"] = all(result["status"] == "pass" for result in results.values()) and "fatal_error" not in report
        args.output.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        args.output.write_text(redactor.clean(payload), encoding="utf-8")
    counts = dict(Counter(result["status"] for result in results.values()))
    print(json.dumps({"source_mode": "synthetic", "passed": report["passed"], "checks": counts, "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Python interpreter with full backend dependencies")
    parser.add_argument("--output", required=True, type=Path, help="Sanitized machine-readable JSON result")
    args = parser.parse_args()
    args.python = str(Path(args.python).resolve())
    args.output = args.output.resolve()
    if Path(args.python) != Path(sys.executable).resolve():
        return subprocess.call([args.python, str(Path(__file__).resolve()), "--python", args.python, "--output", str(args.output)],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
