# Model / episode quality protocol (prompt item 6) — draft

Owner: A09. Goal: honest, reproducible numbers per signal and per episode rule — never one averaged "accuracy".

## Data

* **Holdout clips** recorded by the team with written consent (no real students), stored outside Git, referenced by
  replay manifest id + SHA256 (A02 format, A10 content). Separate people/rooms/lighting from any clip used to tune
  thresholds (per-person split). Synthetic frames/fixtures are **never** part of a quality number.
* Each clip has a label file (who labels, when): episode intervals per rule (`phone_visible`, `phone_raised`,
  `possible_screen_capture`, `gaze_prolonged_down`, `gaze_prolonged_side`, `face_missing`, `multiple_faces`) and
  "normal behaviour" segments (reading, typing, thinking, drinking, glancing at keyboard).

## Metrics (event level, with denominators)

* Matching: a predicted episode matches a labelled one if their intervals overlap ≥ 1 frame and the rule is the
  same (stricter IoU variant reported separately).
* Per rule: TP, FP, FN → recall = TP/(TP+FN), precision = TP/(TP+FP), each printed with counts (e.g. "8/10").
* False episodes per 10 minutes of labelled normal behaviour (per rule).
* Latency: episode open time − labelled start (median, p95, n).
* `unknown`/`insufficient_evidence` share per clip (coverage), reported — not counted as correct.
* Breakdown by condition: lighting (normal/low), glasses, distance, camera angle, mirrored preview on/off.

## Run

Replay clips through the **same** pipeline (`source_mode=replay`) on the candidate SHA via `/v1` (create replay
session → preflight → skip/complete calibration → start → wait clip end → finish → read incidents) and compare with
labels by a script in `qa/` (to be added when A02 replay + A05 rules are integrated). Results go to `qa/RESULTS.md`
with clip ids, SHA, settings snapshot (`config_snapshot()` of the engine) and model manifests.

## Wording rules for any report/pitch number

"phone visible episodes: recall 9/10, precision 9/11 on 12 holdout clips (3 people), 0.4 false episodes per 10 min
of normal behaviour" — fine. "99 % accuracy", "probability of cheating", "detects photographing" — not allowed.
