"""End-to-end scenario through the public API of a running backend (owner: A09).

preflight → informed session start → calibration → exam (answers, environment events, phone episode)
→ incidents → human review → summary/report/export → finish → restart.

Returns PASS / FAIL / NOT_RUN rows. NOT_RUN is used only where the contract says a part is not
delivered yet (e.g. 501 report until A08 lands) — it is never folded into PASS.
On a synthetic session this proves wiring and contract conformance, NOT CV quality.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from . import contract
from .backend import FAKE_SHELL_CAPABILITIES, Api, BackendProcess, env_event, wait_for
from .stream import StreamRecorder, incidents, states

REQUIRED_ENV_ACTIONS = (  # PDF 2.3: Alt+Tab, Ctrl+C/V, Win, PrtScn, tab switching, foreign windows
    ("shortcut_alt_tab", "Alt+Tab"),
    ("shortcut_ctrl_c", "Ctrl+C"),
    ("shortcut_ctrl_v", "Ctrl+V"),
    ("shortcut_win", "Win"),
    ("shortcut_print_screen", "PrtScn"),
    ("shortcut_ctrl_tab", "Ctrl+Tab"),
    ("foreign_window_foreground", None),
    ("focus_lost", None),
)


@dataclass
class Rows:
    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, "PASS" if ok else "FAIL", detail[:300]))
        return bool(ok)

    def not_run(self, name: str, why: str) -> None:
        self.rows.append((name, "NOT_RUN", why[:300]))

    @property
    def failed(self) -> list[tuple[str, str, str]]:
        return [r for r in self.rows if r[1] == "FAIL"]

    def as_json(self) -> list[dict[str, str]]:
        return [{"name": n, "status": s, "detail": d} for n, s, d in self.rows]


def _try(rows: Rows, name: str, fn: Any) -> Any:
    try:
        return fn()
    except AssertionError as exc:
        rows.check(name, False, f"AssertionError: {exc}")
    except Exception as exc:
        rows.check(name, False, f"{type(exc).__name__}: {exc}")
    return None


def run_full_flow(be: BackendProcess, phone_timeout: float = 45.0) -> Rows:
    rows = Rows()
    http = be.http
    api = Api(http)
    rows.check("ready_line_contract", be.ready.get("contract") == "qorgau.v1" and be.ready.get("contract_version") == "1.0.0", be.ready_line.strip()[:200])

    health = _try(rows, "health", lambda: contract.ok(http.get("/health"), "HealthReport"))
    if health is not None:
        rows.check("health", True, ", ".join(f"{c.component.value}={c.code}" for c in health.components))

    caps = _try(rows, "capabilities_put", lambda: contract.ok(http.put("/environment/capabilities", json=FAKE_SHELL_CAPABILITIES), "EnvironmentCapabilities"))
    if caps is not None:
        got = http.get("/environment/capabilities").json()
        rows.check("capabilities_roundtrip", got["platform"] == FAKE_SHELL_CAPABILITIES["platform"], "fake shell, labelled")

    stream = StreamRecorder(be.ws_url("/stream"), headers=be.auth)
    try:
        hello = stream.wait(lambda ms: ms[:1], 5)
        rows.check("stream_hello_first", bool(hello) and hello[0]["message"]["type"] == "hello" and hello[0]["seq"] == 1, str(hello[:1])[:200])

        info = _try(rows, "create_session", lambda: contract.ok(http.post("/sessions", json=_create_body()), "SessionInfo", 201))
        if info is None:
            return rows
        sid = info.session_id
        rows.check("create_session", info.state.value == "created" and info.source_mode.value == "synthetic", sid)
        exam = _try(rows, "exam_definition", lambda: contract.ok(http.get(f"/sessions/{sid}/exam"), "ExamDefinition"))
        question_id = exam.questions[0].question_id if exam is not None else "q1"
        if exam is not None:
            rows.check("exam_definition", True, f"{exam.exam_id}: {len(exam.questions)} question(s)")

        pf = _try(rows, "preflight", lambda: contract.ok(http.post(f"/sessions/{sid}/preflight"), "PreflightReport"))
        if pf is not None:
            checks = {c.check_id.value: c for c in pf.checks}
            rows.check("preflight_ready", pf.ready, ", ".join(f"{k}={c.status.value}" for k, c in checks.items()))
            labelled = all(
                c.status.value != "pass"
                for k, c in checks.items()
                if k in ("phone_model", "face_model", "fusion", "storage")
                and c.details.get("impl") == "bootstrap"
            )
            rows.check("preflight_bootstrap_parts_labelled_not_pass", labelled, "bootstrap parts must be WARN + impl=bootstrap, never PASS")
            # Once real modules land, their checks may correctly PASS. Inspect actual
            # bootstrap substitutions rather than requiring every module to be missing.
            incomplete = any(c.required and c.status.value == "fail" for c in pf.checks)
            rows.check("preflight_no_failed_required_check_when_ready", not (pf.ready and incomplete))

        cal = _try(rows, "calibration", lambda: api.calibrate(sid))
        if cal is not None:
            contract.validate("CalibrationState", cal)
            rows.check("calibration", cal["phase"] == "completed", "5 targets, sample-driven")

        started = _try(rows, "start", lambda: contract.ok(http.post(f"/sessions/{sid}/start"), "SessionInfo"))
        if started is not None:
            rows.check("start", started.state.value == "running" and started.exam_started_t_ms is not None, str(started.exam_started_t_ms))

        a1 = _try(rows, "answer_save", lambda: contract.ok(http.put(f"/sessions/{sid}/answers/{question_id}", json={"value": ["b"], "client_seq": 5}), "AnswerRecord"))
        if a1 is not None:
            stale = contract.ok(http.put(f"/sessions/{sid}/answers/{question_id}", json={"value": ["a"], "client_seq": 4}), "AnswerRecord")
            rows.check("answer_last_writer_by_client_seq", stale.value == ["b"] and stale.client_seq == 5, f"stale write kept {stale.value}")
            listed = contract.validate_list("AnswerRecord", http.get(f"/sessions/{sid}/answers").json())
            rows.check("answers_list", len(listed) == 1, f"{len(listed)} answer(s)")

        events = [env_event(a, i + 1, shortcut=s) for i, (a, s) in enumerate(REQUIRED_ENV_ACTIONS)]
        ack = _try(rows, "environment_events", lambda: contract.ok(http.post(f"/sessions/{sid}/environment/events", json={"session_id": sid, "events": events}), "EnvironmentEventAck"))
        if ack is not None:
            rows.check("environment_events", ack.accepted == len(events) and ack.duplicates == 0, f"accepted={ack.accepted}")
            again = contract.ok(http.post(f"/sessions/{sid}/environment/events", json={"session_id": sid, "events": events}), "EnvironmentEventAck")
            rows.check("environment_events_dedup", again.accepted == 0 and again.duplicates == len(events), f"dup={again.duplicates}")
            # A05 intentionally merges related actions into episodes. Requiring one
            # incident per event is a bootstrap implementation detail, not the contract.
            required_actions = {action for action, _ in REQUIRED_ENV_ACTIONS}
            def environment_covered(ms):
                seen = {m["message"]["observation"]["action"] for m in ms
                        if m["message"]["type"] == "observation"
                        and m["message"]["observation"]["kind"] == "environment"}
                episodes = [c for c in incidents(ms) if c["incident"]["category"] == "environment"]
                return required_actions <= seen and bool(episodes)
            env_incs = stream.wait(environment_covered, 10)
            rows.check("environment_incidents_on_stream", bool(env_incs), "all actions observed; one or more grouped environment episodes")

        opened = stream.wait(lambda ms: [c for c in incidents(ms, "phone_visible") if c["change"] == "opened"], phone_timeout)
        rows.check("phone_episode_opened_on_stream", bool(opened), (opened[0]["incident"]["explanation"]["summary_ru"] if opened else f"none in {phone_timeout}s"))
        if opened:
            inc = opened[0]["incident"]
            rows.check(
                "episode_explainable_and_labelled",
                inc["source_mode"] == "synthetic" and bool(inc["explanation"]["summary_ru"]) and bool(inc["rule_version"]) and inc["review_status"] == "pending",
                f"priority={inc['priority']} caveats={len(inc['explanation']['caveats_ru'])}",
            )

        listed_incs = _try(rows, "incidents_list", lambda: contract.validate_list("Incident", http.get(f"/sessions/{sid}/incidents").json()))
        if listed_incs is not None:
            ts = [i.t_start_ms for i in listed_incs]
            rows.check("incidents_list", len(listed_incs) >= 1 and ts == sorted(ts), f"{len(listed_incs)} incident(s), ordered by t_start_ms")
            target = next((i for i in listed_incs if i.rule_id.value == "phone_visible"), listed_incs[0] if listed_incs else None)
            if target is not None:
                iid = target.incident_id
                r1 = contract.ok(http.post(f"/sessions/{sid}/incidents/{iid}/reviews", json={"decision": "dismissed", "comment": "<script>alert(1)</script>", "operator": "qa-teacher"}), "HumanReview")
                r2 = contract.ok(http.post(f"/sessions/{sid}/incidents/{iid}/reviews", json={"decision": "confirmed", "comment": "second look", "operator": "qa-teacher"}), "HumanReview")
                detail = contract.ok(http.get(f"/sessions/{sid}/incidents/{iid}"), "IncidentDetail")
                rows.check(
                    "human_review_append_only",
                    len(detail.reviews) == 2 and r2.supersedes_review_id == r1.review_id and detail.incident.review_status.value == "confirmed",
                    f"reviews={len(detail.reviews)} status={detail.incident.review_status.value}",
                )
                rows.check("review_comment_stored_verbatim", detail.reviews[0].comment == "<script>alert(1)</script>", "escaping is the renderer/report job; API stores text")

        summary = _try(rows, "summary", lambda: contract.ok(http.get(f"/sessions/{sid}/summary"), "SessionSummary"))
        if summary is not None:
            rows.check("summary", summary.incidents_total >= 1, f"total={summary.incidents_total} by_rule={dict(summary.incidents_by_rule)}")

        for path, name in (("report.html", "report_html"), ("report.json", "report_json_export")):
            r = http.get(f"/sessions/{sid}/{path}")
            if r.status_code == 501:
                contract.api_error(r, 501, "NOT_IMPLEMENTED")
                rows.not_run(name, "501 NOT_IMPLEMENTED: report/export are delivered by A08 (evidence)")
            else:
                rows.check(name, r.status_code == 200, f"{r.status_code} {r.headers.get('content-type')}")

        m = _try(rows, "metrics", lambda: contract.ok(http.get(f"/sessions/{sid}/metrics"), "RuntimeMetrics"))
        if m is not None:
            rows.check("metrics", m.frames_captured > 0 and m.session_id == sid, f"capture_fps={m.capture_fps} frames={m.frames_captured}")

        pv = http.get(f"/sessions/{sid}/preview.jpg")
        if pv.status_code == 200:
            meta = contract.validate("PreviewFrameMeta", json.loads(pv.headers["X-Qorgau-Preview-Meta"]))
            rows.check(
                "preview_jpeg",
                pv.content[:2] == b"\xff\xd8" and pv.content[-2:] == b"\xff\xd9" and meta.session_id == sid and meta.mirrored is False,
                f"{len(pv.content)} bytes frame_id={meta.frame_id} cache={pv.headers.get('cache-control')}",
            )
        else:
            rows.check("preview_jpeg", False, f"status {pv.status_code}")

        fin = _try(rows, "finish", lambda: contract.ok(http.post(f"/sessions/{sid}/finish"), "SessionInfo"))
        if fin is not None:
            rows.check("finish", fin.state.value == "finished" and fin.finished_at is not None, fin.state.value)
            left_open = [i.incident_id for i in contract.validate_list("Incident", http.get(f"/sessions/{sid}/incidents").json()) if i.state.value == "open"]
            rows.check("finish_closes_all_incidents", not left_open, f"open after finish: {left_open}")
            rows.check("no_answers_after_finish", http.put(f"/sessions/{sid}/answers/{question_id}", json={"value": ["c"], "client_seq": 99}).status_code == 409)
            late = http.post(f"/sessions/{sid}/environment/events", json={"session_id": sid, "events": [env_event("shortcut_ctrl_c", 1000)]})
            rows.check("no_environment_events_after_finish", late.status_code == 409, str(late.status_code))
            seen = stream.wait(lambda ms: "finished" in states(ms, sid) or None, 5)
            order = states(stream.snapshot(), sid)
            rows.check("stream_state_order", bool(seen) and order[-1] == "finished" and order[:1] == ["created"], " → ".join(order))

        again = http.post("/sessions", json=_create_body())
        rows.check("restart_new_session", again.status_code == 201, str(again.status_code))
        if again.status_code == 201:
            sid2 = again.json()["session_id"]
            pf2 = http.post(f"/sessions/{sid2}/preflight").json()
            rows.check("restart_capture_reopens", pf2.get("ready") is True)
            ab = contract.ok(http.post(f"/sessions/{sid2}/abort", json={"reason": "qa restart check"}), "SessionInfo")
            rows.check("restart_abort", ab.state.value == "aborted")
    finally:
        stream.close()

    msgs = stream.snapshot()
    bad = []
    for raw in msgs:
        try:
            contract.validate("StreamEnvelope", raw)
        except Exception as exc:
            bad.append(str(exc)[:120])
    rows.check("stream_envelopes_valid", bool(msgs) and not bad, f"{len(msgs)} envelopes, {len(bad)} invalid {bad[:1]}")
    seqs = [m["seq"] for m in msgs]
    rows.check("stream_seq_contiguous", seqs == list(range(1, len(seqs) + 1)), f"first={seqs[:3]} last={seqs[-3:]}")
    obs = [m["message"]["observation"] for m in msgs if m["message"]["type"] == "observation"]
    mislabelled = [o["observation_id"] for o in obs if o["source_mode"] != "synthetic" or (o["kind"] in ("phone", "attention") and not o["producer"]["module"].startswith("bootstrap."))]
    rows.check("synthetic_observations_labelled", bool(obs) and not mislabelled, f"{len(obs)} observations, mislabelled={mislabelled[:3]}")
    return rows


def _create_body() -> dict[str, Any]:
    from .backend import session_create_body

    return session_create_body("synthetic")
