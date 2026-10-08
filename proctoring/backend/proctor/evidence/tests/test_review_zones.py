"""A08 zone adapter and plumbing. Real A05 is tested when the integration supplies it."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from proctor_contracts.v1 import HumanReviewCreate

from proctor.evidence import report, review_zones
from proctor.evidence.review_zones import ZoneAssessment


def test_missing_a05_is_explicitly_uncalculated(store, fx, monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(name=name)
    monkeypatch.setattr(review_zones, 'import_module', missing)
    store.upsert_session(fx.session_info('pending-zone'))
    summary = store.summary('pending-zone')
    assert summary.review_zone is None and summary.review_zone_rule_version is None
    assert 'не рассчитана' in summary.review_zone_reasons_ru[0]
    snapshot = store.export_snapshot('pending-zone')
    assert report.build_json(snapshot)['summary']['review_zone'] is None
    assert 'Зона не рассчитана' in report.render_html(snapshot)
    assert 'zone-green' not in report.render_html(snapshot).split('</style>', 1)[1]


def test_adapter_forwards_inputs_and_validates_a05_result(store, fx, monkeypatch):
    calls = []
    incidents, config = [], object()
    store.upsert_session(fx.session_info('adapter'))
    summary = store.summary('adapter')
    def assess(got_incidents, got_summary, got_config):
        calls.append((got_incidents, got_summary, got_config))
        return SimpleNamespace(zone='yellow', reasons_ru=['Причина от A05'], rule_version='delegated-rule',
                               config_version='extra-A05-field')
    monkeypatch.setattr(review_zones, 'import_module', lambda name: SimpleNamespace(assess_session_zone=assess))
    result = review_zones.assess_session_zone(incidents, summary, config)
    assert len(calls) == 1
    assert all(got is sent for got, sent in zip(calls[0], (incidents, summary, config)))
    assert result.model_dump(mode='json') == {
        'zone': 'yellow', 'reasons_ru': ['Причина от A05'], 'rule_version': 'delegated-rule'}


def test_adapter_does_not_hide_broken_a05_dependency(monkeypatch):
    def missing_dependency(name):
        raise ModuleNotFoundError(name='broken_dependency')
    monkeypatch.setattr(review_zones, 'import_module', missing_dependency)
    with pytest.raises(ModuleNotFoundError):
        review_zones.assess_session_zone([], None)


def test_adapter_matches_real_a05_assessment(store, fx):
    zones = pytest.importorskip('proctor.fusion.zones', reason='A05 is supplied by the A01 integration checkout')
    store.upsert_session(fx.session_info('real-assessor', state='finished'))
    summary = store.summary('real-assessor')
    incidents = [fx.incident_change('real-assessor', priority='high').incident]
    config = zones.ZoneConfig(max_reasons=1)
    expected = zones.assess_session_zone(incidents, summary, config)
    actual = review_zones.assess_session_zone(incidents, summary, config)
    assert actual.zone.value == expected.zone == 'red'
    assert actual.reasons_ru == expected.reasons_ru
    assert actual.rule_version == expected.rule_version == 'zone-rule-1'


def test_overview_sorts_priority_then_newest_and_excludes_deleted(store, fx, monkeypatch):
    zones = {'green': 'green', 'grey': 'grey', 'red-old': 'red', 'yellow': 'yellow', 'red-new': 'red'}
    monkeypatch.setattr(review_zones, 'assess_session_zone',
        lambda incidents, summary, config: ZoneAssessment(zone=zones[summary.session.session_id],
            reasons_ru=['fixture assessment'], rule_version='qa-fixture-not-A05'))
    for index, name in enumerate(zones):
        store.upsert_session(fx.session_info(name, state='finished', created_at=fx.T0 + timedelta(seconds=index)))
    assert [r.session.session_id for r in store.overview()] == ['red-new', 'red-old', 'yellow', 'grey', 'green']
    store.delete_session('red-new')
    assert [r.session.session_id for r in store.overview()] == ['red-old', 'yellow', 'grey', 'green']


def test_teacher_review_changes_pending_count_but_not_zone_input(store, fx, monkeypatch):
    inputs = []
    def assess(incidents, summary, config):
        inputs.append([i.review_status.value for i in incidents])
        return ZoneAssessment(zone='yellow', reasons_ru=['fixture'], rule_version='qa-fixture-not-A05')
    monkeypatch.setattr(review_zones, 'assess_session_zone', assess)
    store.upsert_session(fx.session_info('review'))
    store.record_incident_change(fx.incident_change('review', priority='medium'))
    before = store.overview()[0]
    store.add_review('review', 'inc-1', HumanReviewCreate(decision='dismissed', operator='qa'))
    after = store.overview()[0]
    assert before.review_zone == after.review_zone == 'yellow'
    assert (before.pending_reviews, after.pending_reviews) == (1, 0)
    assert after.incidents_total == 1 and after.incidents_by_priority == {'low': 0, 'medium': 1, 'high': 0}
    assert inputs == [['pending'], ['pending']]


def test_overview_router_returns_exact_field_names(store, fx):
    store.upsert_session(fx.session_info('row'))
    ctx = SimpleNamespace(get_session=store.get_session, active_session_id=lambda: 'row')
    app = FastAPI()
    app.include_router(store.create_router(ctx), prefix='/v1')
    with TestClient(app) as client:
        response = client.get('/v1/sessions/overview')
    assert response.status_code == 200
    assert set(response.json()[0]) == {'session', 'review_zone', 'reasons_ru', 'incidents_total',
                                      'incidents_by_priority', 'pending_reviews'}
    assert response.json()[0]['review_zone'] == store.summary('row').model_dump(mode='json')['review_zone']


def test_zone_reasons_are_bounded_and_escaped(store, fx, monkeypatch):
    with pytest.raises(ValidationError):
        ZoneAssessment(reasons_ru=['1', '2', '3', '4'])
    monkeypatch.setattr(review_zones, 'assess_session_zone',
        lambda incidents, summary, config: ZoneAssessment(zone='red',
            reasons_ru=['<script>alert(1)</script> — 00:41, 7 с'], rule_version='qa-fixture-not-A05'))
    store.upsert_session(fx.session_info('escape'))
    snap = store.export_snapshot('escape')
    page = report.render_html(snap)
    assert 'Проверить в первую очередь' in page and 'zone-red' in page
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert page.index('Проверить в первую очередь') < page.index('Как читать отчёт')
    exported = report.build_json(snap)['summary']
    assert exported['review_zone_rule_version'] == 'qa-fixture-not-A05'
    assert exported['review_zone_reasons_ru'] == snap.summary.review_zone_reasons_ru
