"""`python -m proctor.fusion.replay` on an exported observation log (JSON Lines)."""

from __future__ import annotations

import json
from pathlib import Path

from proctor.fusion.replay import load_observations, main
from proctor.fusion.scenario import build_scenario, load_golden
from proctor_contracts.v1 import IncidentChange

GOLDEN = Path(__file__).parent / "golden" / "g03_phone_with_gaze_down_correlated.json"


def _export(tmp_path: Path) -> Path:
    path = tmp_path / "observations.jsonl"
    path.write_text("\n".join(o.model_dump_json() for o in build_scenario(load_golden(GOLDEN))), encoding="utf-8")
    return path


def test_replay_cli_table_and_json(tmp_path, capsys):
    path = _export(tmp_path)
    assert len(load_observations(path)) > 100
    assert main([str(path), "--tick-ms", "250", "--finish-ms", "14000"]) == 0
    table = capsys.readouterr().out
    assert "phone_visible" in table and "gaze_prolonged_down" in table and "high" in table
    assert "Телефон виден 3,9 с" in table

    assert main([str(path), "--json", "--finish-ms", "14000"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    changes = [IncidentChange.model_validate_json(line) for line in lines]
    assert {c.incident.rule_id.value for c in changes} == {"phone_visible", "gaze_prolonged_down"}


def test_replay_cli_config_override_changes_version(tmp_path, capsys):
    path = _export(tmp_path)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"gaze_prolonged_down": {"min_duration_ms": 5000}}), encoding="utf-8")
    assert main([str(path), "--config", str(cfg), "--finish-ms", "14000"]) == 0
    out = capsys.readouterr().out
    assert "gaze_prolonged_down" not in out.split("\n", 1)[1]  # 3.9 s of "down" < 5 s
    assert "a05-default-1+" in out
