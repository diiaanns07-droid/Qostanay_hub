# ADAL-LOCK-ACK

Branch: codex/adal-lock-ack. Origin: Qostanay_hub.git (verified).
Assigned by coordinator: reliable teacher overlay lock/unlock acknowledgment.

Checkpoint 1: new scoped LockCoordinator distinguishes requested and applied UI
state. Includes receipt identity, deadline, supersession, durable replay journal,
and re-confirmation of prior lock after backend restart. Integration and tests
are in progress. No native restrictions, cameras or microphones used.

Next: merge coordinator baseline fc8a3a8, add uplink/API and Electron/renderer
round trip, then focused backend and real Electron fixture validation.
No push requested for these isolated subtask commits; coordinator integrates.

Checkpoint 2: uplink/API+renderer+main helper implemented; 18 Python lock/recovery,
4 main lock, 7 renderer state tests pass; desktop typecheck and product build pass.
Actual Electron fixture is under investigation (first receipt reports not applied,
second run timed out), so rendering is not yet claimed verified.

Audio integration imports sibling helper audio.py from audio agent 1933195 for
local validation only; that file is not part of this subtask commit. Coordinator
must merge audio branch alongside this branch. main.ts integration belongs to exam
agent/coordinator; exact ClassLockController hooks sent separately.
