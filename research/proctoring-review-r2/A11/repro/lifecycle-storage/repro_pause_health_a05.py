"""A11 repro (variant with REAL A05 engine, combo2 = combo + A05 8f763a1 fusion): capture health recovery delivered while PAUSED never reaches A08 -> stale gap until session end.

ISOLATED REPRO (combo tree A01r2 29cadde + A08 5509950), synthetic bootstrap pipeline.
The synthetic capture does not emit health, so the A02 listener call is reproduced by invoking the exact callback
A01 registers with capture.set_health_listener() (SessionRuntime._on_capture_health, session.py:447/610).
A02 @7769f13 emits health only on change (service.py _set_health_locked -> `if changed`), so there is no re-send.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from proctor_contracts.v1 import Component, Health, HealthStatus, ObservationMsg

import repro_common
from repro_common import client, evidence_store, make_app, record_stream, running_synthetic
repro_common.HIDE_ALL_BUT_EVIDENCE.pop("fusion")  # use the REAL A05 engine from combo2

base = Path(sys.argv[1])
data = base / "data"
shutil.rmtree(data, ignore_errors=True)
app = make_app(data)
seen = record_stream(app)
with client(app) as c:
    sid = running_synthetic(c)
    rt = app.state.proctor["manager"].runtime(sid)
    time.sleep(1.0)
    t_disc = rt.clock.now_ms()
    rt._on_capture_health(Health(component=Component.CAPTURE, status=HealthStatus.UNAVAILABLE, code="camera_disconnected",
                                 message="camera lost (simulated A02 listener call)"))
    time.sleep(1.0)
    c.post(f"/v1/sessions/{sid}/pause", json={"reason": "teacher pause"})
    time.sleep(0.5)
    t_ok = rt.clock.now_ms()
    rt._on_capture_health(Health(component=Component.CAPTURE, status=HealthStatus.OK, code="running",
                                 message="camera back (simulated A02 listener call)"))
    ev = {"action": "exam_mode_released", "enforcement": "allowed", "mechanism": "electron.kiosk", "scope": "window",
          "client_seq": 7, "client_wall_time": "2026-10-08T09:00:05Z",
          "detail": {"shortcut": "session_paused", "process_name": None, "duration_ms": None}}
    ack = c.post(f"/v1/sessions/{sid}/environment/events", json={"session_id": sid, "events": [ev]}).json()
    time.sleep(0.5)
    c.post(f"/v1/sessions/{sid}/resume")
    time.sleep(4.0)
    fin = c.post(f"/v1/sessions/{sid}/finish").json()
    rep = c.get(f"/v1/sessions/{sid}/report.json").json()
    stream_health = [(m.observation.health.status.value, m.observation.health.code) for m in seen
                     if isinstance(m, ObservationMsg) and m.observation.kind == "health"]
    stored_health = [(o["health"]["status"], o["health"]["code"], round(o["t_session_ms"])) for o in rep["observations"]
                     if o["kind"] == "health"]
    out = {
        "t_disconnect_ms": round(t_disc), "t_camera_ok_ms (during pause)": round(t_ok),
        "window": rep["coverage"]["window"],
        "pauses": rep["coverage"]["pauses"],
        "stream_health_observations": stream_health,
        "A08_stored_health_observations": stored_health,
        "env_event_during_pause_ack": ack,
        "env_event_during_pause_in_A08_report": any(o["observation_id"] in ack["observation_ids"] for o in rep["observations"]),
        "engine_label": app.state.registry.factories.get("fusion").__module__,
        "A08_incidents": [(i["incident"]["rule_id"], i["incident"]["state"], round(i["incident"]["t_start_ms"]), i["incident"].get("t_end_ms") and round(i["incident"]["t_end_ms"]), i["incident"].get("end_reason"), i["incident"]["explanation"]["summary_ru"][:90]) for i in rep["incidents"]],
        "A08_capture_gaps": [g for g in rep["coverage"]["gaps"] if g["component"] == "capture"],
    }
print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
