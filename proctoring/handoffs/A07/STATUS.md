# A07 — STATUS

## Текущая поставка A07-student, 2026-10-08

Ветка `codex/proctor-A07-student`, база `5c6a8e4e18bf29eedee4181cb6dd0c6d79eb72dc`.
Описание экранов, минимального IPC и блокеров аудио: [STUDENT.md](STUDENT.md).
Последующие исправления и актуальные результаты тестов: [RECHECK.md](RECHECK.md).
Оставшаяся проверка с человеком перед камерой: [LIVE_STUDENT.md](LIVE_STUDENT.md).
LIVE: все пять точек собраны, backend зарегистрировал `gaze_prolonged_down` на **4593 ms**.
Капитан подтвердил намеренный взгляд вниз; LIVE «взгляд вниз после калибровки» — **PASS**.
Доказательства и точный SHA запуска — в конце [RECHECK.md](RECHECK.md).

Ниже сохранён исторический handoff предыдущего этапа.

## Historical STATUS (round 2: connect the UI to the real bridge/backend)

Role: product UI / renderer. Branch: `claude/upbeat-gauss-798nt9` (platform-assigned).
Continuation SHA (previous A07 checkpoint): `3fef6fb80d4106b5faad1609e42f31981401da13`; original BOOTSTRAP
`35bea4c7b28d2c622cf7ba26ff354273cc7b6c49`. The SHA of this checkpoint is reported out-of-band after push.
Contract: `qorgau.v1` (A01 r2 docs 1.0.1, wire types unchanged), bridge 1.0.0.
Read-only references used: A01 r2 `29cadde` (claude/nifty-ride-ux8e4j), A06 `62a7fb1` (shell), A08 `5509950` (storage).
No A01 integration candidate SHA existed at the time of this checkpoint (A01 branch = r2 backend fixes only).
Diff: renderer-only (`proctoring/desktop/renderer/`) + this handoff.

## What changed (renderer)
* **Teacher rights follow the shell.** `lib/permissions.ts` mirrors A06 `ipc/api.ts` (BLOCKED_IN_EXAM, OPERATOR_ONLY);
  a running session counts as exam mode even before the guard engaged (closes a real race seen on the backend).
  During exam mode the console shows only stream data, says why, and offers no review/materials/export;
  review works on pause and after finish. PIN: 4–64 alnum (shell validator), shell reasons for wrong / rate-limited /
  not configured. No built-in PIN anywhere: the product build has **no FixtureBridge code at all** (checked: 0 hits);
  the fixture uses a random one-time PIN per tab.
* **Stream vs storage.** `LiveStore` merges A05 stream bodies (by `update_seq`) with A08 REST review/evidence
  (authoritative): a late `pending`/`[]` from the stream never rolls back a recorded review. Stream changes trigger
  a debounced `listIncidents` + `getIncident` re-read when the shell allows it; after a review the card and list
  re-read storage and only then show "записано в хранилище". Session-scoped stream data is accepted only for the
  bound session; `bindSession` vs `setSession` + `isCurrent()` drop late responses of a cancelled/previous session.
* **Autosave** (`lib/autosave.ts`): one request in flight per question, newest edit wins by `client_seq`, "saved"
  only after the backend acknowledged ≥ the latest edit; retryable errors (STORAGE_ERROR, connection) back off
  1→15 s; while paused/offline edits wait and are re-sent on resume/reconnect; unsent values survive a renderer
  reload (sessionStorage); restore merges server + stash by seq (no duplicates). Finish flushes and, if something
  is still unsaved, asks explicitly ("Повторить сохранение" / "Завершить без них").
* **Timer** from server time: session-clock samples (metrics/observations) or backend wall clock (`sent_at`,
  `server_time`), re-anchored on every sample, frozen while paused, `paused_total_ms` from SessionInfo.
* **Failure UX:** backend starting/restarting/failed/stopped/health-down banners; shell errors by `details.shell_code`
  in Russian (`lib/errors.ts`); evidence reasons (`media_missing`, `ttl_expired`, `hash_mismatch`…); health/gap
  codes in Russian; capabilities "not measured yet" (retryable) → auto-retry; "Защита частичная" summary from the
  matrix; a session lost by a backend crash is shown as "Сессия недоступна" with a clean restart; exports show success
  only when the shell returns `saved: true` (cancel → "файл не записан").
* **Preview overlays**: drawn from the observation of exactly the shown `frame_id` (ring of 48 per kind); a nearest
  older one only within 400 ms, dashed and labelled with its lag; older ones are not drawn ("разметка устарела").
* Polish at 1366×768: timeline shows only lanes with data, negative episode times shown as `−00:00.x`, dialogs keep
  focus trap/Escape/focus restore, reduced motion respected, readable Russian reasons. No new pages.

