# A04 → A05 / A07 / A08 / A01: attention module interface and value semantics

Module: `proctor.attention` (owner A04). Entry: `create_attention_analyzer(settings) -> AttentionAnalyzer`
(`name = "attention"`). Wire type: `AttentionObservation` (contracts v1, unchanged). Producer:
`module="attention"`, `version=0.1.0`, `model_id="mediapipe-face-landmarker-f16-v1"`, `model_sha256`, `config_version=att-cfg-1-<hash>`.

**Gaze here is an approximate estimate from head pose plus eye landmarks. It is not eye tracking and not
evidence of cheating.** Nothing in this module identifies a person, stores images or landmarks, or decides
on sanctions.

## 1. One observation per analyzed frame

`process(frame)` returns exactly one `AttentionObservation` for every frame of the active session (an empty
list for a frame of another session, or when no session has started). Rate = `settings.attention_max_fps`
(15 by default); measured inference on a cloud CPU at 640×480: about 13–15 ms with one face and about 21 ms with two.
`observation_id = "att-<frame_id>-<session_id>"` (cut to 128 characters; it stays unique within a session
because `frame_id` comes first). `latency_ms` is the capture-to-observation time in the same process.

### Status (per observation)
| status | meaning |
|---|---|
| `ok` | `face_count` can be trusted; if a primary face exists, pose and direction were determined. Calibration may still be missing (flag `uncalibrated`). |
| `degraded` | `face_count` can be trusted, but pose or direction quality is limited (see `quality_flags`/`reasons`); direction may be `unknown`. Also used when faces are visible but the primary face is ambiguous or uncertain. |
| `unknown` | It is not known whether a face is present: `face_count = null` (dark or featureless frame). |
| `error` | The model is unavailable (`face_model_unavailable`) or inference failed on this frame (`inference_error`). |

### Fields
* `face_count`: the number of faces the model reported in this frame (up to `num_faces = 4`; `face_count_at_limit` when the limit is reached).
  It is `0` only when the frame itself was usable. It is `null` when the frame was too dark or featureless to tell (reasons `no_face_low_light`/`no_face_poor_image`).
  A missing frame produces no observation at all; that is a capture problem (A02 health), not absence of a person.
* `faces[]`: normalized bboxes in the unmirrored frame, taken from the landmarks and clipped to [0,1]. `confidence` is always `null`, because the
  Tasks API returns no per-face score. Exactly one face has `is_primary = true` when a primary face exists.
* `primary_face_present`: `true` = the student's face (the primary track) is visible; `false` = no face at all;
  `null` = faces are visible but it cannot be decided which one is the student (`primary_ambiguous`), or the
  only visible face does not continue the student's track yet (`primary_uncertain`, at most 1.5 s, after that the track is re-acquired with `primary_reacquired`).
* `head_pose`: degrees, filtered (One-Euro). It is measured **relative to the line of sight from the face to the camera**, not the
  camera axis, so a student who shifts sideways but keeps looking at the camera keeps yaw ≈ 0.
  +yaw = the student turns to **their** right, +pitch = up, +roll = tilt towards their right shoulder.
  The pinhole model assumes MediaPipe's default 63° vertical FOV, so the ray correction is approximate for other webcams.
* `head_direction`: the class from head pose only, relative to the calibrated head center (default thresholds ±22° yaw, +15° up, −18° down, 3° hysteresis).
* `gaze`: `direction`, `yaw_deg`/`pitch_deg` (fused head+eye angles in the same frame as `head_pose`), `confidence` (a heuristic
  estimator score: quality × method × calibration factor; **not a probability**), `method` = `fused` (iris and/or blendshape eye cue used)
  or `head_pose_only` (eyes closed or not measurable), `calibrated`.
* `gaze.direction`: classified against the screen edges measured in calibration: `center` = the gaze stays within the screen edges plus a margin
  (30 % of the center-to-edge span, at least 4°). `left/right/up/down` = beyond that margin. When calibration was skipped, generic laptop-camera edges are used
  and the flag is `uncalibrated` (lower confidence, also less reliable).
* `calibration_id`: set only when gaze is calibrated.

### Unknown is unknown
`gaze.direction`/`head_direction = unknown` when: there is no primary face, the pose is unavailable, face quality is below 0.3, or |roll| > 50°.
Unknown is never turned into `left/right/down` and never into "all clear". A05 should treat unknown, and observations older than the TTL, as a
coverage gap.

