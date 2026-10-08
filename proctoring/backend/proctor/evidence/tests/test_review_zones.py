"""A08 zone plumbing only. Named assessments are doubles, not a copy of A05 rules."""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from proctor_contracts.v1 import HumanReviewCreate

from proctor.evidence import report, review_zones
from proctor.evidence.review_zones import ZoneAssessment


def test_unpublished_a05_is_explicitly_uncalculated(store, fx):
    store.upsert_session(fx.session_info('pending-zone'))
    summary = store.summary('pending-zone')
    assert summary.review_zone is None and summary.review_zone_rule_version is None
    assert 'не рассчитана' in summary.review_zone_reasons_ru[0]
    snapshot = store.export_snapshot('pending-zone')
    assert report.build_json(snapshot)['summary']['review_zone'] is None
    assert 'Зона не рассчитана' in report.render_html(snapshot)
    assert 'zone-green' not in report.render_html(snapshot).split('</style>', 1)[1]


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
    assert response.json()[0]['review_zone'] is None


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
