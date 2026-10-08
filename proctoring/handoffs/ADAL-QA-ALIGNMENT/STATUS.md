# ADAL-QA-ALIGNMENT

Branch: `codex/adal-qa-alignment`, isolated worktree `Adal-qa-alignment`. Own scope: `proctoring/qa/**`, this handoff, and coordinator-authorized canonical `proctoring/contracts/**`. No classroom-contract, phone, attention, desktop, native or launcher edits. Coordinator owns publication; no agent push.

Requested A11 material at `research/proctoring-review-r2/A11` is absent in this checkout. There was no earlier handoff for this task.

## Checkpoint 1

Confirmed the 1.1 contract omission: A08 production summary exposes explicit `review_zone`, `review_zone_reasons_ru` (at most 3), and `review_zone_rule_version`; canonical SessionSummary lacked them. Added the typed fields/ReviewZone enum to the canonical model and regenerated JSON Schema/TypeScript artifacts. Unknown extra fields remain forbidden, and negative tests exercise both independent validators. READY validation now requires agreement among checked-in schema metadata, canonical Python constants and runtime handshake, rather than a stale literal 1.0.0.

QA fault injection now replaces A14 AudioMonitor before backend startup with `qa.fake_audio`. The double emits a labelled degraded health event but uses no PCM, microphone API, lease, audio model or worker thread. Self-check confirms no sounddevice/VAD import; the real backend LIVE-wire fixture confirms only labelled fake audio health. Camera unplug verification selects the capture component/code rather than whichever health event arrived last.

Validation: **124 passed in 47.66 s** across canonical contract tests, QA harness self-checks and the full fault-injection suite. Ran with the shared Python 3.14 interpreter, explicit own-worktree PYTHONPATH, task-only TEMP/TMP/basetemp, and scoped localhost-process escalation. No device/native interaction performed.

Next: review narrow offline preparation/LAN transport exemptions, prove the runtime audit still blocks public network, run complete QA, and remove only verified obsolete xfail markers. Files in this checkpoint are local; push remains coordinator-owned.
