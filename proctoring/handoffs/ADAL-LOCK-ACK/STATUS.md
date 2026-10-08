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