### Temporal behaviour (what A04 already does, so A05 does not double count)
* One-Euro filter on pose and gaze angles. Filters reset after a gap of more than 600 ms or after a primary-face switch.
* Debounce: a new direction must persist for **150 ms** of frame time before it is reported. This suppresses single-frame flicker and delays
  both the start and the end of a direction by about 150 ms, so durations are preserved.
* Hysteresis: a direction is entered above 1.0 × threshold and left below 0.85 × threshold.
* Blink: the last eye cue is held for up to 350 ms (reason `blink_hold`). After that the method becomes `head_pose_only` (reason `eyes_closed`).
* A05 owns episode durations, for example when "prolonged down/side" starts. A04 adds no duration logic.

### Reasons (machine codes; at most 16 per observation)
| code | meaning |
|---|---|
| `no_face_detected` | usable frame, no face (`face_count = 0`) |
| `no_face_low_light`, `no_face_poor_image` | no face found, but the frame is too dark or featureless to claim absence (`face_count = null`) |
| `face_lost_after_down` / `_side` / `_up` | the face disappeared within 1.5 s after a down/side/up direction (likely a strong head turn or bend, not someone leaving) |
| `multiple_faces` | `face_count ≥ 2` (counts every face the detector saw; posters, photos, TV and reflections can count) |
| `face_count_at_limit` | `face_count == num_faces`; more faces may be present |
| `primary_ambiguous`, `primary_uncertain`, `primary_reacquired` | see `primary_face_present` |
| `gaze_beyond_calibrated_<dir>_edge`, `gaze_beyond_generic_<dir>_edge` | why `gaze.direction` is not center |
| `head_turned_<dir>` | why `head_direction` is not center |
| `head_pitch_extreme_down` | head pitch < −35° (strong bend towards the desk or lap) |
| `blink_hold`, `eyes_closed`, `iris_unreliable`, `far_eye_ignored` | eye-cue handling |
| `low_face_quality`, `extreme_roll`, `pose_unavailable` | why direction is `unknown` |
| `face_model_unavailable`, `inference_error` | status `error` |

Quality flags: `low_light`, `overexposed`, `blur`, `small_face` (bbox height < 60 px), `small_eyes` (eye width < 14 px), `partial_face`
(> 8 % of landmarks outside the frame), `extreme_pose` (|yaw| > 45° or |pitch| > 35°), `uncalibrated`. All thresholds are in
`attention/config.py` and are tracked by `config_version`. They are engineering defaults, **not tuned on exam recordings**.

### Recommendations for A05 (A05 decides)
* Freshness TTL for attention observations: **1000 ms**. With no attention observation for 1 s, treat attention as stale/unknown.
* `face_missing`: use only `face_count == 0` with status `ok`. Never use `null`/`unknown`, and never a gap in observations. When `face_lost_after_*` is present,
  the explanation should say "лицо не видно (возможно, сильно отвернулся/наклонился)" rather than "ушёл".
* `multiple_faces`: require persistence (for example ≥ 2–3 s). False positives include photos, posters and screens in the background, and reflections.
  Second faces smaller than about 80 px (at 640×480) may be missed: in a measurement, a face about 62 px wide was not detected and one about 83 px wide was.
  The detector card lists faces farther than about 2 m as out of scope.
* `gaze_prolonged_down/side`: use `gaze.direction` from observations with status `ok`/`degraded`. When `gaze.calibrated == false`, lower the review priority
  and say so in the explanation. Reading the lower part of the screen should stay `center` after calibration. A brief keyboard glance is `down` for its real duration (about 150 ms of debounce).

## 2. Calibration (A07 UI → A01 routes → A04)

The flow: `POST calibration/start` → for each target, `POST calibration/target {target}`, then poll `GET calibration` (about 4 Hz) until that target is
`ok` or `failed` → `POST calibration/finish`. A01 publishes `CalibrationMsg` only on API calls, so progress during sample collection is visible
only by polling (see DEPENDENCIES request R2).

* Targets: `center`, `left`, `right`, `up`, `down`. These are **points on the screen** that the student looks at: center, and points near the left,
  right, top and bottom edges (for example 5 % from the edge). The student looks at each point naturally (head and eyes) and holds still for about 2 s.
  `left` is the student's left, which is the left side of the screen (the UI must not mirror target positions).
  Recommended order: center first, because edge targets are checked against it.
