# ADAL-QA-ALIGNMENT

Branch: `codex/adal-qa-alignment`, isolated worktree `Adal-qa-alignment`. Own scope: `proctoring/qa/**`, this handoff, and coordinator-authorized canonical `proctoring/contracts/**`. No classroom-contract, phone, attention, desktop, native or launcher edits. Coordinator owns publication; no agent push.

Requested A11 material at `research/proctoring-review-r2/A11` is absent in this checkout. There was no earlier handoff for this task.

## Checkpoint 1

Confirmed the 1.1 contract omission: A08 production summary exposes explicit `review_zone`, `review_zone_reasons_ru` (at most 3), and `review_zone_rule_version`; canonical SessionSummary lacked them. Added the typed fields/ReviewZone enum to the canonical model and regenerated JSON Schema/TypeScript artifacts. Unknown extra fields remain forbidden, and negative tests exercise both independent validators. READY validation now requires agreement among checked-in schema metadata, canonical Python constants and runtime handshake, rather than a stale literal 1.0.0.

QA fault injection now replaces A14 AudioMonitor before backend startup with `qa.fake_audio`. The double emits a labelled degraded health event but uses no PCM, microphone API, lease, audio model or worker thread. Self-check confirms no sounddevice/VAD import; the real backend LIVE-wire fixture confirms only labelled fake audio health. Camera unplug verification selects the capture component/code rather than whichever health event arrived last.

Validation: **124 passed in 47.66 s** across canonical contract tests, QA harness self-checks and the full fault-injection suite. Ran with the shared Python 3.12.14 interpreter, explicit own-worktree PYTHONPATH, task-only TEMP/TMP/basetemp, and scoped localhost-process escalation. No device/native interaction performed.

Next: review narrow offline preparation/LAN transport exemptions, prove the runtime audit still blocks public network, run complete QA, and remove only verified obsolete xfail markers. Files in this checkpoint are local; push remains coordinator-owned.

## Checkpoint 2

The static offline gate now reviews exact AST function scopes and call counts for phone/attention/audio/identity preparation and C2 upload/demo-teacher transport. No entire file is exempted; an extra call in an existing allowed scope or a call moved to another function fails. The fully offline runtime guard remains loopback-only, with classroom settings scrubbed by the harness; this does not alter production LAN transport. A real C2 upload call targeting TEST-NET-3 was blocked and logged by the guard before network access. **7 offline/self-check tests passed** (2.02 s).

Independently reran QA-OBS-003 (unknown exam question rejected) and QA-OBS-004 (deleted session inaccessible) with `--runxfail`: **2 passed**, so only those two obsolete markers were removed. Remaining markers still track lax int/bool coercion and uppercase Host behavior; full QA is next. No canonical production behavior was weakened and no network/device configuration was changed.

## Final validation

Full QA on committed source **`361e2445153de5c3035234331a616ee6e5887fa4`**: **380 PASS, 1 SKIP, 4 XFAIL, 0 FAIL, 0 XPASS** in 134.95 s. Both product tree and harness were clean. Evidence: `proctoring/qa/results/20261008T140835Z_adal_alignment_361e2445153d/` (pytest log, JUnit, JSON and Markdown summary, exact harness hashes). The 91 warnings are existing `record_property`/JUnit xunit2 compatibility warnings. Contract generator `--check` also passes.

Command from the repository root: shared Python `proctoring/qa/run_qa.py --label adal_alignment --expected-sha 361e2445153de5c3035234331a616ee6e5887fa4`, with own backend/contracts/qa PYTHONPATH and isolated task TEMP/TMP/pytest basetemp.

The skip is Linux-only network-namespace isolation. Remaining expected failures are the three lax typed-value cases (QA-OBS-005) and uppercase LOCALHOST (QA-OBS-006); they were reproduced in this run and retained. QA-OBS-003 is verified only for the unknown-question assertion actually covered, not every historical option/text claim. Physical capture, models, native enforcement and a release verdict are outside this automated run. No camera, microphone or native guards were activated.

Source checkpoints: `689cc1dc1ae5488a993bb1209cb6a72e403994b8`, `361e2445153de5c3035234331a616ee6e5887fa4`. Final evidence commit changes only QA reports/docs and this handoff, so tested source is unchanged. Next: coordinator merges this branch, checks frontend compatibility with the generated additive summary fields, and pushes. Agent has not pushed.