## Checks — on FIXTURE (not integration)
`VITE_QORGAU_FIXTURE=1 npx vite build --outDir "$PWD/dist/renderer-fixture"` → `npx vite preview --outDir …` →
`node renderer/tests/e2e-fixture.mjs <out>` — **44/44 PASS** at 1366×768 and 1920×1080: capabilities retry,
LIVE blocked, wrong PIN reason, calibration failure/retry, autosave, request race (random 0.2–1.5 s latencies) settles
on the latest value, disk error → retry, offline answer sent after reconnect, journal closed in exam mode, camera loss
→ technical episode, review at pause, late stream `pending` does not roll back, finish blocked by an unsaved answer
until explicit choice, HTML/JSON export downloads, dialog focus trap + Escape + focus restore, no overflow, 0
`getUserMedia`, 0 console errors.

## Checks — on the REAL bridge logic + REAL backend (no Electron)
Local scratch integration (NOT published, NOT a candidate): A01 r2 `29cadde` + merge A06 `62a7fb1` + merge A08
`5509950` + this renderer; Python 3.12 venv from `requirements/full.txt --require-hashes`.
Harness `renderer/tests/real-bridge/` runs A06's own `createApi`, `ShellStateMachine`, `BackendSupervisor`,
`BackendClient`, `BackendSocket`, `OperatorAuth` (scrypt PIN) in Node exactly as `main.ts` wires them; only the IPC
transport (Playwright `exposeBinding`) and the OS guard (no-op stand-in) differ. Renderer = product build served with
A06's CSP header. `npm run build:renderer && node renderer/tests/real-bridge/run-real.mjs <out>` — **24/24 PASS**:
READY handshake, real preflight, calibration from backend samples, exam mode engaged by the real state machine,
2 answers stored by A08, wrong PIN rejected by OperatorAuth, preview via WS `/v1/preview`, incidents via WS, **0 calls
rejected by shell gating during the whole run** (only the intentional wrong PIN), review persisted in A08 and shown,
timer drift 0.46 s after a pause (tolerance 2.5 s), finish releases exam mode, **HTML (13 KB, no `<script>`) and JSON
(with manifest) written to disk via the shell's saveFile**, restart with a second session, SIGKILL of the backend →
outage visible → shell restarts it → lost session reported, 0 `getUserMedia`, 0 CSP violations, 0 console errors.
Data: SYNTHETIC only (bootstrap capture/analyzers/engine — A02–A05 not integrated); labelled in UI and report.

Other: `npm run typecheck` PASS (renderer; in the scratch integration also main/preload); `npm run build:renderer`
PASS (product bundle has no fixture code); `git diff --check` clean; `verify_ownership --agent A07` vs `3fef6fb` and vs
`35bea4c` — see commit report.

## NOT run / not verified
* **Inside Electron** (binary not downloadable here): real IPC, preload, window, `qorgau://` origin with CSP via
  protocol handler, kiosk/keyboard guard, focus loss events. To do on Windows: `npm ci && npm start`, then the same
  path by hand (list below).
* **Windows**, camera, LIVE/REPLAY, real CV (A02–A05), evidence images (synthetic session had `retain_media: false`;
  evidence rendering is covered only on fixture), long sessions (2 h) and timeline density at that scale.
* Kazakh dictionary is still a draft without native review.

## Manual checks for the Windows demo machine (after A01 candidate)
1. `npm start`: renderer loads from `qorgau://app/` without CSP errors; production build without preload shows the error page.
2. Preflight with LIVE: matrix banner shows partial protection honestly; missing model → preflight fail, no green.
3. Calibration on camera: per-target A04 messages readable (`low_light`, `extreme_pose`…), retry/cancel/skip (PIN).
4. Exam: autosave while unplugging the network is irrelevant (loopback) — instead kill `python` in Task Manager:
   banner, answers kept, restart, lost-session screen.
5. Teacher PIN (`QORGAU_OPERATOR_PIN_HASH`): 5 wrong → rate limit message; console during exam = stream only.
6. Pause → review with evidence images (`retain_media: true`) → resume → finish → HTML/JSON saved via the dialog;
   cancel the dialog → "файл не записан".

## Integration order
Renderer depends on A06 (preload) for Electron and on A08 routes for review/report; no shared file change needed.
A01: take this SHA together with A06 and A08; `renderer/tests/real-bridge/run-real.mjs` is the ready end-to-end
check for the candidate (needs `.venv` and a Playwright install).

## Next (A07)
Run `run-real.mjs` on the A01 candidate SHA, then on LIVE/REPLAY once A02–A05 are merged; evidence images path;
Electron manual pass on Windows with A09.
