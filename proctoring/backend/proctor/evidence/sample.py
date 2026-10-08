"""Build a SYNTHETIC demo session in a throw-away store and export its report (owner: A08).

    python -m proctor.evidence.sample --out <dir>        # writes report.synthetic.html / .json

Everything is synthetic: scripted observations/incidents and generated gradient frames (no person,
no camera). Used by tests, by A07 as a golden example of report.json, and for print checks.
"""

from __future__ import annotations

import argparse
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from proctor.settings import Settings
from proctor_contracts.interfaces import FramePacket
from proctor_contracts.v1 import (
    AnswerUpsert,
    AttentionObservation,
    CalibrationState,
    Direction,
    EnvironmentDetail,
    EnvironmentObservation,
    Explanation,
    ExplanationFact,
    FramePacketMeta,
    GazeEstimate,
    Health,
    HealthObservation,
    HumanReviewCreate,
    Incident,
    IncidentChange,
    IncidentState,
    PhoneDetection,
    PhoneObservation,
    PhoneSignal,
    BBox,
    Producer,
    SessionInfo,
    SessionState,
    SourceConfig,
)

from .config import EvidenceConfig
from .store import SqliteEvidenceStore

SID = "s-sample-synthetic-0001"
PRODUCERS = {
    "phone": Producer(module="bootstrap.phone", version="sample-1", config_version="script-v1"),
    "attention": Producer(module="bootstrap.attention", version="sample-1", config_version="script-v1"),
    "environment": Producer(module="environment", version="sample-1"),
    "capture": Producer(module="capture", version="sample-1"),
}


def synthetic_frame(session_id: str, frame_id: int, t_ms: float, wall: datetime, mode: str = "synthetic") -> FramePacket:
    """Gradient test card with a dark rectangle standing in for a 'phone' (no person)."""
    h, w = 240, 320
    x = np.linspace(40, 220, w, dtype=np.float32)[None, :]
    y = np.linspace(60, 200, h, dtype=np.float32)[:, None]
    img = np.stack([np.broadcast_to(x, (h, w)), np.broadcast_to(y, (h, w)), np.full((h, w), 150.0, np.float32)], axis=2)
    img = img.astype(np.uint8)
    img[120:200, 200:240] = (30, 30, 30)
    img[::40, :, :] = 255
    img[:, ::40, :] = 255
    img = np.ascontiguousarray(img)
    img.flags.writeable = False
    meta = FramePacketMeta(
        session_id=session_id, frame_id=frame_id, t_session_ms=t_ms, wall_time=wall, t_capture_mono_ns=0,
        width=w, height=h, source_mode=mode, source_id="synthetic:sample",
    )
    return FramePacket(meta=meta, image=img)


