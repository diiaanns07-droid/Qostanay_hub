# A07 — STATUS

Role: product UI / renderer (Qorgau Exam).
Branch: `claude/upbeat-gauss-798nt9` (platform-assigned), created from BOOTSTRAP `35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`
(A01, `claude/nifty-ride-ux8e4j`). Contract baseline: `qorgau.v1` 1.0.0, bridge 1.0.0.
Previous checkpoint: none (first A07 commit). The SHA of this checkpoint is reported out-of-band after push.
Stage: **checkpoint 1 — full UI flow on a labelled FixtureBridge + first visual polish.**

## What works (verified on FixtureBridge only)
Flow: preflight → informed start (consent, mode, retain_media) → checks → calibration (5 targets, backend-driven
progress, failed target + retry, cancel, skip with reason) → ready → student exam (timer, progress, autosave with
client_seq, save errors + auto retry, finish confirm, emergency exit) → teacher console via PIN (preview with
frame_id-synced overlays, mirror toggle, letterbox-safe box, sources/metrics, live timeline by category, filters,
episode card: explanation facts, caveats, timing, provenance, evidence, append-only review) → pause/resume/finish/abort
→ post-exam review (auto-selects first pending episode) → summary (coverage: observed/pause/gaps, episodes by rule,
decisions, limitations, export HTML/JSON, delete with confirm) → new session.

Honesty rules implemented:
* `FIXTURE` strip + badge on every screen when the FixtureBridge is used; `LIVE/REPLAY/SYNTHETIC` badge from
  `session.source_mode` in the top bar, on the preview and on each episode.
* FixtureBridge is used ONLY when `window.qorgau` is absent AND (Vite dev server OR `?bridge=fixture`). A packaged
  build without the preload shows an error screen — no silent fallback (checked).
* LIVE/REPLAY on the FixtureBridge fail preflight (no camera, modules not integrated); "К калибровке" stays disabled.
  No green "ready" while a required check is not `pass`.
* No `getUserMedia` anywhere (checked by an instrumented e2e run: 0 calls). Preview only from `subscribePreview`.
* No hard-coded metrics: numbers come from `RuntimeMetrics`/`SessionSummary`/`Incident`; `null` → "нет данных"/"—"
  (e.g. FixtureBridge reports e2e latency `null` → UI says it is not measured). `max_confidence` is labelled
  "оценка детектора, не вероятность нарушения"; priority is "приоритет проверки".
* "Нет эпизодов" is never shown as "нарушений нет": empty states point to coverage/sources.
* Teacher console requires `operatorUnlock(pin)` (checked in main by A06); unlock is cleared at exam start by the shell;
  skipping calibration also asks for the teacher PIN.

## Paths (all inside A07 ownership)
```
proctoring/desktop/renderer/index.html
proctoring/desktop/renderer/src/main.tsx                 entry
proctoring/desktop/renderer/src/App.tsx                  routing by session.state, role, PIN dialog, fixture panel
proctoring/desktop/renderer/src/bridge/selectBridge.ts   window.qorgau | explicit FixtureBridge | error
proctoring/desktop/renderer/src/bridge/fixtureBridge.ts  DEV-ONLY QorgauBridge on contract fixtures + fault injection
proctoring/desktop/renderer/src/bridge/fixtureFrames.ts  synthetic JPEG frames (vector drawing, "FIXTURE" burned in)
proctoring/desktop/renderer/src/lib/*                    labels (RU), format, i18n (RU + KK draft), liveStore, result
proctoring/desktop/renderer/src/components/*             ui primitives, PreviewPanel, Timeline, IncidentCard
proctoring/desktop/renderer/src/screens/*                Preflight, Calibration, Exam, Operator, Summary
proctoring/desktop/renderer/src/styles.css               system fonts only, no CDN
proctoring/desktop/renderer/tests/e2e-fixture.mjs        Playwright flow check (fixture)
```

