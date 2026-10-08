"""A07 verifies the real A08 HTML renderer against additive contract 1.1 episodes (synthetic only)."""
from dataclasses import replace
from pathlib import Path
import os

import pytest

from proctor.evidence import report
from proctor.evidence.sample import SID, build_sample
from proctor_contracts.v1 import Incident, IncidentDetail, IncidentRule, IncidentCategory

NEW_RULES = [
    ("background_speech", "audio", "Возможная речь рядом", "звук"),
    ("headphones_visible", "objects", "Видны наушники", "предметы"),
    ("identity_mismatch", "identity", "Лицо не совпадает с началом экзамена", "личность"),
    ("foreign_object_visible", "objects", "Посторонний предмет в кадре", "предметы"),
    ("second_screen_visible", "objects", "Второй экран или ноутбук в кадре", "предметы"),
]

@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    store = build_sample(tmp_path_factory.mktemp("adal-report") / "data")
    try:
        snap = store.export_snapshot(SID)
    finally:
        store.close()
    raw = snap.incidents[0].incident.model_dump(mode="json")
    incidents = []
    for rule, category, label, _ in NEW_RULES:
        body = {**raw, "incident_id": f"fixture-{rule}", "rule_id": rule, "category": category,
                "observation_ids": [], "observation_count": 0, "evidence_ids": [], "review_status": "pending",
                "max_confidence": None, "mean_quality": None, "trigger_frame_id": None,
                "explanation": {"summary_ru": f"FIXTURE: {label}", "facts": [], "caveats_ru": ["Синтетическая проверка подписи, не работа детектора."]}}
        incidents.append(IncidentDetail(incident=Incident.model_validate(body)))
    snapshot = replace(snap, incidents=incidents, incidents_total=len(incidents), evidence={}, observations=[], observations_total=0)
    snapshot.summary = snap.summary.model_copy(update={"incidents_total": len(incidents), "incidents_by_rule": {r[0]: 1 for r in NEW_RULES}, "reviews_by_decision": {"pending": len(incidents)}})
    page = report.render_html(snapshot)
    if out := os.environ.get("ADAL_REPORT_OUTPUT"):
        Path(out).write_text(page, encoding="utf-8")
    return page

def test_all_contract_rules_and_categories_have_report_labels():
    assert not {x.value for x in IncidentRule} - report.RULE_RU.keys()
    assert not {x.value for x in IncidentCategory} - report.CATEGORY_RU.keys()

@pytest.mark.parametrize("rule,category,label,category_ru", NEW_RULES)
def test_new_rules_render_in_table_and_card(rendered, rule, category, label, category_ru):
    assert f"<td>{label}</td>" in rendered
    assert any(label in h.split("</h3>")[0] for h in rendered.split("<h3>")[1:])
    assert f'<span class="tag">{category_ru}</span>' in rendered
    assert "СИНТЕТИЧЕСКИЕ ДАННЫЕ" in rendered
