"""LIVE camera verification checklist (owner: A02). Run on the TARGET machine (Windows demo laptop).

    .venv\\Scripts\\python -m proctor.capture verify-live                       # automatic checks, ~1 min
    .venv\\Scripts\\python -m proctor.capture verify-live --interactive         # + mirror / unplug / busy steps
    .venv\\Scripts\\python -m proctor.capture verify-live --camera 1 --backend msmf --seconds 60

Close Qorgau Exam (and any app using the camera) first: this script opens the camera through
the same FrameCaptureService the backend uses. Results are PASS / FAIL / MANUAL / NOT_RUN per
check plus the measured numbers; a check that did not run is never reported as passed.
The JSON report is written OUTSIDE the repository (default: <data_dir>/a02-live/). With
--snapshot a JPEG of the camera view is saved next to it for the mirror check: it may show a
face — keep it local, never commit it.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from proctor_contracts.interfaces import CaptureError, SessionClock
from proctor_contracts.v1 import SourceConfig, SourceMode

from .measure import SimulatedConsumer, hardware_info, run_soak
from .service import FrameCaptureService
from .sources import CameraSource, cv2, default_backends

BACKENDS = {"auto": None, "dshow": "CAP_DSHOW", "msmf": "CAP_MSMF", "v4l2": "CAP_V4L2", "avfoundation": "CAP_AVFOUNDATION", "any": "CAP_ANY"}


def resolve_backends(name: str) -> list[int] | None:
    attr = BACKENDS.get(name)
    if attr is None:
        return None
    if cv2 is None or not hasattr(cv2, attr):
        raise SystemExit(f"backend {name!r} is not available in this OpenCV build")
    return [getattr(cv2, attr)]


class Checklist:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, check: str, status: str, detail: str = "", **data: Any) -> None:
        self.items.append({"check": check, "status": status, "detail": detail, **data})
        print(f"{status:8s} {check:24s} {detail}", flush=True)

    def worst(self) -> str:
        statuses = {i["status"] for i in self.items}
        return "FAIL" if "FAIL" in statuses else ("PASS" if statuses <= {"PASS"} else "INCOMPLETE")


def _ask(question: str, interactive: bool, timeout_s: float | None = None) -> str | None:
    if not interactive:
        return None
    try:
        return input(f"\n>>> {question} ").strip().lower()
    except EOFError:
        return None


def probe_cameras(max_index: int, backends: list[int] | None, camera_kwargs: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Which indices open and deliver a frame (each opened and released in turn)."""
    found = []
    kw: dict[str, Any] = {"probe_timeout_s": 2.0, **(camera_kwargs or {})}
    if backends:
        kw["backends"] = backends
    for index in range(max_index + 1):
        src = CameraSource(index, 640, 480, 30, **kw)
        t0 = time.monotonic()
        try:
            src.open(threading.Event(), time.monotonic() + 6.0)
        except CaptureError as exc:
            found.append({"index": index, "ok": False, "code": exc.code.value, "reason": exc.details.get("reason")})
            continue
        info = {"index": index, "ok": True, "open_ms": round((time.monotonic() - t0) * 1000, 1), **src.describe()}
        src.close()
        found.append(info)
    return found


def _wait(pred: Callable[[], bool], timeout_s: float) -> float | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        if pred():
            return time.monotonic() - t0
        time.sleep(0.05)
    return None


