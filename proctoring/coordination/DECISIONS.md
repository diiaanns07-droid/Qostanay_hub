# Architecture decisions v1 (A01, 2026-10-08)

## Stack (one compatible configuration, CPU mandatory)

| Layer | Choice | Pinned | Why / verified |
|---|---|---|---|
| OS target | Windows 10/11 x64 (demo laptop) | — | Linux x86_64 used by cloud agents/CI only |
| Python | CPython **3.12** x64 | `requires-python >=3.12,<3.13` | numpy 2.4/onnxruntime/mediapipe wheels for win_amd64 exist (checked on PyPI); 3.13+ not guaranteed for mediapipe binary |
| Backend API | FastAPI + uvicorn + websockets | 0.141.1 / 0.53.0 / 17.1 | loopback HTTP + WS; pydantic 2.13.5 contracts |
| Arrays | numpy | 2.4.6 | |
| Video/cv2 | **opencv-contrib-python** only | 4.13.0.92 | required by mediapipe; never install opencv-python next to it (both ship `cv2`) |
| Faces | MediaPipe **Tasks FaceLandmarker** | mediapipe 0.10.35 | verified: `mediapipe.tasks.python.vision.FaceLandmarker` present, **legacy `mp.solutions` absent**. 1.x not chosen: newer major, adds a privacy notice — re-evaluate only on request |
| Phone | YOLOv8n/YOLO11n (COCO `cell phone`) exported to ONNX, inferred with **onnxruntime CPU** | onnxruntime 1.29.0 | light (no torch at runtime), deterministic offline. Ultralytics (AGPL-3.0, pulls torch, may fetch assets) only in a separate prep venv for export; A03 decides and records license/sha256 in the manifest |
| Storage | SQLite (stdlib `sqlite3`) | — | A08 |
| Desktop | Electron + TypeScript + React + Vite; esbuild bundles main/preload | electron 43.7.5, typescript 5.9.3, react 19.2.8, vite 7.3.6, @vitejs/plugin-react 5.2.0, esbuild 0.28.2, ws 8.21.3 | all releases ≥ 2 weeks old at pin time; Electron 43 = supported stable line since 2026-06 |
| Locks | `proctoring/uv.lock` (win_amd64 + linux x86_64), `requirements/*.txt` (hashes, for pip), `desktop/package-lock.json` | | A01 is the only author |

Not chosen: cloud APIs/LLMs (forbidden in the mandatory path), torch at runtime, browser getUserMedia (one camera
owner = backend A02), generic IPC passthrough.

## Process model

```
Electron main (A06) ──spawn, token via stdin──► python -m proctor serve (A01 composition root)
   │  ▲ READY line (port)                         ├─ capture (A02) ─ frames ─► phone (A03), attention (A04)
   │  └─ HTTP + WS (Bearer) ◄─────────────────────┤  observations ─► fusion thread (A05 engine) ─► incidents
   │                                               ├─ evidence store + review/report routes (A08)
   ▼ contextBridge window.qorgau (fixed methods)   └─ environment events from main (A06) ─► fusion
Renderer (A07)  — never sees token/port, never opens a camera
```

## Key rules fixed by A01
* One non-terminal session per backend; capture opened/closed only by the session lifecycle.
* Session timeline `t_session_ms` is the only ordering clock; wall time is derived.
* Unknown ≠ violation ≠ all-clear. Priority = review priority, not guilt. No automatic sanctions.
* Synthetic/bootstrap parts never serve live/replay; LIVE preflight fails instead of falling back.
* Environment protection is required for LIVE start: the shell must report a capability matrix
  (blocked / detected_only / unsupported / unverified) — partial protection stays visible as partial.
* Pause is an operator action; it releases restrictions and is a coverage gap.

## Known gaps at BOOTSTRAP (honest)
* No CV, capture, fusion rules, storage, shell or UI implemented yet — owners A02–A08.
* Electron binary download failed in the cloud sandbox; `npm ci` on Windows downloads it (to verify by A06/A09).
* A JS unit-test runner is not pinned yet (vitest 4.1.11 triggered an npm arborist crash); request via DEPENDENCIES.
* `replay` pacing (realtime vs as-fast-as-possible) is not yet a `SourceConfig` field — A02 may request it.
