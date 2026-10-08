"""LIVE / replay measurement of the identity analyzer (owner: A13). Tool for people, not used by the runtime.

    python -m proctor.identity.live --camera 0 --seconds 40 --swap-at 20 --label "captain, then <name> (consent)"
    python -m proctor.identity.live --replay zone_a_green_01

Frames come through A02 FrameCaptureService.add_consumer exactly as in the product. The reference is taken in the
first seconds (as after RUNNING). Prints one line per analysis; the JSON report holds ONLY times, states,
similarity numbers and reasons - no frames, no face features. Default report dir: %LOCALAPPDATA%\\QorgauExam\\a13-live.
Similarity values from a few people are a factual record, NOT an accuracy measurement.
LIVE with a second person: only with that person's spoken consent; nothing is recorded.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from proctor.settings import Settings
from proctor_contracts.interfaces import SessionClock
from proctor_contracts.v1 import HealthStatus, IdentityObservation, SourceConfig, SourceMode

from .analyzer import IdentityAnalyzer
from .prepare import default_models_dir


def _local_dir(name: str) -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "QorgauExam" / name


def summarize(records: list[dict], swap_at_s: float | None) -> dict:
    def stats(rows: list[dict]) -> dict:
        sims = [r["similarity"] for r in rows if r["similarity"] is not None]
        states = [r["same_person"] for r in rows]
        out: dict = {"analyses": len(rows), **{s: states.count(s) for s in ("present", "absent", "unknown")}}
        if sims:
            out.update(similarity_min=min(sims), similarity_median=round(statistics.median(sims), 4), similarity_max=max(sims))
        return out

    longest, start = 0.0, None  # longest run of consecutive "absent" analyses (what a >= 3 s rule would see)
    for r in records:
        if r["same_person"] == "absent":
            start = r["t_s"] if start is None else start
            longest = max(longest, r["t_s"] - start + 1.0)
        else:
            start = None
    enrolled_at = next((r["t_s"] for r in records if r["enrolled"]), None)
    summary = {"all": stats(records), "enrolled_at_s": enrolled_at, "longest_absent_run_s": round(longest, 1)}
    if swap_at_s is not None:
        summary["before_swap"] = stats([r for r in records if r["t_s"] < swap_at_s])
        summary["after_swap"] = stats([r for r in records if r["t_s"] >= swap_at_s])
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m proctor.identity.live", description=__doc__.splitlines()[0])
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--camera", type=int, help="live camera index")
    src.add_argument("--replay", help="replay_id from the replay dir (consented clip)")
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--swap-at", type=float, default=None, help="live: cue (second) for the other person to sit down")
    ap.add_argument("--label", default="", help="who was in front of the camera (names + how consent was given)")
    ap.add_argument("--models-dir", type=Path, default=None)
    ap.add_argument("--replay-dir", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    from proctor.capture import create_capture_service  # noqa: PLC0415 - A02, only for this tool

    replay_dir = args.replay_dir or Path(os.environ.get("QORGAU_REPLAY_DIR") or _local_dir("replay"))
    settings = replace(Settings.from_env(), models_dir=args.models_dir or default_models_dir(), replay_dir=replay_dir)
    analyzer = IdentityAnalyzer(settings)
    health = analyzer.load()
    print(f"[{health.code}] {health.message}")
    if health.status != HealthStatus.OK:
        return 1
    if args.camera is not None:
        source, name = SourceConfig(mode=SourceMode.LIVE, camera_index=args.camera), f"live{args.camera}"
    else:
        source, name = SourceConfig(mode=SourceMode.REPLAY, replay_id=args.replay), args.replay
    session_id = f"a13-{name}"[:64]

    records: list[dict] = []
    lock = threading.Lock()

    def on_frame(frame) -> None:
        for obs in analyzer.process(frame):
            assert isinstance(obs, IdentityObservation)
            rec = {
                "t_s": round(obs.t_session_ms / 1000.0, 2),
                "same_person": obs.same_person.value,
                "similarity": obs.similarity,
                "enrolled": obs.enrolled,
                "face_score": obs.quality,
                "reasons": list(obs.reasons),
            }
            with lock:
                records.append(rec)
            sim = "  -   " if obs.similarity is None else f"{obs.similarity:+.3f}"
            print(f"t={rec['t_s']:6.1f}s  {rec['same_person']:8s} sim={sim} enrolled={str(obs.enrolled):5s} {','.join(obs.reasons)}", flush=True)

    capture = create_capture_service(settings)
    analyzer.start_session(session_id, source.mode)
    analyzer.exam_started(0.0)  # reference from the first good frames, as right after RUNNING
    capture.add_consumer("identity", on_frame, max_fps=4.0)
    clock = SessionClock()
    print("Reference: look at the camera for the first ~3 s (one person in the frame).")
    try:
        capture.open(session_id, source, clock)
    except Exception as exc:
        print(f"[capture_error] {type(exc).__name__}: {exc}")
        return 1
    t0 = time.monotonic()
    cued = False
    try:
        while (elapsed := time.monotonic() - t0) < args.seconds:
            if args.swap_at is not None and not cued and elapsed >= args.swap_at:
                print(f"\n>>> t={elapsed:.0f}s SWAP NOW: the first person leaves, the second person (consent given) sits down\n", flush=True)
                cued = True
            if source.mode == SourceMode.REPLAY and capture.health().code == "replay_ended":
                break
            time.sleep(0.2)
    finally:
        capture.close()
        analyzer.end_session()  # drops the reference + features
    with lock:
        rows = list(records)
    report = {
        "tool": "proctor.identity.live",
        "measured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": {"mode": source.mode.value, "camera_index": args.camera, "replay_id": args.replay},
        "label": args.label,
        "swap_at_s": args.swap_at,
        "config": {"match_threshold": analyzer.config.match_threshold, "interval_ms": analyzer.config.interval_ms,
                   "enroll_min_samples": analyzer.config.enroll_min_samples, "config_version": analyzer.config.config_version},
        "models": {k: v for k, v in health.details.items() if k.endswith(("_sha256_prefix", "license")) or k in ("detector", "embedder")},
        "note": "Factual similarity record for a few people; NOT an accuracy measurement. No frames or face features stored.",
        "summary": summarize(rows, args.swap_at),
        "records": rows,
    }
    out = args.out or _local_dir("a13-live") / f"{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"[report] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
