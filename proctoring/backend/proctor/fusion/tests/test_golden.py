"""Golden scenarios: expectations in golden/*.json are derived by hand from the rule definitions
(see each file's "derivation"), built from the shared contract fixtures, and checked here."""

from __future__ import annotations

from pathlib import Path

import pytest

from proctor.fusion import create_incident_engine
from proctor.fusion.replay import final_incidents, replay
from proctor.fusion.scenario import build_scenario, load_golden
from proctor.fusion.tests.checks import assert_contract_valid, assert_lifecycle

GOLDEN = sorted((Path(__file__).parent / "golden").glob("*.json"))
CADENCES = [(50.0, 0.0), (250.0, 0.0), (1000.0, 0.0), (250.0, 300.0)]


def _run(spec: dict, *, tick_ms: float | None = None, lead_ms: float = 0.0, delay_ms: dict | None = None):
    observations = build_scenario(spec, delay_ms=delay_ms)
    engine = create_incident_engine(spec.get("session_id", "fx-session-0001"), spec.get("mode", "synthetic"), None,
                                    config=spec.get("config"))
    changes = replay(engine, observations, tick_ms=tick_ms, lead_ms=lead_ms, finish_t=spec["finish"])
    return engine, changes


def test_golden_files_present():
    assert len(GOLDEN) >= 10


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.stem)
def test_golden_expectations(path: Path):
    spec = load_golden(path)
    engine, changes = _run(spec)
    assert_contract_valid(changes)
    final = assert_lifecycle(changes, finished=True)
    got = sorted(final.values(), key=lambda i: (i.t_start_ms, i.rule_id.value))
    want = sorted(spec["expected"], key=lambda e: (e["t_start_ms"], e["rule_id"]))
    assert [(i.rule_id.value, i.t_start_ms) for i in got] == [(e["rule_id"], float(e["t_start_ms"])) for e in want], (
        "incident set differs: " + "; ".join(f"{i.rule_id.value}[{i.t_start_ms}-{i.t_end_ms}]" for i in got)
    )
    for inc, exp in zip(got, want):
        where = f"{path.stem}/{exp['rule_id']}"
        assert inc.t_end_ms == pytest.approx(exp["t_end_ms"]), where
        assert inc.end_reason.value == exp["end_reason"], where
        assert inc.priority.value == exp["priority"], (where, inc.explanation.facts)
        assert inc.source_mode.value == spec.get("mode", "synthetic")
        assert inc.rule_version == engine.rule_version and inc.config_version == engine.config_version
        if "observation_count" in exp:
            assert inc.observation_count == exp["observation_count"], where
        related_rules = sorted(final[r].rule_id.value for r in inc.related_incident_ids)
        assert related_rules == sorted(exp.get("related_rules", [])), where
        facts = {f.key: f.value for f in inc.explanation.facts}
        for key, value in exp.get("facts", {}).items():
            assert key in facts, f"{where}: missing fact {key} in {sorted(facts)}"
            if isinstance(value, float):
                assert facts[key] == pytest.approx(value, abs=0.05), f"{where}: {key}"
            else:
                assert facts[key] == value, f"{where}: {key}"
        for text in exp.get("summary_contains", []):
            assert text in inc.explanation.summary_ru, f"{where}: {inc.explanation.summary_ru}"
        for text in exp.get("caveats_contain", []):
            assert any(text in c for c in inc.explanation.caveats_ru), f"{where}: {inc.explanation.caveats_ru}"
    assert not {k: v for k, v in engine.stats().items() if not k.startswith("accepted_")}, engine.stats()


@pytest.mark.parametrize("tick_ms,lead_ms", CADENCES, ids=lambda v: str(v))
@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.stem)
def test_golden_independent_of_tick_cadence(path: Path, tick_ms: float, lead_ms: float):
    """Same replay + config => same incidents whatever the advance() cadence / clock lead."""
    spec = load_golden(path)
    _, base = _run(spec)
    _, ticked = _run(spec, tick_ms=tick_ms, lead_ms=lead_ms)
    assert_lifecycle(ticked, finished=True)
    assert final_incidents(ticked) == final_incidents(base)


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.stem)
def test_golden_independent_of_cross_source_delivery_order(path: Path):
    """Phone results delivered 90 ms later than attention (analyzer latency) change nothing but
    the emission order: same incidents, same times, facts, links and priorities."""
    spec = load_golden(path)
    _, base = _run(spec)
    _, delayed = _run(spec, tick_ms=250.0, delay_ms={"phone": 90.0, "environment": 20.0})
    assert_lifecycle(delayed, finished=True)
    strip = {"update_seq"}
    a = {k: v.model_dump(exclude=strip) for k, v in final_incidents(base).items()}
    b = {k: v.model_dump(exclude=strip) for k, v in final_incidents(delayed).items()}
    assert b == a