* A sample is counted only from frames of the current target, after a 400 ms settle, and only when there is exactly one face, quality ≥ 0.45, no extreme pose and
  open eyes. `required_samples = 20` (about 1.3 s at 15 fps plus the settle).
* A target becomes `ok` when it has enough samples, a stable fixation (robust spread ≤ 5°), and, for edges, the right side of center by ≥ 3°.
  Completion is never decided by a timer. A target **fails** after 12 s of frame time without enough samples. The failure carries the most frequent rejection reason.
* `finish`: `completed` only when all 5 targets are `ok` and they fit together. Otherwise `failed` (`targets_incomplete` / `targets_not_distinct`) and
  the session stays `calibrating`. The UI retries only the failed targets (`target` again) and then calls `finish` again. Completed targets are kept.
* `cancel` → `cancelled` (A01 → preflight, all data dropped). `skip(reason)` → `skipped` (generic edges, flag `uncalibrated`).
  `start` again → a new `calibration_id` and everything is reset. `end_session` drops all calibration data (privacy). Nothing is persisted.

### Texts for message codes (RU; KK is a DRAFT and needs review by a native speaker)
| code (target or phase) | RU | KK (черновик) |
|---|---|---|
| `select_target` | Посмотрите на точку на экране и удерживайте взгляд | Экрандағы нүктеге қарап, көзіңізді тоқтатыңыз |
| `collecting` | Смотрите на точку… идёт замер | Нүктеге қараңыз… өлшеу жүріп жатыр |
| `no_face` | Лицо не видно. Сядьте напротив камеры | Бет көрінбейді. Камераға қарсы отырыңыз |
| `multiple_faces` | В кадре больше одного лица. Калибровку проходит один человек | Кадрда бірнеше бет бар. Калибрлеуді бір адам өтеді |
| `low_light` | Слишком темно. Добавьте свет спереди | Тым қараңғы. Алдыңыздан жарық қосыңыз |
| `low_quality`, `poor_image` | Изображение нечёткое. Проверьте камеру и освещение | Сурет анық емес. Камера мен жарықты тексеріңіз |
| `eyes_closed` | Не закрывайте глаза во время замера | Өлшеу кезінде көзіңізді жұммаңыз |
| `extreme_pose` | Держите голову ровнее | Басыңызды түзу ұстаңыз |
| `primary_uncertain`, `pose_unavailable`, `inference_error` | Не удалось измерить. Попробуйте ещё раз | Өлшеу мүмкін болмады. Қайталап көріңіз |
| `unstable_fixation` | Взгляд не был неподвижен. Повторите, удерживая взгляд на точке | Көзқарас тұрақты болмады. Нүктеге қарап тұрып қайталаңыз |
| `target_wrong_side` | Похоже, вы смотрели в другую сторону. Повторите для этой точки | Басқа жаққа қарағандай болдыңыз. Осы нүктені қайталаңыз |
| `target_not_distinct` | Положение почти не отличается от центра. Посмотрите прямо на точку у края экрана | Орталықтан айырмашылық аз. Экран шетіндегі нүктеге тура қараңыз |
| `insufficient_samples`, `not_collected` | Недостаточно данных для этой точки. Повторите | Бұл нүкте үшін дерек жеткіліксіз. Қайталаңыз |
| `interrupted` | Замер прерван. Повторите эту точку | Өлшеу үзілді. Осы нүктені қайталаңыз |
| `targets_incomplete` | Не все точки пройдены. Повторите отмеченные | Барлық нүкте өтпеді. Белгіленгендерін қайталаңыз |
| `targets_not_distinct` | Точки не различаются. Повторите отмеченные | Нүктелер ажыратылмайды. Белгіленгендерін қайталаңыз |
| `calibration_ok` | Калибровка выполнена | Калибрлеу аяқталды |
| `skipped_by_operator` | Калибровка пропущена оператором: направление взгляда оценивается грубее | Калибрлеуді оператор өткізіп жіберді: көзқарас бағыты дөрекірек бағаланады |
| `cancelled` | Калибровка отменена | Калибрлеу тоқтатылды |

UI rules: show the per-target state (`pending/collecting/ok/failed`) and `samples/required_samples`. Show a **Retry** button for failed targets.
Never show "calibration complete" until `phase == completed`. Say plainly that calibration improves only the rough estimate of gaze direction.
