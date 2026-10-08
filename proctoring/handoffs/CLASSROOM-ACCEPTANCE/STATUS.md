# Classroom acceptance and teacher workflow

Branch: `codex/classroom-acceptance`. User authorized autonomous integration and UI/UX improvements.
Separate worktree; active agents' branches and original files are not changed.

## Integrated snapshot

- Captain integration `2a6e87e` (native helper, latest T03/T05).
- T01 `8c94f3a` (actual C1 server).
- T04 `8f0b67c` (control UI).
- A07-student `ae41e1e` (student overlays).
- Latest captain `f46565c`: optional upward calibration target for laptop webcams; **19 calibration tests
  PASS** on the integrated tree. Final desktop build/typecheck PASS after this merge.

## Teacher panel checkpoint

Changed only `proctoring/class-panel/` and this handoff:
- Session creation and join code through C1's actual session API, not curl.
- Existing class replacement requires explicit acknowledgement; this does not finish external exams.
- Site policy is labelled configuration, not proof of enforcement.
- View options moved behind a disclosure. Main surface focuses on cards and attention queue.
- Removed developer-facing protocol text and absent feature placeholders.
- Student detail keeps connection diagnostics collapsed; background is inert during the dialog.
- More restrained typography, spacing and status colours; source/demo labels retained.
- Panel meta CSP permits same-origin media (C1's HTTP CSP must be updated by its owner when mounting video).

Checked: strict JS TypeScript check PASS; 25 panel unit tests PASS, including source/frame provenance.
Actual C1 + Chrome session UI: 39/39 PASS (Windows, local loopback). Screenshots inspected separately.
Panel data flow/keyboard/load checks functionally pass; the first browser run also detected local antivirus
CSP injection and a missing favicon. The favicon is fixed; the test now reports verified antivirus
injection separately, without ignoring application errors. Full rerun pending.

Second checkpoint: source labels for each student/frame/event; absent source means unknown,
earlier synthetic frames keep their label after a switch to live. DEMO explicitly supplies simulated.
Localized invalid-address and network errors. Login link on expired teacher authentication.
Desktop npm ci/build/typecheck PASS. Shell test exposed one old synthetic incident count mismatch;
assigned to recovery agent for investigation, not counted as passing yet.

## Independent acceptance work

`codex/classroom-chain-check`: real C1 + two full synthetic backends (no webcam).
`codex/classroom-windows-launch`: launch/check-only scripts.
`codex/classroom-client-recovery`: stale resume and initial class_state replay fixes.

## Known boundaries

T01 feature adapters for T03/T04/T05 are still being developed externally. Source merge alone is not mounting.
No claim of 100 real cameras, working WebRTC in Electron, or complete external-site confinement.
Current C2 still acknowledges lock/mic flags before actual renderer/device confirmation.
Synthetic backend provenance was corrected by c3c1329; initial/legacy unknown sources remain unknown.

## Final acceptance on Windows (2026-10-08)

- Actual C1 + two complete `proctor serve` / C2 processes: **15/15 PASS** after provenance fix.
  Sanitized evidence: `proctoring/acceptance/classroom/chain/windows-integration-result.json`.
  Includes targeted commands, distinct student event histories, previews, C1 crash/restart, student restart,
  persistence and bounded cleanup. Synthetic input; loopback network.
- Teacher panel: **61/61 browser checks PASS**, 2/30/100 synthetic cards, focus, filters, reconnect,
  layout, reduced motion, mock-adapter errors. 100-card render p95 = 2.4 ms on this machine.
  10 CSP console warnings attributed to observed Kaspersky DOM injection were counted separately.
- Real C1 + Chrome teacher form: **39/39 PASS** (no mock API), 390/1366 widths.
  Screenshot masking now replaces DOM text temporarily, since the strict CSP blocked injected CSS.
- Panel unit tests: **25 PASS**; strict JS checking PASS.
- C1/class contracts/C2 tests: **150 PASS** (source-owner run); generated schemas/42 fixtures/TS PASS.
- Windows launchers: **9/9** on PowerShell 5.1 and **9/9** on PowerShell 7 (source-owner runs).
- Desktop build/typecheck PASS. Full shell suite: **80 PASS, 1 platform-specific SKIP**, no failures.
- Student preparation UX: two fixture viewport runs, 11 checks each, PASS; these are not real CV tests.
- Actual Electron + actual backend startup PASS. No session/camera/microphone started.
  Real startup exposed a stale preflight error: health requests now wait for readiness, rerun on state
  changes, and ignore late results from the previous effect. Preflight reloads after recovery.
  Portable regression: `desktop/renderer/tests/electron-startup.mjs` (build first; set QORGAU_PYTHON
  and PLAYWRIGHT_MODULE where needed). It checks visible connection/diagnostics, not only process state.

## Publishing and next step

Agent ASTRA-CHAIN's individual push was rejected due to the unrelated GOV_DIPLOME instructions in its
limited context. Its saved commits were integrated locally. Root revalidated the exact origin against
the human's explicit Qostanay_hub repository request; the approval review accepted root's integration
push. Therefore the earlier blocked agent branch is not the delivery: use `codex/classroom-acceptance`.

This is a verified integration checkpoint, not a Windows installer or a proven 100-camera deployment.
Next milestone: T01 mounts the supplied history/control/audio feature adapters; validate clips, explicit
student audio indication and effective lock acknowledgements across the user's three physical PCs.
The main teaching flow should then expose only capabilities those adapters actually confirm.

Interactive teacher UI preview is running locally at `http://127.0.0.1:18790/?students=30` with the explicit
DEMO adapter (synthetic cards). It is a visual preview, not the live class server or a camera test.
The real startup regression ran separately against the built Electron and the actual current backend.

## Adal checkpoint — 2026-10-08, second integration pass

User confirmed the public name Adal. Visible student/launcher branding has been integrated from
771ee10; internal qorgau bridge/protocol/env/data identifiers intentionally retain compatibility.
Root merged latest A07 cb424a6 and contracts 6263aef in fc8a3a8, preserving the simplified preparation
screen and health readiness fixes. Desktop typecheck and full build passed after both merges.
The bounded C2 fix ad01b78 is integrated: audio_start/audio_update return not_supported until a real
media endpoint exists. This prevents the old boolean ACK from falsely claiming microphone capture.
Agent tested 7 regressions and 32 related uplink/server tests, all passed.

Teacher panel now has a single class heading, compact priority filters with an All action, image-led
tiles, shorter status labels, and a row-based review queue. The previous layered main-workspace CSS
was replaced. Source labels and unknown/stale states remain explicit. Panel strict check and 25 unit
tests passed; Chrome e2e 61/61 passed after layout changes (100 synthetic cards p95 2.8ms, not 100 cameras).
The later dark synthetic SVG preview was visually inspected in Chrome at 1366x900; source marking stays
visible. The real C1 session form passed 39/39 at 390/1366 widths after the redesign. Contract generation
check and 48 Python contract tests passed. Latest A07 renderer unit tests: 7/7; fixture class UI: 46/46.
Teacher login now displays Adal with a masked PIN input; existing panel-serving/auth checks: 3/3.
HTML presentation prompt: coordination/launch-prompts/ADAL_HTML_PRESENTATION.txt.

Verified gaps before the next wave: C1 sends exam policy as metadata but the student does not yet
enforce it; Electron overlay is not OS-wide PC lockdown; old C2 lock ACK preceded the visible overlay;
T05 media signaling still needs the C1/C2/Electron bridge. LAN student connections are supported by
Start-Teacher -Lan; teacher browser remains restricted to the server's local machine. Three physical
PCs/Ethernet/Wi-Fi and real media have not been tested.

User requested ten Astra workers; environment permits four active agents including root. Work is
split into ten bounded tasks, with three Astra-ultra workers at a time and root integration:
1. Exam website surface/allowlist (astra_exam_web, active, isolated Adal-exam-web).
2. Effective lock acknowledgement (astra_lock_ack, active, isolated Adal-lock-ack).
3. Transparent audio bridge (astra_audio_bridge, active, isolated Adal-audio-bridge).
4. Teacher commands/policy UI (queued behind 1/2).
5. Teacher audio UI (queued behind 3).
6. Clip/history feature adapter (queued, independent module).
7. LAN launch/pilot diagnostics (queued, no claimed physical pilot).
8. UX/browser acceptance of integrated controls (queued).
9. Cross-process negative/recovery regression (queued).
10. Root integration, public Adal branding, visual review, and presentation prompt (in progress).

All worker changes stay in isolated branches; root publishes the integrated checkpoint. No native
Windows hooks or real cameras/microphones are to be activated by automated acceptance on this machine.

## Shutdown checkpoint — user needs to leave with the laptop

Save/stop requested at the work boundary in response to imminent laptop shutdown. This is a checkpoint,
not a completed product. Root will push the following branches, then confirm exact remote tips.

- Integrated branch codex/classroom-acceptance: latest Adal teacher UI, login, A07/contracts and exam
  website branch (merge951cc8a). Added capability-driven fixed UI extension loader and C1 media CSP;
  loader strict check passed, production feature combinations still need verification.
- codex/adal-exam-web: fc01c62, completed bounded website surface. 13 actual Electron checks, 32 related
  shell tests, build/typecheck passed. IMPORTANT UX limitation: lock/stream loss/pause destroys website
  DOM and can lose unsent external-form values; consider retaining a detached muted view while closed
  network gate applies, and test before claiming lossless resume. No OS-wide application allowlist.
- codex/adal-lock-ack: 5b706be4aae96b51543f58a609876b06d7298500. 42 Python C2, 5 main, 7 renderer, 7
  real Electron fixture checks plus build/typecheck. Needs root main.ts hooks and C1 propagation of
  lock_state/lock_confirmed/lock_requested/lock_scope. Boolean false alone is not confirmed unlock.
- codex/adal-audio-bridge: 55d2929d4648d90f8addfa88e2be35777a40c94a. Contains lock537ab55 and rootfc8
  ancestry. Real C1->C2->Electron->WebRTC fake-tone check passed (bytes2778/packets34; playable Opus10922
  bytes, student banner before capture, talk-only no capture, denial/no-device, ended tracks).
  Latest race/playback/reconnect fixes saved but NOT rerun. Exact main.ts hooks and tests in own handoff.
  Factory classroom.server.audio_feature:create_classroom_feature. No real microphone or camera used.
- codex/adal-review-adapter: 61adf313b1b297c35ddc844de4d4f54966e7ad32. Adapter/store/provenance/clip
  lifecycle/UI saved; existing69 tests passed. Production C1 integration tests and browser playback
  NOT yet verified. Factory classreview.classroom_feature:create_classroom_feature. Event
  history.changed sends {type:incident,student_id,incident}; real panel needs metadata refresh.

Resume order:
1. Read this checkpoint and four ADAL-* handoffs; inspect clean status and exact origin Qostanay_hub.
2. Merge audio branch then final lock branch into root, preserving newer A07/provenance/Adal changes.
3. Wire classLock before examSurface processing, rendererLost on crash/main-frame navigation,
   classAudio.observe/reset/register/scoped permissions, and combine stream close handlers.
4. Propagate lock confirmation fields through C1 contracts/core to teacher UI; do not label old ACKs
   as current confirmed state. Complete actual teacher commands/reason presets and audio UI.
5. Integrate review adapter, config feature defaults, assets loader, C1 history.changed refresh, and
   run real C1->C2 clip upload/playback tests incl run-id/provenance/restarts.
6. Full integrated startup/chain/browser tests then LAN pilot on user's three physical PCs.
Tasks4/5/7/8/9 of the ten-task allocation have not yet started; only first four Astra workers ran.

Additional baseline evidence before shutdown: full A07 fixture flow60/60 passed (no getUserMedia);
fixture class46/46; renderer units7/7. Own Vite fixture serverPID5080 stopped. Audio/review/lock test
processes finished. UI preview18790 may still run until shutdown; restart via class-panel/serve.mjs.
HTML presentation prompt is already in coordination/launch-prompts/ADAL_HTML_PRESENTATION.txt.

## Resumed integration and parallel work boundary — 2026-10-08

Integrated saved audio/lock/history and upstream proctor-integration2799163 (merge90ce179).
This brings the friend's A07, A13, A14, native environment work and T03/T04 adapters into one branch.
It does not certify live CV accuracy, simultaneous physical audio capture, or all of case2.3.
Root connected production main.ts lock receipts before exam view/renderer forwarding, scoped audio
IPC/permissions, and recovery hooks. Fixed teacher audio module404 and its wrong signaling route;
closing a student's card now ends its call. Review changes coalesce a metadata refresh without
changing detector evidence. Full desktop typecheck passed; combined regression is in progress.

Parallel human/other-agent boundary: use a separate checkout and codex/cv-real-check for
backend/proctor/phone/**, backend/proctor/attention/** and handoffs/CV-REAL-CHECK/**. Start from the
published classroom-acceptance checkpoint; own CV real-video false-positive/miss validation.
Do not modify the integration checkout, desktop, classroom, class-panel, classreview, class-audio,
class-control-ui, uplink, proctor_classctl, shared contracts/dependencies/launchers. Propose changes
to fusion/session wiring in the CV handoff for root to integrate. No private exam videos in GitHub.

Current isolated workers: review production tests, external exam state preservation, and teacher
controls plus C1 lock confirmation fields. Root owns production wiring/audio UI assets/real adapter.

## Integrated acceptance checkpoint — 2026-10-08 (supersedes earlier resume queue)

Integrated the exam resume, review adapter, teacher commands, QA alignment and unified Windows
launcher branches through 6b2026a. Production main/preload wiring now connects lock paint receipts,
transparent audio signaling and the external website surface. C1 advertises the mounted history,
audio and exam modules. Teacher actions use the canonical class command API; lock confirmation
requires a current student renderer receipt. Reload/new-session/backend-loss revokes operator access.

The external website start_url is now honored only when allowed by the teacher policy. The policy
identity includes this entry URL so changing an assignment cannot keep the previous page. Updated
teacher copy describes the actual in-Adal URL scope; stale microphone status is shown as unknown.

Verification at this integration boundary:
- Root desktop typecheck and complete Electron/renderer production build passed.
- Root shell suite: 105 PASS, one platform SKIP, zero failures (synthetic backend/mock guards).
- Root C1 server/class contracts/exam tests: 211 PASS. A test-only WebSocket pong-close race emitted
  a thread warning; this does not count as physical network acceptance.
- Audio extension/hub: 19 PASS; updated legacy-audio/disconnect persistence checks: 7 PASS.
- Integrated QA worker evidence on its recorded source 361e244: 380 PASS, one SKIP, four known XFAIL,
  no FAIL/XPASS; evidence and exact source are in qa/results/20261008T140835Z_adal_alignment_361e2445153d.
  This is a source-specific result, not a claim that subsequent A11 fixes were already covered.
- Review worker: 78 checks, including actual C1, authorized clip upload, Chrome Range playback,
  seeking, review decisions and restart. Exam worker: 21 actual Electron checks plus 32 related tests.
- Teacher controls worker: 19 actual C1/Chrome checks and 66 JavaScript checks, with synthetic peers.
- Canonical Start-Adal launcher: 11 stub checks on each of PowerShell 5.1 and 7 plus actual CheckOnly;
  follow-up strengthens freshness checks for shared lock/audio sources. No real guards were engaged.

Work still underway in separate checkouts: actual C1+C2+production Electron lock-chain acceptance,
and bounded A11 persistence/paused-health/restart fixes. Neither is counted as completed here.

Physical acceptance remains outstanding: three PCs over Ethernet/Wi-Fi; camera/phone/gaze accuracy;
simultaneous physical A14 audio and teacher microphone monitoring; LIVE plus native Enforce and
emergency recovery. Native Windows application allowlisting is not implemented. The teacher browser
is local to the server PC; LAN students are supported. Existing WebSocket exam pages are destroyed on
suspend to close their connections, so unsent answers on those pages can be lost. No claim of 100 real
cameras, full case 2.3 completion, or a fully validated release is made.

Parallel Claude tasks are the four ADAL-QUALITY prompts published at 8ae332f, using baseline
2974f643d669afc3219eac44192025008bad1617. Phone and attention work remain isolated from integration;
the other two tasks own offline acceptance and Kazakh localization respectively.

### Real control-chain defect found during acceptance

The worker's actual production Electron run exposed a false lock-receipt rejection: Windows' classic
scrollbar makes innerWidth larger than the fixed overlay's content viewport. The overlay, reason,
inert state and aria-hidden were present, but main reported failed. Root commit52a6ed9 checks the
positive-sized document content viewport instead. Six focused lock tests (including execution of the
actual DOM predicate with scrollbar and incomplete-overlay geometry), full TypeScript checks and
Electron main/preload build pass. Actual chain rerun is still pending; this is app-overlay confirmation,
not proof of OS-wide blocking. Separate test-only pong-close race is handled as a closed socket;
expired-command and silent-client tests pass with thread exceptions promoted to errors (2/2).

Published checkpoint426b9d958eaf39e44b14b5d2e2802948c634a705 was verified against the GitHub remote
tip. Later fixes are committed/published separately; do not infer their availability from this entry.
