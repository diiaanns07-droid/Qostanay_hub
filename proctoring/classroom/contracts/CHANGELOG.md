# Classroom contract changes

## 1.1.0 — source provenance (wire extension 1.2)

Coordinator-approved additive extension; the student wire remains
`qorgau.class.v1` with `v: 1`. Local backend `qorgau.v1` contracts are unchanged.

- Optional `source_mode` (`live`, `synthetic`, `replay`, `unknown`) and
  `source_session_id` on student `hello`, `status`, `incident`, `preview`.
  The ID refers to the local backend session, never the classroom session.
  Legacy messages with no fields remain accepted; older servers ignore fields.
- Teacher `DataOrigin` adds `unknown` and `replay` to `real` and `simulated`.
  A regular connected client starts unknown until its source is declared.
  `synthetic` maps to simulated; `live` maps to real; recorded media maps to
  replay. This is client-declared provenance, not camera or CV attestation.
- Legacy `hello.simulated=true` or the known simulator version prefix continues
  to mark all data as simulated. Neither a display name nor `camera=ok` is
  evidence of a live source.
- Card origin follows current hello/status, resets on rejoin and persists.
  Events carry their own source through the offline queue; changing current
  source cannot relabel previous observations. Missing provenance is unknown,
  even if the current card is real. Incident aggregate origin follows the first
  event; each constituent observation retains its own declared origin.
- Preview metadata and `X-Qorgau-Origin` describe the exact stored frame,
  independently from current status. C2 uses frame metadata, not a potentially
  newer session snapshot, and sends no source guess for a legacy preview adapter.

Consumers must render unknown/replay separately from live. Generated schema,
TypeScript union and fixtures reflect all four origins. The same major wire
protocol still accepts old clients, but teacher consumers with exhaustive enum
switches must handle the two added values.

Existing stored historical observations are not rewritten: old origin values
cannot retrospectively prove their original source. No SQL migration is needed;
new per-event provenance lives in existing JSON, card origin in its TEXT column.
