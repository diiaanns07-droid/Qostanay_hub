# A04 — STATUS

Role: faces, presence, primary face, head pose, approximate gaze, calibration (`proctoring/backend/proctor/attention/`).
Branch: `claude/compassionate-goodall-8mxley` (platform-assigned). It was fast-forwarded from 7bccece to the BOOTSTRAP commit, and nothing was reset.
Contract/baseline: **BOOTSTRAP `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`** (A01 `claude/nifty-ride-ux8e4j`), contracts `qorgau.v1` 1.0.0, unchanged.
Previous checkpoint: none (first A04 commit). The new SHA is reported in the final message.
Stage: **checkpoint 1**: analyzer with the real MediaPipe Tasks FaceLandmarker, multi-face, primary face, head pose,
approximate gaze, 5-target calibration API, tests, handoff.

## Delivered
* `create_attention_analyzer(settings)` → `MediaPipeAttentionAnalyzer` (implements `AttentionAnalyzer`, `name="attention"`).
* Backend: **MediaPipe Tasks `FaceLandmarker`** (pinned `mediapipe==0.10.35`, no legacy `mp.solutions`), VIDEO mode,
  `num_faces=4`, blendshapes on, facial transformation matrix on. The local model is checked against
  `models.manifest.json` (size and sha256). Nothing is downloaded at runtime. A missing or invalid model or runtime gives
  `Health UNAVAILABLE` (`model_missing`/`model_invalid`/`runtime_unavailable`), never an exception.
* Face count and presence are separate from the **primary-face** decision. The primary face is chosen by geometry and time only
  (track continuation, dominance, calibration anchor). An ambiguous or uncertain choice gives `primary_face_present=null` and direction `unknown`. There is no identity and there are no embeddings.
* **Head pose** (yaw/pitch/roll, subject-centric, relative to the line of sight to the camera) comes from the FaceLandmarker
  transformation matrix. The **approximate gaze** fuses head pose with the iris offset (parallax-compensated) and eye blendshapes.
  `head_direction` and `gaze.direction` are separate signals: center/left/right/up/down/unknown, with quality, flags and reasons.
* One-Euro filtering, 150 ms debounce, hysteresis, and a blink hold of 350 ms. Episode durations are left to A05.
* **Calibration**: 5 screen targets, samples taken from real frames, settle 400 ms, 20 samples. Each target is checked for stability and the correct side/distinctness.
  Every failure is explicit with a message code, retry works, and completion is never decided by a timer. Everything is dropped at `end_session`, `cancel`, `skip` or `start`.
* Quality heuristics: low_light/overexposed/blur/small_face/small_eyes/partial_face/extreme_pose/uncalibrated. A dark or featureless
  frame without a face gives `face_count=null`/`unknown`, not 0.
* Tools: `python -m proctor.attention.model_tool fetch|verify` (offline preparation), `python -m proctor.attention.evaluate`
  (labelled frame folders → per-segment confusion and candidate false-alarm runs, for future volunteer clips).
* `testing.py`: a synthetic 3D face with known pose, eye rotation and blendshapes, plus `FakeFaceBackend`, `make_frame` (also usable by A01/A09).
* Handoff: `INTERFACE.md` (semantics for A05, calibration flow and RU/KK texts for A07), `DEPENDENCIES.txt` (requests to A01).

## Interfaces
* Provides `proctor.attention.create_attention_analyzer(settings) -> AttentionAnalyzer` (OWNERSHIP.json).
* Consumes `FramePacket` (BGR, read-only, never mutated) and `CalibrationTarget`. Emits `AttentionObservation` and `CalibrationState`.
* Imports only `proctor_contracts.v1`, `proctor_contracts.interfaces` and `proctor.settings.Settings`.

