# ADAL-LOCK-RECOVERY

Branch: `codex/adal-lock-recovery`, based on `f3a4a28` in Qostanay_hub.

The production renderer can reload while the local backend WebSocket stays connected. The navigation-time loss notification generated a recovery request before the replacement renderer subscribed; the next periodic state arrived after its receipt deadline.

Preload now reports subscription availability only after registering the callback. Main accepts that notification only from the existing trusted primary frame. The first subscription waits for the previous renderer-loss invalidation, then asks the local backend for fresh scoped proof. Later subscribers receive only the current, unexpired class state; they do not invalidate proof again. The cache is cleared on navigation, backend loss and local-session change. Audio commands and other stream events are never cached. The existing independent DOM/visibility verification and exact request receipt remain the only ACK path; receipt deadlines are unchanged.

Validation checkpoint:

- Focused class-lock suite: 11 PASS, including ordered loss/readiness, cancellation, late subscribers, cache invalidation, scope and expiry rejection.
- All three TypeScript configurations: PASS.
- Electron + production renderer build: PASS (Windows realpath required escalation).
- Broader shell run: 99 PASS, one Python protocol fixture failed because this isolated checkout has no local `.venv`; backend fixtures skipped for the same missing interpreter. Rerun with the explicitly selected shared Python is pending.
- Actual production-chain reload validation is delegated to the existing upstream audit harness; no actual-chain PASS claimed at this checkpoint.

No camera, microphone, native keyboard hook or enforced exam mode was exercised. This is application overlay recovery, not an OS lockdown claim. Root integrates and pushes; this agent does not push.