## Interfaces consumed
`window.qorgau: QorgauBridge` (contracts/ts/bridge.ts) — every method is used: shell state/unlock/lock/emergency exit,
health, capabilities, lifecycle, calibration (start/target/state/finish/cancel/skip), exam, answers, incidents,
reviews, evidence, summary, export, delete, `subscribeEvents`, `subscribePreview`. Wire types from
`@contracts/qorgau-v1.generated`, fixtures from `@contracts/fixtures.generated`. Stream handling: idempotent
incidents by `(incident_id, update_seq)`, seq-gap / reconnect (`hello`) → REST refetch, foreign-session messages ignored.

## Checks (Linux container, Node 22.22.0, Chromium via preinstalled Playwright 1.56.1; no Electron, no camera)
| Command (from `proctoring/desktop`) | Result |
|---|---|
| `npm run typecheck` | PASS contracts, PASS renderer (main SKIP: A06 not present) |
| `npm run check:contracts` | PASS (part of typecheck) |
| `npm run build:renderer` | PASS (`dist/renderer`, ~315 kB JS, no external URLs) |
| `npx vite preview` + `node renderer/tests/e2e-fixture.mjs <out>` | **30/30 PASS** at 1366×768 and 1920×1080 |
| packaged build without `?bridge=fixture` | error screen, no fixture fallback — PASS |
| `npx vite` (dev) | renders with FixtureBridge labelled — PASS |
| `python coordination/verify_ownership.py --agent A07 --base 35bea4c…` | see commit report |

The e2e covers: fixture label, LIVE preflight blocked, abort at preflight, synthetic preflight ready, calibration
failure + retry + finish, start, autosave, save failure → visible error → auto-recovery, wrong PIN, operator live
(preview + overlays + incidents), review recorded, backend loss banner + recovery, camera loss → technical episode,
pause/resume, finish, review, summary, HTML export NOT_IMPLEMENTED surfaced, JSON export download, no horizontal
overflow on 4 screens per viewport, 0 `getUserMedia` calls, 0 console errors.
Screenshots were produced locally (synthetic drawings only, no personal data); not committed.

## Bug found and fixed during checks
Resuming a paused session closed a "Finish" dialog opened in the meantime and left its button disabled (shared busy
flag). Now each operator action closes only its own dialog and has its own busy state.

## Not verified / limitations
* Nothing ran against the real backend or Electron: A06 preload is not on this base. Integration against
  `python -m proctor serve` needs the shell (the renderer never talks HTTP itself).
* Kazakh (`KK*`) dictionary covers only UI chrome and is a **draft without native-speaker review**; domain labels stay RU.
* Preview overlays use the latest observation per kind; matching is by `frame_id`, otherwise age vs. the shown frame
  (dashed when lagging, hidden when older than 1.5 s). Not validated against real A02/A04 timing.
* Exam timer uses `started_at` + local clock − `paused_total_ms`; backend does not send remaining time.
* No CSP meta in `index.html` yet (Vite dev preamble is inline; file:// semantics of `'self'` not testable here).
  Proposed policy for A06 (header or meta, after testing in Electron):
  `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'`.
* HTML export/delete/evidence are A08 routes; on the fixture JSON export is a browser download labelled fixture.

## Requests (see DEPENDENCIES.txt)
exam id before session creation; JS test runner/Playwright pin; shell error codes for backend loss.

## Integration order
A07 needs A06 (preload exposing `window.qorgau`) to run inside Electron; no change in A01 shared files is required.
After A06 lands: `npm run build && electron .` → same flow on `synthetic`, then on `replay/live` once A02–A05/A08 merge.

## Next (A07)
1. Run the flow through A06 preload against `python -m proctor serve` (synthetic), fix contract mismatches.
2. Visual polish on real frames: overlay label collision, timeline zoom for long sessions, print-friendly summary.
3. Demo scripting with A10 (replay scenario) and screenshot set for the pitch.
