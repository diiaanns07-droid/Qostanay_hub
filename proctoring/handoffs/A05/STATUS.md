# A05 — STATUS (fusion: observations → explainable incidents)

Role: A05, rules/episode engineer. Branch: `claude/zen-mayer-e0tivt` (platform-assigned, fast-forwarded to the
BOOTSTRAP commit, no other history). Contract/baseline: BOOTSTRAP `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`
(A01, `claude/nifty-ride-ux8e4j`, contracts `qorgau.v1` 1.0.0). Previous A05 checkpoint: none (this is the first).
Stage: **checkpoint 1 — engine + replay + golden tests done; no CV data yet** (SYNTHETIC/fixture only).

## Changed paths (A05 only)
* `proctoring/backend/proctor/fusion/` — `__init__.py` (factory), `engine.py`, `config.py`, `explain.py`,
  `episode.py`, `replay.py` (+ CLI), `scenario.py` (observation builder from contract fixtures),
  `tests/` (`test_engine.py`, `test_golden.py`, `test_replay_cli.py`, `checks.py`, `golden/g01…g10.json`).
* `proctoring/handoffs/A05/` — this file, `DEPENDENCIES.txt`, `EXPLANATION_TABLE.md` (for A10).

## Interface
* `proctor.fusion.create_incident_engine(session_id, source_mode, settings, *, config=None) -> FusionEngine`
  — implements `proctor_contracts.interfaces.IncidentEngine` (`consume / advance / set_paused / finish /
  config_snapshot`, `rule_version`, `config_version`). Emits contract `IncidentChange` only. Pure: no camera,
  UI, DB, clock or files. Extras (not in the Protocol): `stats()` (ignored-input counters).
* `rule_version = a05-rules-1.0.0`; `config_version = a05-default-1+<sha12 of the thresholds>` (any override
  changes it). `config_snapshot()` = rule/config version + every threshold + "hypotheses, not norms".
* Replay/tuning: `python -m proctor.fusion.replay <observations.jsonl|json> [--mode] [--tick-ms] [--finish-ms]
  [--config overrides.json] [--json]`. Builder for tests/demos: `proctor.fusion.scenario`.

## Rules (default thresholds are hypotheses for tuning)
Continuous rules (hysteresis): open when the condition is observed from t_first to t_last with
`t_last − t_first ≥ min_duration` and `≥ min_count` true observations (pending run restarts after a gap
> `pending_gap`); close `condition_cleared` at the first false observation ≥ `merge_gap` after the last true one
(`t_end` = last true observation); a return within `merge_gap` is the same incident (`appearances` + 1).

| Rule (source) | min_duration / min_count / pending_gap / merge_gap | base priority, escalation |
|---|---|---|
| phone_visible (A03 signal) | 1000 / 3 / 500 / 2000 ms | medium; ≥10 s +1; gaze «down» ≥1 s inside → high |
| phone_raised (A03 signal) | 0 / 1 / 500 / 5000 | medium |
| possible_screen_capture (A03, `insufficient_evidence` = unknown) | 500 / 2 / 500 / 5000 | high |
| gaze_prolonged_down (A04 gaze, head fallback) | 3000 / 3 / 400 / 1500 | low; ≥10 s +1; phone visible ≥1 s inside → high |
| gaze_prolonged_side (left/right, subject-centric) | 3000 / 3 / 400 / 1500 | low; ≥10 s +1 |
| face_missing (`face_count = 0`) | 2000 / 3 / 500 / 2000 | medium; ≥15 s +1 |
| multiple_faces (`face_count ≥ 2`) | 1000 / 3 / 500 / 3000 | high |
| environment_blocked_action (A06 shortcuts, blocked windows/navigation/devtools/clipboard, display change) | one incident per burst (gaps ≤ 10 s) | low; any non-blocked +1; ≥5 actions +1 |
| environment_escape (focus_lost…regained, foreign_window_foreground) | merge 3 s | medium; without focus ≥5 s +1; ≥3 losses +1 |
| monitoring_degraded (health of any component, stale source > TTL, undetermined ≥ 3 s, enforcement_error, exam_mode_released) | one incident per coverage gap, merge 3 s | low; any cause ≥30 s +1 |

All continuous rules: ≥3 appearances in one incident +1. Escalation reasons are listed in fact `priority_basis`.
Links (`related_incident_ids`, overlap or gap ≤ 1 s): phone_visible ↔ phone_raised ↔ possible_screen_capture ↔
gaze_prolonged_down. Correlation raises **review priority only**; caveat says it proves nothing.

