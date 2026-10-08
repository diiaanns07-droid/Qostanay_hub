"""Shared invariant checks for A05 tests (contract validity, lifecycle, explanation honesty)."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from functools import lru_cache
from typing import Sequence

import jsonschema

from proctor.fusion.explain import ACTION_LABELS, render_fact
from proctor.settings import PROCTORING_ROOT
from proctor_contracts.v1 import Incident, IncidentChange, IncidentChangeType, IncidentState, ReviewStatus

SCHEMA_PATH = PROCTORING_ROOT / "contracts" / "schema" / "v1" / "qorgau.v1.schema.json"
NUMBER = re.compile(r"\d+(?:,\d+)?")
FORBIDDEN_WORDS = ("вероятност", "%", "виновн", "доказано", "списал")


@lru_cache(maxsize=None)
def _validator(name: str) -> jsonschema.Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{name}"})


def assert_contract_valid(changes: Sequence[IncidentChange]) -> None:
    validator = _validator("IncidentChange")
    for change in changes:
        data = json.loads(change.model_dump_json())
        validator.validate(data)
        assert IncidentChange.model_validate(data) == change


def assert_numbers_from_facts(incident: Incident) -> None:
    """Every number in summary_ru is the rendering of a fact value (no free-floating numbers)."""
    text = incident.explanation.summary_ru
    for fact in incident.explanation.facts:
        if isinstance(fact.value, str):
            text = text.replace(fact.value, " ")
    for label in ACTION_LABELS.values():
        text = text.replace(label, " ")
    allowed = {render_fact(f) for f in incident.explanation.facts if not isinstance(f.value, str)}
    for number in NUMBER.findall(text):
        assert number in allowed, f"{incident.rule_id}: '{number}' in summary is not a fact: {incident.explanation.summary_ru}"


def assert_honest_wording(incident: Incident) -> None:
    texts = [incident.explanation.summary_ru, *incident.explanation.caveats_ru]
    for text in texts:
        low = text.lower()
        for word in FORBIDDEN_WORDS:
            assert word not in low, f"forbidden wording '{word}' in: {text}"


def assert_lifecycle(changes: Sequence[IncidentChange], *, finished: bool = True) -> dict[str, Incident]:
    """opened first, update_seq 0..n without gaps, exactly one closed (if finished), after close only
    link updates; returns the final incidents."""
    by_id: dict[str, list[IncidentChange]] = defaultdict(list)
    for change in changes:
        by_id[change.incident.incident_id].append(change)
    final: dict[str, Incident] = {}
    for iid, chs in by_id.items():
        assert [c.incident.update_seq for c in chs] == list(range(len(chs))), iid
        assert chs[0].change == IncidentChangeType.OPENED and chs[0].incident.state == IncidentState.OPEN, iid
        closed = [i for i, c in enumerate(chs) if c.change == IncidentChangeType.CLOSED]
        assert len(closed) == (1 if finished else len(closed)) and len(closed) <= 1, iid
        for i, c in enumerate(chs):
            inc = c.incident
            assert inc.review_status == ReviewStatus.PENDING  # the decision belongs to A08/teacher
            assert inc.session_id == chs[0].incident.session_id and inc.rule_id == chs[0].incident.rule_id
            assert inc.t_start_ms == chs[0].incident.t_start_ms
            if closed and i > closed[0]:
                ref = chs[closed[0]].incident
                assert c.change == IncidentChangeType.UPDATED and inc.state == IncidentState.CLOSED, iid
                strip = {"related_incident_ids", "update_seq"}
                assert inc.model_dump(exclude=strip) == ref.model_dump(exclude=strip), "post-close update changed more than links"
            elif closed and i == closed[0]:
                assert inc.state == IncidentState.CLOSED and inc.end_reason is not None and inc.t_end_ms is not None
                assert inc.t_end_ms >= inc.t_start_ms and inc.wall_end >= inc.wall_start
                assert abs(inc.duration_ms - (inc.t_end_ms - inc.t_start_ms)) < 0.01
            else:
                assert inc.state == IncidentState.OPEN and inc.t_end_ms is None and inc.end_reason is None
                if i > 0:
                    assert c.change == IncidentChangeType.UPDATED
            assert_numbers_from_facts(inc)
            assert_honest_wording(inc)
        final[iid] = chs[-1].incident
    # links are symmetric and point to known incidents
    for iid, inc in final.items():
        for other in inc.related_incident_ids:
            assert other in final, f"{iid} -> unknown {other}"
            assert iid in final[other].related_incident_ids, f"asymmetric link {iid} -> {other}"
    return final
