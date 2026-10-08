"""A11 ISOLATED REPRO (not an integration run).

Feeds A05's golden scenarios (real FusionEngine @8f763a1) into A08's real SqliteEvidenceStore (@5509950)
in the order A01r2 (@29cadde) uses in SessionRuntime._fusion_loop/_emit:
    consume(obs) -> store.record_observation(obs) -> [advance()] -> for change: record_incident_change(change), publish
finish: engine.finish() changes recorded BEFORE upsert_session(finished).
Then compares the engine's final incident set with what the store returns, checks counters, missing refs,
and adds a teacher review to show what REST returns vs what the stream carried.
Writes stream.json (IncidentMsg envelopes as A01 publishes them) + rest.json (REST after review) for the TS replay.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from proctor.evidence import create_evidence_store
from proctor.evidence.config import EvidenceConfig
from proctor.fusion import create_incident_engine
from proctor.fusion.replay import final_incidents
from proctor.fusion.scenario import build_scenario, load_golden
from proctor.settings import Settings
from proctor_contracts.v1 import (
    HumanReviewCreate,
    IncidentChange,
    IncidentEndReason,
    SessionInfo,
    SessionState,
    utc_now,
)

HERE = Path(__file__).parent
GOLDEN = HERE / "combo/backend/proctor/fusion/tests/golden"
FIX = HERE / "combo/contracts/fixtures/v1"
STRIP = {"review_status", "evidence_ids"}


def run(path: Path, out_dir: Path | None = None, review: bool = False) -> dict:
    spec = load_golden(path)
    sid = spec.get("session_id", "fx-session-0001")
    mode = spec.get("mode", "synthetic")
    observations = build_scenario(spec)
    engine = create_incident_engine(sid, mode, None, config=spec.get("config"))
    tmp = tempfile.mkdtemp(prefix="a11-store-", dir=str(HERE / "tmp"))
    settings = Settings(data_dir=Path(tmp))
    store = create_evidence_store(settings, EvidenceConfig())
    h = store.open()
    assert h.status.value == "ok", h
    info = SessionInfo.model_validate_json((FIX / "SessionInfo.running.json").read_text())
    info = SessionInfo.model_validate(
        {**info.model_dump(mode="json"), "session_id": sid, "source_mode": mode, "source": {**info.source.model_dump(mode="json"), "mode": mode},
         "exam_started_t_ms": 0.0, "created_at": utc_now().isoformat(), "started_at": utc_now().isoformat()}
    )
    store.upsert_session(info)
    stream: list[IncidentChange] = []
    seq = 0
    envelopes = []

    def emit(changes):
        nonlocal seq
        for ch in changes:
            store.record_incident_change(ch)
            stream.append(ch)
            seq += 1
            envelopes.append({"seq": seq, "message": {"type": "incident", "change": json.loads(ch.model_dump_json())}})

    tick = 250.0
    next_tick = None
    for obs in observations:
        changes = engine.consume(obs)          # A01r2 order: consume first
        store.record_observation(obs)          # then record
        if next_tick is None:
            next_tick = obs.t_session_ms + tick
        if obs.t_session_ms >= next_tick:      # advance on the loop tick
            changes = changes + engine.advance(obs.t_session_ms)
            next_tick = obs.t_session_ms + tick
        emit(changes)
    emit(engine.finish(spec["finish"], IncidentEndReason.SESSION_FINISHED))
    store.upsert_session(info.model_copy(update={"state": SessionState.FINISHED, "finished_at": utc_now()}))

    engine_final = final_incidents(stream)
    stored = {i.incident_id: i for i in store.list_incidents(sid)}
    mismatch = []
    for iid, inc in engine_final.items():
        s = stored.get(iid)
        if s is None:
            mismatch.append((iid, "missing in store"))
        elif s.model_dump(exclude=STRIP) != inc.model_dump(exclude=STRIP):
            mismatch.append((iid, "body differs"))
    extra = sorted(set(stored) - set(engine_final))
    snap = store.export_snapshot(sid)
    counters = {k: v for k, v in snap.counters.items() if v}
    result = {
        "scenario": path.stem,
        "changes": len(stream),
        "engine_incidents": len(engine_final),
        "store_incidents": len(stored),
        "mismatch": mismatch,
        "extra_in_store": extra,
        "missing_observation_refs": snap.missing_observation_refs,
        "store_counters": counters,
        "summary_reviews_by_decision": snap.summary.reviews_by_decision,
        "summary_incidents_total": snap.summary.incidents_total,
        "post_close_updates": sum(1 for c in stream if c.change.value == "updated" and c.incident.state.value == "closed"),
    }
    if review:
        first = next(iter(engine_final))
        rv = store.add_review(sid, first, HumanReviewCreate(decision="confirmed", comment="a11 repro", operator="T1"))
        rest = store.list_incidents(sid)
        r0 = next(i for i in rest if i.incident_id == first)
        last_ws = [c.incident for c in stream if c.incident.incident_id == first][-1]
        summ = store.summary(sid)
        result["review"] = {
            "incident_id": first,
            "ws_last_update_seq": last_ws.update_seq,
            "ws_last_review_status": last_ws.review_status.value,
            "rest_update_seq_after_review": r0.update_seq,
            "rest_review_status_after_review": r0.review_status.value,
            "summary_incidents_total": summ.incidents_total,
            "summary_reviews_by_decision": summ.reviews_by_decision,
        }
        if out_dir is not None:
            (out_dir / "stream.json").write_text(json.dumps(envelopes, ensure_ascii=False))
            (out_dir / "rest_incidents.json").write_text(json.dumps([json.loads(i.model_dump_json()) for i in rest], ensure_ascii=False))
            (out_dir / "rest_summary.json").write_text(summ.model_dump_json())
            (out_dir / "session_id.txt").write_text(sid)
    store.close()
    return result


if __name__ == "__main__":
    (HERE / "tmp").mkdir(exist_ok=True)
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    review_target = sys.argv[1] if len(sys.argv) > 1 else "g03_phone_with_gaze_down_correlated"
    for p in sorted(GOLDEN.glob("*.json")):
        r = run(p, out_dir=out if p.stem == review_target else None, review=p.stem == review_target)
        print(json.dumps(r, ensure_ascii=False))