## Policies (time, unknown, late)
* Only `t_session_ms` orders/measures. `wall_*` = anchor + t (anchor from the first observation): a wall-clock
  jump cannot reorder or produce `wall_end < wall_start`.
* Late/out-of-order: per stream (phone, attention, environment, health.<component>) an observation older than the
  stream head is dropped and counted (`late`); equal timestamps accepted; cross-stream disorder is harmless (each
  rule reads one stream, correlation uses intervals). Duplicate `observation_id`, other session, other
  `source_mode` (LIVE never takes fixtures) → ignored and counted.
* Stale: TTL phone 2000 ms, attention 1500 ms (startup/resume grace 5000 ms). A stale source stops participating;
  its open episodes close `source_lost` (never `condition_cleared`), plus a `monitoring_degraded` gap incident.
  An observation already older than W − TTL on arrival is not evidence (`stale_on_arrival`).
* Unknown (`unknown/error` status, quality < 0.2, `face_count=None`, `Direction.unknown`, signal
  unknown/insufficient_evidence) never opens, extends or clears an episode; ≥ 3 s of it is a coverage gap.
* Pause: everything closes `session_paused`, input ignored, freshness restarts on resume; health problems persist
  across the pause. `finish` closes everything with the given reason (episode time = last evidence; focus still lost
  / gap still active run to the finish time), is idempotent, later input → `[]`.
* Replay mode: `advance()` is a no-op — the recording is the clock.
* Determinism: incident times/facts come from data only; ids are per-rule counters. Updates are data-driven
  (≥ 5 s of episode time, priority change, new appearance, link) — never per tick.

## Checks run (Linux x86_64 container, Python 3.12.3, venv from `requirements/full.txt`; no camera/Windows/GPU)
| Command (from `proctoring/`) | Result |
|---|---|
| `python -m pytest -q backend/proctor/fusion` | **88 passed** |
| `python -m pytest -q` | 150 passed, **2 failed** — A01's `test_health_reports_missing_modules_honestly` and `test_live_never_falls_back_to_synthetic` assert that fusion is NOT integrated (see DEPENDENCIES [A01-1]) |
| same, with those 2 deselected | 62 passed (contracts + A01 lifecycle/API) |
| `python -m proctor smoke` | **34/34 PASS** with the A05 engine in the real backend process (preflight fusion=pass, phone incident on stream «Телефон виден 1,1 с…», 159 stream envelopes valid) |
| `python contracts/tools/generate.py --check` | PASS (contracts untouched) |
| `python coordination/verify_ownership.py --self-test` / `--agent A05 --base 35bea4c…` | PASS / PASS (see commit) |

What the fusion tests prove: every emitted change validates in Pydantic + JSON Schema; lifecycle invariants
(opened → updates → one closed, gap-free `update_seq`, post-close updates touch only links, symmetric links);
every number in `summary_ru` is a rendered fact; no probability/guilt wording; 10 golden scenarios on the shared
contract fixtures with hand-derived expectations; identical final incidents for advance cadence none/50/250/1000 ms
and a 300 ms clock lead; identical incidents when phone results arrive 90 ms after attention; edge cases: one long
sequence = one episode, brief glance ≠ prolonged, duplicates/same timestamps, wall clock jumps back 1 h,
out-of-order, dropped frames vs TTL hole, foreign session/mode, source vanishes, startup silence, correlation and
link boundaries (exactly 1000 ms), min-duration boundary, finish/abort/pause/resume, replay ignores ticks,
50 key presses = 1 incident, 30 min session < 20 s CPU (worst case with long open episodes ≈ 60 µs/observation).

## Not verified / limitations
* No real CV data: A03/A04 are not integrated; all thresholds are untuned hypotheses. Golden/fixture results say
  nothing about detection accuracy.
* No Windows / live camera / Electron run. `summary_kk` is not produced (needs a language reviewer).
* The full config snapshot is not yet stored in reports (needs an A01→A08 path, [A01-2]).

## Integration (order per OWNERSHIP: A02 → A03/A04 → **A05** → A08 …)
A01 only needs to merge `proctoring/backend/proctor/fusion/`; the registry already discovers
`proctor.fusion.create_incident_engine`. Then adjust the 2 A01 tests ([A01-1]). No new dependencies.

## Next (A05)
1. Tune thresholds on real A03/A04 observation logs (replay CLI) once those modules publish; record measured
   episode-level results separately from fixture tests.
2. Link `source_lost` episodes to the covering `monitoring_degraded` incident; per-track phone grouping if A03
   provides stable track ids.
3. Config snapshot in the report once [A01-2] is decided; optional Settings-based overrides ([A01-3]).