## Measured facts (Linux x86_64 cloud container, CPU, Python 3.12.3, mediapipe 0.10.35, 2026-10-08)
| What | Result |
|---|---|
| Legacy API | `mediapipe.solutions` absent. `mediapipe.tasks.python.vision.FaceLandmarker` present and used |
| Linux runtime | needs `libGLESv2.so.2`/`libEGL.so.1` (apt `libgles2 libegl1`), otherwise `OSError` at create → health `runtime_unavailable` |
| Built-in smoothing | **only with num_faces=1** in VIDEO mode: inter-frame jitter on a static noisy scene was 0.014 px with num_faces=1, 0.305 px with 2 and with 4. Therefore A04 does its own filtering |
| Second face | num_faces=1 never reports it. With num_faces=2/4 it is reported on the first frame it appears. In a 640×480 frame an inserted face about 83 px wide was detected and one about 62 px wide was **not** |
| Per-face score | the Tasks result has none, so `FaceBox.confidence = null` |
| Sign conventions | on public MediaPipe test images, matrix yaw has the opposite sign of the image nose offset and flips under horizontal mirror while pitch stays; roll ±88° on a 90°-rotated portrait, symmetric under flip |
| Iris parallax | 3D de-rotation with MediaPipe z did not remove the head-turn leakage (iris z ≈ flat). With uncorrected 2D offsets, 0.56–0.84 × head yaw leaked into "eye yaw" on 4 photos of people facing the camera, consistent with the anatomy estimate (≈0.73). Hence `iris_parallax=0.27` |
| Vertical eye cue | the vertical iris offset barely moved on a clear "eyes down" face (eyeLookDown ≈ 0.7). Blendshapes are used for eye pitch, with the iris as fallback |
| Blendshape side naming | `...Left` = the subject's left eye (consistent with iris direction on mirrored images) |
| Speed | analyzer about 13–15 ms/frame with 1 face, about 21 ms with 2, 3–11 ms with no face (640×480, this CPU; Windows laptop not measured) |
| Model cards | Apache-2.0. Out of scope per the cards: identity recognition/surveillance, faces beyond about 2 m, faces turned far away or less than half visible, life-critical decisions |

Public MediaPipe test images (`storage.googleapis.com/mediapipe-assets/`, not participants, **not committed**) were used only
for sign, count and smoke checks. **No accuracy, precision or false-alarm rate is claimed**: there was no camera and no recordings of people.

## Checks (commands from `proctoring/`)
| Command | Result |
|---|---|
| `.venv/bin/python -m pytest -q backend/proctor/attention/tests` | 172 passed, 4 skipped (public-image tests skip without `QORGAU_A04_TEST_IMAGES`) |
| same with `QORGAU_A04_TEST_IMAGES=<folder with public MediaPipe test images>` | 176 passed |
| `.venv/bin/python -m pytest -q` (whole repo) | 234 passed, 4 skipped, 2 failed: both are A01 tests that hard-code the bootstrap state (attention not integrated); see DEPENDENCIES R3 |
| `.venv/bin/python -m proctor smoke` | 34/34 PASS (synthetic; the synthetic pipeline uses A01's scripted attention, not A04) |
| `.venv/bin/python contracts/tools/generate.py --check` | up to date (contracts untouched) |
| `python coordination/verify_ownership.py --agent A04 --base 35bea4c…` | PASS |
| `.venv/bin/python -m proctor.attention.model_tool fetch --models-dir <tmp>` / `verify` | OK, sha256 matches the manifest |

## Not verified / limitations (honest)
* No camera, no Windows, no GPU, no recordings of real students. Nothing here is a measured gaze or false-alarm accuracy.
  Thresholds are engineering defaults (`config.py`, versioned by `config_version`).
* Gaze is approximate: the gains come from eyeball geometry, and the parallax term from anatomy plus 4 photos. Uncalibrated directions assume a laptop camera
  above the screen. Calibration makes classification relative to this person's screen edges, but not more precise than the landmarks.
* Down-looks with lowered lids rely on the eyeLookDown blendshape; this has not been measured on real exam behaviour.
* A head turned or bent strongly enough to lose the face reads as `face_count=0` with `face_lost_after_*`. A05 must not call that "left".
* Posters, photos, screens and reflections can count as faces. Faces smaller than about 80 px or farther than about 2 m may be missed.
* Live end-to-end through A01 is not possible yet: there is no capture module (A02), and synthetic sessions use A01's scripted analyzer.
* KK texts in INTERFACE.md are a draft and need a native speaker.

## Manual checks for the user / A09 (on the target laptop with a camera)
1. `python -m proctor.attention.model_tool verify` → OK. The backend health shows `attention: ok`.
2. Live session after A02: calibrate (5 points), then normal work, reading the long question and the bottom of the screen, a brief keyboard glance,
   a long look aside, glasses, weak light, leaving the frame, a second person entering. Record a replay and run `python -m proctor.attention.evaluate`
   on labelled frames. Use separate people and sessions for tuning and for evaluation, and label single-person results as such.
3. Mirror check: UI preview mirrored, the student turns to their right → `head_direction=right`.

## Integration order
A02 (capture) → **A04** (with A03) → A05 consumes `AttentionObservation` (TTL 1000 ms recommended) → A07 calibration UI (INTERFACE.md §2).

## Next (A04)
Apply the findings of the adversarial review (checkpoint 2). Once A02 replay exists, run the evaluation tool on recorded clips.
Optional: a static-face heuristic against poster/photo false positives; a self-adapting center for uncalibrated sessions.
