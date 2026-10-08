"""A08 QA-BUG-002, QA-OBS-003/004/009 and Windows regression coverage."""
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from proctor_contracts.interfaces import NotFoundError
from .test_evidence_api import api, _running_session


def test_long_path_ids_return_4xx_and_client_remains_usable(api):
    sid = _running_session(api, retain_media=False)
    for bad in ('x' * 129, 'x' * 1500, 'bad%20id'):
        for path in (f'/v1/sessions/{bad}/summary', f'/v1/sessions/{sid}/incidents/{bad}',
                     f'/v1/sessions/{sid}/evidence/{bad}'):
            response = api.get(path)
            assert response.status_code == 422, response.text
            assert response.json()['error']['code'] == 'INVALID_ARGUMENT'
            assert api.get('/v1/health').status_code == 200
        response = api.put(f'/v1/sessions/{sid}/answers/{bad}', json={'value': ['a'], 'client_seq': 1})
        assert response.status_code == 422
        response = api.post(f'/v1/sessions/{sid}/incidents/{bad}/reviews',
                            json={'decision': 'dismissed', 'operator': 'qa'})
        assert response.status_code == 422
    api.post(f'/v1/sessions/{sid}/finish')


def test_answers_match_exam_before_persistence(api):
    sid = _running_session(api, retain_media=False)
    bad = [('unknown', ['a']), ('q1', ['unknown']), ('q1', ['a', 'b']),
           ('q1', ['a', 'a']), ('q1', 'a'), ('q2', ['a']), ('q2', 'x' * 201)]
    for seq, (qid, value) in enumerate(bad):
        r = api.put(f'/v1/sessions/{sid}/answers/{qid}', json={'value': value, 'client_seq': seq})
        assert r.status_code == 422, r.text
        assert r.json()['error']['code'] == 'INVALID_ARGUMENT'
    assert api.get(f'/v1/sessions/{sid}/answers').json() == []
    for qid, value in [('q1', ['b']), ('q1', []), ('q2', 'Әлия HTTP')]:
        assert api.put(f'/v1/sessions/{sid}/answers/{qid}', json={'value': value, 'client_seq': 10}).status_code == 200
    api.post(f'/v1/sessions/{sid}/finish')


def test_deleted_session_is_absent_from_all_a08_reads(api):
    sid = _running_session(api, retain_media=False)
    api.post(f'/v1/sessions/{sid}/finish')
    assert api.delete(f'/v1/sessions/{sid}').status_code == 200
    for suffix in ('incidents', 'incidents/any', 'answers', 'summary', 'report.html', 'report.json', 'evidence/any'):
        r = api.get(f'/v1/sessions/{sid}/{suffix}')
        assert r.status_code == 404, (suffix, r.text)
        assert r.json()['error']['code'] == 'SESSION_NOT_FOUND'


def test_delete_calls_public_forget_session_hook(store, fx):
    store.upsert_session(fx.session_info('gone', state='finished'))
    forgotten = []
    ctx = SimpleNamespace(active_session_id=lambda: None, get_session=store.get_session,
                          forget_session=forgotten.append)
    app = FastAPI()
    app.include_router(store.create_router(ctx))
    with TestClient(app) as client:
        assert client.delete('/sessions/gone').status_code == 200
    assert forgotten == ['gone']


def test_symlink_evidence_is_refused_when_os_allows_symlink(store, fx, tmp_path):
    sid = 'symlink'
    store.upsert_session(fx.session_info(sid, retain_media=True))
    store.record_incident_change(fx.incident_change(sid))
    item = store.capture_snapshot(sid, 'inc-1', fx.frame(sid))
    secret = tmp_path / 'outside.jpg'
    secret.write_bytes(b'\xff\xd8\xff outside')
    name = 'b' * 32 + '.jpg'
    try:
        os.symlink(secret, store.vault.root / store._live[sid].media_token / name)
    except OSError as exc:
        if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows symlink privilege unavailable (WinError 1314); OS settings unchanged')
        raise
    store._conn.execute('UPDATE evidence SET file_name=? WHERE evidence_id=?', (name, item.evidence_id))
    with pytest.raises(NotFoundError):
        store.evidence_media(sid, item.evidence_id)


def test_cyrillic_path_atomic_media_and_lock_release(make_store, fx, tmp_path):
    folder = tmp_path / 'Сессии Әлия'
    first = make_store(folder)
    assert first.open() == first.health()
    first.upsert_session(fx.session_info('unicode', retain_media=True))
    first.record_incident_change(fx.incident_change('unicode'))
    item = first.capture_snapshot('unicode', 'inc-1', fx.frame('unicode'))
    assert item is not None and first.evidence_media('unicode', item.evidence_id)[1][:3] == b'\xff\xd8\xff'
    assert not list(folder.rglob('*.tmp'))
    locked = make_store(folder)
    health = locked.open()
    assert health.code == 'store_in_use' and locked.health() == health
    first.upsert_session(fx.session_info('unicode', state='finished', retain_media=True))
    first.close()
    assert locked.open().code == 'ok'
    assert locked.evidence_media('unicode', item.evidence_id)[0].sha256 == item.sha256
    locked.delete_session('unicode')
    assert locked.get_session('unicode') is None