def verify_live(settings: Any, args: Any, camera_kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run the checklist. ``camera_kwargs`` (tests only) injects a fake device into CameraSource."""
    cl = Checklist()
    backends = resolve_backends(args.backend)
    report: dict[str, Any] = {
        "kind": "qorgau.a02.verify_live.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "hardware": hardware_info(),
        "requested": {"camera": args.camera, "width": args.width, "height": args.height, "fps": args.fps, "backend": args.backend},
        "default_backends": [str(b) for b in (backends or default_backends())],
    }
    print("A02 LIVE verification — real camera, real OS. Close other apps that use the camera.\n", flush=True)
    if cv2 is None:
        cl.add("opencv", "FAIL", "cv2 is not installed (install requirements/full.txt)")
        report["checks"] = cl.items
        return report

    # 1. which cameras exist
    cams = probe_cameras(args.max_index, backends, camera_kwargs)
    report["cameras"] = cams
    ok_idx = [c["index"] for c in cams if c["ok"]]
    cl.add("camera_enumeration", "PASS" if ok_idx else "FAIL", f"working indices: {ok_idx or 'none'}", cameras=cams)
    if args.camera not in ok_idx:
        chosen = next((c for c in cams if c["index"] == args.camera), None)
        cl.add("open_requested_camera", "FAIL", f"camera {args.camera}: {chosen}")
        report["checks"] = cl.items
        report["overall"] = cl.worst()
        return report

    svc_kwargs = dict(camera_kwargs or {})
    if backends:
        svc_kwargs["backends"] = backends
    svc = FrameCaptureService(settings, camera_kwargs=svc_kwargs or None)
    source = SourceConfig(mode=SourceMode.LIVE, camera_index=args.camera, width=args.width, height=args.height, fps=args.fps)
    events: list[tuple[float, str, str]] = []
    svc.set_health_listener(lambda h: events.append((time.monotonic(), h.status.value, h.code)))

    # 2. open + first frames
    t0 = time.monotonic()
    try:
        svc.open("verify-live", source, SessionClock())
    except CaptureError as exc:
        cl.add("open", "FAIL", f"{exc.code.value}: {exc.message}", details=exc.details)
        report["checks"] = cl.items
        report["overall"] = cl.worst()
        return report
    open_ms = (time.monotonic() - t0) * 1000
    h = svc.health()
    cl.add("open", "PASS", f"{open_ms:.0f} ms via {h.details.get('backend')}; actual {h.details.get('actual_width')}x{h.details.get('actual_height')} driver_fps={h.details.get('driver_fps')}", open_ms=round(open_ms, 1), health=h.details)

    # 3. frame format + preview
    got: list[Any] = []
    svc.add_consumer("verify", lambda p: got.append(p) if len(got) < 3 else None)
    _wait(lambda: len(got) >= 3, 5.0)
    if got:
        p = got[-1]
        fmt_ok = p.image.dtype == np.uint8 and p.image.ndim == 3 and p.image.shape[2] == 3 and not p.image.flags.writeable
        mean = float(p.image.mean())
        cl.add("frame_format", "PASS" if fmt_ok else "FAIL", f"{p.image.shape} {p.image.dtype} BGR read-only, mean brightness {mean:.0f}/255")
        if mean < 8:
            cl.add("not_black", "FAIL", "frames are black: lens cover / privacy shutter / camera used by another app?")
        if args.snapshot:
            out_dir = Path(args.out).parent if args.out else Path(settings.data_dir) / "a02-live"
            out_dir.mkdir(parents=True, exist_ok=True)
            snap = out_dir / "verify_live_snapshot.jpg"
            cv2.imwrite(str(snap), p.image)
            report["snapshot"] = str(snap)
    else:
        cl.add("frame_format", "FAIL", "no frames reached a consumer within 5 s")
    svc.remove_consumer("verify")
    prev = svc.latest_preview()
    if prev is not None:
        meta, data = prev
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        cl.add("preview_jpeg", "PASS" if img is not None else "FAIL", f"{meta.width}x{meta.height}, {len(data)} bytes, frame {meta.frame_id}")
    else:
        cl.add("preview_jpeg", "FAIL", "no preview")
    svc.close()

    # 4. sustained run with simulated consumers (measured fps / latency)
    print(f"\n... measuring {args.seconds:.0f} s with simulated consumers (phone-like 8 FPS x 60 ms, attention-like 15 FPS x 25 ms) ...", flush=True)
    soak = run_soak(settings, source, args.seconds, service=svc)
    report["soak"] = {k: v for k, v in soak.items() if k != "samples"}
    ev = soak["evaluation"]
    cl.add(
        "capture_fps",
        ev.get("capture_fps", "NOT_RUN"),
        f"measured mean {soak['capture_fps_mean']} FPS vs requested {args.fps} (target >= {int(80)}% of requested)",
    )
    worst_e2e = max((c["e2e_ms"]["p95"] or 0) for c in soak["consumers"].values())
    cl.add("e2e_latency", ev.get("e2e_p95", "NOT_RUN"), f"worst consumer e2e p95 {worst_e2e} ms (target <= 250 ms with simulated load)")
    cl.add("preview_fps", ev.get("preview_fps", "NOT_RUN"), "preview encoder keeps >= 10 FPS under load")
    cl.add("threads_released", ev["threads_released"], f"threads {soak['threads']}")
    svc.set_health_listener(lambda h: events.append((time.monotonic(), h.status.value, h.code)))  # run_soak replaced it

    # 5. restart x3 releases the device each time
    times = []
    restart_ok = True
    for i in range(3):
        t0 = time.monotonic()
        try:
            svc.open(f"verify-restart-{i}", source, SessionClock())
            first = _wait(lambda: svc.metrics().frames_captured > 0, 5.0)
            restart_ok &= first is not None
            times.append(round((time.monotonic() - t0) * 1000, 1))
        except CaptureError as exc:
            restart_ok = False
            times.append(f"{exc.code.value}")
        finally:
            svc.close()
    cl.add("restart_x3", "PASS" if restart_ok and svc.leaked_runs() == 0 else "FAIL", f"open+first frame ms: {times}")

    # 6. interactive / manual checks
    interactive = bool(args.interactive)
    if interactive:
        svc.open("verify-manual", source, SessionClock())
        if args.snapshot:
            print("A snapshot was saved (unmirrored camera view):", report.get("snapshot"))
        ans = _ask("Raise your RIGHT hand. In the saved snapshot / an unmirrored view it must appear on the image's LEFT. Correct? [y/n]", True)
        cl.add("unmirrored_view", "PASS" if ans == "y" else ("FAIL" if ans == "n" else "MANUAL"), "driver delivers an unmirrored image (CONTRACTS.md mirroring)")
        ans = _ask("Unplug the USB camera now (laptop: disable it in Device Manager), then press Enter.", True)
        t_unplug = time.monotonic()
        dt = _wait(lambda: svc.health().code == "camera_disconnected", 30.0)
        cl.add("disconnect_detected", "PASS" if dt is not None else "FAIL", f"health camera_disconnected after {dt and round(dt, 2)} s (frames stop -> capture health, not face_missing)")
        _ask("Plug the camera back in (or re-enable it), then press Enter.", True)
        dt2 = _wait(lambda: svc.health().code == "running", 60.0)
        cl.add("reconnected", "PASS" if dt2 is not None else "FAIL", f"running again after {dt2 and round(dt2, 2)} s, reconnects={svc.health().details.get('reconnects')}")
        svc.close()
        _ask("Open the Windows Camera app (keep it running), then press Enter.", True)
        try:
            svc.open("verify-busy", source, SessionClock())
            frames = _wait(lambda: svc.metrics().frames_captured > 5, 5.0)
            cl.add("busy_camera", "MANUAL", f"opened while another app runs; frames flowing: {frames is not None} (Windows may share the camera)")
        except CaptureError as exc:
            cl.add("busy_camera", "PASS", f"refused with {exc.code.value}/{exc.details.get('reason')} (expected CAMERA_BUSY or CAMERA_UNAVAILABLE)")
        finally:
            svc.close()
        _ask("Close the Camera app. Optional: turn OFF 'Let desktop apps access your camera' in Windows privacy settings, then press Enter (or just Enter to skip).", True)
        try:
            svc.open("verify-denied", source, SessionClock())
            cl.add("privacy_denied", "MANUAL", "camera opened: privacy switch was not turned off (or not effective)")
        except CaptureError as exc:
            cl.add("privacy_denied", "PASS" if exc.code.value == "CAMERA_DENIED" else "MANUAL", f"{exc.code.value}/{exc.details.get('reason')}")
        finally:
            svc.close()
        print("Remember to turn camera access back ON.")
    else:
        for name in ("unmirrored_view", "disconnect_detected", "reconnected", "busy_camera", "privacy_denied"):
            cl.add(name, "NOT_RUN", "run with --interactive on the target machine")

    report["health_events"] = [{"dt_s": round(t - events[0][0], 3), "status": s, "code": c} for t, s, c in events] if events else []
    report["checks"] = cl.items
    report["overall"] = cl.worst()
    print(f"\nOVERALL: {report['overall']} (LIVE on {report['hardware']['platform']})")
    return report


def write(report: dict[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":  # pragma: no cover
    from .__main__ import main

    sys.exit(main(["verify-live", *sys.argv[1:]]))
