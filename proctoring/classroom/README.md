# Qorgau Classroom — class server and teacher console (T01 = global role C1)

The proctoring of a Windows classroom over the LAN or Wi-Fi.
* Each student PC runs the local Qorgau app: CV, the student shell, and the uplink C2.
* The teacher PC runs this server and the teacher panel.
* The exam itself runs on an external site or a separate program.

| Path | What | Owner |
|---|---|---|
| `contracts/` | Pydantic contracts (source of truth), generated JSON Schema, TS types, fixtures | T01 |
| `server/` | `python -m classroom.server`: pairing, teacher PIN auth, heartbeat, event dedup, SQLite persistence, command bus, teacher stream, audio relay, feature plug-ins | T01 |
| `simulator/` | `python -m classroom.simulator`: SIMULATED students, always labelled | T01 |
| `teacher-ui/` | React/TS shell; `src/features/<dir>/` belong to T02–T05 | T01 + carve-outs |
| `coordination/` | `OWNERSHIP.json`, `verify_ownership.py`, `INTERFACES.md`, `DECISIONS.md`, `RUN.md` | T01 |

Start here: [`coordination/RUN.md`](coordination/RUN.md) (run), [`coordination/INTERFACES.md`](coordination/INTERFACES.md)
(APIs and plug-in points), [`coordination/OWNERSHIP.json`](coordination/OWNERSHIP.json) (who may write where).
