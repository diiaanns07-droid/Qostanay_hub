"""Versioned network contracts of Qorgau Classroom (owner: T01). Source of truth: models.py.

Two surfaces, never mixed up with the per-student local API (proctor_contracts / qorgau.v1 on 127.0.0.1):

* Student wire  — `qorgau.class.v1` (frozen 2026-10-08, proctoring/contracts/class/PROTOCOL_v1.md, owner A01).
                  Models here mirror it 1:1; every v1.1 addition is OPTIONAL so plain v1 clients keep working.
* Teacher API   — `qorgau.classroom` contract 1.1.0: REST /api/teacher/* + WS /ws/teacher between the class
                  server and the teacher console (not specified in v1; defined here).

Generated artifacts (never edit by hand): schema/classroom.v1.schema.json, ts/classroom-v1.generated.ts.
    python -m classroom.contracts.generate --check
"""

from .models import *  # noqa: F401,F403
from .models import CONTRACT_ID, CONTRACT_VERSION, WIRE_EXTENSION, WIRE_PROTOCOL  # noqa: F401