def build_sample(data_dir: Path, *, student_label: str = "student-demo", review_comment: str = "Телефон лежал на столе экраном вниз.") -> SqliteEvidenceStore:
    store = SqliteEvidenceStore(Settings(data_dir=data_dir), EvidenceConfig(min_free_disk_bytes=0))
    store.open()
    created = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=12)

    def wall(t: float) -> datetime:
        return created + timedelta(milliseconds=t)

    cal = CalibrationState(phase="completed", calibration_id="cal-sample", updated_at=wall(20_000))
    info = SessionInfo(
        session_id=SID, state="running", source=SourceConfig(mode="synthetic"), source_mode="synthetic",
        exam_id="demo-exam-1", student_label=student_label, retain_media=True, created_at=created,
        started_at=wall(30_000), exam_started_t_ms=30_000.0, calibration=cal, backend_version="0.1.0",
    )
    store.upsert_session(info)

    def base(kind: str, oid: str, t: float, frame_id: int | None, status: str = "ok") -> dict:
        return dict(observation_id=oid, session_id=SID, frame_id=frame_id, t_session_ms=t, wall_time=wall(t),
                    source_mode="synthetic", producer=PRODUCERS[kind], status=status, quality=0.8 if status == "ok" else None)

    synth = "СИНТЕТИКА: сценарные данные, не результат CV."
    end_t = 630_000.0

    def incident(iid, rule, cat, prio, t0, t1, summary, facts, caveats, obs, trigger=None, seq=1):
        return Incident(
            incident_id=iid, session_id=SID, rule_id=rule, category=cat, state="closed" if t1 is not None else "open", priority=prio,
            t_start_ms=t0, t_end_ms=t1, wall_start=wall(t0), wall_end=wall(t1) if t1 is not None else None,
            duration_ms=(t1 if t1 is not None else end_t) - t0, source_mode="synthetic",
            max_confidence=0.71 if cat == "phone" else None, mean_quality=0.8,
            explanation=Explanation(summary_ru=summary, facts=[ExplanationFact(**f) for f in facts], caveats_ru=caveats),
            observation_ids=obs, observation_count=len(obs), trigger_frame_id=trigger, rule_version="sample-rules-1",
            config_version="sample-config-1", end_reason="condition_cleared" if t1 is not None else None, update_seq=seq,
        )

    def fid(t: float) -> int:
        return int((t - 30_000.0) / 250.0)

    phone = incident("inc-phone-1", "phone_visible", "phone", "medium", 95_000, 101_000,
                     "Телефон виден 6,0 с; требуется проверка преподавателем.",
                     [dict(key="phone_visible_ms", value=6000.0, unit="ms", label_ru="Телефон виден")],
                     ["Обнаружение телефона не доказывает фотографирование экрана.", synth],
                     [f"ph-{fid(95_000)}", f"ph-{fid(96_000)}", f"ph-{fid(101_000)}"], trigger=fid(96_000))
    phone_open = phone.model_copy(update={"state": IncidentState.OPEN, "t_end_ms": None, "wall_end": None, "end_reason": None,
                                          "duration_ms": 1000.0, "observation_ids": phone.observation_ids[:2], "update_seq": 0})
    events: list[tuple[float, object]] = [
        (96_000, lambda: store.record_incident_change(IncidentChange(change="opened", incident=phone_open))),
        (96_000, lambda: store.capture_snapshot(SID, "inc-phone-1", synthetic_frame(SID, fid(96_000), 96_000.0, wall(96_000)))),
        (101_500, lambda: store.record_incident_change(IncidentChange(change="closed", incident=phone))),
        (150_000, lambda: store.record_observation(EnvironmentObservation(
            **base("environment", "env-sample-1", 150_000, None), action="shortcut_alt_tab", enforcement="blocked",
            mechanism="electron.before_input_event", scope="window", client_seq=1, client_wall_time=wall(149_990),
            detail=EnvironmentDetail(shortcut="Alt+Tab")))),
        (150_000, lambda: store.record_incident_change(IncidentChange(change="closed", incident=incident(
            "inc-env-1", "environment_blocked_action", "environment", "low", 150_000, 150_000,
            "Нажато Alt+Tab; действие заблокировано оболочкой.", [dict(key="action", value="shortcut_alt_tab", label_ru="Действие")],
            [synth], ["env-sample-1"])))),
        (209_500, lambda: store.record_incident_change(IncidentChange(change="closed", incident=incident(
            "inc-gaze-1", "gaze_prolonged_down", "attention", "low", 200_000, 209_000,
            "Голова опущена вниз 9,0 с (приблизительная оценка).", [dict(key="down_ms", value=9000.0, unit="ms", label_ru="Взгляд вниз")],
            ["Направление взгляда — приблизительная оценка, не eye-tracking.", synth],
            [f"at-{fid(200_000)}", f"at-{fid(209_000)}"])))),
        (300_000, lambda: store.record_observation(HealthObservation(
            **base("capture", "hl-1", 300_000, None, "error"), health=Health(component="capture", status="error", code="camera_disconnected")))),
        (306_000, lambda: store.record_observation(HealthObservation(
            **base("capture", "hl-2", 306_000, None), health=Health(component="capture", status="ok", code="ok")))),
        (306_000, lambda: store.record_incident_change(IncidentChange(change="closed", incident=incident(
            "inc-tech-1", "monitoring_degraded", "technical", "low", 300_000, 306_000,
            "Камера не передавала кадры 6,0 с: интервал не наблюдался.", [dict(key="gap_ms", value=6000.0, unit="ms", label_ru="Пропуск")],
            [synth], ["hl-1", "hl-2"])))),
        (480_000, lambda: store.record_observation(EnvironmentObservation(
            **base("environment", "env-sample-2", 480_000, None), action="focus_lost", enforcement="detected_only",
            mechanism="electron.blur", scope="window", client_seq=2, client_wall_time=wall(479_990),
            detail=EnvironmentDetail(duration_ms=3200.0)))),
        (483_500, lambda: store.record_incident_change(IncidentChange(change="closed", incident=incident(
            "inc-env-2", "environment_escape", "environment", "high", 480_000, 483_200,
            "Окно экзамена потеряло фокус на 3,2 с; блокировка не сработала (только зафиксировано).",
            [dict(key="focus_lost_ms", value=3200.0, unit="ms", label_ru="Без фокуса")], [synth], ["env-sample-2"])))),
        (601_000, lambda: store.record_incident_change(IncidentChange(change="opened", incident=incident(
            "inc-faces-1", "multiple_faces", "presence", "medium", 600_000, None,
            "В кадре два лица; эпизод не закрыт до конца сессии.", [dict(key="face_count", value=2, unit="count", label_ru="Лиц в кадре")],
            [synth], [f"at-{fid(600_000)}"], seq=0)))),
    ]
    for i, value in enumerate([["b"], ["a", "c"], "Ответ текстом: 42"], 1):
        events.append((60_000.0 * i, lambda i=i, value=value: store.save_answer(SID, f"q{i}", AnswerUpsert(value=value, client_seq=i))))
    events.sort(key=lambda e: e[0])

    phone_box = BBox(x_min=0.62, y_min=0.5, x_max=0.75, y_max=0.83)
    t = 30_000.0
    while t <= end_t:  # 10 minutes, phone and attention at 4 fps (sample density)
        frame_id = fid(t)
        phone_present = 95_000 <= t <= 101_000
        store.record_observation(PhoneObservation(
            **base("phone", f"ph-{frame_id}", t, frame_id),
            detections=[PhoneDetection(bbox=phone_box, confidence=0.71, class_name="cell phone (scripted)", class_index=67)] if phone_present else [],
            signals=[PhoneSignal(name="phone_visible", state="present" if phone_present else "absent", reason="scripted_synthetic")],
        ))
        if not (300_000 <= t <= 306_000):  # camera gap
            low_light = 400_000 <= t <= 410_000
            down = 200_000 <= t <= 209_000
            store.record_observation(AttentionObservation(
                **base("attention", f"at-{frame_id}", t, frame_id, "unknown" if low_light else "ok"),
                face_count=None if low_light else (2 if t >= 600_000 else 1),
                primary_face_present=None if low_light else True,
                head_direction=Direction.UNKNOWN if low_light else (Direction.DOWN if down else Direction.CENTER),
                gaze=None if low_light else GazeEstimate(direction="down" if down else "center", method="head_pose_only", calibrated=True, confidence=0.6),
                reasons=["low_light"] if low_light else [],
            ))
        while events and events[0][0] <= t:
            events.pop(0)[1]()
        t += 250.0
    store.upsert_session(info.model_copy(update={"state": SessionState.FINISHED, "finished_at": wall(end_t)}))
    # the teacher reviews after the exam; history is append-only
    store.add_review(SID, "inc-phone-1", HumanReviewCreate(decision="inconclusive", comment="Нужно пересмотреть снимок.", operator="teacher-demo"))
    store.add_review(SID, "inc-phone-1", HumanReviewCreate(decision="dismissed", comment=review_comment, operator="teacher-demo"))
    store.add_review(SID, "inc-env-2", HumanReviewCreate(decision="confirmed", comment="Переключение на другое окно подтверждено.", operator="teacher-demo"))
    return store


def main(argv: list[str] | None = None) -> int:
    from . import report

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        store = build_sample(Path(tmp))
        try:
            snap = store.export_snapshot(SID)
            html_bytes = report.render_html(snap).encode("utf-8")
            (args.out / "report.synthetic.html").write_bytes(html_bytes)
            (args.out / "report.synthetic.json").write_bytes(report.dumps_json(report.build_json(snap, html_bytes)))
        finally:
            store.close()
    print(f"wrote {args.out / 'report.synthetic.html'} and report.synthetic.json (SYNTHETIC sample)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
