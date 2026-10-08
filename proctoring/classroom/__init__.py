"""Qorgau Classroom — class server, teacher console and shared network contracts (owner: T01).

Packages:
    classroom.contracts   versioned contracts (student wire qorgau.class.v1 + teacher API qorgau.classroom v1)
    classroom.server      `python -m classroom.server` — the class server (pairing, auth, heartbeat, events,
                          commands, teacher stream, feature mounting)
    classroom.simulator   `python -m classroom.simulator` — SIMULATED student clients (test data, labelled)

Feature modules of T02–T05 live in their own paths and plug in through classroom.server.features
(see classroom/coordination/INTERFACES.md). CV runs on the student PC; this package never touches it.
"""
