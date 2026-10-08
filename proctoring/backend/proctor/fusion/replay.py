"""Replay observations through the fusion engine (tests, golden scenarios, threshold tuning).

    python -m proctor.fusion.replay observations.jsonl --session-id S --mode replay \
        [--tick-ms 250] [--finish-ms N] [--config overrides.json] [--json]

Input: JSON Lines (one contract Observation per line) or a JSON array, e.g. exported by A08.
The same file + config gives the same incidents for any --tick-ms: incident times come from the data.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Sequence

from proctor_contracts.v1 import Incident, IncidentChange, IncidentEndReason, Observation

from .config import FusionConfig
from .engine import FusionEngine
from .scenario import parse_observation


def replay(
    engine: FusionEngine,
    observations: Iterable[Observation],
    *,
    tick_ms: float | None = None,
    lead_ms: float = 0.0,
    finish_t: float | None = None,
    finish_reason: IncidentEndReason = IncidentEndReason.SESSION_FINISHED,
) -> list[IncidentChange]:
    """Feed observations in order. With ``tick_ms``, ``advance()`` runs every tick of session time
    (``lead_ms`` > 0 simulates a clock running ahead of delayed observations), like A01's fusion loop."""
    out: list[IncidentChange] = []
    next_tick: float | None = None
    for obs in observations:
        if tick_ms:
            if next_tick is None:
                next_tick = obs.t_session_ms + tick_ms
            while next_tick <= obs.t_session_ms:
                out += engine.advance(next_tick + lead_ms)
                next_tick += tick_ms
        out += engine.consume(obs)
    if finish_t is not None:
        if tick_ms and next_tick is not None:
            while next_tick <= finish_t:
                out += engine.advance(next_tick + lead_ms)
                next_tick += tick_ms
        out += engine.finish(finish_t, finish_reason)
    return out


def final_incidents(changes: Sequence[IncidentChange]) -> dict[str, Incident]:
    """Latest state of every incident (highest update_seq), ordered by (t_start_ms, incident_id)."""
    latest: dict[str, Incident] = {}
    for change in changes:
        inc = change.incident
        cur = latest.get(inc.incident_id)
        if cur is None or inc.update_seq > cur.update_seq:
            latest[inc.incident_id] = inc
    return dict(sorted(latest.items(), key=lambda kv: (kv[1].t_start_ms, kv[0])))


def load_observations(path: Path) -> list[Observation]:
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        rows = json.loads(text)
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [parse_observation(row) for row in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay observations through the A05 fusion engine")
    ap.add_argument("observations", type=Path)
    ap.add_argument("--session-id", default=None, help="default: session_id of the first observation")
    ap.add_argument("--mode", default=None, choices=["live", "replay", "synthetic"])
    ap.add_argument("--tick-ms", type=float, default=None)
    ap.add_argument("--finish-ms", type=float, default=None, help="default: last observation time")
    ap.add_argument("--config", type=Path, default=None, help="JSON object with FusionConfig overrides")
    ap.add_argument("--json", action="store_true", help="print IncidentChange JSON lines")
    args = ap.parse_args(argv)

    observations = load_observations(args.observations)
    if not observations:
        print("no observations", file=sys.stderr)
        return 1
    first = observations[0]
    overrides = json.loads(args.config.read_text(encoding="utf-8")) if args.config else None
    engine = FusionEngine(args.session_id or first.session_id, args.mode or first.source_mode, FusionConfig.from_dict(overrides))
    finish = args.finish_ms if args.finish_ms is not None else max(o.t_session_ms for o in observations)
    changes = replay(engine, observations, tick_ms=args.tick_ms, finish_t=finish)
    if args.json:
        for change in changes:
            print(change.model_dump_json())
        return 0
    print(f"rules {engine.rule_version} · config {engine.config_version} · ignored {engine.stats()}")
    for inc in final_incidents(changes).values():
        end = "—" if inc.t_end_ms is None else f"{inc.t_end_ms / 1000:.1f}s"
        print(
            f"{inc.t_start_ms / 1000:8.1f}s → {end:>8}  {inc.priority.value:6}  {inc.rule_id.value:26} "
            f"{(inc.end_reason.value if inc.end_reason else ''):17} {inc.explanation.summary_ru}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
